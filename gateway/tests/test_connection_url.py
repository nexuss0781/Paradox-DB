import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.connection_url import (
    decrypt_database_url,
    encrypt_database_url,
    redact_database_url,
    validate_database_url,
)


def test_validate_and_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "database_url_encryption_key", Fernet.generate_key().decode())
    url = "parad://token@local/project/mydb?passphrase=secret&gateway=https://g/v1"
    encrypted = encrypt_database_url(url)
    assert encrypted != url
    assert decrypt_database_url(encrypted) == url


def test_redact_removes_secret_query_and_userinfo():
    url = "parad://token@local/project/mydb?passphrase=secret&gateway=https://g/v1"
    redacted = redact_database_url(url)
    assert redacted == "parad://<redacted>@local/project/mydb?gateway=https%3A%2F%2Fg%2Fv1"
    assert "token" not in redacted
    assert "secret" not in redacted


def test_invalid_scheme_rejected():
    with pytest.raises(ValueError, match="parad:// or paradox://"):
        validate_database_url("https://example.com/db")


def test_wrong_key_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "database_url_encryption_key", Fernet.generate_key().decode())
    encrypted = encrypt_database_url("parad://local/db?passphrase=secret")
    monkeypatch.setattr(settings, "database_url_encryption_key", Fernet.generate_key().decode())
    with pytest.raises(ValueError, match="cannot be decrypted"):
        decrypt_database_url(encrypted)
