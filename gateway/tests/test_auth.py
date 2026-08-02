import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.auth import hash_api_key, generate_api_key, RateLimiter


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


def test_register_issues_api_key():
    """POST /v1/auth/register returns user_id and a cloud-issued pk_ API key."""
    resp = client.post(
        "/v1/auth/register",
        json={"email": "alice@example.com", "username": "alice", "password": "secret123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data
    assert data["api_key"].startswith("pk_")
    assert "access_token" not in data


def test_register_duplicate_email_409():
    body = {"email": "dup@example.com", "username": "dup1", "password": "secret123"}
    assert client.post("/v1/auth/register", json=body).status_code == 200
    resp = client.post("/v1/auth/register", json=body)
    assert resp.status_code == 409


def test_login_issues_fresh_api_key():
    client.post(
        "/v1/auth/register",
        json={"email": "bob@example.com", "username": "bob", "password": "secret123"},
    )
    resp = client.post(
        "/v1/auth/login",
        json={"email": "bob@example.com", "password": "secret123"},
    )
    assert resp.status_code == 200
    assert resp.json()["api_key"].startswith("pk_")
    assert "access_token" not in resp.json()


def test_login_rotates_and_invalidates_old_key():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "roy@example.com", "username": "roy", "password": "secret123"},
    ).json()
    old_key = reg["api_key"]
    login = client.post(
        "/v1/auth/login",
        json={"email": "roy@example.com", "password": "secret123"},
    ).json()
    assert login["api_key"] != old_key
    # Old key must be rejected
    assert client.get("/v1/projects", headers={"X-API-Key": old_key}).status_code == 401
    # Fresh key works
    assert client.get("/v1/projects", headers={"X-API-Key": login["api_key"]}).status_code == 200


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


def test_missing_api_key_returns_401():
    assert client.get("/v1/projects").status_code == 401


def test_invalid_api_key_returns_401():
    assert client.get("/v1/projects", headers={"X-API-Key": "pk_invalid"}).status_code == 401


def test_bearer_header_not_accepted():
    """Strict: an API key sent as Authorization: Bearer must be rejected."""
    reg = client.post(
        "/v1/auth/register",
        json={"email": "bear@example.com", "username": "bear", "password": "secret123"},
    ).json()
    resp = client.get("/v1/projects", headers={"Authorization": f"Bearer {reg['api_key']}"})
    assert resp.status_code == 401


def test_valid_api_key_passes_auth():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "dave@example.com", "username": "dave", "password": "secret123"},
    ).json()
    resp = client.get("/v1/projects", headers={"X-API-Key": reg["api_key"]})
    assert resp.status_code == 200


def test_mint_api_key_rotates_and_invalidates_old():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "grace@example.com", "username": "grace", "password": "secret123"},
    ).json()
    old_key = reg["api_key"]
    new = client.post("/v1/auth/api-key", headers={"X-API-Key": old_key}).json()
    assert new["api_key"].startswith("pk_")
    assert new["api_key"] != old_key
    # Old key is invalidated
    assert client.get("/v1/projects", headers={"X-API-Key": old_key}).status_code == 401
    # New key works
    assert client.get("/v1/projects", headers={"X-API-Key": new["api_key"]}).status_code == 200


def test_me_returns_current_user():
    reg = client.post(
        "/v1/auth/register",
        json={"email": "me@example.com", "username": "me_user", "password": "secret123"},
    ).json()
    resp = client.get("/v1/auth/me", headers={"X-API-Key": reg["api_key"]})
    assert resp.status_code == 200
    assert resp.json()["username"] == "me_user"


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
