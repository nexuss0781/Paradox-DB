import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# ── Patch DB engine BEFORE any app imports ────────────────────────
import app.database as _db_mod

_db_mod.engine = MagicMock()
_db_mod.async_session_factory = MagicMock()

# Fix MetricsMiddleware to work as a Starlette middleware class
import app.metrics as _metrics_mod


class _FixedMetricsMiddleware:
    def __init__(self, app_obj):
        self.app = app_obj

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


_metrics_mod.MetricsMiddleware = _FixedMetricsMiddleware

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

with patch("app.main.init_db", new_callable=AsyncMock), \
     patch("app.main.close_db", new_callable=AsyncMock):
    pass

from app.main import app  # noqa: E402
from app.auth import get_current_user, get_db  # noqa: E402
from app.models import DatabaseVersion, SyncLog, UserChannel  # noqa: E402

from starlette.middleware import Middleware  # noqa: E402

fixed_middlewares = []
for mw in app.user_middleware:
    if mw.cls.__name__ == "MetricsMiddleware":
        fixed_middlewares.append(Middleware(_FixedMetricsMiddleware))
    else:
        fixed_middlewares.append(mw)
app.user_middleware = fixed_middlewares
app.middleware_stack = None


# ── Helpers ───────────────────────────────────────────────────────


def _mock_user(user_id: str = "u_test123") -> MagicMock:
    user = MagicMock(spec=UserChannel)
    user.user_id = user_id
    user.channel_id = "-100999888"
    user.bot_token_id = "123456:ABC-test"
    user.api_key_hash = "abc123"
    user.created_at = datetime.now(timezone.utc)
    return user


def _mock_db_version(
    user_id: str = "u_test123",
    database_name: str = "test.db",
    version: int = 3,
    message_id: str = "42",
) -> MagicMock:
    dv = MagicMock(spec=DatabaseVersion)
    dv.user_id = user_id
    dv.database_name = database_name
    dv.latest_version = version
    dv.latest_message_id = message_id
    dv.file_hash = "sha256_abc"
    dv.uploaded_at = datetime.now(timezone.utc)
    return dv


def _make_mock_result(scalars=None, scalar_one=None):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scalar_one
    if scalars is not None:
        mock_result.scalars.return_value.all.return_value = scalars
    return mock_result


def _mock_session_factory():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_session():
    return _mock_session_factory()


@pytest.fixture
def mock_auth(mock_session):
    from fastapi import Request

    user = _mock_user()

    async def _get_user(request: Request):
        return user

    async def _get_db():
        yield mock_session

    app.dependency_overrides[get_current_user] = _get_user
    app.dependency_overrides[get_db] = _get_db
    app.middleware_stack = None
    yield user
    app.dependency_overrides.clear()
    app.middleware_stack = None


# ── GET /v1/download ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_valid_db_returns_200(client, mock_auth, mock_session):
    db_version = _mock_db_version()
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    with patch("app.routers.download.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=b"fake_sqlcipher_bytes")

        response = await client.get(
            "/v1/download",
            params={"database_name": "test.db"},
            headers={"X-API-Key": "pk_test_key"},
        )

    assert response.status_code == 200
    assert response.content == b"fake_sqlcipher_bytes"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["X-Version"] == "3"


@pytest.mark.asyncio
async def test_download_without_auth_returns_401(client: AsyncClient):
    response = await client.get(
        "/v1/download",
        params={"database_name": "test.db"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_download_nonexistent_db_returns_404(client, mock_auth, mock_session):
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=None))

    response = await client.get(
        "/v1/download",
        params={"database_name": "nonexistent.db"},
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_specific_version(client, mock_auth, mock_session):
    db_version = _mock_db_version(version=7)
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    with patch("app.routers.download.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=b"version7_bytes")

        response = await client.get(
            "/v1/download",
            params={"database_name": "test.db", "version": 7},
            headers={"X-API-Key": "pk_test_key"},
        )

    assert response.status_code == 200
    assert response.content == b"version7_bytes"
    assert response.headers["X-Version"] == "7"


@pytest.mark.asyncio
async def test_download_version_mismatch_returns_404(client, mock_auth, mock_session):
    db_version = _mock_db_version(version=3)
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    response = await client.get(
        "/v1/download",
        params={"database_name": "test.db", "version": 99},
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_logs_sync_entry(client, mock_auth, mock_session):
    db_version = _mock_db_version()
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    with patch("app.routers.download.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=b"data")

        await client.get(
            "/v1/download",
            params={"database_name": "test.db"},
            headers={"X-API-Key": "pk_test_key"},
        )

    assert mock_session.add.called
    log_call = mock_session.add.call_args[0][0]
    assert log_call.operation == "download"


# ── GET /v1/versions ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_versions_returns_version_list(client, mock_auth, mock_session):
    db_version = _mock_db_version(version=5)
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    response = await client.get(
        "/v1/versions",
        params={"database_name": "test.db"},
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["database_name"] == "test.db"
    assert len(body["versions"]) == 1
    assert body["versions"][0]["version"] == 5
    assert body["versions"][0]["message_id"] == "42"


@pytest.mark.asyncio
async def test_versions_nonexistent_db_returns_empty(client, mock_auth, mock_session):
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=None))

    response = await client.get(
        "/v1/versions",
        params={"database_name": "nonexistent.db"},
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["versions"] == []


@pytest.mark.asyncio
async def test_versions_without_auth_returns_401(client: AsyncClient):
    response = await client.get(
        "/v1/versions",
        params={"database_name": "test.db"},
    )
    assert response.status_code == 401


# ── POST /v1/rollback ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_valid_version_returns_200(client, mock_auth, mock_session):
    db_version = _mock_db_version(version=3)
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    with patch("app.routers.rollback.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=b"rollback_data")
        MockTG.return_value.upload_file = AsyncMock(return_value="99")

        response = await client.post(
            "/v1/rollback",
            json={"database_name": "test.db", "target_version": 3},
            headers={"X-API-Key": "pk_test_key"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "request_id" in body
    assert body["rolled_back_to"] == 3
    assert body["new_message_id"] == "99"


@pytest.mark.asyncio
async def test_rollback_nonexistent_version_returns_404(client, mock_auth, mock_session):
    db_version = _mock_db_version(version=3)
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    response = await client.post(
        "/v1/rollback",
        json={"database_name": "test.db", "target_version": 99},
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rollback_without_auth_returns_401(client: AsyncClient):
    response = await client.post(
        "/v1/rollback",
        json={"database_name": "test.db", "target_version": 1},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollback_increments_version(client, mock_auth, mock_session):
    db_version = _mock_db_version(version=10)
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalar_one=db_version))

    with patch("app.routers.rollback.TelegramClient") as MockTG:
        MockTG.return_value.download_file = AsyncMock(return_value=b"data")
        MockTG.return_value.upload_file = AsyncMock(return_value="200")

        response = await client.post(
            "/v1/rollback",
            json={"database_name": "test.db", "target_version": 10},
            headers={"X-API-Key": "pk_test_key"},
        )

    assert response.status_code == 200
    assert db_version.latest_version == 11
    assert db_version.latest_message_id == "200"


# ── GET /v1/status ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_returns_user_databases(client, mock_auth, mock_session):
    dv1 = _mock_db_version(database_name="db1.db", version=1, message_id="10")
    dv2 = _mock_db_version(database_name="db2.db", version=5, message_id="50")
    mock_session.execute = AsyncMock(
        side_effect=[
            _make_mock_result(scalars=[dv1, dv2]),
            _make_mock_result(scalar_one=None),
            _make_mock_result(scalar_one=None),
        ]
    )

    response = await client.get(
        "/v1/status",
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "u_test123"
    assert len(body["databases"]) == 2
    assert body["databases"][0]["name"] == "db1.db"
    assert body["databases"][1]["name"] == "db2.db"


@pytest.mark.asyncio
async def test_status_empty_when_no_databases(client, mock_auth, mock_session):
    mock_session.execute = AsyncMock(return_value=_make_mock_result(scalars=[]))

    response = await client.get(
        "/v1/status",
        headers={"X-API-Key": "pk_test_key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["databases"] == []


@pytest.mark.asyncio
async def test_status_without_auth_returns_401(client: AsyncClient):
    response = await client.get("/v1/status")
    assert response.status_code == 401
