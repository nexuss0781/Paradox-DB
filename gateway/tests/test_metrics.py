import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.metrics import (
    sync_uploads_total,
    sync_uploads_success,
    sync_uploads_failed,
    sync_upload_latency_ms,
    sync_queue_depth,
    sync_lock_wait_ms,
    telegram_api_errors,
    registry_operations,
    get_metrics,
)

client = TestClient(app)


# ── GET /metrics endpoint ──────────────────────────────────────


def test_metrics_endpoint_returns_200():
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_returns_prometheus_format():
    resp = client.get("/metrics")
    body = resp.text
    assert "# HELP" in body
    assert "# TYPE" in body


def test_metrics_contains_expected_metrics():
    resp = client.get("/metrics")
    body = resp.text
    assert "sync_uploads_total" in body
    assert "sync_uploads_success" in body
    assert "sync_uploads_failed" in body
    assert "sync_upload_latency_ms" in body
    assert "sync_queue_depth" in body
    assert "sync_lock_wait_ms" in body
    assert "telegram_api_errors" in body
    assert "registry_operations" in body


def test_metrics_content_type():
    resp = client.get("/metrics")
    assert "text/plain" in resp.headers["content-type"]


# ── Counter increments ────────────────────────────────────────


def test_sync_uploads_total_increments():
    before = sync_uploads_total._value.get()
    sync_uploads_total.inc()
    after = sync_uploads_total._value.get()
    assert after == before + 1


def test_sync_uploads_success_increments():
    before = sync_uploads_success._value.get()
    sync_uploads_success.inc()
    after = sync_uploads_success._value.get()
    assert after == before + 1


def test_sync_uploads_failed_increments():
    before = sync_uploads_failed._value.get()
    sync_uploads_failed.inc()
    after = sync_uploads_failed._value.get()
    assert after == before + 1


def test_telegram_api_errors_increments():
    before = telegram_api_errors.labels(error_type="timeout")._value.get()
    telegram_api_errors.labels(error_type="timeout").inc()
    after = telegram_api_errors.labels(error_type="timeout")._value.get()
    assert after == before + 1


def test_registry_operations_increments():
    before = registry_operations.labels(operation="upload")._value.get()
    registry_operations.labels(operation="upload").inc()
    after = registry_operations.labels(operation="upload")._value.get()
    assert after == before + 1


# ── Histogram ────────────────────────────────────────────────


def test_sync_upload_latency_records():
    sync_upload_latency_ms.observe(250)
    sync_upload_latency_ms.observe(500)
    # Check that observations were recorded
    assert sync_upload_latency_ms._sum.get() >= 750


def test_sync_lock_wait_records():
    sync_lock_wait_ms.observe(50)
    assert sync_lock_wait_ms._sum.get() >= 50


# ── Gauge ────────────────────────────────────────────────────


def test_sync_queue_depth_reflects_value():
    sync_queue_depth.set(5)
    assert sync_queue_depth._value.get() == 5
    sync_queue_depth.set(0)
    assert sync_queue_depth._value.get() == 0


# ── get_metrics function ─────────────────────────────────────


def test_get_metrics_returns_bytes():
    data = get_metrics()
    assert isinstance(data, bytes)
    assert len(data) > 0


def test_get_metrics_contains_prometheus_text():
    data = get_metrics().decode("utf-8")
    assert "# HELP" in data
    assert "# TYPE" in data
