import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import DatabaseVersion, SyncLog, UserChannel
from app.services.telegram import TelegramClient, TelegramPermanentError

router = APIRouter()


def _get_tg_client(user: UserChannel) -> TelegramClient:
    return TelegramClient(bot_token=user.bot_token_id)


@router.post("/rollback")
async def rollback(
    request: Request,
    body: dict,
    user: UserChannel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    database_name = body.get("database_name")
    target_version = body.get("target_version")

    if not database_name or target_version is None:
        raise HTTPException(status_code=400, detail="database_name and target_version required")

    target_version = int(target_version)

    result = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.user_id == user.user_id,
            DatabaseVersion.database_name == database_name,
        )
    )
    db_version = result.scalar_one_or_none()
    if not db_version:
        raise HTTPException(status_code=404, detail="Database not found")

    if target_version != db_version.latest_version:
        raise HTTPException(status_code=404, detail="Target version not found")

    source_message_id = db_version.latest_message_id
    rollback_request_id = str(uuid.uuid4())

    log_entry = SyncLog(
        request_id=rollback_request_id,
        user_id=user.user_id,
        database_name=database_name,
        operation="rollback",
        telegram_message_id=source_message_id,
        status="in_progress",
        created_at=datetime.now(timezone.utc),
    )
    db.add(log_entry)
    await db.flush()

    try:
        tg = _get_tg_client(user)
        file_bytes = await tg.download_file(
            channel_id=user.channel_id,
            message_id=source_message_id,
        )
    except TelegramPermanentError as exc:
        log_entry.status = "failed"
        log_entry.error_message = str(exc)
        log_entry.completed_at = datetime.now(timezone.utc)
        await db.flush()
        raise HTTPException(status_code=502, detail=f"Telegram download failed: {exc}")
    except Exception as exc:
        log_entry.status = "failed"
        log_entry.error_message = str(exc)
        log_entry.completed_at = datetime.now(timezone.utc)
        await db.flush()
        raise HTTPException(status_code=500, detail="Internal rollback error")

    new_version = db_version.latest_version + 1
    caption = {
        "database_name": database_name,
        "version": new_version,
        "user_id": user.user_id,
        "operation": "rollback",
        "rolled_back_to": target_version,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        new_message_id = await tg.upload_file(
            channel_id=user.channel_id,
            file_bytes=file_bytes,
            caption=caption,
        )
    except TelegramPermanentError as exc:
        log_entry.status = "failed"
        log_entry.error_message = f"Re-upload failed: {exc}"
        log_entry.completed_at = datetime.now(timezone.utc)
        await db.flush()
        raise HTTPException(status_code=502, detail=f"Telegram re-upload failed: {exc}")
    except Exception as exc:
        log_entry.status = "failed"
        log_entry.error_message = f"Re-upload failed: {exc}"
        log_entry.completed_at = datetime.now(timezone.utc)
        await db.flush()
        raise HTTPException(status_code=500, detail="Internal rollback error")

    db_version.latest_message_id = new_message_id
    db_version.latest_version = new_version
    db_version.uploaded_at = datetime.now(timezone.utc)

    log_entry.status = "completed"
    log_entry.telegram_message_id = new_message_id
    log_entry.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return {
        "request_id": rollback_request_id,
        "rolled_back_to": target_version,
        "new_message_id": new_message_id,
    }
