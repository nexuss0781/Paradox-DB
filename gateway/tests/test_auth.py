import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.auth import hash_api_key, generate_api_key, create_jwt, decode_jwt, RateLimiter


client = TestClient(app)


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


# ── JWT (unit) ────────────────────────────────────────────────────


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


# ── Register endpoint (needs PostgreSQL) ──────────────────────────


def test_register_creates_user():
    """POST /v1/auth/register returns user_id, access_token, and api_key."""
    resp = client.post(
        "/v1/auth/register",
        json={"email": "alice@example.com", "username": "alice", "password": "secret123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data
    assert data["access_token"]
    assert data["api_key"].startswith("pk_")


def test_register_duplicate_email_409():
    body = {"email": "dup@example.com", "username": "dup1", "password": "secret123"}
    assert client.post("/v1/auth/register", json=body).status_code == 200
    resp = client.post("/v1/auth/register", json=body)
    assert resp.status_code == 409


def test_login_returns_jwt():
    client.post(
        "/v1/auth/register",
        json={"email": "bob@example.com", "username": "bob", "password": "secret123"},
    )
    resp = client.post(
        "/v1/auth/login",
        json={"email": "bob@example.com", "password": "secret123"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    assert decode_jwt(token)["sub"] == resp.json()["user_id"]


def test_login_invalid_password_401():
    client.post(
        "/v1/auth/register",
        json={"email": "carol@example.com", "username": "carol", "password": "secret123"},
    )
    resp = client.post(
        "/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


# ── Auth enforcement (needs PostgreSQL) ───────────────────────────


def test_unauthenticated_returns_401():
    resp = client.get("/v1/projects")
    assert resp.status_code == 401


def test_invalid_api_key_returns_401():
    resp = client.get("/v1/projects", headers={"X-API-Key": "pk_invalid"})
    assert resp.status_code == 401


def test_valid_api_key_passes_auth():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "dave@example.com", "username": "dave", "password": "secret123"},
    ).json()
    resp = client.get("/v1/projects", headers={"X-API-Key": reg["api_key"]})
    assert resp.status_code == 200


def test_valid_jwt_passes_auth():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "erin@example.com", "username": "erin", "password": "secret123"},
    ).json()
    resp = client.get(
        "/v1/projects",
        headers={"Authorization": f"Bearer {reg['access_token']}"},
    )
    assert resp.status_code == 200


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
    resp = client.get("/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_tampered_jwt_returns_401():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "frank@example.com", "username": "frank", "password": "secret123"},
    ).json()
    tampered = reg["access_token"][:-5] + "XXXXX"
    resp = client.get("/v1/projects", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


def test_mint_api_key_rotates_and_invalidates_old():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "grace@example.com", "username": "grace", "password": "secret123"},
    ).json()
    old_key = reg["api_key"]
    new = client.post(
        "/v1/auth/api-key",
        headers={"X-API-Key": old_key},
    ).json()
    assert new["api_key"].startswith("pk_")
    assert new["api_key"] != old_key
    # Old key is invalidated
    resp = client.get("/v1/projects", headers={"X-API-Key": old_key})
    assert resp.status_code == 401
    # New key works
    resp = client.get("/v1/projects", headers={"X-API-Key": new["api_key"]})
    assert resp.status_code == 200


# ── User scoping ──────────────────────────────────────────────────


def test_user_scoping():
    reg1 = client.post(
        "/v1/auth/register",
        json={"email": "u1@example.com", "username": "userone", "password": "secret123"},
    ).json()
    reg2 = client.post(
        "/v1/auth/register",
        json={"email": "u2@example.com", "username": "usertwo", "password": "secret123"},
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
