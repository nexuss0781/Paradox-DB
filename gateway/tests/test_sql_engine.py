"""Tests for the persistent server-side SQL engine + API."""

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers.sql import get_current_user, get_db
from app.routers import sql as sql_router
from app.routers.sql import session_store
from app.services.session_engine import (
    SqlExecutionError,
    SqlSessionStore,
    encode_value,
    is_commit,
    is_sqlite_bytes,
)


def _fresh_snapshot() -> bytes:
    """Create a small plaintext SQLite file in memory."""
    import tempfile
    from pathlib import Path

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO items (name) VALUES ('seed')")
    conn.commit()
    data = Path(tmp.name).read_bytes()
    conn.close()
    Path(tmp.name).unlink()
    return data


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def reset_store():
    session_store._sessions.clear()
    session_store._persisters.clear()
    yield
    session_store._sessions.clear()
    session_store._persisters.clear()


# ── unit: session engine ─────────────────────────────────────────


class TestSessionEngine:
    @pytest.mark.asyncio
    async def test_fresh_session_create_table_insert_select(self):
        store = SqlSessionStore()
        session, created = await store.get_or_create("u:db", "", b"", 0)
        assert created
        await store.execute(session, "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        await store.execute(session, "INSERT INTO t (name) VALUES (?)", ["alice"])
        result = await store.execute(session, "SELECT id, name FROM t")
        assert result.columns == ["id", "name"]
        assert result.rows == [[1, "alice"]]
        assert session.dirty
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_commit_persists_new_version(self):
        store = SqlSessionStore()
        session, _ = await store.get_or_create("u:db", "", b"", 0)
        versions = []

        async def persister(sess, raw):
            versions.append((sess.key, raw))
            return len(versions)

        await store.execute(session, "CREATE TABLE t (x INTEGER)")
        await store.execute(session, "INSERT INTO t VALUES (1)")
        version = await store.persist(session, persister)
        assert version == 1
        assert not session.dirty
        assert len(versions) == 1
        # committed state is now the baseline — new inserts dirty again
        await store.execute(session, "INSERT INTO t VALUES (2)")
        assert session.dirty
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_plaintext_snapshot_detection(self):
        store = SqlSessionStore()
        snap = _fresh_snapshot()
        assert is_sqlite_bytes(snap)
        session, _ = await store.get_or_create("u:db", "", snap, 5)
        assert session.mode == "plain"
        assert session.base_version == 5
        result = await store.execute(session, "SELECT name FROM items")
        assert result.rows == [["seed"]]
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_encrypted_snapshot_requires_passphrase(self):
        from app.crypto import encrypt_data

        store = SqlSessionStore()
        snap = encrypt_data(_fresh_snapshot(), "hunter2")
        with pytest.raises(ValueError):
            await store.get_or_create("u:db", "", snap, 3)
        session, _ = await store.get_or_create("u:db", "hunter2", snap, 3)
        assert session.mode == "encrypted"
        result = await store.execute(session, "SELECT name FROM items")
        assert result.rows == [["seed"]]
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_begin_commit_spans_statements(self):
        store = SqlSessionStore()
        session, _ = await store.get_or_create("u:db", "", b"", 0)
        await store.execute(session, "CREATE TABLE t (x INTEGER)")
        await store.execute(session, "BEGIN")
        await store.execute(session, "INSERT INTO t VALUES (100)")
        result = await store.execute(session, "SELECT count(*) FROM t")
        assert result.rows == [[1]]  # visible inside the transaction
        await store.execute(session, "COMMIT")
        assert session.dirty
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_rollback_discards_changes(self):
        store = SqlSessionStore()
        session, _ = await store.get_or_create("u:db", "", b"", 0)
        versions = []

        async def persister(sess, raw):
            versions.append(len(versions) + 1)
            return versions[-1]

        await store.execute(session, "CREATE TABLE t (x INTEGER)")
        await store.persist(session, persister)  # baseline = table exists
        assert versions == [1]

        await store.execute(session, "BEGIN")
        await store.execute(session, "INSERT INTO t VALUES (1)")
        await store.execute(session, "ROLLBACK")
        result = await store.execute(session, "SELECT count(*) FROM t")
        assert result.rows == [[0]]
        assert not session.dirty  # net change is zero after rollback

        # persist after a rolled-back transaction must NOT mint a version
        version = await store.persist(session, persister)
        assert version == 1
        assert versions == [1]

        # real changes still persist
        await store.execute(session, "INSERT INTO t VALUES (2)")
        assert session.dirty
        version = await store.persist(session, persister)
        assert version == 2
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_sql_error_raises_execution_error(self):
        store = SqlSessionStore()
        session, _ = await store.get_or_create("u:db", "", b"", 0)
        with pytest.raises(SqlExecutionError):
            await store.execute(session, "SELECT * FROM missing_table")
        await store.evict(session.key)

    @pytest.mark.asyncio
    async def test_blob_roundtrip(self):
        store = SqlSessionStore()
        session, _ = await store.get_or_create("u:db", "", b"", 0)
        await store.execute(session, "CREATE TABLE b (data BLOB)")
        await store.execute(session, "INSERT INTO b VALUES (?)", [b"\x00\x01\xff"])
        result = await store.execute(session, "SELECT data FROM b")
        assert result.rows[0][0]["__parad_bytes__"]  # encoded on the wire
        await store.evict(session.key)


def test_helpers():
    assert is_commit("COMMIT")
    assert is_commit("  commit ;")
    assert not is_commit("SELECT 1")
    assert is_sqlite_bytes(b"SQLite format 3\x00junk")
    assert not is_sqlite_bytes(b"not sqlite")
    assert encode_value(b"abc") == {"__parad_bytes__": "YWJj"}


# ── API: endpoint ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_sql_endpoint(client, reset_store):
    user = MagicMock()
    user.id = "user-1"
    paradox_db = MagicMock()
    paradox_db.id = "db-1"
    paradox_db.user_id = "user-1"
    paradox_db.name = "test-db"
    paradox_db.latest_version = 0
    paradox_db.latest_message_id = None

    fake_db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = paradox_db
    fake_db.execute.return_value = result_mock

    async def _get_db():
        yield fake_db

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _get_db
    persist_mock = AsyncMock(return_value=1)
    with patch("app.routers.sql._load_snapshot", new_callable=AsyncMock) as load:
        load.return_value = (b"", 0)
        with patch("app.routers.sql.persist_snapshot", persist_mock):
            try:
                resp = await client.post(
                    "/v1/databases/db-1/sql",
                    json={"sql": "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"},
                )
                assert resp.status_code == 200
                body = resp.json()
                assert body["columns"] is None

                resp = await client.post(
                    "/v1/databases/db-1/sql",
                    json={"sql": "INSERT INTO t (name) VALUES (?)", "params": ["alice"]},
                )
                assert resp.status_code == 200
                assert resp.json()["lastrowid"] == 1

                resp = await client.post(
                    "/v1/databases/db-1/sql",
                    json={"sql": "SELECT name FROM t"},
                )
                assert resp.status_code == 200
                assert resp.json()["rows"] == [["alice"]]

                resp = await client.post(
                    "/v1/databases/db-1/sql",
                    json={"sql": "COMMIT"},
                )
                assert resp.status_code == 200
                assert resp.json()["persisted_version"] == 1
                persist_mock.assert_awaited()
            finally:
                app.dependency_overrides.pop(get_current_user, None)
                app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_execute_sql_error_returns_400(client, reset_store):
    user = MagicMock()
    user.id = "user-1"
    paradox_db = MagicMock()
    paradox_db.id = "db-1"
    paradox_db.user_id = "user-1"
    paradox_db.name = "test-db"
    paradox_db.latest_version = 0
    paradox_db.latest_message_id = None

    fake_db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = paradox_db
    fake_db.execute.return_value = result_mock

    async def _get_db():
        yield fake_db

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _get_db
    with patch("app.routers.sql._load_snapshot", new_callable=AsyncMock) as load:
        load.return_value = (b"", 0)
        try:
            resp = await client.post(
                "/v1/databases/db-1/sql",
                json={"sql": "SELECT * FROM nope"},
            )
            assert resp.status_code == 400
            assert resp.json()["error"] == "sql_error"
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_session_endpoints(client, reset_store):
    user = MagicMock()
    user.id = "user-1"
    paradox_db = MagicMock()
    paradox_db.id = "db-1"
    paradox_db.user_id = "user-1"
    paradox_db.name = "test-db"
    paradox_db.latest_version = 0
    paradox_db.latest_message_id = None

    fake_db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = paradox_db
    fake_db.execute.return_value = result_mock

    async def _get_db():
        yield fake_db

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _get_db
    with patch("app.routers.sql._load_snapshot", new_callable=AsyncMock) as load:
        load.return_value = (b"", 0)
        try:
            resp = await client.get("/v1/databases/db-1/sql/session")
            assert resp.status_code == 200
            assert resp.json()["session"] is None

            resp = await client.post(
                "/v1/databases/db-1/sql/session", json={"sql": "SELECT 1"}
            )
            assert resp.status_code == 200
            assert resp.json()["mode"] == "plain"

            resp = await client.get("/v1/databases/db-1/sql/session")
            assert resp.status_code == 200
            assert resp.json()["session"]["id"] == "user-1:db-1"

            resp = await client.delete("/v1/databases/db-1/sql/session")
            assert resp.status_code == 200
            assert resp.json()["closed"] is True

            resp = await client.get("/v1/databases/db-1/sql/session")
            assert resp.status_code == 200
            assert resp.json()["session"] is None
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)
