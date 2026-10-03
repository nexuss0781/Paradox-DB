"""Runtime startup state used by liveness and diagnostic endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from app.startup_diagnostics import redact_diagnostic

_state: dict[str, object] = {
    "status": "starting",
    "phase": "initializing",
    "started_at": datetime.now(UTC).isoformat(),
}


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
        error=redact_diagnostic(str(exc)) or "no error message",
        failed_at=datetime.now(UTC).isoformat(),
    )


def snapshot() -> dict[str, object]:
    return dict(_state)
