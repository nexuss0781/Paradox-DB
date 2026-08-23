"""parad configuration management."""

import json
import os
from pathlib import Path
from parad.types import Config


CONFIG_DIR = Path(os.environ.get("PARADOX_HOME", "~/.paradox")).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.json"


def config_file() -> Path:
    """Return the current config path, honoring runtime PARADOX_HOME changes."""
    return config_dir() / "config.json"


def config_dir() -> Path:
    """Return the current config directory.

    Read at call time (not import time) so modules that bind it by value
    still honour a runtime ``PARADOX_HOME`` change (tests, containers).
    """
    return Path(os.environ.get("PARADOX_HOME", "~/.paradox")).expanduser()

DEFAULT_CONFIG = {
    "database_url": "",
    "database_path": "~/.paradox/data.db",
    "encryption": {
        "cipher": "aes-256-cbc",
        "kdf_iterations": 256000,
        "page_size": 4096,
        "passphrase": "",
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


def _load_dotenv():
    """Load .env file from current directory or home directory if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass


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
    """Load config from ~/.paradox/config.json, merged with defaults.

    Environment variables can override config values:
    - PARADOX_PASSPHRASE → encryption.passphrase
    - PARADOX_GATEWAY    → sync.gateway_url
    - PARADOX_DATABASE   → database_path
    - PARADOX_API_KEY    → sync.api_key
    - DATABASE_URL       → canonical Parad connection URL

    .env files are loaded automatically if python-dotenv is installed.
    """
    _load_dotenv()

    user_config = {}
    config_path = config_file()
    if config_path.exists():
        try:
            user_config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    merged = _deep_merge(DEFAULT_CONFIG, user_config)

    # Check environment variables (env vars always win over file config)
    if "PARADOX_PASSPHRASE" in os.environ:
        merged["encryption"]["passphrase"] = os.environ["PARADOX_PASSPHRASE"]
    if "PARADOX_GATEWAY" in os.environ:
        merged["sync"]["gateway_url"] = os.environ["PARADOX_GATEWAY"]
    if "PARADOX_DATABASE" in os.environ:
        merged["database_path"] = os.environ["PARADOX_DATABASE"]
    if "PARADOX_API_KEY" in os.environ:
        merged["sync"]["api_key"] = os.environ["PARADOX_API_KEY"]
    if "DATABASE_URL" in os.environ:
        merged["database_url"] = os.environ["DATABASE_URL"]

    return Config(**merged)


def save_config(config: Config):
    """Save config to ~/.paradox/config.json."""
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(), indent=2))


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


def get_passphrase() -> str:
    """Get the encryption passphrase from config."""
    config = load_config()
    return config.encryption.passphrase


def get_canonical_database_url(name: str | None = None) -> str:
    """Return the canonical single-value database URL.

    Precedence is ``DATABASE_URL`` environment variable, then the persisted
    ``database_url`` config field, then legacy split fields. When the legacy
    fields are sufficient, the reconstructed URL is persisted immediately so
    later processes can use the canonical value only.
    """
    config = load_config()
    configured = os.environ.get("DATABASE_URL", "").strip() or config.database_url.strip()
    if configured:
        from parad.connection import parse_url

        parsed = parse_url(configured)
        if name and parsed["name"] != name:
            raise ValueError(
                f"Canonical DATABASE_URL points to '{parsed['name']}', not '{name}'"
            )
        return configured

    inferred_name = name or gateway_db_name(config.database_path)
    if not inferred_name:
        raise ValueError("No database name is configured; run parad init <name> first")

    passphrase = os.environ.get("PARADOX_PASSPHRASE", "").strip() or config.encryption.passphrase.strip()
    gateway_url = os.environ.get("PARADOX_GATEWAY", "").strip() or config.sync.gateway_url.strip()
    api_key = os.environ.get("PARADOX_API_KEY", "").strip() or config.sync.api_key.strip()
    if gateway_url and not passphrase:
        raise ValueError(
            f"No passphrase is configured for '{inferred_name}'. Set PARADOX_PASSPHRASE "
            "or recover DATABASE_URL from the original provisioning output."
        )

    from parad.connection import generate_url

    canonical = generate_url(
        inferred_name,
        passphrase,
        gateway_url,
        config.project_name or None,
        api_key,
    )
    config.database_url = canonical
    save_config(config)
    return canonical


def get_connection_url(name: str) -> str:
    """Backward-compatible alias for :func:`get_canonical_database_url`."""
    return get_canonical_database_url(name)
