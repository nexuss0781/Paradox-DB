from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.version_lock import (
    VersionWriteLockTimeoutError,
    acquire_version_write_lock,
    version_write_lock_key,
)


class _Result:
    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _Session:
    def __init__(self, row=None):
        self.row = row
        self.statement = None

    async def execute(self, statement, params=None):
        self.statement = statement
        return _Result(self.row)


def test_version_write_lock_key_is_namespaced_and_stable():
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    database_id = UUID("00000000-0000-0000-0000-000000000002")
    key = version_write_lock_key(user_id, database_id)

    assert key == f"paradox-db:version-write:{user_id}:{database_id}"


@pytest.mark.asyncio
async def test_acquire_version_lock_reloads_row_with_for_update():
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    database_id = UUID("00000000-0000-0000-0000-000000000002")
    row = object()
    session = _Session(row)
    acquire = AsyncMock(return_value=True)

    with patch("app.version_lock.postgres_advisory_lock.acquire", acquire):
        result = await acquire_version_write_lock(
            session,
            user_id=user_id,
            database_id=database_id,
            timeout=7,
        )

    assert result is row
    acquire.assert_awaited_once_with(
        session,
        version_write_lock_key(user_id, database_id),
        timeout=7,
    )
    assert "FOR UPDATE" in str(session.statement).upper()


@pytest.mark.asyncio
async def test_acquire_version_lock_times_out_without_querying_row():
    user_id = UUID("00000000-0000-0000-0000-000000000001")
    database_id = UUID("00000000-0000-0000-0000-000000000002")
    session = _Session()

    with patch("app.version_lock.postgres_advisory_lock.acquire", AsyncMock(return_value=False)):
        with pytest.raises(VersionWriteLockTimeoutError):
            await acquire_version_write_lock(
                session,
                user_id=user_id,
                database_id=database_id,
                timeout=0,
            )

    assert session.statement is None
