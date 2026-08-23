"""parad — Encrypted local-first SQLite with cloud sync."""

from parad.connection import connect, ParadConnection, parse_url, generate_url, redact_url, db_state_key, generate_passphrase
from parad.engine import Engine
from parad.config import load_config, get_passphrase, get_canonical_database_url, recover_canonical_database_url, register_canonical_database_url, get_connection_url

__version__ = "2.2.5"
__all__ = ["connect", "ParadConnection", "parse_url", "generate_url", "redact_url", "db_state_key", "generate_passphrase", "Engine", "load_config", "get_canonical_database_url", "recover_canonical_database_url", "register_canonical_database_url", "get_connection_url"]
