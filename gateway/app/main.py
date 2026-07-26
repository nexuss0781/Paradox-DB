import uuid
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST

from app.database import close_db, init_db
from app.logging_config import setup_logging
from app.metrics import MetricsMiddleware, get_metrics
from app.routers import auth, health, test, notifications, projects, databases
from app.telegram_logger import log_operation


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Paradox-DB Gateway",
    version="2.0.0",
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
    return {"service": "paradox-db-gateway", "version": "2.0.0"}
