"""Secure storage and presentation helpers for canonical database URLs."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    configured = settings.database_url_encryption_key.strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("DATABASE_URL_ENCRYPTION_KEY must be a valid Fernet key") from exc

    # Stable fallback keeps upgrades compatible when JWT_SECRET is configured,
    # but never allow the development placeholder to protect production data.
    if not settings.jwt_secret.strip() or settings.jwt_secret == "change-me-in-production":
        raise RuntimeError(
            "Set DATABASE_URL_ENCRYPTION_KEY (recommended) or a strong JWT_SECRET "
            "before storing database_url"
        )
    seed = settings.jwt_secret.encode("utf-8")
    derived = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(derived)


def validate_database_url(value: str) -> str:
    """Validate and normalize a canonical Parad URL before storage."""
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"parad", "paradox"}:
        raise ValueError("database_url must use the parad:// or paradox:// scheme")
    if not parsed.path.strip("/"):
        raise ValueError("database_url must include a database path")
    return value


def encrypt_database_url(value: str) -> str:
    normalized = validate_database_url(value)
    return _fernet().encrypt(normalized.encode("utf-8")).decode("ascii")


def decrypt_database_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            "Stored database_url cannot be decrypted with the current server key"
        ) from exc


def redact_database_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("passphrase", "token", "api_key", "apikey", "password", "secret", "key"):
        query.pop(key, None)
    userinfo = "<redacted>@" if parsed.username else ""
    host = parsed.hostname or "local"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    safe_netloc = f"{userinfo}{host}"
    safe_query = urlencode(query, doseq=True)
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, safe_query, ""))
