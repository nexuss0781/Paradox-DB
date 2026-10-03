"""Secret-safe diagnostics for startup and runtime failures."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

_Scope = dict[str, Any]
_Receive = Callable[[], Awaitable[dict[str, Any]]]
_Send = Callable[[dict[str, Any]], Awaitable[None]]
_ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]

_URL_CREDENTIALS = re.compile(r"(?i)(://[^/@\s]+:)[^/@\s]+@")
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b([a-z0-9_]*(?:password|passwd|pwd|token|secret|api[_-]?key|"
    r"api[_-]?hash|authorization|credential|database[_-]?url|redis[_-]?url|"
    r"jwt[_-]?secret|api[_-]?key[_-]?salt|database[_-]?url[_-]?encryption[_-]?key|"
    r"telegram[_-]?bot[_-]?token|telegram[_-]?api[_-]?hash)[a-z0-9_]*)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_TELEGRAM_URL_TOKEN = re.compile(r"(?i)(/bot)[0-9]{5,}:[A-Za-z0-9_-]{20,}")
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def redact_diagnostic(value: object, *, limit: int = 500) -> str:
    """Return a bounded diagnostic string with common credential formats masked."""
    text = str(value).replace("\x00", "?")
    text = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", text)
    text = _TELEGRAM_URL_TOKEN.sub(r"\1[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = _ASSIGNMENT_SECRET.sub(r"\1=[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def create_startup_failure_app(exc: BaseException) -> _ASGIApp:
    """Create a minimal ASGI app so import failures surface as JSON, not host HTML 500s."""
    detail = redact_diagnostic(exc) or "no error message"
    body: dict[str, str] = {
        "error": "application_startup_failed",
        "error_type": type(exc).__name__,
        "detail": detail,
    }

    missing_module = getattr(exc, "name", None)
    if not missing_module:
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", detail)
        missing_module = match.group(1) if match else None
    if missing_module:
        missing_module = redact_diagnostic(missing_module, limit=200)
        body["missing_module"] = missing_module
        if missing_module == "pydantic_core._pydantic_core":
            body["action"] = (
                "Install the CPython 3.13 WASIX-compatible pydantic-core wheel "
                "from the repository's Wasmer requirements and rebuild."
            )
        else:
            body["action"] = (
                f"Install the missing runtime dependency '{missing_module}' and rebuild."
            )

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

    async def startup_failure_app(scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
        elif scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"cache-control", b"no-store"),
                        (b"retry-after", b"60"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})

    return startup_failure_app
