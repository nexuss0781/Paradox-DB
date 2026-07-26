"""parad — Encrypted local-first SQLite with cloud sync."""

from parad.connection import connect, ParadConnection, parse_url, generate_url
from parad.engine import Engine
from parad.config import load_config, get_passphrase, get_connection_url

__version__ = "0.4.0"
__all__ = ["connect", "ParadConnection", "parse_url", "generate_url", "Engine", "load_config"]
