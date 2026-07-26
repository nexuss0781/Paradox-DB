"""Sync state tracker — stores version/hash info outside the encrypted DB.

State lives in ~/.paradox/<name>.sync.json so it survives pull operations
(which replace the entire DB file).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from parad.config import CONFIG_DIR


def _state_path(db_name: str) -> Path:
    return CONFIG_DIR / f"{db_name}.sync.json"


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
    }


def save_state(db_name: str, state: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
