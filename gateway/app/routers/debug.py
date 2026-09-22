"""Safe runtime diagnostics for temporary deployment debugging.

These endpoints intentionally never return secret values. They expose the
runtime environment as seen by the deployed service, which is the only
Render environment available from inside the application process.
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.config import settings

router = APIRouter(prefix="/dbg", tags=["debug"])

_SECRET_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PRIVATE",
    "CREDENTIAL",
    "DATABASE_URL",
    "REDIS_URL",
    "API_HASH",
)


def _is_secret(name: str) -> bool:
    upper_name = name.upper()
    return any(marker in upper_name for marker in _SECRET_MARKERS)


def _safe_environment() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, value in sorted(os.environ.items()):
        if _is_secret(name):
            result[name] = {
                "set": bool(value),
                "length": len(value),
                "value": "[REDACTED]" if value else "",
            }
        else:
            result[name] = {"set": bool(value), "value": value}
    return result


def _setting_status() -> dict[str, dict[str, object]]:
    names = (
        "DATABASE_URL",
        "REDIS_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "JWT_SECRET",
        "API_KEY_SALT",
        "NEXUSS_AUTH_URL",
        "NEXUSS_AUTH_PROJECT_ID",
    )
    return {
        name: {"set": bool(os.getenv(name)), "value": "[REDACTED]" if os.getenv(name) else ""}
        for name in names
    }


@router.get("")
async def debug_index() -> dict[str, object]:
    """Return links and a warning without exposing environment values."""
    return {
        "service": "paradox-db-gateway",
        "debug": True,
        "warning": "Values are redacted; this is a temporary diagnostics endpoint.",
        "routes": ["/dbg", "/dbg/env", "/dbg/env.txt", "/dbg/config", "/dbg/ping"],
    }


@router.get("/env")
async def debug_environment() -> dict[str, object]:
    """Show every runtime environment variable with secret values redacted."""
    render_keys = {
        name: value
        for name, value in _safe_environment().items()
        if name.startswith("RENDER_")
    }
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "cwd": os.getcwd(),
        },
        "render_environment": render_keys,
        "environment": _safe_environment(),
        "warning": "Secret values are intentionally redacted.",
    }


@router.get("/env.txt", response_class=PlainTextResponse)
async def debug_environment_text() -> PlainTextResponse:
    """Show the runtime environment as copyable KEY=value lines."""
    lines = [
        "# Paradox-DB runtime environment",
        "# Secret values are intentionally redacted.",
    ]
    for name, item in _safe_environment().items():
        lines.append(f"{name}={item['value']}")
    return PlainTextResponse("\n".join(lines) + "\n")


@router.get("/config")
async def debug_config() -> dict[str, object]:
    """Show whether required service configuration is present, never its values."""
    return {
        "required_environment": _setting_status(),
        "defaults_in_use": {
            "database_url": settings.database_url.startswith("postgresql+asyncpg://postgres:postgres@localhost"),
            "redis_url": settings.redis_url == "redis://localhost:6379/0",
            "jwt_secret": settings.jwt_secret == "change-me-in-production",
            "api_key_salt": settings.api_key_salt == "change-me-in-production",
        },
        "warning": "Secret values are intentionally redacted.",
    }


@router.get("/ping")
async def debug_ping() -> dict[str, object]:
    return {"ok": True, "service": "paradox-db-gateway"}
