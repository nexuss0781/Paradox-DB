import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST

from app.database import close_db, init_db
from app.halt import maybe_halt_on_rate_limit
from app.logging_config import setup_logging
from app.metrics import MetricsMiddleware, get_metrics
from app.routers import auth, databases, health, notifications, projects, test
from app.services.telegram import (
    TelegramError,
    TelegramRateLimitError,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from app.telegram_logger import log_operation


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Paradox-DB Gateway",
    version="2.2.5",
    description="Web Gateway for Telegram-synced SQLite database",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(MetricsMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    route = getattr(request, "url", "unknown")
    try:
        await log_operation(
            "gateway",
            f"Unhandled exception on {request.method} {route}: {type(exc).__name__}: {exc}\n{tb}",
            "fail",
        )
    except Exception:
        pass
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": f"{type(exc).__name__}: {exc}"},
    )


@app.exception_handler(TelegramError)
async def telegram_error_handler(request: Request, exc: TelegramError):
    """Safety net: any Telegram error that escapes a route is mapped cleanly.

    A 429 additionally trips the rate-limit kill switch (if enabled).
    """
    if isinstance(exc, TelegramRateLimitError):
        halted = maybe_halt_on_rate_limit(
            exc, context=f"request {request.method} {request.url.path}"
        )
        if halted:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_stopping",
                    "detail": "Telegram rate limit triggered kill switch; service is halting",
                },
            )
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "retry_after": exc.retry_after},
        )
    if isinstance(exc, TelegramUnauthorizedError):
        return JSONResponse(
            status_code=503,
            content={
                "error": "telegram_unauthorized",
                "detail": f"Telegram bot token is invalid or revoked: {exc}",
            },
        )
    if isinstance(exc, TelegramServerError):
        return JSONResponse(
            status_code=502,
            content={"error": "telegram_unavailable", "detail": str(exc)},
        )
    return JSONResponse(
        status_code=502,
        content={"error": "telegram_failed", "detail": str(exc)},
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.get("/metrics")
async def metrics_endpoint():
    return Response(content=get_metrics(), media_type=CONTENT_TYPE_LATEST)


app.include_router(health.router, tags=["health"])
app.include_router(auth.router, tags=["auth"])
app.include_router(test.router, tags=["test"])
app.include_router(notifications.router, tags=["notifications"])
app.include_router(projects.router, tags=["projects"])
app.include_router(databases.router, tags=["databases"])


@app.get("/")
async def root():
    await log_operation("gateway", "Gateway info requested", "info")
    return {"service": "paradox-db-gateway", "version": "2.2.5"}
