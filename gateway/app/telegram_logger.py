"""Telegram channel logger for all Paradox-DB operations.

Sends structured logs to the configured storage channel for every
database operation: register, upload, download, versions, rollback, status.
"""

import logging
from datetime import UTC, datetime

from app.config import settings
from app.halt import maybe_halt_on_rate_limit
from app.services.telegram import (
    TelegramClient,
    TelegramError,
    TelegramRateLimitError,
)

logger = logging.getLogger("gateway.telegram_logger")

_CHANNEL_ID: str = ""
_CHANNEL_NAME: str = ""
_RESOLVED: bool = False


async def _resolve_channel() -> tuple[str, str]:
    global _CHANNEL_ID, _CHANNEL_NAME, _RESOLVED
    if _RESOLVED:
        return _CHANNEL_ID, _CHANNEL_NAME

    _CHANNEL_ID = settings.telegram_log_chat_id
    if not _CHANNEL_ID:
        _CHANNEL_ID = settings.telegram_storage_chat_id
    if not _CHANNEL_ID:
        _RESOLVED = True
        return _CHANNEL_ID, "unknown"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getChat",
                json={"chat_id": _CHANNEL_ID},
            )
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                _CHANNEL_NAME = (
                    data.get("title")
                    or data.get("username")
                    or str(data.get("id", "unknown"))
                )
    except Exception as e:
        logger.warning("Failed to resolve channel name: %s", e)
        _CHANNEL_NAME = "unknown"

    _RESOLVED = True
    return _CHANNEL_ID, _CHANNEL_NAME


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


async def log_operation(
    action: str,
    log: str,
    status: str = "info",
    extra_chat_ids: list[str] | None = None,
) -> None:
    """Send a structured log entry to the system channel(s) plus any extra ones.

    Format:
        #Main_Channel_name: <channel name>
        #Main_id: <storage chat id>
        #action: <operation name>
        #log: <log details>
    """
    channel_id, channel_name = await _resolve_channel()
    targets: list[str] = []
    if channel_id:
        targets.append(channel_id)
    for cid in extra_chat_ids or []:
        if cid and cid != channel_id:
            targets.append(cid)
    if not targets:
        logger.debug("No storage chat ID configured, skipping log")
        return

    status_icon = {
        "info": "ℹ️",
        "success": "✅",
        "fail": "❌",
        "warn": "⚠️",
    }.get(status, "ℹ️")

    msg = (
        f"#Main_Channel_name: {channel_name}\n"
        f"#Main_id: {channel_id}\n"
        f"#action: {action}\n"
        f"#log: {status_icon} {log}\n"
        f"🕐 {_now()}"
    )

    try:
        tg = TelegramClient(
            bot_token=settings.telegram_bot_token,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
    except Exception as e:
        logger.error("Failed to init TelegramClient for logging: %s", e)
        return

    for cid in targets:
        try:
            result = await tg.send_message(cid, msg)
            logger.info("Logged [%s] to channel %s: msg_id=%s", action, cid, result)
        except TelegramRateLimitError as e:
            # A 429 means the bot is globally throttled — honor the kill switch.
            maybe_halt_on_rate_limit(e, context=f"logging to channel {cid}")
            logger.error("Telegram log rate-limited [%s]: %s", action, e)
        except TelegramError as e:
            logger.error("Telegram log failed [%s]: %s", action, e)
        except Exception as e:
            logger.error("Log send failed [%s]: %s", action, e)
