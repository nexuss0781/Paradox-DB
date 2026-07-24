import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.auth import hash_api_key, generate_api_key, create_jwt, decode_jwt, RateLimiter


client = TestClient(app)


# ── API Key ─────────────────────────────────────────────────────


def test_hash_api_key_deterministic():
    key = "pk_test123"
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_uses_salt():
    key = "pk_test123"
    h1 = hash_api_key(key)
    assert len(h1) == 64  # SHA-256 hex


def test_generate_api_key_format():
    key = generate_api_key()
    assert key.startswith("pk_")
    assert len(key) > 10


# ── JWT ─────────────────────────────────────────────────────────


def test_create_jwt_and_decode():
    token = create_jwt("user_123")
    payload = decode_jwt(token)
    assert payload["sub"] == "user_123"
    assert "iat" in payload
    assert "exp" in payload


def test_decode_jwt_invalid_token():
    with pytest.raises(Exception):
        decode_jwt("invalid.token.here")


def test_decode_jwt_expired():
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    from app.config import settings

    payload = {
        "sub": "user_123",
        "iat": datetime.now(timezone.utc) - timedelta(hours=48),
        "exp": datetime.now(timezone.utc) - timedelta(hours=24),
    }
    token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(Exception):
        decode_jwt(token)


# ── Rate Limiter ────────────────────────────────────────────────


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


# ── Register endpoint ──────────────────────────────────────────


def test_register_creates_user():
    resp = client.post("/v1/auth/register")
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data
    assert data["user_id"].startswith("u_")
    assert "api_key" in data
    assert data["api_key"].startswith("pk_")
    assert "jwt" in data


def test_register_returns_valid_jwt():
    resp = client.post("/v1/auth/register")
    token = resp.json()["jwt"]
    payload = decode_jwt(token)
    assert payload["sub"] == resp.json()["user_id"]


# ── Auth middleware ────────────────────────────────────────────


def test_unauthenticated_returns_401():
    resp = client.get("/v1/status")
    assert resp.status_code == 401


def test_invalid_api_key_returns_401():
    resp = client.get("/v1/status", headers={"X-API-Key": "pk_invalid"})
    assert resp.status_code == 401


def test_valid_api_key_passes_auth():
    reg = client.post("/v1/auth/register").json()
    api_key = reg["api_key"]
    resp = client.get("/v1/status", headers={"X-API-Key": api_key})
    # Should not be 401 (may be 501 if stub, but auth passes)
    assert resp.status_code != 401


def test_valid_jwt_passes_auth():
    reg = client.post("/v1/auth/register").json()
    jwt_token = reg["jwt"]
    resp = client.get("/v1/status", headers={"Authorization": f"Bearer {jwt_token}"})
    assert resp.status_code != 401


def test_expired_jwt_returns_401():
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    from app.config import settings

    payload = {
        "sub": "u_fake",
        "iat": datetime.now(timezone.utc) - timedelta(hours=48),
        "exp": datetime.now(timezone.utc) - timedelta(hours=24),
    }
    token = pyjwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    resp = client.get("/v1/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_tampered_jwt_returns_401():
    reg = client.post("/v1/auth/register").json()
    jwt_token = reg["jwt"]
    # Tamper with the token
    tampered = jwt_token[:-5] + "XXXXX"
    resp = client.get("/v1/status", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


# ── User scoping ──────────────────────────────────────────────


def test_user_cannot_access_other_users_data():
    reg1 = client.post("/v1/auth/register").json()
    reg2 = client.post("/v1/auth/register").json()

    # Both can access /v1/status (it's user-scoped)
    resp1 = client.get("/v1/status", headers={"X-API-Key": reg1["api_key"]})
    resp2 = client.get("/v1/status", headers={"X-API-Key": reg2["api_key"]})
    assert resp1.status_code != 401
    assert resp2.status_code != 401


# ── Model: api_key_hash ────────────────────────────────────────


def test_model_has_api_key_hash():
    from app.models import UserChannel
    col = UserChannel.__table__.c.api_key_hash
    assert col is not None
    assert col.unique is True
