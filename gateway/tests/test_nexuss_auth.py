import pytest
from fastapi import HTTPException

from app.config import settings
from app.nexuss_auth import parse_nexuss_identity


def test_parse_nexuss_identity_accepts_google_profile_shape():
    identity = parse_nexuss_identity(
        {"user": {"id": "google-user-123", "email": "Person@Example.com", "name": "Person Example"}}
    )
    assert identity.user_id == "google-user-123"
    assert identity.email == "person@example.com"
    assert identity.name == "Person Example"


def test_parse_nexuss_identity_rejects_signed_out_response():
    with pytest.raises(HTTPException) as exc:
        parse_nexuss_identity({"user": None})
    assert exc.value.status_code == 401


def test_parse_nexuss_identity_flag_off_accepts_legacy_shape(monkeypatch):
    monkeypatch.setattr(settings, "paradox_envx_only_auth_enabled", False)
    identity = parse_nexuss_identity({"user": {"id": "legacy"}})
    assert identity.user_id == "legacy"


def _envx_payload(**auth):
    return {
        "user": {
            "id": "envx-user", "email": "user@example.com", "name": "User",
            "avatarUrl": "https://avatar",
        },
        "auth": {
            "projectId": "paradox", "provider": "envx", "issuer": "https://issuer",
            "subject": "sub-1", "permissions": ["project:paradox:access"], **auth,
        },
    }


def test_parse_nexuss_identity_flag_on_accepts_envx(monkeypatch):
    monkeypatch.setattr(settings, "paradox_envx_only_auth_enabled", True)
    monkeypatch.setattr(settings, "nexuss_auth_project_id", "paradox")
    monkeypatch.setattr(settings, "envx_oidc_issuer_url", "https://issuer")
    identity = parse_nexuss_identity(_envx_payload())
    assert identity.provider == "envx"
    assert identity.subject == "sub-1"
    assert identity.permissions == ("project:paradox:access",)
    assert identity.avatar_url == "https://avatar"


@pytest.mark.parametrize("auth", [
    {"provider": "google"},
    {"projectId": "other"},
    {"issuer": "https://other"},
    {"subject": ""},
])
def test_parse_nexuss_identity_flag_on_rejects_invalid_claims(monkeypatch, auth):
    monkeypatch.setattr(settings, "paradox_envx_only_auth_enabled", True)
    monkeypatch.setattr(settings, "nexuss_auth_project_id", "paradox")
    monkeypatch.setattr(settings, "envx_oidc_issuer_url", "https://issuer")
    with pytest.raises(HTTPException) as exc:
        parse_nexuss_identity(_envx_payload(**auth))
    assert exc.value.status_code == 401


def test_direct_nxa_api_path_enforces_envx_flag(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request

    import app.auth as auth

    monkeypatch.setattr(settings, "paradox_envx_only_auth_enabled", True)
    observed = {}

    async def fake_verify(_api_key, *, require_envx=False):
        observed["require_envx"] = require_envx
        return SimpleNamespace(user_id="nexuss-user")

    async def fake_provision(identity, _db):
        assert identity.user_id == "nexuss-user"
        return SimpleNamespace(id="local-user")

    monkeypatch.setattr(auth, "verify_nexuss_api_key", fake_verify)
    monkeypatch.setattr(auth, "provision_nexuss_user", fake_provision)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    user = asyncio.run(auth.get_current_user(request, "nxa_test", object()))

    assert observed["require_envx"] is True
    assert user.id == "local-user"
    assert request.state.user_id == "local-user"


def test_direct_nxa_api_path_preserves_legacy_access_before_envx_cutover(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request

    import app.auth as auth

    monkeypatch.setattr(settings, "paradox_envx_only_auth_enabled", False)
    observed = {}

    async def fake_verify(_api_key, *, require_envx=False):
        observed["require_envx"] = require_envx
        return SimpleNamespace(user_id="legacy-nexuss-user", provider="google")

    async def fake_provision(identity, _db):
        assert identity.user_id == "legacy-nexuss-user"
        assert identity.provider == "google"
        return SimpleNamespace(id="legacy-local-user")

    monkeypatch.setattr(auth, "verify_nexuss_api_key", fake_verify)
    monkeypatch.setattr(auth, "provision_nexuss_user", fake_provision)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    user = asyncio.run(auth.get_current_user(request, "nxa_existing", object()))

    assert observed["require_envx"] is False
    assert user.id == "legacy-local-user"
    assert request.state.user_id == "legacy-local-user"


def test_envx_exchange_key_records_verified_identity_without_replacing_legacy_key():
    import uuid

    from app.models import User
    from app.nexuss_auth import NexussIdentity
    from app.routers.auth import _issue_api_key

    class MemoryDb:
        def __init__(self):
            self.records = []

        def add(self, value):
            self.records.append(value)

    user = User(
        id=uuid.uuid4(), email="owner@example.com", username="owner",
        api_key_hash="legacy-key-hash", auth_provider="google",
    )
    identity = NexussIdentity(
        user_id="nexuss-user", email=user.email, name="Owner",
        project_id="paradox", provider="envx", issuer="https://issuer",
        subject="immutable-sub", permissions=("project:paradox:access",),
    )
    db = MemoryDb()

    _, record = _issue_api_key(user, db, "envx", identity=identity)

    assert record.auth_project_id == "paradox"
    assert record.auth_provider == "envx"
    assert record.auth_issuer == "https://issuer"
    assert record.auth_subject == "immutable-sub"
    assert record.auth_permissions == "project:paradox:access"
    assert user.api_key_hash == "legacy-key-hash"


def _callback_request(form, *, cookie_state="a" * 64, content_type="application/x-www-form-urlencoded"):
    from urllib.parse import urlencode

    from starlette.requests import Request

    body = urlencode(form).encode("utf-8")
    headers = [
        (b"content-type", content_type.encode("ascii")),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cookie", f"paradox_nexuss_state={cookie_state}".encode("ascii")),
    ]
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "https",
        "path": "/v1/auth/nexuss/callback", "raw_path": b"/v1/auth/nexuss/callback",
        "query_string": b"", "headers": headers, "client": ("test", 1),
        "server": ("paradox-db.wasmer.app", 443),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _configure_browser_auth(monkeypatch, *, enabled=True):
    monkeypatch.setattr(settings, "paradox_envx_only_auth_enabled", enabled)
    monkeypatch.setattr(settings, "nexuss_auth_url", "https://nexuss-auth.vercel.app")
    monkeypatch.setattr(settings, "nexuss_auth_project_id", "paradox")
    monkeypatch.setattr(settings, "paradox_auth_callback_url", "https://paradox-db.wasmer.app/v1/auth/nexuss/callback")


def test_paradox_envx_browser_login_is_default_off_and_uses_exact_redirect(monkeypatch):
    import asyncio
    from http.cookies import SimpleCookie

    from fastapi import HTTPException

    from app.routers import auth as auth_routes

    _configure_browser_auth(monkeypatch, enabled=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_routes.nexuss_login())
    assert exc.value.status_code == 503

    _configure_browser_auth(monkeypatch, enabled=True)
    response = asyncio.run(auth_routes.nexuss_login())
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://nexuss-auth.vercel.app/oauth/start/envx?")
    assert "project_id=paradox" in location
    assert "handoff=1" in location
    assert "redirect_uri=https%3A%2F%2Fparadox-db.wasmer.app%2Fv1%2Fauth%2Fnexuss%2Fcallback" in location
    assert "handoff_token" not in location
    cookie = SimpleCookie(); cookie.load(response.headers["set-cookie"])
    state_cookie = cookie["paradox_nexuss_state"]
    assert state_cookie["httponly"]
    assert state_cookie["secure"]
    assert state_cookie["samesite"].lower() == "none"
    assert state_cookie["path"] == "/v1/auth/nexuss/callback"
    assert state_cookie.value in location


def test_paradox_envx_callback_rejects_state_before_handoff_exchange(monkeypatch):
    import asyncio
    from fastapi import HTTPException

    from app.routers import auth as auth_routes

    _configure_browser_auth(monkeypatch, enabled=True)
    called = False

    async def forbidden_exchange(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("handoff exchange must not happen before CSRF validation")

    monkeypatch.setattr(auth_routes, "exchange_nexuss_handoff", forbidden_exchange)
    request = _callback_request({
        "project_id": "paradox", "client_state": "b" * 64, "handoff_token": "h" * 43,
    })
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_routes.nexuss_callback(request, object()))
    assert exc.value.status_code == 400
    assert not called


def test_paradox_envx_callback_issues_only_scoped_cookie_without_token_in_url(monkeypatch):
    import asyncio
    import uuid
    from http.cookies import SimpleCookie
    from types import SimpleNamespace

    from app.nexuss_auth import NexussIdentity
    from app.routers import auth as auth_routes

    _configure_browser_auth(monkeypatch, enabled=True)
    identity = NexussIdentity(
        user_id="central-user-1", email="owner@example.com", name="Owner",
        project_id="paradox", provider="envx", issuer="https://issuer.example/auth/v1",
        subject="immutable-subject", permissions=("project:paradox:access",),
    )
    exchange_calls = []

    async def fake_exchange(token, *, require_envx=False):
        exchange_calls.append((token, require_envx))
        return identity

    async def fake_provision(_identity, _db):
        return SimpleNamespace(
            id=uuid.uuid4(), email="owner@example.com", username="owner",
            api_key_hash=None, auth_project_id=None, auth_provider=None,
            auth_issuer=None, auth_subject=None, auth_permissions=None,
        )

    class MemoryDb:
        def __init__(self): self.records = []
        def add(self, record): self.records.append(record)
        async def flush(self): pass

    monkeypatch.setattr(auth_routes, "exchange_nexuss_handoff", fake_exchange)
    monkeypatch.setattr(auth_routes, "provision_nexuss_user", fake_provision)
    db = MemoryDb()
    handoff_token = "h" * 43
    response = asyncio.run(auth_routes.nexuss_callback(
        _callback_request({"project_id": "paradox", "client_state": "a" * 64, "handoff_token": handoff_token}),
        db,
    ))

    assert exchange_calls == [(handoff_token, True)]
    assert response.status_code == 303
    assert response.headers["location"] == "/v1/auth/me"
    assert handoff_token not in response.headers["location"]
    assert handoff_token.encode() not in response.body
    cookie = SimpleCookie(); cookie.load(response.headers["set-cookie"])
    api_cookie = cookie["paradox_api_key"]
    assert api_cookie["httponly"]
    assert api_cookie["secure"]
    assert api_cookie["samesite"].lower() == "lax"
    assert api_cookie["path"] == "/"
    assert api_cookie.value.startswith("pk_")
    assert handoff_token not in api_cookie.value
    assert len(db.records) == 1
    record = db.records[0]
    assert record.auth_project_id == "paradox"
    assert record.auth_provider == "envx"
    assert record.auth_issuer == "https://issuer.example/auth/v1"
    assert record.auth_subject == "immutable-subject"
    assert record.auth_permissions == "project:paradox:access"
