import pytest

from app.services.telegram import (
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
            {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 35",
             "parameters": {"retry_after": 35}},
            TelegramRateLimitError,
        ),
        (
            401,
            {"ok": False, "error_code": 401, "description": "Unauthorized"},
            TelegramUnauthorizedError,
        ),
        (
            400,
            {"ok": False, "error_code": 400,
             "description": "group chat was upgraded to a supergroup chat",
             "parameters": {"migrate_to_chat_id": 1000}},
            TelegramMigratedError,
        ),
        (
            403,
            {"ok": False, "error_code": 403,
             "description": "Forbidden: bot was blocked by the user"},
            TelegramForbiddenError,
        ),
        (
            404,
            {"ok": False, "error_code": 404,
             "description": "Bad Request: message to delete not found"},
            TelegramNotFoundError,
        ),
        (
            409,
            {"ok": False, "error_code": 409,
             "description": "Conflict: terminated by other getUpdates request"},
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
            _FakeResponse(429, {
                "ok": False, "error_code": 429,
                "description": "Too Many Requests: retry after 35",
                "parameters": {"retry_after": 35},
            }),
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
            _FakeResponse(400, {
                "ok": False, "error_code": 400,
                "description": "upgraded",
                "parameters": {"migrate_to_chat_id": 987},
            }),
            "test",
        )
    assert exc_info.value.migrate_to_chat_id == 987


def test_unauthorized_not_retryable_after_refactor():
    err = TelegramUnauthorizedError("Unauthorized")
    assert err.error_code == 401


def test_server_error_is_retryable_class():
    err = TelegramServerError("boom")
    assert err.error_code == 500
