"""Sync state tracker — stores version/hash info outside the encrypted DB.

State lives in PARADOX_HOME/<name>.sync.json so it survives pull operations
(which replace the entire DB file).
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import parad.config as _config

# Characters that are unsafe in a filename segment.  Multi-part keys like
# "myproject/mydb" are flattened to "myproject__mydb".
_UNSAFE_STATE_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def sanitize_state_key(db_key: str) -> str:
    """Convert a possibly multi-part db key into a safe filename segment.

    ``"myproject/mydb"`` -> ``"myproject__mydb"``; already-safe keys like
    ``"myproject__mydb"`` or ``"main"`` pass through unchanged.
    """
    key = _UNSAFE_STATE_CHARS.sub("__", str(db_key)).strip().strip(".")
    if not key or not any(c.isalnum() for c in key):
        raise ValueError(f"db_key must not be empty after sanitizing: {db_key!r}")
    return key


def _state_path(db_name: str) -> Path:
    # Read CONFIG_DIR from the config module at call time so a runtime
    # PARADOX_HOME override (test suites, embedded use) is always honored.
    return _config.config_dir() / f"{sanitize_state_key(db_name)}.sync.json"


def load_state(db_name: str) -> dict:
    path = _state_path(db_name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "database_name": db_name,
        "remote_version": None,
        "remote_hash": None,
        "last_sync": None,
        "last_local_hash": None,
        "dirty": False,
        "offline": False,
    }


def save_state(db_name: str, state: dict):
    _config.config_dir().mkdir(parents=True, exist_ok=True)
    _state_path(db_name).write_text(json.dumps(state, indent=2))


def get_remote_version(db_name: str) -> int | None:
    s = load_state(db_name)
    v = s.get("remote_version")
    return int(v) if v is not None else None


def set_remote_version(db_name: str, version: int, file_hash: str = ""):
    s = load_state(db_name)
    s["remote_version"] = version
    s["remote_hash"] = file_hash
    s["last_sync"] = datetime.now(timezone.utc).isoformat()
    save_state(db_name, s)


def get_last_local_hash(db_name: str) -> str | None:
    return load_state(db_name).get("last_local_hash")


def set_last_local_hash(db_name: str, file_hash: str):
    s = load_state(db_name)
    s["last_local_hash"] = file_hash
    save_state(db_name, s)


# ── dirty flag (un-pushed local changes) ────────────────────────────


def mark_dirty(db_key: str):
    s = load_state(db_key)
    s["dirty"] = True
    save_state(db_key, s)


def clear_dirty(db_key: str):
    s = load_state(db_key)
    s["dirty"] = False
    save_state(db_key, s)


def is_dirty(db_key: str) -> bool:
    return bool(load_state(db_key).get("dirty", False))


# ── offline flag (offline → batch push on reconnect) ───────────────


def set_offline(db_key: str, offline: bool):
    s = load_state(db_key)
    s["offline"] = bool(offline)
    save_state(db_key, s)


def is_offline(db_key: str) -> bool:
    return bool(load_state(db_key).get("offline", False))


# ── one-stop status read ────────────────────────────────────────────


def get_sync_status(db_key: str) -> dict:
    """Return the full sync status for a db key as a single dict.

    Keys: ``database_name``, ``remote_version``, ``remote_hash``,
    ``last_sync``, ``last_local_hash``, ``dirty``, ``offline``.
    """
    s = load_state(db_key)
    return {
        "database_name": s.get("database_name", db_key),
        "remote_version": s.get("remote_version"),
        "remote_hash": s.get("remote_hash"),
        "last_sync": s.get("last_sync"),
        "last_local_hash": s.get("last_local_hash"),
        "dirty": bool(s.get("dirty", False)),
        "offline": bool(s.get("offline", False)),
    }
