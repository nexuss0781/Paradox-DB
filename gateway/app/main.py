import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST

from app.database import close_db, init_db
from app.logging_config import setup_logging
from app.metrics import MetricsMiddleware, get_metrics
from app.routers import auth, health, upload, download, versions, rollback, status


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Paradox-DB Gateway",
    version="1.0.0",
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
app.include_router(upload.router, prefix="/v1", tags=["upload"])
app.include_router(download.router, prefix="/v1", tags=["download"])
app.include_router(versions.router, prefix="/v1", tags=["versions"])
app.include_router(rollback.router, prefix="/v1", tags=["rollback"])
app.include_router(status.router, prefix="/v1", tags=["status"])


@app.get("/")
async def root():
    return {"service": "paradox-db-gateway", "version": "1.0.0"}
