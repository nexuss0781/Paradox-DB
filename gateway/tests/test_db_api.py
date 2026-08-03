"""DB-backed API tests for the Phase-4 gateway.

Runs against a live gateway when ``GATEWAY_BASE_URL`` is set (e.g. Render),
or against local Postgres via TestClient otherwise. Skipped when neither is
available. All identifiers are unique per run so repeated live runs never
collide.
"""

import base64
import os
import time

import httpx
import pytest


def _unique(prefix: str) -> str:
    return f"{prefix}{time.time_ns()}"


def _postgres_available() -> bool:
    """Probe the configured Postgres with a short timeout."""
    import asyncio

    from sqlalchemy import text

    from app.config import settings
    from app.database import create_async_engine

    engine = create_async_engine(settings.database_url, connect_args={"timeout": 2})

    async def _ping():
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_ping())
        return True
    except Exception:
        return False


_LIVE_URL = os.environ.get("GATEWAY_BASE_URL", "").strip()
_LIVE = bool(_LIVE_URL)
_PG = _postgres_available()
DB_AVAILABLE = _LIVE or _PG

if _LIVE_URL:
    client = httpx.Client(base_url=_LIVE_URL, timeout=30)
else:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

# Success paths talk to real Telegram, which only a live deployment provides.
skip_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="needs Postgres locally or GATEWAY_BASE_URL for live testing",
)
skip_live = pytest.mark.skipif(
    not _LIVE,
    reason="needs live gateway (GATEWAY_BASE_URL) for real Telegram",
)


def _register() -> dict:
    resp = client.post(
        "/v1/auth/register",
        json={
            "email": _unique("api@example.com"),
            "username": _unique("apiuser"),
            "password": "secret123",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["api_key"].startswith("pk_")
    return data


def _create_project(api_key: str) -> str:
    resp = client.post(
        "/v1/projects",
        json={"name": _unique("proj")},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_database(api_key: str, project_id: str) -> dict:
    resp = client.post(
        f"/v1/projects/{project_id}/databases",
        json={"name": _unique("db")},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload(api_key: str, database_id: str, file_bytes: bytes, version: int | None = None) -> httpx.Response:
    payload = {
        "database_id": database_id,
        "file_data": base64.b64encode(file_bytes).decode(),
        "version_type": "full",
    }
    if version is not None:
        payload["version"] = version
    return client.post("/v1/upload", json=payload, headers={"X-API-Key": api_key})


# ── Auth enforcement ──────────────────────────────────────────────


@skip_db
def test_upload_requires_auth():
    resp = client.post("/v1/upload", json={"database_name": "x", "file_data": "eA=="})
    assert resp.status_code == 401


@skip_db
def test_upload_invalid_api_key_401():
    resp = client.post(
        "/v1/upload",
        json={"database_name": "x", "file_data": "eA=="},
        headers={"X-API-Key": "pk_invalid"},
    )
    assert resp.status_code == 401


# ── Request validation (no Telegram needed) ──────────────────────


@skip_db
def test_upload_missing_database_returns_400():
    user = _register()
    resp = client.post(
        "/v1/upload",
        json={"file_data": base64.b64encode(b"data").decode()},
        headers={"X-API-Key": user["api_key"]},
    )
    assert resp.status_code == 400


@skip_db
def test_upload_invalid_base64_returns_400():
    user = _register()
    project_id = _create_project(user["api_key"])
    db_rec = _create_database(user["api_key"], project_id)
    resp = client.post(
        "/v1/upload",
        json={"database_id": db_rec["id"], "file_data": "not-valid-base64!@#"},
        headers={"X-API-Key": user["api_key"]},
    )
    assert resp.status_code == 400


@skip_db
def test_upload_unknown_database_returns_404():
    user = _register()
    resp = client.post(
        "/v1/upload",
        json={
            "database_id": "00000000-0000-0000-0000-000000000000",
            "file_data": base64.b64encode(b"data").decode(),
        },
        headers={"X-API-Key": user["api_key"]},
    )
    assert resp.status_code == 404


# ── Full sync flow (live only: real Telegram) ────────────────────


@skip_live
def test_full_sync_flow():
    user = _register()
    api_key = user["api_key"]
    project_id = _create_project(api_key)
    db_rec = _create_database(api_key, project_id)
    db_name = db_rec["name"]
    database_id = db_rec["id"]

    payload_v1 = b"fake sqlite data v1"
    r1 = _upload(api_key, database_id, payload_v1)
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["version"] == 1
    assert "message_id" in d1
    assert "request_id" in d1

    payload_v2 = b"fake sqlite data v2 with more content"
    r2 = _upload(api_key, database_id, payload_v2)
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == 2

    # Conflict: stale client version behind latest
    r_conflict = _upload(api_key, database_id, b"stale data", version=0)
    assert r_conflict.status_code == 409
    cdata = r_conflict.json()
    assert cdata["error"] == "conflict_detected"
    assert cdata["remote_version"] == 2
    assert cdata["your_version"] == 0

    # Versions list
    rv = client.get("/v1/versions", params={"database_name": db_name}, headers={"X-API-Key": api_key})
    assert rv.status_code == 200, rv.text
    versions = rv.json().get("versions", [])
    assert len(versions) == 2

    # Status
    rs = client.get("/v1/status", headers={"X-API-Key": api_key})
    assert rs.status_code == 200, rs.text
    dbs = rs.json()["databases"]
    mine = [d for d in dbs if d["name"] == db_name]
    assert len(mine) == 1
    assert mine[0]["latest_version"] == 2

    # Download latest → matches v2 payload
    rd = client.get(
        "/v1/download",
        params={"database_id": database_id},
        headers={"X-API-Key": api_key},
    )
    assert rd.status_code == 200, rd.text
    assert rd.headers["X-Version"] == "2"
    assert rd.content == payload_v2

    # Download specific version v1 → matches v1 payload
    rd1 = client.get(
        "/v1/download",
        params={"database_id": database_id, "version": 1},
        headers={"X-API-Key": api_key},
    )
    assert rd1.status_code == 200, rd1.text
    assert rd1.headers["X-Version"] == "1"
    assert rd1.content == payload_v1

    # Rollback to v1 → creates a new version 3 containing v1's bytes
    rr = client.post(
        "/v1/rollback",
        json={"database_name": db_name, "target_version": 1},
        headers={"X-API-Key": api_key},
    )
    assert rr.status_code == 200, rr.text
    rdata = rr.json()
    assert rdata["rolled_back_to"] == 1
    assert rdata["new_message_id"]

    rd3 = client.get(
        "/v1/download",
        params={"database_id": database_id},
        headers={"X-API-Key": api_key},
    )
    assert rd3.status_code == 200, rd3.text
    assert rd3.headers["X-Version"] == "3"
    assert rd3.content == payload_v1
