"""Account and API-key endpoints."""

import re
import uuid
from hmac import compare_digest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    generate_api_key,
    get_current_user,
    hash_api_key,
    rate_limiter,
)
from ..database import get_db
from ..config import settings
from ..models import (
    APIKey,
    APIKeyCreateRequest,
    APIKeyResponse,
    AuthResponse,
    LoginRequest,
    NexussApiKeyExchangeRequest,
    NexussHandoffExchangeRequest,
    RegisterRequest,
    User,
    UserResponse,
)
from ..nexuss_auth import NexussIdentity, exchange_nexuss_handoff, provision_nexuss_user, verify_nexuss_api_key

router = APIRouter(prefix="/v1/auth", tags=["auth"])
_CALLBACK_PATH = "/v1/auth/nexuss/callback"
_CALLBACK_URL = "https://paradox-db.wasmer.app/v1/auth/nexuss/callback"
_STATE_COOKIE = "paradox_nexuss_state"
_API_COOKIE = "paradox_api_key"

def _https_url(value: str) -> str | None:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        return None
    return value

def _browser_config() -> tuple[str, str, str]:
    auth_url = _https_url(settings.nexuss_auth_url)
    callback = settings.paradox_auth_callback_url.strip()
    parsed = urlsplit(callback)
    if (not auth_url or callback != _CALLBACK_URL or parsed.path != _CALLBACK_PATH
            or parsed.username or parsed.password):
        raise HTTPException(status_code=503, detail="Nexuss browser authentication is not configured")
    return auth_url, settings.nexuss_auth_project_id.strip(), callback


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="expires_at must be an ISO-8601 timestamp") from exc


def _issue_api_key(
    user: User,
    db: AsyncSession,
    name: str = "default",
    expires_at: datetime | None = None,
    identity: NexussIdentity | None = None,
) -> tuple[str, APIKey]:
    new_key = generate_api_key()
    record = APIKey(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name.strip()[:100] or "default",
        key_hash=hash_api_key(new_key),
        expires_at=expires_at,
        auth_project_id=identity.project_id if identity else user.auth_project_id,
        auth_provider=identity.provider if identity else user.auth_provider,
        auth_issuer=identity.issuer if identity else user.auth_issuer,
        auth_subject=identity.subject if identity else user.auth_subject,
        auth_permissions=(",".join(identity.permissions) or None) if identity else user.auth_permissions,
    )
    db.add(record)
    # Keep the legacy field populated for older gateway code during migration.
    # Keep a pre-existing legacy key valid; only populate this compatibility
    # column for users that do not already have one.
    if user.api_key_hash is None:
        user.api_key_hash = record.key_hash
    return new_key, record


def _auth_response(user: User, api_key: str) -> AuthResponse:
    return AuthResponse(user_id=str(user.id), email=user.email, username=user.username, api_key=api_key)


def _key_response(record: APIKey, plaintext: str | None = None) -> APIKeyResponse:
    return APIKeyResponse(
        id=str(record.id),
        name=record.name,
        created_at=record.created_at.isoformat() if record.created_at else "",
        last_used_at=record.last_used_at.isoformat() if record.last_used_at else None,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        revoked_at=record.revoked_at.isoformat() if record.revoked_at else None,
        api_key=plaintext,
    )


@router.post("/register", status_code=410)
async def register(_: RegisterRequest):
    """Password registration is retired in favor of Nexuss Auth."""
    raise HTTPException(
        status_code=410,
        detail="Use Nexuss Auth to sign in, then exchange its API key",
    )


@router.post("/login", status_code=410)
async def login(_: LoginRequest):
    """Password login is retired in favor of Nexuss Auth."""
    raise HTTPException(
        status_code=410,
        detail="Use parad auth login --api-key with a Paradox or Nexuss API key",
    )


@router.get("/nexuss/login")
async def nexuss_login():
    """Start the strict ENVX browser handoff without deriving any URL from Host."""
    if not settings.paradox_envx_only_auth_enabled:
        raise HTTPException(status_code=503, detail="ENVX authentication is not enabled")
    auth_url, project_id, callback = _browser_config()
    if not project_id:
        raise HTTPException(status_code=503, detail="Nexuss browser authentication is not configured")
    state = uuid.uuid4().hex + uuid.uuid4().hex
    location = (f"{auth_url}/oauth/start/envx?redirect_uri={httpx_encode(callback)}"
                f"&project_id={httpx_encode(project_id)}&handoff=1&client_state={httpx_encode(state)}")
    response = RedirectResponse(url=location, status_code=307)
    response.set_cookie(_STATE_COOKIE, state, max_age=300, httponly=True, secure=True, samesite="none", path=_CALLBACK_PATH)
    return response

def httpx_encode(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")

@router.post("/nexuss/callback")
async def nexuss_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Consume the one-time form handoff and establish only a local cookie session."""
    _, project_id, _ = _browser_config()
    if not settings.paradox_envx_only_auth_enabled:
        raise HTTPException(status_code=503, detail="ENVX authentication is disabled")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Expected form-encoded callback")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 4096:
                raise HTTPException(status_code=413, detail="Callback form is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid callback form size") from exc
    try:
        raw_body = await request.body()
        if len(raw_body) > 4096:
            raise HTTPException(status_code=413, detail="Callback form is too large")
        values = parse_qs(raw_body.decode("utf-8"), strict_parsing=True, max_num_fields=4)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid callback form")
    state_values = values.get("client_state", [])
    cookie_state = request.cookies.get(_STATE_COOKIE, "")
    if (len(state_values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", state_values[0])
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", cookie_state)
            or not compare_digest(state_values[0], cookie_state)):
        raise HTTPException(status_code=400, detail="Invalid callback state")
    posted_project = values.get("project_id", [])
    if len(posted_project) != 1 or not compare_digest(posted_project[0], project_id):
        raise HTTPException(status_code=400, detail="Invalid callback project")
    handoff_values = values.get("handoff_token", [])
    if len(handoff_values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", handoff_values[0]):
        raise HTTPException(status_code=400, detail="Invalid callback handoff")
    identity = await exchange_nexuss_handoff(handoff_values[0], require_envx=True)
    user = await provision_nexuss_user(identity, db)
    api_key, _ = _issue_api_key(user, db, "nexuss-browser", identity=identity)
    await db.flush()
    response = RedirectResponse(url="/v1/auth/me", status_code=303)
    response.set_cookie(_API_COOKIE, api_key, max_age=86400, httponly=True, secure=True, samesite="lax", path="/")
    response.delete_cookie(_STATE_COOKIE, path=_CALLBACK_PATH)
    return response

@router.post("/nexuss/logout")
async def nexuss_logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(_API_COOKIE, path="/")
    response.delete_cookie(_STATE_COOKIE, path=_CALLBACK_PATH)
    return response

@router.post("/nexuss/exchange", response_model=AuthResponse)
async def exchange_nexuss_api_key(
    body: NexussApiKeyExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Convert a verified Nexuss ``nxa_`` key into a local Paradox ``pk_`` key."""
    if not settings.paradox_envx_only_auth_enabled:
        raise HTTPException(status_code=401, detail="Nexuss Auth is disabled")
    if not rate_limiter.check(f"nexuss:{hash_api_key(body.api_key)[:16]}"):
        raise HTTPException(status_code=429, detail="Too many Nexuss authentication attempts")
    identity = await verify_nexuss_api_key(
        body.api_key, require_envx=settings.paradox_envx_only_auth_enabled
    )
    user = await provision_nexuss_user(identity, db)
    api_key, _ = _issue_api_key(user, db, "nexuss-exchange", identity=identity)
    await db.flush()
    return _auth_response(user, api_key)


@router.post("/nexuss/handoff", response_model=AuthResponse)
async def exchange_nexuss_handoff_token(
    body: NexussHandoffExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trusted server callback for a one-time Google/Nexuss handoff token."""
    if not settings.paradox_envx_only_auth_enabled:
        raise HTTPException(status_code=401, detail="Nexuss Auth is disabled")
    identity = await exchange_nexuss_handoff(body.handoff_token, require_envx=True)
    user = await provision_nexuss_user(identity, db)
    api_key, _ = _issue_api_key(user, db, "nexuss-handoff", identity=identity)
    await db.flush()
    return _auth_response(user, api_key)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(user.id), email=user.email, username=user.username,
        is_active=user.is_active, created_at=user.created_at.isoformat() if user.created_at else "",
    )


@router.post("/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    body: APIKeyCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plaintext, record = _issue_api_key(user, db, body.name, _parse_expiry(body.expires_at))
    await db.flush()
    return _key_response(record, plaintext)


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(APIKey).where(APIKey.user_id == user.id).order_by(APIKey.created_at.desc()))
    return [_key_response(record) for record in result.scalars().all()]


@router.post("/api-key", response_model=AuthResponse)
async def mint_api_key(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    plaintext, _ = _issue_api_key(user, db, "rotated")
    await db.flush()
    return _auth_response(user, plaintext)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(key_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="API key not found")
    record.revoked_at = datetime.utcnow()
    await db.flush()
