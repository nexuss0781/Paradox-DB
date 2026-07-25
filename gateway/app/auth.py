import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .telegram_logger import log_operation

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def hash_api_key(key: str) -> str:
    return hashlib.sha256((settings.api_key_salt + key).encode()).hexdigest()


def generate_api_key() -> str:
    return f"pk_{secrets.token_urlsafe(32)}"


def create_jwt(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    from .models import UserChannel

    user_id = None

    if api_key:
        hashed = hash_api_key(api_key)
        result = await db.execute(
            select(UserChannel).where(UserChannel.api_key_hash == hashed)
        )
        user = result.scalar_one_or_none()
        if user:
            user_id = user.user_id

    if not user_id and bearer:
        payload = decode_jwt(bearer.credentials)
        user_id = payload.get("sub")

    if not user_id:
        ip = request.client.host if request.client else "unknown"
        await log_operation("auth", f"Missing/invalid auth from {ip}", "fail")
        raise HTTPException(status_code=401, detail="Missing or invalid authentication")

    result = await db.execute(
        select(UserChannel).where(UserChannel.user_id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        await log_operation("auth", f"User not found: {user_id}", "fail")
        raise HTTPException(status_code=401, detail="User not found")

    request.state.user_id = user_id
    return user


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = {}

    def check(self, user_id: str) -> bool:
        now = time.time()
        self._requests.setdefault(user_id, [])
        self._requests[user_id] = [
            t for t in self._requests[user_id] if now - t < self.window
        ]
        if len(self._requests[user_id]) >= self.max_requests:
            return False
        self._requests[user_id].append(now)
        return True


rate_limiter = RateLimiter()
