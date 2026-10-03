import pytest
from httpx import ASGITransport, AsyncClient

from app.startup_diagnostics import create_startup_failure_app, redact_diagnostic


def test_redact_diagnostic_masks_common_credential_formats():
    token = "123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLM"
    detail = (
        "connect failed at postgresql://db-user:db-password@db.example/app; "
        f"DATABASE_URL=postgresql://user:another-password@db.example/app "
        f"TELEGRAM_BOT_TOKEN={token} API_KEY_SALT=key-salt-value "
        "DATABASE_URL_ENCRYPTION_KEY=fernet-key-value Bearer abc.def.ghi"
        " DB_PASSWORD=db-pass-value PGPASSWORD=pg-pass-value "
        "WASMER_API_TOKEN=wasmer-token-value"
    )

    safe = redact_diagnostic(detail)

    assert "db-password" not in safe
    assert "another-password" not in safe
    assert token not in safe
    assert "key-salt-value" not in safe
    assert "fernet-key-value" not in safe
    assert "abc.def.ghi" not in safe
    assert "db-pass-value" not in safe
    assert "pg-pass-value" not in safe
    assert "wasmer-token-value" not in safe
    assert "[REDACTED]" in safe


@pytest.mark.asyncio
async def test_startup_failure_returns_actionable_json_503_and_redacts_secrets():
    token = "123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLM"
    exc = ModuleNotFoundError(
        "No module named 'pydantic_core._pydantic_core'; "
        "DATABASE_URL=postgresql://user:db-password@db.example/app "
        f"TELEGRAM_BOT_TOKEN={token}",
        name="pydantic_core._pydantic_core",
    )
    app = create_startup_failure_app(exc)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["error"] == "application_startup_failed"
    assert body["error_type"] == "ModuleNotFoundError"
    assert body["missing_module"] == "pydantic_core._pydantic_core"
    assert "WASIX-compatible" in body["action"]
    assert "db-password" not in response.text
    assert token not in response.text
    assert "TELEGRAM_BOT_TOKEN=[REDACTED]" in body["detail"]


@pytest.mark.asyncio
async def test_startup_failure_app_supports_lifespan():
    app = create_startup_failure_app(RuntimeError("missing environment setting"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 503
