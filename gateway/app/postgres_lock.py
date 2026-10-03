"""Transaction-scoped PostgreSQL advisory locks for version writers.

Locks are acquired on the caller's AsyncSession and are released automatically
when that session's current transaction commits or rolls back. Never call
commit inside a locked critical section.
"""

from __future__ import annotations

import asyncio
import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_POLL_INTERVAL_SECONDS = 0.05
_LOCK_NAMESPACE = b"paradox-db-v1"


def advisory_lock_id(key: str) -> int:
    """Map an application lock key to a stable signed PostgreSQL bigint."""
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8, person=_LOCK_NAMESPACE).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class PostgresAdvisoryLock:
    """Acquire a distributed lock scoped to the current DB transaction."""

    async def acquire(
        self,
        session: AsyncSession,
        key: str,
        timeout: float = 30,
    ) -> bool:
        lock_id = advisory_lock_id(key)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        statement = text("SELECT pg_try_advisory_xact_lock(:lock_id)")

        while True:
            result = await session.execute(statement, {"lock_id": lock_id})
            if bool(result.scalar_one()):
                return True

            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))


postgres_advisory_lock = PostgresAdvisoryLock()
