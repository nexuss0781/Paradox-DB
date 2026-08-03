import os
import time

import httpx
import pytest

from app.auth import hash_api_key, generate_api_key, RateLimiter


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
_PG = _postgres_available()
DB_AVAILABLE = bool(_LIVE_URL) or _PG

if _LIVE_URL:
    client = httpx.Client(base_url=_LIVE_URL, timeout=20)
else:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)


skip_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="needs Postgres locally or GATEWAY_BASE_URL for live testing",
)


# ── API Key (unit) ────────────────────────────────────────────────


def test_hash_api_key_deterministic():
    key = "pk_test123"
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_is_sha256_hex():
    key = "pk_test123"
    h = hash_api_key(key)
    assert len(h) == 64  # SHA-256 hex


def test_generate_api_key_format():
    key = generate_api_key()
    assert key.startswith("pk_")
    assert len(key) > 10


def test_generate_api_key_unique():
    assert generate_api_key() != generate_api_key()


# ── Rate Limiter (unit) ───────────────────────────────────────────


def test_rate_limiter_allows_under_limit():
    rl = RateLimiter(max_requests=5, window_seconds=60)
    for _ in range(5):
        assert rl.check("user1") is True


def test_rate_limiter_blocks_over_limit():
    rl = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        rl.check("user1")
    assert rl.check("user1") is False


def test_rate_limiter_different_users_independent():
    rl = RateLimiter(max_requests=2, window_seconds=60)
    rl.check("user1")
    rl.check("user1")
    assert rl.check("user1") is False
    assert rl.check("user2") is True


def test_rate_limiter_resets_after_window():
    rl = RateLimiter(max_requests=2, window_seconds=1)
    rl.check("user1")
    rl.check("user1")
    assert rl.check("user1") is False
    # Simulate time passing
    rl._requests["user1"] = [rl._requests["user1"][0] - 2]
    assert rl.check("user1") is True


# ── Register / login (needs PostgreSQL) ───────────────────────────


@skip_db
def test_register_issues_api_key():
    """POST /v1/auth/register returns user_id and a cloud-issued pk_ API key."""
    resp = client.post(
        "/v1/auth/register",
        json={"email": _unique("alice@example.com"), "username": _unique("alice"), "password": "secret123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data
    assert data["api_key"].startswith("pk_")
    assert "access_token" not in data


@skip_db
def test_register_duplicate_email_409():
    body = {"email": _unique("dup@example.com"), "username": _unique("dup1"), "password": "secret123"}
    assert client.post("/v1/auth/register", json=body).status_code == 200
    resp = client.post("/v1/auth/register", json=body)
    assert resp.status_code == 409


@skip_db
def test_login_issues_fresh_api_key():
    email = _unique("bob@example.com")
    client.post(
        "/v1/auth/register",
        json={"email": email, "username": _unique("bob"), "password": "secret123"},
    )
    resp = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "secret123"},
    )
    assert resp.status_code == 200
    assert resp.json()["api_key"].startswith("pk_")
    assert "access_token" not in resp.json()


@skip_db
def test_login_rotates_and_invalidates_old_key():
    email = _unique("roy@example.com")
    username = _unique("roy")
    reg = client.post(
        "/v1/auth/register",
        json={"email": email, "username": username, "password": "secret123"},
    ).json()
    old_key = reg["api_key"]
    login = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "secret123"},
    ).json()
    assert login["api_key"] != old_key
    # Old key must be rejected
    assert client.get("/v1/projects", headers={"X-API-Key": old_key}).status_code == 401
    # Fresh key works
    assert client.get("/v1/projects", headers={"X-API-Key": login["api_key"]}).status_code == 200


@skip_db
def test_login_invalid_password_401():
    email = _unique("carol@example.com")
    client.post(
        "/v1/auth/register",
        json={"email": email, "username": _unique("carol"), "password": "secret123"},
    )
    resp = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "wrong"},
    )
    assert resp.status_code == 401


# ── Auth enforcement (needs PostgreSQL) ───────────────────────────


@skip_db
def test_missing_api_key_returns_401():
    assert client.get("/v1/projects").status_code == 401


@skip_db
def test_invalid_api_key_returns_401():
    assert client.get("/v1/projects", headers={"X-API-Key": "pk_invalid"}).status_code == 401


@skip_db
def test_bearer_header_not_accepted():
    """Strict: an API key sent as Authorization: Bearer must be rejected."""
    reg = client.post(
        "/v1/auth/register",
        json={"email": _unique("bear@example.com"), "username": _unique("bear"), "password": "secret123"},
    ).json()
    resp = client.get("/v1/projects", headers={"Authorization": f"Bearer {reg['api_key']}"})
    assert resp.status_code == 401


@skip_db
def test_valid_api_key_passes_auth():
    reg = client.post(
        "/v1/auth/register",
        json={"email": _unique("dave@example.com"), "username": _unique("dave"), "password": "secret123"},
    ).json()
    resp = client.get("/v1/projects", headers={"X-API-Key": reg["api_key"]})
    assert resp.status_code == 200


@skip_db
def test_mint_api_key_rotates_and_invalidates_old():
    reg = client.post(
        "/v1/auth/register",
        json={"email": _unique("grace@example.com"), "username": _unique("grace"), "password": "secret123"},
    ).json()
    old_key = reg["api_key"]
    new = client.post("/v1/auth/api-key", headers={"X-API-Key": old_key}).json()
    assert new["api_key"].startswith("pk_")
    assert new["api_key"] != old_key
    # Old key is invalidated
    assert client.get("/v1/projects", headers={"X-API-Key": old_key}).status_code == 401
    # New key works
    assert client.get("/v1/projects", headers={"X-API-Key": new["api_key"]}).status_code == 200


@skip_db
def test_me_returns_current_user():
    username = _unique("me_user")
    reg = client.post(
        "/v1/auth/register",
        json={"email": _unique("me@example.com"), "username": username, "password": "secret123"},
    ).json()
    resp = client.get("/v1/auth/me", headers={"X-API-Key": reg["api_key"]})
    assert resp.status_code == 200
    assert resp.json()["username"] == username


# ── User scoping ──────────────────────────────────────────────────


@skip_db
def test_user_scoping():
    reg1 = client.post(
        "/v1/auth/register",
        json={"email": _unique("u1@example.com"), "username": _unique("userone"), "password": "secret123"},
    ).json()
    reg2 = client.post(
        "/v1/auth/register",
        json={"email": _unique("u2@example.com"), "username": _unique("usertwo"), "password": "secret123"},
    ).json()

    resp1 = client.get("/v1/projects", headers={"X-API-Key": reg1["api_key"]})
    resp2 = client.get("/v1/projects", headers={"X-API-Key": reg2["api_key"]})
    assert resp1.status_code == 200
    assert resp2.status_code == 200


# ── Model: api_key_hash ───────────────────────────────────────────


def test_user_model_has_api_key_hash():
    from app.models import User

    col = User.__table__.c.api_key_hash
    assert col is not None
    assert col.unique is True
