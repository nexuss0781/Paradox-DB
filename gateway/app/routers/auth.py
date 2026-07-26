"""Auth endpoints — register, login, me."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_jwt, get_current_user, hash_password, verify_password
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

    user = User(
        id=uuid.uuid4(),
        email=body.email,
        username=body.username,
        password_hash=hash_password(body.password[:72]),
    )
    db.add(user)
    await db.flush()

    token = create_jwt(str(user.id))
    return AuthResponse(
        user_id=str(user.id),
        email=user.email,
        username=user.username,
        access_token=token,
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
