from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import UserChannel, VersionHistory
from app.telegram_logger import log_operation

router = APIRouter()


@router.get("/versions")
async def versions(
    database_name: str = Query(...),
    user: UserChannel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VersionHistory).where(
            VersionHistory.user_id == user.user_id,
            VersionHistory.database_name == database_name,
        ).order_by(VersionHistory.version.desc())
    )
    rows = result.scalars().all()

    await log_operation(
        "versions",
        f"{database_name} user={user.user_id} count={len(rows)}",
        "success",
    )

    return {
        "database_name": database_name,
        "versions": [
            {
                "version": row.version,
                "message_id": row.message_id,
                "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else "",
                "size_bytes": row.file_size,
            }
            for row in rows
        ],
    }
