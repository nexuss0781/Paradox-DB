import asyncio
import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory
from app.metrics import registry_operations, telegram_api_errors
from app.models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok")


async def check_postgres() -> None:
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        registry_operations.labels(operation="health_check_pg").inc()
    except Exception:
        registry_operations.labels(operation="health_check_pg_fail").inc()
        raise


async def check_redis() -> None:
    import redis.asyncio as aioredis

    try:
        async with aioredis.from_url(settings.redis_url, decode_responses=True) as client:
            await client.ping()
        registry_operations.labels(operation="health_check_redis").inc()
    except Exception:
        registry_operations.labels(operation="health_check_redis_fail").inc()
        raise


@router.get("/health/ready")
async def readiness_check():
    errors: list[str] = []

    async def _pg():
        try:
            await check_postgres()
        except Exception as e:
            errors.append(f"postgres: {e}")

    async def _redis():
        try:
            await check_redis()
        except Exception as e:
            errors.append(f"redis: {e}")

    await asyncio.gather(_pg(), _redis())

    if errors:
        logger.warning("readiness check failed: %s", errors)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "errors": errors},
        )
    return HealthResponse(status="ready")


TELEGRAM_CHECK_TIMEOUT = 5.0


@router.get("/health/telegram")
async def telegram_check():
    token = settings.telegram_bot_token
    if not token:
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
                    return HealthResponse(status="ok")
            telegram_api_errors.labels(error_type=f"http_{resp.status_code}").inc()
            return JSONResponse(
                status_code=503,
                content={"status": "unreachable", "error": f"Telegram API returned {resp.status_code}"},
            )
    except httpx.HTTPError as e:
        telegram_api_errors.labels(error_type="network_error").inc()
        return JSONResponse(
            status_code=503,
            content={"status": "unreachable", "error": str(e)},
        )
