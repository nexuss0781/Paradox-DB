import asyncio
from unittest.mock import AsyncMock

import pytest

from app.postgres_lock import PostgresAdvisoryLock, advisory_lock_id


class _Result:
    def __init__(self, value: bool):
        self._value = value

    def scalar_one(self) -> bool:
        return self._value


class _Session:
    def __init__(self, outcomes: list[bool]):
        self.outcomes = iter(outcomes)
        self.statements: list[str] = []
        self.params: list[dict[str, int]] = []

    async def execute(self, statement, params):
        self.statements.append(str(statement))
        self.params.append(params)
        return _Result(next(self.outcomes))


def test_advisory_lock_id_is_stable_signed_bigint():
    assert advisory_lock_id("paradox-db:version:user:database") == advisory_lock_id(
        "paradox-db:version:user:database"
    )
    assert -(2**63) <= advisory_lock_id("key") < 2**63
    assert advisory_lock_id("user-a:db") != advisory_lock_id("user-b:db")


@pytest.mark.asyncio
async def test_acquire_uses_transaction_scoped_postgres_advisory_lock():
    session = _Session([True])

    acquired = await PostgresAdvisoryLock().acquire(
        session, "paradox-db:version:user:database", timeout=0
    )

    assert acquired is True
    assert "pg_try_advisory_xact_lock" in session.statements[0]
    assert session.params[0]["lock_id"] == advisory_lock_id("paradox-db:version:user:database")


@pytest.mark.asyncio
async def test_acquire_retries_until_lock_is_available(monkeypatch):
    session = _Session([False, True])
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)

    acquired = await PostgresAdvisoryLock().acquire(session, "key", timeout=1)

    assert acquired is True
    assert len(session.statements) == 2
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_acquire_returns_false_after_one_immediate_timeout_attempt():
    session = _Session([False])

    acquired = await PostgresAdvisoryLock().acquire(session, "key", timeout=0)

    assert acquired is False
    assert len(session.statements) == 1
