from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import DatabaseVersion, UserChannel

router = APIRouter()


@router.get("/versions")
async def versions(
    database_name: str = Query(...),
    user: UserChannel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DatabaseVersion).where(
            DatabaseVersion.user_id == user.user_id,
            DatabaseVersion.database_name == database_name,
        )
    )
    db_version = result.scalar_one_or_none()
    if not db_version:
        return {"database_name": database_name, "versions": []}

    return {
        "database_name": database_name,
        "versions": [
            {
                "version": db_version.latest_version,
                "message_id": db_version.latest_message_id,
                "uploaded_at": db_version.uploaded_at.isoformat() if db_version.uploaded_at else "",
                "size_bytes": 0,
            }
        ],
    }
