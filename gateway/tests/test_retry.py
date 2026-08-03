from unittest.mock import AsyncMock, patch

import pytest

from app.retry import retry_telegram_operation
from app.services.telegram import (
    TelegramForbiddenError,
    TelegramRateLimitError,
    TelegramServerError,
)


@pytest.mark.asyncio
async def test_succeeds_on_first_try():
    func = AsyncMock(return_value="ok")
    result = await retry_telegram_operation(func, max_retries=3)
    assert result == "ok"
    func.assert_awaited_once()


@pytest.mark.asyncio
async def test_retries_on_server_error():
    func = AsyncMock(side_effect=[TelegramServerError("boom"), "ok"])
    with patch("app.retry.asyncio.sleep", new_callable=AsyncMock):
        result = await retry_telegram_operation(func, max_retries=3)
    assert result == "ok"
    assert func.await_count == 2


@pytest.mark.asyncio
async def test_raises_after_max_retries():
    func = AsyncMock(side_effect=TelegramServerError("boom"))
    with patch("app.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TelegramServerError):
            await retry_telegram_operation(func, max_retries=3)
    assert func.await_count == 3


@pytest.mark.asyncio
async def test_exponential_backoff_delays():
    func = AsyncMock(side_effect=[TelegramServerError("e1"), TelegramServerError("e2"), "ok"])
    sleep_mock = AsyncMock()
    with patch("app.retry.asyncio.sleep", sleep_mock):
        await retry_telegram_operation(func, max_retries=3)

    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_rate_limit_uses_retry_after():
    func = AsyncMock(side_effect=[TelegramRateLimitError("429", retry_after=7), "ok"])
    sleep_mock = AsyncMock()
    with patch("app.retry.asyncio.sleep", sleep_mock):
        result = await retry_telegram_operation(func, max_retries=3)
    assert result == "ok"
    assert sleep_mock.await_args.args[0] == 7


@pytest.mark.asyncio
async def test_does_not_retry_permanent_telegram_error():
    func = AsyncMock(side_effect=TelegramForbiddenError("blocked"))
    with patch("app.retry.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TelegramForbiddenError):
            await retry_telegram_operation(func, max_retries=3)
    func.assert_awaited_once()


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
