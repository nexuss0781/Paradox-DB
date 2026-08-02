"""Authentication utilities — bcrypt password hashing + cloud-issued API keys.

The cloud issues every user an API key (`pk_...`). Keys are SHA-256 hashed
at rest; the plaintext is shown once at issue time. All authenticated
endpoints require the `X-API-Key` header.
"""

import hashlib
import secrets
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def generate_api_key() -> str:
    return f"pk_{secrets.token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def get_current_user(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the authenticated user from a cloud-issued API key."""
    from .models import User

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    result = await db.execute(
        select(User).where(User.api_key_hash == hash_api_key(api_key))
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid API key")

    request.state.user_id = str(user.id)
    return user


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        import time
        now = time.time()
        self._requests.setdefault(key, [])
        self._requests[key] = [
            t for t in self._requests[key] if now - t < self.window
        ]
        if len(self._requests[key]) >= self.max_requests:
            return False
        self._requests[key].append(now)
        return True


rate_limiter = RateLimiter()
