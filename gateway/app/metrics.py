import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger("gateway.access")

sync_uploads_total = Counter("sync_uploads_total", "Total upload attempts")
sync_uploads_success = Counter("sync_uploads_success", "Successful uploads")
sync_uploads_failed = Counter("sync_uploads_failed", "Failed uploads")
sync_upload_latency_ms = Histogram(
    "sync_upload_latency_ms",
    "Upload round-trip time",
    buckets=[50, 100, 200, 500, 1000, 2000, 5000],
)
sync_queue_depth = Gauge("sync_queue_depth", "Pending uploads in queue")
sync_lock_wait_ms = Histogram(
    "sync_lock_wait_ms",
    "Time waiting for distributed lock",
    buckets=[10, 50, 100, 200, 500, 1000],
)
telegram_api_errors = Counter("telegram_api_errors", "Telegram API failures", ["error_type"])
registry_operations = Counter("registry_operations", "Registry DB operations", ["operation"])


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000

        req_id = ""
        if hasattr(request.state, "request_id"):
            req_id = request.state.request_id

        logger.info(
            "request",
            extra={
                "request_id": req_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
        return response


def get_metrics() -> bytes:
    return generate_latest()
