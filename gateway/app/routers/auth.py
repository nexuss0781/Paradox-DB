"""Account and API-key endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    generate_api_key,
    get_current_user,
    hash_api_key,
    hash_password,
    rate_limiter,
    validate_password,
    verify_password,
)
from ..database import get_db
from ..models import (
    APIKey,
    APIKeyCreateRequest,
    APIKeyResponse,
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    User,
    UserResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="expires_at must be an ISO-8601 timestamp") from exc


def _issue_api_key(user: User, db: AsyncSession, name: str = "default", expires_at: datetime | None = None) -> tuple[str, APIKey]:
    new_key = generate_api_key()
    record = APIKey(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name.strip()[:100] or "default",
        key_hash=hash_api_key(new_key),
        expires_at=expires_at,
    )
    db.add(record)
    # Keep the legacy field populated for older gateway code during migration.
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


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if not rate_limiter.check(f"register:{body.email.lower()}"):
        raise HTTPException(status_code=429, detail="Too many registrations, try again later")
    try:
        validate_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password),
    )
    api_key, _ = _issue_api_key(user, db, "initial")
    db.add(user)
    await db.flush()
    return _auth_response(user, api_key)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    if not rate_limiter.check(f"login:{body.email.lower()}"):
        raise HTTPException(status_code=429, detail="Too many login attempts, try again later")
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    api_key, _ = _issue_api_key(user, db, "login")
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
