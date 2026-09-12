from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def prepare_async_database_url(url: str) -> tuple[str, dict[str, Any]]:
    """Convert a Render PostgreSQL URL into an asyncpg-compatible SQLAlchemy URL."""
    parsed = urlparse(url)
    if parsed.scheme in {"postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg"}:
        parsed = parsed._replace(scheme="postgresql+asyncpg")
    elif parsed.scheme != "postgresql+asyncpg":
        raise ValueError("DATABASE_URL must use a PostgreSQL URL supported by asyncpg")

    params = parse_qs(parsed.query)
    sslmode = params.pop("sslmode", None)
    connect_args: dict[str, Any] = {}
    if sslmode:
        ssl_value = sslmode[0]
        if ssl_value == "require":
            connect_args["ssl"] = "require"
        elif ssl_value in {"prefer", "allow"}:
            connect_args["ssl"] = "prefer"
        elif ssl_value in {"disable", "none"}:
            connect_args["ssl"] = False
        else:
            connect_args["ssl"] = "require"

    return urlunparse(parsed._replace(query=urlencode(params, doseq=True))), connect_args
