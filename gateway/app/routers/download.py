import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import DatabaseVersion, SyncLog, UserChannel, VersionHistory
from app.services.telegram import TelegramClient, TelegramPermanentError

router = APIRouter()


def _get_tg_client(user: UserChannel) -> TelegramClient:
    return TelegramClient(
        bot_token=settings.telegram_bot_token,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )


@router.get("/download")
async def download(
    request: Request,
    database_name: str = Query(...),
    version: int | None = Query(default=None),
    user: UserChannel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if version is not None:
        result = await db.execute(
            select(VersionHistory).where(
                VersionHistory.user_id == user.user_id,
                VersionHistory.database_name == database_name,
                VersionHistory.version == version,
            )
        )
        history_entry = result.scalar_one_or_none()
        if not history_entry:
            raise HTTPException(status_code=404, detail="Version not found")
        message_id = history_entry.message_id
        resolved_version = history_entry.version
    else:
        result = await db.execute(
            select(DatabaseVersion).where(
                DatabaseVersion.user_id == user.user_id,
                DatabaseVersion.database_name == database_name,
            )
        )
        db_version = result.scalar_one_or_none()
        if not db_version:
            raise HTTPException(status_code=404, detail="Database not found")
        message_id = db_version.latest_message_id
        resolved_version = db_version.latest_version

    log_entry = SyncLog(
        request_id=str(uuid.uuid4()),
        user_id=user.user_id,
        database_name=database_name,
        operation="download",
        telegram_message_id=message_id,
        status="in_progress",
        created_at=datetime.now(timezone.utc),
    )
    db.add(log_entry)
    await db.flush()

    try:
        tg = _get_tg_client(user)
        file_bytes = await tg.download_file(
            channel_id=user.channel_id,
            message_id=message_id,
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
        raise HTTPException(status_code=500, detail="Internal download error")

    log_entry.status = "completed"
    log_entry.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return Response(
        content=file_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{database_name}"',
            "X-Message-ID": message_id,
            "X-Version": str(resolved_version),
        },
    )
