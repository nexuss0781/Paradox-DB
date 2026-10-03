import logging
import uuid

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory
from app.metrics import registry_operations, telegram_api_errors
from app.models import HealthResponse
from app.postgres_lock import postgres_advisory_lock
from app.startup_diagnostics import redact_diagnostic
from app.startup_state import snapshot
from app.telegram_logger import log_operation

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    state = snapshot()
    status = "ok" if state.get("status") == "ready" else "degraded"
    await log_operation("health", f"Health check: {status}", "success")
    return HealthResponse(status=status)


async def check_postgres() -> None:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            lock_acquired = await postgres_advisory_lock.acquire(
                session,
                f"health-check:{uuid.uuid4().hex}",
                timeout=0,
            )
            if not lock_acquired:
                raise RuntimeError("PostgreSQL advisory locks are unavailable")
        registry_operations.labels(operation="health_check_pg").inc()
    except Exception:
        registry_operations.labels(operation="health_check_pg_fail").inc()
        raise


@router.get("/health/ready", response_model=None)
async def readiness_check() -> HealthResponse | JSONResponse:
    errors: list[str] = []
    state = snapshot()
    if state.get("status") != "ready":
        phase = state.get("phase", "startup")
        error = state.get("error", "application startup is incomplete")
        errors.append(f"startup ({phase}): {error}")

    try:
        await check_postgres()
    except Exception as exc:
        errors.append(f"postgres/advisory-lock: {redact_diagnostic(exc)}")

    if errors:
        safe_errors = [redact_diagnostic(error) for error in errors]
        logger.warning("readiness check failed: %s", safe_errors)
        await log_operation("health", f"Readiness failed: {safe_errors}", "fail")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "errors": safe_errors},
        )
    await log_operation("health", "Readiness check: ready", "success")
    return HealthResponse(status="ready")


TELEGRAM_CHECK_TIMEOUT = 5.0


@router.get("/health/telegram", response_model=None)
async def telegram_check() -> HealthResponse | JSONResponse:
    token = settings.telegram_bot_token
    if not token:
        await log_operation("health", "Telegram check: not configured", "warn")
        return JSONResponse(
            status_code=503,
            content={"status": "not_configured", "error": "TELEGRAM_BOT_TOKEN not set"},
        )

    try:
        async with httpx.AsyncClient(timeout=TELEGRAM_CHECK_TIMEOUT) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    await log_operation("health", "Telegram check: ok", "success")
                    return HealthResponse(status="ok")
            if resp.status_code == 401:
                telegram_api_errors.labels(error_type="http_401").inc()
                await log_operation(
                    "health", "Telegram check: bot token invalid or revoked", "fail"
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "invalid_token",
                        "error": "TELEGRAM_BOT_TOKEN is invalid or revoked",
                    },
                )
            telegram_api_errors.labels(error_type=f"http_{resp.status_code}").inc()
            await log_operation(
                "health",
                f"Telegram check: unreachable (HTTP {resp.status_code})",
                "fail",
            )
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unreachable",
                    "error": f"Telegram API returned {resp.status_code}",
                },
            )
    except httpx.HTTPError as exc:
        telegram_api_errors.labels(error_type="network_error").inc()
        safe_error = redact_diagnostic(exc)
        await log_operation("health", f"Telegram check: network error — {safe_error}", "fail")
        return JSONResponse(
            status_code=503,
            content={"status": "unreachable", "error": safe_error},
        )
