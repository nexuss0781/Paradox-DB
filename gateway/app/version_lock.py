"""Serialize version writers through PostgreSQL transaction advisory locks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ParadoxDB
from app.postgres_lock import postgres_advisory_lock


class VersionWriteLockTimeoutError(TimeoutError):
    """The version-writer lock was not acquired within its timeout."""


def version_write_lock_key(user_id: UUID | str, database_id: UUID | str) -> str:
    return f"paradox-db:version-write:{user_id}:{database_id}"


async def acquire_version_write_lock(
    session: AsyncSession,
    *,
    user_id: UUID | str,
    database_id: UUID | str,
    timeout: float = 30,
) -> ParadoxDB | None:
    """Acquire the transaction lock and refresh the row under ``FOR UPDATE``.

    The lock remains held until the caller commits or rolls back ``session``.
    Refreshing the row after acquiring the advisory lock prevents a writer that
    waited behind another request from using a stale ``latest_version`` value.
    """
    acquired = await postgres_advisory_lock.acquire(
        session,
        version_write_lock_key(user_id, database_id),
        timeout=timeout,
    )
    if not acquired:
        raise VersionWriteLockTimeoutError("Timed out waiting for PostgreSQL version-writer lock")

    result = await session.execute(
        select(ParadoxDB)
        .where(ParadoxDB.id == database_id, ParadoxDB.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()
