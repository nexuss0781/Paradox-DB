"""Auth endpoints — register, login, me."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    create_jwt,
    generate_api_key,
    get_current_user,
    hash_api_key,
    hash_password,
    verify_password,
)
from ..database import get_db
from ..models import User, RegisterRequest, LoginRequest, AuthResponse, UserResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user with email, username, and password."""
    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # Check username uniqueness
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    api_key = generate_api_key()
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password[:72]),
        api_key_hash=hash_api_key(api_key),
    )
    db.add(user)
    await db.flush()

    token = create_jwt(str(user.id))
    return AuthResponse(
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        access_token=token,
        api_key=api_key,
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email and password."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password[:72], user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    token = create_jwt(str(user.id))
    return AuthResponse(
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        access_token=token,
    )


@router.post("/api-key", response_model=AuthResponse)
async def mint_api_key(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint a new API key for the current user (old key is invalidated)."""
    new_key = generate_api_key()
    user.api_key_hash = hash_api_key(new_key)
    db.add(user)
    await db.flush()
    return AuthResponse(
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        access_token=create_jwt(str(user.id)),
        api_key=new_key,
    )


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
