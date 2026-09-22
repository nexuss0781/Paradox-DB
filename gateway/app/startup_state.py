"""Runtime startup state used by liveness and diagnostic endpoints."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_state: dict[str, object] = {
    "status": "starting",
    "phase": "initializing",
    "started_at": datetime.now(UTC).isoformat(),
}


def _redact(value: str) -> str:
    """Avoid returning passwords or connection-string credentials in diagnostics."""
    value = re.sub(r"(://[^:/@]+:)[^@]+(@)", r"\1[REDACTED]\2", value)
    value = re.sub(r"(?i)(password|passwd|pwd)=([^&\s]+)", r"\1=[REDACTED]", value)
    return value[:500]


def mark_ready() -> None:
    _state.clear()
    _state.update(
        status="ready",
        phase="database_initialized",
        ready_at=datetime.now(UTC).isoformat(),
    )


def mark_degraded(phase: str, exc: BaseException) -> None:
    _state.clear()
    _state.update(
        status="degraded",
        phase=phase,
        error_type=type(exc).__name__,
        error=_redact(str(exc)) or "no error message",
        failed_at=datetime.now(UTC).isoformat(),
    )


def snapshot() -> dict[str, object]:
    return dict(_state)
