import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import generate_api_key, hash_api_key, create_jwt
from ..config import settings
from ..database import get_db
from ..models import UserChannel
from ..telegram_logger import log_operation

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register")
async def register(db: AsyncSession = Depends(get_db)):
    user_id = f"u_{secrets.token_urlsafe(16)}"
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    result = await db.execute(
        select(UserChannel).where(UserChannel.user_id == user_id)
    )
    if result.scalar_one_or_none():
        await log_operation("register", f"User already exists: {user_id}", "fail")
        raise HTTPException(status_code=409, detail="User already exists")

    user = UserChannel(
        user_id=user_id,
        channel_id=settings.telegram_storage_chat_id,
        bot_token_id=settings.telegram_bot_token,
        api_key_hash=api_key_hash,
    )
    db.add(user)
    await db.commit()

    jwt_token = create_jwt(user_id)
    await log_operation("register", f"New user: {user_id}", "success")
    return {"user_id": user_id, "api_key": api_key, "jwt": jwt_token}
