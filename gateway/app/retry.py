import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.services.telegram import (
    TelegramError,
    TelegramRateLimitError,
    TelegramServerError,
)

logger = logging.getLogger(__name__)

# Errors that are safe to retry: Telegram throttling (429) and server hiccups
# (5xx). Everything else (401 token revoked, 403 blocked, 404, 400, …) is
# permanent and must surface immediately.
TRANSIENT_EXCEPTIONS = (TelegramRateLimitError, TelegramServerError)


async def retry_telegram_operation(
    func: Callable,
    *args,
    max_retries: int = 3,
    transient_exceptions: tuple = TRANSIENT_EXCEPTIONS,
    **kwargs,
) -> Any:
    last_error: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except transient_exceptions as e:
            last_error = e
            delay = None
            if isinstance(e, TelegramRateLimitError):
                delay = e.retry_after
            if delay is None:
                delay = min(2 ** attempt, 10)
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed: {e}, retrying in {delay}s"
            )
            await asyncio.sleep(delay)
        except TelegramError:
            # Non-transient Telegram errors must not be retried.
            raise
    raise last_error
