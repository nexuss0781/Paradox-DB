"""End-to-end integration tests for Paradox-DB Gateway.

Tests the full flow against the new phase-4 architecture:
register → project → database → upload → download → versions → rollback

Telegram API is mocked. Auth is mocked to bypass real DB.
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
from app.models import DatabaseVersion, ParadoxDB, Project, SyncLog, User

fixed_middlewares = []
for mw in app.user_middleware:
    if mw.cls.__name__ == "MetricsMiddleware":
        fixed_middlewares.append(Middleware(_FixedMetricsMiddleware))
    else:
        fixed_middlewares.append(mw)
app.user_middleware = fixed_middlewares
app.middleware_stack = None

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
API_KEY = "pk_e2e_test_key"
PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DB_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def _mock_user():
    user = MagicMock(spec=User)
    user.id = USER_ID
    user.email = "e2e@test.com"
    user.username = "e2e_user"
    user.is_active = True
    user.api_key_hash = hashlib.sha256(API_KEY.encode()).hexdigest()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def _mock_project():
    p = MagicMock(spec=Project)
    p.id = PROJECT_ID
    p.user_id = USER_ID
    p.name = "e2e_project"
    p.description = "test"
    p.created_at = datetime.now(timezone.utc)
    p.updated_at = datetime.now(timezone.utc)
    return p


def _mock_paradox_db(
    name: str = "test",
    latest_version: int = 0,
    latest_message_id: str | None = None,
    file_hash: str | None = None,
):
    pdb = MagicMock(spec=ParadoxDB)
    pdb.id = DB_ID
    pdb.project_id = PROJECT_ID
    pdb.user_id = USER_ID
    pdb.name = name
    pdb.description = None
    pdb.latest_version = latest_version
    pdb.latest_message_id = latest_message_id
    pdb.file_hash = file_hash
    pdb.created_at = datetime.now(timezone.utc)
    pdb.updated_at = datetime.now(timezone.utc)
    return pdb


def _mock_db_version(
    version: int = 1,
    message_id: str = "100",
    file_hash: str = "sha256_default",
    file_size: int = 100,
):
    dv = MagicMock(spec=DatabaseVersion)
    dv.id = uuid.uuid4()
    dv.db_id = DB_ID
    dv.version_number = version
    dv.file_hash = file_hash
    dv.file_size = file_size
    dv.message_id = message_id
    dv.notes = None
    dv.created_by = USER_ID
    dv.created_at = datetime.now(timezone.utc)
    return dv


def _mock_sync_log(
    status: str = "completed",
    operation: str = "upload",
):
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
    mock_result.scalars.return_value.all.return_value = scalars or []
    mock_result.scalars.return_value.first.return_value = scalar_one
    mock_result.scalar_one.return_value = scalar_one
    return mock_result


class _E2EMockSession:
    """Stateful mock session tracking uploads per database."""

    def __init__(self):
        self._versions: dict[str, list[dict]] = {}
        self._db_versions: dict[str, list[MagicMock]] = {}
        self._paradox_dbs: dict[str, MagicMock] = {}
        self.added_objects: list = []

    def _key(self, user_id, db_name: str) -> str:
        uid = str(user_id) if user_id else ""
        return f"{uid}:{db_name}"

    def add_upload(self, user_id, db_name: str, version: int, message_id: str, file_bytes: bytes):
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        key = self._key(user_id, db_name)
        self._versions.setdefault(key, [])
        self._versions[key].append({
            "version": version,
            "message_id": message_id,
            "file_hash": file_hash,
            "file_size": len(file_bytes),
        })
        dv = _mock_db_version(
            version=version, message_id=message_id, file_hash=file_hash,
            file_size=len(file_bytes),
        )
        self._db_versions.setdefault(key, [])
        self._db_versions[key].append(dv)
        if key in self._paradox_dbs:
            self._paradox_dbs[key].latest_version = version
            self._paradox_dbs[key].latest_message_id = message_id
            self._paradox_dbs[key].file_hash = file_hash

    def register_db(self, user_id, db_name: str):
        key = self._key(user_id, db_name)
        pdb = _mock_paradox_db(name=db_name)
        self._paradox_dbs[key] = pdb
        return pdb

    def _get_version_entries(self, user_id, db_name: str) -> list[dict]:
        return self._versions.get(self._key(user_id, db_name), [])

    def _get_db_versions(self, user_id, db_name: str) -> list[MagicMock]:
        return self._db_versions.get(self._key(user_id, db_name), [])

    def _get_latest_db_version(self, user_id, db_name: str) -> MagicMock | None:
        versions = self._get_db_versions(user_id, db_name)
        if versions:
            return versions[-1]
        return None

    def _extract_params(self, stmt) -> dict:
        try:
            return stmt.compile().params
        except Exception:
            return {}

    async def execute(self, stmt):
        stmt_str = str(stmt)
        params = self._extract_params(stmt)

        user_id = params.get("user_id_1") or params.get("user_id")
        db_name = params.get("name_1") or params.get("database_name_1") or params.get("database_name")
        version = params.get("version_number_1") or params.get("version_number") or params.get("version_1") or params.get("target_version")
        db_id = params.get("db_id_1") or params.get("db_id") or params.get("id_1") or params.get("id")

        if "paradox_dbs" in stmt_str:
            # Try match by db_id
            if db_id:
                for pdb in self._paradox_dbs.values():
                    if str(pdb.id) == str(db_id):
                        return _make_mock_result(scalar_one=pdb)
                return _make_mock_result(scalar_one=None)
            # Try match by user_id + name
            if user_id and db_name:
                pdb = self._paradox_dbs.get(self._key(user_id, db_name))
                if pdb:
                    return _make_mock_result(scalar_one=pdb)
            # Try match by user_id only (list)
            if user_id and not db_name:
                results = [v for k, v in self._paradox_dbs.items() if k.startswith(str(user_id))]
                return _make_mock_result(scalars=results)
            # Fallback: return mock for any db_name query
            if db_name:
                uid = user_id or str(uuid.uuid4())
                pdb = _mock_paradox_db(name=db_name)
                self._paradox_dbs[self._key(uid, db_name)] = pdb
                return _make_mock_result(scalar_one=pdb)
            return _make_mock_result(scalar_one=None)

        if "database_versions" in stmt_str:
            db_id_str = str(db_id) if db_id else None
            # Match by db_id + version_number
            if db_id_str and version:
                for key, dv_list in self._db_versions.items():
                    for dv in dv_list:
                        if str(dv.db_id) == db_id_str and dv.version_number == version:
                            return _make_mock_result(scalar_one=dv)
                return _make_mock_result(scalar_one=None)
            # Match by db_id (list all versions)
            if db_id_str:
                results = []
                for dv_list in self._db_versions.values():
                    for dv in dv_list:
                        if str(dv.db_id) == db_id_str:
                            results.append(dv)
                return _make_mock_result(scalars=results if results else None)
            # Match by name
            if db_name:
                dv = self._get_latest_db_version(user_id, db_name)
                return _make_mock_result(scalar_one=dv)
            # Match all for user
            results = []
            for key, dv_list in self._db_versions.items():
                if not user_id or key.startswith(str(user_id)):
                    results.extend(dv_list)
            return _make_mock_result(scalars=results if results else None)

        if "sync_log" in stmt_str:
            return _make_mock_result(scalar_one=_mock_sync_log())

        if "users" in stmt_str:
            return _make_mock_result(scalar_one=_mock_user())

        if "projects" in stmt_str:
            return _make_mock_result(scalar_one=_mock_project())

        return _make_mock_result(scalars=[], scalar_one=None)

    def add(self, obj):
        self.added_objects.append(obj)
        if isinstance(obj, ParadoxDB):
            key = self._key(obj.user_id, obj.name)
            self._paradox_dbs[key] = obj
        elif isinstance(obj, DatabaseVersion):
            for k, pdb in self._paradox_dbs.items():
                if pdb.id == obj.db_id:
                    self._db_versions[k] = obj
                    break

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
def mock_redis_and_rate_limit():
    """Mock the Redis-based lock and rate limiter so tests don't need Redis."""
    with patch("app.routers.databases._upload_lock") as mock_lock, \
         patch("app.routers.databases.rate_limiter") as mock_rl:
        mock_lock.acquire = AsyncMock(return_value=True)
        mock_lock.release = AsyncMock()
        mock_rl.check = MagicMock(return_value=True)
        yield mock_lock, mock_rl


# ── 1. Full sync flow: upload → download ────────────────────────────


@pytest.mark.asyncio
async def test_full_sync_flow(client: AsyncClient, mock_auth):
    """Upload a file and download it back; verify content matches."""
    user, session = mock_auth
    session.register_db(user.id, "sync")
    file_bytes = b"paradox database content v1"
    file_b64 = base64.b64encode(file_bytes).decode()

    with patch("app.routers.databases.TelegramClient") as MockTG:
        MockTG.return_value.upload_file = AsyncMock(return_value="200")
        MockTG.return_value.download_file = AsyncMock(return_value=file_bytes)

        resp_upload = await client.post(
            "/v1/upload",
            json={"database_name": "sync", "file_data": file_b64},
            headers={"X-API-Key": API_KEY},
        )

    assert resp_upload.status_code == 200, f"Upload failed: {resp_upload.status_code} {resp_upload.text}"
    upload_data = resp_upload.json()
    assert upload_data["version"] == 1
    assert upload_data["message_id"] == "200"

    with patch("app.routers.databases.TelegramClient") as MockDL:
        MockDL.return_value.download_file = AsyncMock(return_value=file_bytes)

        resp_download = await client.get(
            "/v1/download",
            params={"database_name": "sync"},
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
    session.register_db(user.id, "multi")

    v1_bytes = b"version 1 data"
    v2_bytes = b"version 2 data"
    v3_bytes = b"version 3 data"

    session.add_upload(user.id, "multi", 1, "101", v1_bytes)
    session.add_upload(user.id, "multi", 2, "102", v2_bytes)
    session.add_upload(user.id, "multi", 3, "103", v3_bytes)

    resp = await client.get(
        "/v1/versions",
        params={"database_name": "multi"},
        headers={"X-API-Key": API_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["database_name"] == "multi"
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
    session.register_db(user.id, "conflict")

    file_bytes = b"conflict test data"
    file_b64 = base64.b64encode(file_bytes).decode()

    session.add_upload(user.id, "conflict", 1, "300", file_bytes)

    with patch("app.routers.databases.TelegramClient") as MockTG:
        MockTG.return_value.upload_file = AsyncMock(return_value="301")

        resp = await client.post(
            "/v1/upload",
            json={
                "database_name": "conflict",
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
    session.register_db(user.id, "specific")

    v1_bytes = b"version 1 content"
    v2_bytes = b"version 2 content"

    session.add_upload(user.id, "specific", 1, "400", v1_bytes)
    session.add_upload(user.id, "specific", 2, "401", v2_bytes)

    with patch("app.routers.databases.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=v1_bytes)

        resp = await client.get(
            "/v1/download",
            params={"database_name": "specific", "version": 1},
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
    session.register_db(user.id, "rollback")

    v1_bytes = b"rollback version 1"
    v2_bytes = b"rollback version 2"

    session.add_upload(user.id, "rollback", 1, "500", v1_bytes)
    session.add_upload(user.id, "rollback", 2, "501", v2_bytes)

    with patch("app.routers.databases.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=v1_bytes)
        MockTG.return_value.upload_file = AsyncMock(return_value="502")

        resp = await client.post(
            "/v1/rollback",
            json={"database_name": "rollback", "target_version": 1},
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
        ("GET", "/v1/download", {"database_name": "test"}),
        ("GET", "/v1/versions", {"database_name": "test"}),
        ("GET", "/v1/status", {}),
        ("POST", "/v1/upload", {"database_name": "test", "file_data": "dGVzdA=="}),
        ("POST", "/v1/rollback", {"database_name": "test", "target_version": 1}),
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
    session.register_db(user.id, "status")

    file_bytes = b"status test data"

    session.add_upload(user.id, "status", 1, "600", file_bytes)

    resp = await client.get(
        "/v1/status",
        headers={"X-API-Key": API_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == str(user.id)
    assert len(data["databases"]) == 1
    db_info = data["databases"][0]
    assert db_info["name"] == "status"
    assert db_info["latest_version"] == 1
    assert db_info["latest_message_id"] == "600"
    assert db_info["pending_changesets"] == 0
