"""parad configuration management."""

import json
from pathlib import Path
from parad.types import Config


CONFIG_DIR = Path.home() / ".paradox"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "database_path": "~/.paradox/data.db",
    "encryption": {
        "cipher": "aes-256-cbc",
        "kdf_iterations": 256000,
        "page_size": 4096,
    },
    "sync": {
        "gateway_url": "https://paradox-db.onrender.com/v1",
        "api_key": "",
        "trigger_timer_seconds": 30,
        "trigger_ops_threshold": 50,
        "max_file_size_mb": 50,
        "auto_sync_on_shutdown": True,
    },
    "conflict": {
        "strategy": "last-write-wins",
        "log_conflicts": True,
    },
    "logging": {
        "level": "info",
        "path": "~/.paradox/logs",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> Config:
    """Load config from ~/.paradox/config.json, merged with defaults."""
    user_config = {}
    if CONFIG_FILE.exists():
        try:
            user_config = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    merged = _deep_merge(DEFAULT_CONFIG, user_config)
    return Config(**merged)


def save_config(config: Config):
    """Save config to ~/.paradox/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config.model_dump(), indent=2))


def set_config_value(key: str, value: str):
    """Set a single config value using dot notation (e.g. sync.api_key)."""
    config = load_config()
    keys = key.split(".")
    d = config.model_dump()
    current = d
    for k in keys[:-1]:
        current = current[k]
    # Try to parse as appropriate type
    if value.lower() in ("true", "false"):
        current[keys[-1]] = value.lower() == "true"
    elif value.isdigit():
        current[keys[-1]] = int(value)
    else:
        current[keys[-1]] = value
    save_config(Config(**d))


def gateway_db_name(db_path) -> str:
    """Strip .db suffix from a database filename to get the gateway name.
    
    Local file: ~/.paradox/main.db → gateway name: main
    """
    from pathlib import Path
    name = Path(db_path).name
    if name.endswith(".db"):
        name = name[:-3]
    return name
