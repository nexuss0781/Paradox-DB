import asyncio
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)


async def retry_telegram_operation(
    func: Callable,
    *args,
    max_retries: int = 3,
    transient_exceptions: tuple = (Exception,),
    **kwargs,
) -> Any:
    last_error = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except transient_exceptions as e:
            last_error = e
            delay = min(2 ** attempt, 10)
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed: {e}, retrying in {delay}s"
            )
            await asyncio.sleep(delay)
    raise last_error
