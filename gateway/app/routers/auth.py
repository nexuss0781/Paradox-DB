"""Auth endpoints — register, login, me, api-key (cloud-issued keys only)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    generate_api_key,
    get_current_user,
    hash_api_key,
    hash_password,
    rate_limiter,
    verify_password,
)
from ..database import get_db
from ..models import User, RegisterRequest, LoginRequest, AuthResponse, UserResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _issue_api_key(user: User) -> str:
    """Generate a fresh API key for the user, invalidating any previous key."""
    new_key = generate_api_key()
    user.api_key_hash = hash_api_key(new_key)
    return new_key


def _auth_response(user: User, api_key: str) -> AuthResponse:
    return AuthResponse(
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        api_key=api_key,
    )


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user. The cloud issues the account's first API key."""
    if not rate_limiter.check(f"register:{body.email}"):
        raise HTTPException(status_code=429, detail="Too many registrations, try again later")

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
        password_hash=hash_password(body.password[:72]),
    )
    api_key = _issue_api_key(user)
    db.add(user)
    await db.flush()
    return _auth_response(user, api_key)


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email and password. Issues a fresh API key, invalidating the old one."""
    if not rate_limiter.check(f"login:{body.email}"):
        raise HTTPException(status_code=429, detail="Too many login attempts, try again later")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password[:72], user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    api_key = _issue_api_key(user)
    db.add(user)
    await db.flush()
    return _auth_response(user, api_key)


@router.get("/me", response_model=UserResponse)
async def me(
    user: User = Depends(get_current_user),
):
    """Get current user info."""
    return UserResponse(
        id=str(user.id),
        email=user.email,
        username=user.username,
        is_active=user.is_active,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


@router.post("/api-key", response_model=AuthResponse)
async def mint_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint a fresh API key for the current user (old key is invalidated)."""
    api_key = _issue_api_key(user)
    db.add(user)
    await db.flush()
    return _auth_response(user, api_key)
