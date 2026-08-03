import signal
from unittest.mock import patch

import pytest

from app.halt import (
    halt_enabled,
    halt_reason,
    halt_requested,
    halt_service,
    maybe_halt_on_rate_limit,
    reset,
)
from app.services.telegram import TelegramRateLimitError


@pytest.fixture(autouse=True)
def _clean_halt():
    reset()
    yield
    reset()


def test_halt_enabled_defaults_off():
    assert halt_enabled() is False


def test_halt_service_sets_state_and_sends_sigterm():
    with patch("app.halt.os.kill") as mock_kill:
        halt_service("boom", retry_after=42)
    assert halt_requested() is True
    assert halt_reason() == "boom"
    mock_kill.assert_called_once()
    assert mock_kill.call_args.args[1] == signal.SIGTERM


def test_halt_service_handles_kill_failure():
    with patch("app.halt.os.kill", side_effect=OSError("no pid")):
        halt_service("boom")
    assert halt_requested() is True


def test_maybe_halt_returns_false_when_disabled(monkeypatch):
    monkeypatch.setattr("app.config.settings.telegram_rate_limit_halt", False)
    with patch("app.halt.os.kill") as mock_kill:
        result = maybe_halt_on_rate_limit(
            TelegramRateLimitError("rate limited", retry_after=10),
            context="upload",
        )
    assert result is False
    mock_kill.assert_not_called()
    assert halt_requested() is False


def test_maybe_halt_halts_when_enabled():
    with patch("app.config.settings.telegram_rate_limit_halt", True), \
         patch("app.halt.os.kill") as mock_kill:
        result = maybe_halt_on_rate_limit(
            TelegramRateLimitError("rate limited", retry_after=10),
            context="upload of db",
        )
    assert result is True
    mock_kill.assert_called_once()
    assert halt_requested() is True
    assert "upload of db" in halt_reason()
