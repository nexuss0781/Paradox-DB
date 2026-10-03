from unittest.mock import AsyncMock

import pytest

from app.services.telegram import (
    TelegramClient,
    TelegramConflictError,
    TelegramError,
    TelegramForbiddenError,
    TelegramMigratedError,
    TelegramNotFoundError,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramServerError,
    TelegramUnauthorizedError,
    raise_for_status,
)


class _FakeResponse:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (str(payload) if payload else "")

    def json(self):
        return self._payload


def test_200_does_not_raise():
    raise_for_status(_FakeResponse(200, {"ok": True}), "test")


@pytest.mark.parametrize(
    "code, payload, expected",
    [
        (
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 35",
                "parameters": {"retry_after": 35},
            },
            TelegramRateLimitError,
        ),
        (
            401,
            {"ok": False, "error_code": 401, "description": "Unauthorized"},
            TelegramUnauthorizedError,
        ),
        (
            400,
            {
                "ok": False,
                "error_code": 400,
                "description": "group chat was upgraded to a supergroup chat",
                "parameters": {"migrate_to_chat_id": 1000},
            },
            TelegramMigratedError,
        ),
        (
            403,
            {
                "ok": False,
                "error_code": 403,
                "description": "Forbidden: bot was blocked by the user",
            },
            TelegramForbiddenError,
        ),
        (
            404,
            {
                "ok": False,
                "error_code": 404,
                "description": "Bad Request: message to delete not found",
            },
            TelegramNotFoundError,
        ),
        (
            409,
            {
                "ok": False,
                "error_code": 409,
                "description": "Conflict: terminated by other getUpdates request",
            },
            TelegramConflictError,
        ),
        (
            500,
            {"ok": False, "error_code": 500, "description": "Internal Server Error"},
            TelegramServerError,
        ),
        (
            400,
            {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
            TelegramPermanentError,
        ),
    ],
)
def test_raise_for_status_classifies(code, payload, expected):
    with pytest.raises(expected) as exc_info:
        raise_for_status(_FakeResponse(code, payload), "test")
    assert isinstance(exc_info.value, TelegramError)


def test_rate_limit_carries_retry_after():
    with pytest.raises(TelegramRateLimitError) as exc_info:
        raise_for_status(
            _FakeResponse(
                429,
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 35",
                    "parameters": {"retry_after": 35},
                },
            ),
            "test",
        )
    assert exc_info.value.retry_after == 35


def test_rate_limit_defaults_retry_after_when_missing():
    with pytest.raises(TelegramRateLimitError) as exc_info:
        raise_for_status(
            _FakeResponse(429, {"ok": False, "error_code": 429, "description": "rate"}),
            "test",
        )
    assert exc_info.value.retry_after == 30


def test_migration_carries_migrate_to_chat_id():
    with pytest.raises(TelegramMigratedError) as exc_info:
        raise_for_status(
            _FakeResponse(
                400,
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "upgraded",
                    "parameters": {"migrate_to_chat_id": 987},
                },
            ),
            "test",
        )
    assert exc_info.value.migrate_to_chat_id == 987


def test_unauthorized_not_retryable_after_refactor():
    err = TelegramUnauthorizedError("Unauthorized")
    assert err.error_code == 401


def test_server_error_is_retryable_class():
    err = TelegramServerError("boom")
    assert err.error_code == 500


@pytest.mark.asyncio
async def test_download_best_prefers_stored_file_id():
    client = TelegramClient(bot_token="", api_id="", api_hash="")
    client.download_file_by_id = AsyncMock(return_value=b"file-id bytes")
    client.download_file = AsyncMock(return_value=b"message bytes")

    result = await client.download_best("channel", "123", "FILE_123")

    assert result == b"file-id bytes"
    client.download_file_by_id.assert_awaited_once_with("FILE_123")
    client.download_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_best_falls_back_when_file_id_is_stale():
    client = TelegramClient(bot_token="", api_id="", api_hash="")
    client.download_file_by_id = AsyncMock(
        side_effect=TelegramPermanentError("wrong file identifier")
    )
    client.download_file = AsyncMock(return_value=b"message bytes")

    result = await client.download_best("channel", "123", "STALE_FILE_ID")

    assert result == b"message bytes"
    client.download_file_by_id.assert_awaited_once_with("STALE_FILE_ID")
    client.download_file.assert_awaited_once_with(channel_id="channel", message_id="123")
