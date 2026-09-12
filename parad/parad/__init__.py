"""parad — Encrypted local-first SQLite with cloud sync."""

from parad.connection import connect, ParadConnection, parse_url, generate_url, db_state_key
from parad.engine import Engine
from parad.config import load_config, get_passphrase, get_connection_url
from parad import dbapi

__version__ = "2.3.0"

__all__ = [
    "connect",
    "ParadConnection",
    "parse_url",
    "generate_url",
    "db_state_key",
    "Engine",
    "load_config",
    "dbapi",
    "create_engine",
]


def create_engine(url: str, **kwargs):
    """Create a SQLAlchemy engine backed by the gateway SQL sessions.

    ``url`` is a ``parad://`` connection string (see :mod:`parad.dbapi`).
    """
    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.pool import SingletonThreadPool

    kwargs.setdefault("poolclass", SingletonThreadPool)
    return _create_engine(url, **kwargs)


# Register the dialect in-process so ``create_engine("parad://…")`` works
# even when the package is installed without the entry point being picked
# up (e.g. editable installs).  The entry point is authoritative.
try:
    from sqlalchemy.dialects import registry

    registry.register("parad", "parad.dialect", "ParadDialect")
    registry.register("parad.remote", "parad.dialect", "ParadDialect")
except ImportError:  # sqlalchemy not installed
    pass
