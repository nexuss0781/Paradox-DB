"""Service-halt helper for when Telegram rate-limits the gateway.

When `TELEGRAM_RATE_LIMIT_HALT=true`, any Telegram 429 (a
`TelegramRateLimitError`) trips a full shutdown of the service so the gateway
stops hammering Telegram while throttled and the operator is forced to look.
When disabled (default), rate limits keep their normal 429 handling and the
daemon retries.
"""

import logging
import os
import signal

from app.config import settings

logger = logging.getLogger(__name__)

_halt_requested = False
_halt_reason: str | None = None


def halt_enabled() -> bool:
    """Whether the rate-limit kill switch is on in the environment."""
    return bool(settings.telegram_rate_limit_halt)


def halt_requested() -> bool:
    """Whether a halt has been requested in this process lifetime."""
    return _halt_requested


def halt_reason() -> str | None:
    """The reason string of the most recent halt (None if never halted)."""
    return _halt_reason


def halt_service(reason: str, retry_after: int | None = None) -> None:
    """Stop the service entirely (graceful uvicorn shutdown via SIGTERM)."""
    global _halt_requested, _halt_reason
    _halt_requested = True
    _halt_reason = reason
    suffix = f" (retry_after={retry_after}s)" if retry_after is not None else ""
    logger.error("HALT: Telegram rate limit tripped kill switch — %s%s", reason, suffix)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception as e:  # pragma: no cover - process usually gone before this
        logger.error("HALT: failed to send SIGTERM: %s", e)


def maybe_halt_on_rate_limit(
    err: Exception, *, context: str
) -> bool:
    """Halt the service on a Telegram rate limit if the switch is on.

    Returns True when a halt was requested (callers should stop doing work and
    return a 503), or False when the switch is off and normal 429 handling
    should proceed.
    """
    if not halt_enabled():
        return False
    retry_after = getattr(err, "retry_after", None)
    halt_service(f"rate limit while {context}", retry_after=retry_after)
    return True


def reset() -> None:
    """Clear halt state (tests only)."""
    global _halt_requested, _halt_reason
    _halt_requested = False
    _halt_reason = None
