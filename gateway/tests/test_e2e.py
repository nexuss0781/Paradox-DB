"""End-to-end integration tests for Paradox-DB Gateway.

These tests mock the Telegram API and test the full flow:
register → upload → download → versions → rollback
"""

import base64
import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import app.database as _db_mod

_db_mod.engine = MagicMock()
_db_mod.async_session_factory = MagicMock()

import app.metrics as _metrics_mod


class _FixedMetricsMiddleware:
    def __init__(self, app_obj):
        self.app = app_obj

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


_metrics_mod.MetricsMiddleware = _FixedMetricsMiddleware

with patch("app.main.init_db", new_callable=AsyncMock), \
     patch("app.main.close_db", new_callable=AsyncMock):
    pass

import httpx
import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from starlette.middleware import Middleware

from app.auth import get_current_user, get_db
from app.main import app
from app.models import DatabaseVersion, SyncLog, UserChannel, VersionHistory

fixed_middlewares = []
for mw in app.user_middleware:
    if mw.cls.__name__ == "MetricsMiddleware":
        fixed_middlewares.append(Middleware(_FixedMetricsMiddleware))
    else:
        fixed_middlewares.append(mw)
app.user_middleware = fixed_middlewares
app.middleware_stack = None

USER_ID = "u_e2e_user"
API_KEY = "pk_e2e_test_key"


def _mock_user(user_id: str = USER_ID) -> MagicMock:
    user = MagicMock(spec=UserChannel)
    user.user_id = user_id
    user.channel_id = "-100999888"
    user.bot_token_id = "123456:ABC-test"
    user.api_key_hash = "abc123"
    user.created_at = datetime.now(timezone.utc)
    return user


def _mock_db_version(
    user_id: str = USER_ID,
    database_name: str = "test.db",
    version: int = 1,
    message_id: str = "100",
    file_hash: str = "sha256_default",
) -> MagicMock:
    dv = MagicMock(spec=DatabaseVersion)
    dv.user_id = user_id
    dv.database_name = database_name
    dv.latest_version = version
    dv.latest_message_id = message_id
    dv.file_hash = file_hash
    dv.uploaded_at = datetime.now(timezone.utc)
    return dv


def _mock_version_history(
    user_id: str = USER_ID,
    database_name: str = "test.db",
    version: int = 1,
    message_id: str = "100",
    file_hash: str = "sha256_default",
    file_size: int = 100,
) -> MagicMock:
    vh = MagicMock(spec=VersionHistory)
    vh.user_id = user_id
    vh.database_name = database_name
    vh.version = version
    vh.message_id = message_id
    vh.file_hash = file_hash
    vh.file_size = file_size
    vh.version_type = "full"
    vh.uploaded_at = datetime.now(timezone.utc)
    return vh


def _mock_sync_log(
    status: str = "completed",
    operation: str = "upload",
) -> MagicMock:
    sl = MagicMock(spec=SyncLog)
    sl.request_id = str(uuid.uuid4())
    sl.user_id = USER_ID
    sl.database_name = "test.db"
    sl.operation = operation
    sl.telegram_message_id = "100"
    sl.status = status
    sl.error_message = None
    sl.created_at = datetime.now(timezone.utc)
    sl.completed_at = datetime.now(timezone.utc)
    return sl


def _make_mock_result(scalars=None, scalar_one=None):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scalar_one
    if scalars is not None:
        mock_result.scalars.return_value.all.return_value = scalars
    return mock_result


class _E2EMockSession:
    """Stateful mock session that tracks uploaded versions per database.

    Uses compiled SQL params and table name detection to route queries.
    """

    def __init__(self):
        self._versions: dict[str, list[dict]] = {}
        self._db_versions: dict[str, MagicMock] = {}
        self.added_objects: list = []

    def _key(self, user_id: str, db_name: str) -> str:
        return f"{user_id}:{db_name}"

    def add_upload(
        self, user_id: str, db_name: str, version: int, message_id: str, file_bytes: bytes
    ):
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        key = self._key(user_id, db_name)
        self._versions.setdefault(key, [])
        self._versions[key].append(
            {
                "version": version,
                "message_id": message_id,
                "file_hash": file_hash,
                "file_size": len(file_bytes),
            }
        )
        self._db_versions[key] = _mock_db_version(
            user_id=user_id,
            database_name=db_name,
            version=version,
            message_id=message_id,
            file_hash=file_hash,
        )

    def _get_version_entries(self, user_id: str, db_name: str) -> list[dict]:
        key = self._key(user_id, db_name)
        return self._versions.get(key, [])

    def _get_db_version(self, user_id: str, db_name: str) -> MagicMock | None:
        key = self._key(user_id, db_name)
        return self._db_versions.get(key)

    def _get_all_db_versions(self, user_id: str) -> list[MagicMock]:
        return [
            dv
            for key, dv in self._db_versions.items()
            if key.split(":", 1)[0] == user_id
        ]

    def _extract_params(self, stmt) -> dict:
        try:
            return stmt.compile().params
        except Exception:
            return {}

    async def execute(self, stmt):
        stmt_str = str(stmt)
        params = self._extract_params(stmt)

        user_id = params.get("user_id_1", "")
        db_name = params.get("database_name_1", "")
        version = params.get("version_1")

        # version_history table
        if "version_history" in stmt_str:
            entries = self._get_version_entries(user_id, db_name) if user_id and db_name else []

            if version is not None:
                for entry in entries:
                    if entry["version"] == version:
                        return _make_mock_result(
                            scalar_one=_mock_version_history(
                                user_id=user_id,
                                database_name=db_name,
                                version=entry["version"],
                                message_id=entry["message_id"],
                                file_hash=entry["file_hash"],
                                file_size=entry["file_size"],
                            )
                        )
                return _make_mock_result(scalar_one=None)

            sorted_entries = sorted(entries, key=lambda e: e["version"], reverse=True)
            mocks = [
                _mock_version_history(
                    user_id=user_id,
                    database_name=db_name,
                    version=e["version"],
                    message_id=e["message_id"],
                    file_hash=e["file_hash"],
                    file_size=e["file_size"],
                )
                for e in sorted_entries
            ]
            return _make_mock_result(scalars=mocks)

        # database_versions table
        if "database_versions" in stmt_str:
            if db_name:
                dv = self._get_db_version(user_id, db_name)
                return _make_mock_result(scalar_one=dv)
            results = self._get_all_db_versions(user_id)
            return _make_mock_result(scalars=results if results else [])

        # sync_log table
        if "sync_log" in stmt_str:
            return _make_mock_result(scalar_one=_mock_sync_log())

        # user_channels table
        if "user_channels" in stmt_str:
            return _make_mock_result(scalar_one=_mock_user())

        return _make_mock_result(scalars=[], scalar_one=None)

    def add(self, obj):
        self.added_objects.append(obj)
        if isinstance(obj, DatabaseVersion):
            key = self._key(obj.user_id, obj.database_name)
            self._db_versions[key] = obj

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_session():
    return _E2EMockSession()


@pytest.fixture
def mock_auth(mock_session):
    user = _mock_user()

    async def _get_user(request: Request):
        return user

    async def _get_db():
        yield mock_session

    app.dependency_overrides[get_current_user] = _get_user
    app.dependency_overrides[get_db] = _get_db
    app.middleware_stack = None
    yield user, mock_session
    app.dependency_overrides.clear()
    app.middleware_stack = None


@pytest.fixture(autouse=True)
def mock_redis_lock():
    """Mock the Redis-based upload lock so tests don't need Redis."""
    with patch("app.routers.upload._upload_lock") as mock_lock:
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        yield mock_lock


# ── 1. Full sync flow: upload → download ────────────────────────────


@pytest.mark.asyncio
async def test_full_sync_flow(client: AsyncClient, mock_auth):
    """Upload a file and download it back; verify content matches."""
    user, session = mock_auth
    file_bytes = b"paradox database content v1"
    file_b64 = base64.b64encode(file_bytes).decode()

    with (
        patch("app.routers.upload.TelegramClient") as MockUploadTG,
        patch("app.routers.download.TelegramClient") as MockDownloadTG,
    ):
        MockUploadTG.return_value.upload_file = AsyncMock(return_value="200")
        MockDownloadTG.return_value.download_file = AsyncMock(return_value=file_bytes)

        resp_upload = await client.post(
            "/v1/upload",
            json={"database_name": "sync.db", "file_data": file_b64},
            headers={"X-API-Key": API_KEY},
        )

    assert resp_upload.status_code == 200
    upload_data = resp_upload.json()
    assert upload_data["version"] == 1
    assert upload_data["message_id"] == "200"

    with patch("app.routers.download.TelegramClient") as MockDL:
        MockDL.return_value.download_file = AsyncMock(return_value=file_bytes)

        resp_download = await client.get(
            "/v1/download",
            params={"database_name": "sync.db"},
            headers={"X-API-Key": API_KEY},
        )

    assert resp_download.status_code == 200
    assert resp_download.content == file_bytes
    assert resp_download.headers["X-Version"] == "1"
    assert resp_download.headers["content-type"] == "application/octet-stream"


# ── 2. Upload multiple versions, check /versions ───────────────────


@pytest.mark.asyncio
async def test_upload_then_versions(client: AsyncClient, mock_auth):
    """Verify /versions returns all uploaded versions for a database."""
    user, session = mock_auth

    v1_bytes = b"version 1 data"
    v2_bytes = b"version 2 data"
    v3_bytes = b"version 3 data"

    session.add_upload(USER_ID, "multi.db", 1, "101", v1_bytes)
    session.add_upload(USER_ID, "multi.db", 2, "102", v2_bytes)
    session.add_upload(USER_ID, "multi.db", 3, "103", v3_bytes)

    resp = await client.get(
        "/v1/versions",
        params={"database_name": "multi.db"},
        headers={"X-API-Key": API_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["database_name"] == "multi.db"
    assert len(data["versions"]) == 3
    versions = sorted(data["versions"], key=lambda v: v["version"])
    assert versions[0]["version"] == 1
    assert versions[0]["message_id"] == "101"
    assert versions[1]["version"] == 2
    assert versions[1]["message_id"] == "102"
    assert versions[2]["version"] == 3
    assert versions[2]["message_id"] == "103"


# ── 3. Conflict detection ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_conflict_detection(client: AsyncClient, mock_auth):
    """Upload v1, then try to upload with client_version=0; expect 409."""
    user, session = mock_auth
    file_bytes = b"conflict test data"
    file_b64 = base64.b64encode(file_bytes).decode()

    session.add_upload(USER_ID, "conflict.db", 1, "300", file_bytes)

    with patch("app.routers.upload.TelegramClient") as MockTG:
        MockTG.return_value.upload_file = AsyncMock(return_value="301")

        resp = await client.post(
            "/v1/upload",
            json={
                "database_name": "conflict.db",
                "file_data": file_b64,
                "version": 0,
            },
            headers={"X-API-Key": API_KEY},
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "conflict_detected"
    assert body["remote_version"] == 1
    assert body["your_version"] == 0
    assert body["resolution"] == "pull_before_push"


# ── 4. Download a specific version ─────────────────────────────────


@pytest.mark.asyncio
async def test_download_specific_version(client: AsyncClient, mock_auth):
    """Upload v1 and v2, then download v1 specifically."""
    user, session = mock_auth
    v1_bytes = b"version 1 content"
    v2_bytes = b"version 2 content"

    session.add_upload(USER_ID, "specific.db", 1, "400", v1_bytes)
    session.add_upload(USER_ID, "specific.db", 2, "401", v2_bytes)

    with patch("app.routers.download.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=v1_bytes)

        resp = await client.get(
            "/v1/download",
            params={"database_name": "specific.db", "version": 1},
            headers={"X-API-Key": API_KEY},
        )

    assert resp.status_code == 200
    assert resp.content == v1_bytes
    assert resp.headers["X-Version"] == "1"


# ── 5. Rollback flow ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_flow(client: AsyncClient, mock_auth):
    """Upload v1 and v2, rollback to v1, verify a new version is created."""
    user, session = mock_auth
    v1_bytes = b"rollback version 1"
    v2_bytes = b"rollback version 2"

    session.add_upload(USER_ID, "rollback.db", 1, "500", v1_bytes)
    session.add_upload(USER_ID, "rollback.db", 2, "501", v2_bytes)

    with patch("app.routers.rollback.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=v1_bytes)
        MockTG.return_value.upload_file = AsyncMock(return_value="502")

        resp = await client.post(
            "/v1/rollback",
            json={"database_name": "rollback.db", "target_version": 1},
            headers={"X-API-Key": API_KEY},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "request_id" in body
    assert body["rolled_back_to"] == 1
    assert body["new_message_id"] == "502"


# ── 6. Unauthenticated access returns 401 ──────────────────────────


@pytest.mark.asyncio
async def test_unauthenticated_access(client: AsyncClient):
    """All protected endpoints return 401 without auth."""
    endpoints = [
        ("GET", "/v1/download", {"database_name": "test.db"}),
        ("GET", "/v1/versions", {"database_name": "test.db"}),
        ("GET", "/v1/status", {}),
        ("POST", "/v1/upload", {"database_name": "test.db", "file_data": "dGVzdA=="}),
        ("POST", "/v1/rollback", {"database_name": "test.db", "target_version": 1}),
    ]

    for method, path, body_or_params in endpoints:
        if method == "GET":
            resp = await client.get(path, params=body_or_params)
        else:
            resp = await client.post(path, json=body_or_params)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"


# ── 7. Status after upload ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_after_upload(client: AsyncClient, mock_auth):
    """Upload a database, then check /status shows correct version info."""
    user, session = mock_auth
    file_bytes = b"status test data"

    session.add_upload(USER_ID, "status.db", 1, "600", file_bytes)

    resp = await client.get(
        "/v1/status",
        headers={"X-API-Key": API_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == USER_ID
    assert len(data["databases"]) == 1
    db_info = data["databases"][0]
    assert db_info["name"] == "status.db"
    assert db_info["latest_version"] == 1
    assert db_info["latest_message_id"] == "600"
    assert db_info["pending_changesets"] == 0
