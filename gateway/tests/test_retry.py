import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from app.retry import retry_telegram_operation


@pytest.mark.asyncio
async def test_succeeds_on_first_try():
    func = AsyncMock(return_value="ok")
    result = await retry_telegram_operation(func, max_retries=3)
    assert result == "ok"
    func.assert_awaited_once()


@pytest.mark.asyncio
async def test_retries_on_transient_error():
    func = AsyncMock(side_effect=[ValueError("transient"), "ok"])
    with patch("app.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await retry_telegram_operation(func, max_retries=3)
    assert result == "ok"
    assert func.await_count == 2


@pytest.mark.asyncio
async def test_raises_after_max_retries():
    func = AsyncMock(side_effect=ValueError("permanent"))
    with patch("app.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ValueError, match="permanent"):
            await retry_telegram_operation(func, max_retries=3)
    assert func.await_count == 3


@pytest.mark.asyncio
async def test_exponential_backoff_delays():
    func = AsyncMock(side_effect=[ValueError("e1"), ValueError("e2"), "ok"])
    sleep_mock = AsyncMock()
    with patch("app.retry.asyncio.sleep", sleep_mock):
        await retry_telegram_operation(func, max_retries=3)

    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_does_not_retry_unlisted_exceptions():
    func = AsyncMock(side_effect=TypeError("not transient"))
    with patch("app.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TypeError, match="not transient"):
            await retry_telegram_operation(
                func,
                max_retries=3,
                transient_exceptions=(ValueError,),
            )
    func.assert_awaited_once()
