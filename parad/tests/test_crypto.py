"""Hermetic tests for parad.crypto (no network)."""

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from parad.crypto import (
    MIN_ENCRYPTED_LENGTH,
    SALT,
    SQLITE_MAGIC,
    DecryptionError,
    derive_key,
    encrypt_file,
    decrypt_file,
    validate_passphrase,
)

SQLITE_PLAIN = SQLITE_MAGIC + b"\x00" * 512  # a plausible first page


def test_roundtrip_returns_identical_bytes():
    for plain in (SQLITE_PLAIN, b"SQLite format 3\x00x" * 3, SQLITE_MAGIC + b"A"):
        assert decrypt_file(encrypt_file(plain, "secret"), "secret") == plain


def test_format_is_iv_plus_cbc_ciphertext_with_pkcs7():
    plain = SQLITE_MAGIC + bytes(range(256)) * 2
    data = encrypt_file(plain, "secret")

    # iv(16) is prepended; the remainder is the ciphertext
    assert len(data) == 16 + len(plain) + (16 - len(plain) % 16)
    assert len(data) % 16 == 0
    iv, ct = data[:16], data[16:]

    # Independent AES-256-CBC decrypt must reveal PKCS7 padding
    key = derive_key("secret")
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    pad_len = padded[-1]
    assert 1 <= pad_len <= 16
    assert padded[-pad_len:] == bytes([pad_len]) * pad_len
    assert padded[:-pad_len] == plain


def test_encrypt_is_randomized_per_call():
    plain = SQLITE_PLAIN
    a, b = encrypt_file(plain, "secret"), encrypt_file(plain, "secret")
    assert a != b
    assert a[:16] != b[:16]  # fresh IV each time
    assert decrypt_file(a, "secret") == decrypt_file(b, "secret") == plain


def test_wrong_passphrase_raises_clear_error():
    data = encrypt_file(SQLITE_PLAIN, "right")
    with pytest.raises(DecryptionError) as excinfo:
        decrypt_file(data, "wrong")
    assert "Invalid passphrase or corrupt database file" in str(excinfo.value)


def test_wrong_passphrase_is_valueerror_subclass():
    # Backwards compatibility: existing `except ValueError` handlers work.
    with pytest.raises(ValueError):
        decrypt_file(encrypt_file(SQLITE_PLAIN, "a"), "b")


def test_truncated_data_raises():
    data = encrypt_file(SQLITE_PLAIN, "secret")
    for cut in (0, 1, 16, len(data) - 16, len(data) - 1):
        with pytest.raises(DecryptionError):
            decrypt_file(data[:cut], "secret")


def test_empty_data_raises():
    with pytest.raises(DecryptionError):
        decrypt_file(b"", "secret")


def test_garbage_data_raises():
    with pytest.raises(DecryptionError):
        decrypt_file(b"\x00" * MIN_ENCRYPTED_LENGTH, "secret")
    with pytest.raises(DecryptionError):
        decrypt_file(b"\xff" * 64, "secret")


def test_tampered_iv_raises():
    data = bytearray(encrypt_file(SQLITE_PLAIN, "secret"))
    data[0] ^= 0xFF
    with pytest.raises(DecryptionError):
        decrypt_file(bytes(data), "secret")


def test_validate_passphrase():
    data = encrypt_file(SQLITE_PLAIN, "secret")
    assert validate_passphrase(data, "secret") is True
    assert validate_passphrase(data, "wrong") is False
    assert validate_passphrase(b"", "secret") is False
    assert validate_passphrase(b"\x00\x00" * 32, "secret") is False


def test_derive_key_matches_spec():
    # PBKDF2-HMAC-SHA512, salt=b"paradox-salt", 256000 iters, 32 bytes.
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=32,
        salt=SALT,
        iterations=256000,
    )
    assert derive_key("secret") == kdf.derive(b"secret")
