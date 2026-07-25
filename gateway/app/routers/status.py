from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import DatabaseVersion, SyncLog, UserChannel
from app.telegram_logger import log_operation

router = APIRouter()


@router.get("/status")
async def status(
    user: UserChannel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DatabaseVersion).where(DatabaseVersion.user_id == user.user_id)
    )
    db_versions = result.scalars().all()

    databases = []
    for dv in db_versions:
        last_sync_result = await db.execute(
            select(SyncLog)
            .where(
                SyncLog.user_id == user.user_id,
                SyncLog.database_name == dv.database_name,
                SyncLog.status == "completed",
            )
            .order_by(SyncLog.completed_at.desc())
            .limit(1)
        )
        last_sync = last_sync_result.scalar_one_or_none()

        databases.append({
            "name": dv.database_name,
            "latest_version": dv.latest_version,
            "latest_message_id": dv.latest_message_id,
            "pending_changesets": 0,
            "last_sync_at": last_sync.completed_at.isoformat() if last_sync and last_sync.completed_at else None,
        })

    await log_operation(
        "status",
        f"user={user.user_id} databases={len(databases)}",
        "success",
    )

    return {
        "user_id": user.user_id,
        "databases": databases,
    }
