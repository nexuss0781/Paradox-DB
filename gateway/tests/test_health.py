from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# 3.1 Unit Tests — Health Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_liveness(client: AsyncClient):
    """3.1.1 GET /health returns 200 with status ok"""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
@patch("app.routers.health.check_postgres", new_callable=AsyncMock)
@patch("app.routers.health.check_redis", new_callable=AsyncMock)
async def test_readiness_ok(mock_redis, mock_pg, client: AsyncClient):
    """3.1.2 GET /health/ready returns 200 when PG + Redis up"""
    mock_pg.return_value = None
    mock_redis.return_value = None
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
@patch("app.routers.health.check_postgres", new_callable=AsyncMock)
@patch("app.routers.health.check_redis", new_callable=AsyncMock)
async def test_readiness_pg_down(mock_redis, mock_pg, client: AsyncClient):
    """3.1.3 GET /health/ready returns 503 when PG down"""
    mock_pg.side_effect = Exception("connection refused")
    mock_redis.return_value = None
    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert any("postgres" in e for e in body["errors"])


@pytest.mark.asyncio
@patch("app.routers.health.check_postgres", new_callable=AsyncMock)
@patch("app.routers.health.check_redis", new_callable=AsyncMock)
async def test_readiness_redis_down(mock_redis, mock_pg, client: AsyncClient):
    """3.1.4 GET /health/ready returns 503 when Redis down"""
    mock_pg.return_value = None
    mock_redis.side_effect = Exception("connection refused")
    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert any("redis" in e for e in body["errors"])


def _make_mock_response(status_code: int, json_data: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data
    return mock_response


def _make_mock_httpx_client(mock_response: MagicMock) -> MagicMock:
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    return mock_client


@pytest.mark.asyncio
@patch("app.routers.health.httpx.AsyncClient")
async def test_telegram_check_ok(mock_client_cls, client: AsyncClient):
    """3.1.5 GET /health/telegram returns 200 when bot valid (mocked)"""
    mock_response = _make_mock_response(200, {"ok": True, "result": {"id": 123}})
    mock_client = _make_mock_httpx_client(mock_response)
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    with patch("app.routers.health.settings") as mock_settings:
        mock_settings.telegram_bot_token = "test-token-123"
        response = await client.get("/health/telegram")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_telegram_check_no_token(client: AsyncClient):
    """3.1.6 GET /health/telegram returns 503 when token not configured"""
    with patch("app.routers.health.settings") as mock_settings:
        mock_settings.telegram_bot_token = ""
        response = await client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "not_configured"


@pytest.mark.asyncio
@patch("app.routers.health.httpx.AsyncClient")
async def test_telegram_check_api_error(mock_client_cls, client: AsyncClient):
    """3.1.6b GET /health/telegram returns 503 when Telegram API returns non-ok"""
    mock_response = _make_mock_response(401, {"ok": False})
    mock_client = _make_mock_httpx_client(mock_response)
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    with patch("app.routers.health.settings") as mock_settings:
        mock_settings.telegram_bot_token = "bad-token"
        response = await client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "invalid_token"


@pytest.mark.asyncio
@patch("app.routers.health.httpx.AsyncClient")
async def test_telegram_check_network_error(mock_client_cls, client: AsyncClient):
    """3.1.6c GET /health/telegram returns 503 on network error"""
    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.ConnectError("connection refused")
    mock_client_cls.return_value.__aenter__.return_value = mock_client

    with patch("app.routers.health.settings") as mock_settings:
        mock_settings.telegram_bot_token = "valid-token"
        response = await client.get("/health/telegram")
    assert response.status_code == 503
    assert response.json()["status"] == "unreachable"


# ---------------------------------------------------------------------------
# 3.2 Unit Tests — Stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_requires_auth(client: AsyncClient):
    """3.2.1 POST /v1/upload returns 401 without an API key"""
    response = await client.post("/v1/upload")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_download_requires_auth(client: AsyncClient):
    """3.2.2 GET /v1/download returns 401 without an API key"""
    response = await client.get("/v1/download")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_versions_requires_auth(client: AsyncClient):
    """3.2.3 GET /v1/versions returns 401 without an API key"""
    response = await client.get("/v1/versions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_rollback_requires_auth(client: AsyncClient):
    """3.2.4 POST /v1/rollback returns 401 without an API key"""
    response = await client.post("/v1/rollback")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_status_requires_auth(client: AsyncClient):
    """3.2.5 GET /v1/status returns 401 without an API key"""
    response = await client.get("/v1/status")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 3.3 Unit Tests — Registry DB (model-level, no real DB needed)
# ---------------------------------------------------------------------------


def test_user_model_fields():
    """3.3.1 users table is properly defined"""
    from app.models import User

    assert User.__tablename__ == "users"
    cols = {c.name for c in User.__table__.columns}
    assert "email" in cols
    assert "username" in cols
    assert "password_hash" in cols
    assert "api_key_hash" in cols
    assert "is_active" in cols


def test_database_version_model_fields():
    """3.3.2 database_versions table is properly defined"""
    from app.models import DatabaseVersion

    assert DatabaseVersion.__tablename__ == "database_versions"
    cols = {c.name for c in DatabaseVersion.__table__.columns}
    assert "db_id" in cols
    assert "version_number" in cols
    assert "file_hash" in cols
    assert "file_size" in cols
    assert "message_id" in cols
    assert "created_at" in cols


def test_sync_log_model_fields():
    """3.3.3 sync_log table is properly defined"""
    from app.models import SyncLog

    assert SyncLog.__tablename__ == "sync_log"
    cols = {c.name for c in SyncLog.__table__.columns}
    assert "request_id" in cols
    assert "user_id" in cols
    assert "database_name" in cols
    assert "operation" in cols
    assert "telegram_message_id" in cols
    assert "status" in cols
    assert "error_message" in cols
    assert "created_at" in cols
    assert "completed_at" in cols


def test_user_primary_key():
    """3.3.4 users has id as PK"""
    from app.models import User

    pk_cols = [c.name for c in User.__table__.primary_key.columns]
    assert pk_cols == ["id"]


def test_database_version_primary_key():
    """3.3.5 database_versions has id as PK"""
    from app.models import DatabaseVersion

    pk_cols = [c.name for c in DatabaseVersion.__table__.primary_key.columns]
    assert pk_cols == ["id"]


def test_sync_log_primary_key():
    """3.3.6 sync_log has request_id as PK"""
    from app.models import SyncLog

    pk_cols = [c.name for c in SyncLog.__table__.primary_key.columns]
    assert pk_cols == ["request_id"]


def test_database_version_foreign_key():
    """3.3.7 database_versions has FK to paradox_dbs"""
    from app.models import DatabaseVersion

    fks = list(DatabaseVersion.__table__.foreign_keys)
    tables = {fk.column.table.name for fk in fks}
    assert "paradox_dbs" in tables


def test_sync_log_foreign_key():
    """3.3.7b sync_log has FK to users"""
    from app.models import SyncLog

    fks = list(SyncLog.__table__.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "users"


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "paradox-db-gateway"
    assert body["version"] == "2.2.5"
