"""AES-256-CBC file encryption for parad local database."""

import os
import threading
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SALT = b"paradox-salt"
KDF_ITERATIONS = 256_000
KEY_LENGTH = 32  # 256 bits
IV_LENGTH = 16  # 128 bits

# Every SQLite database starts with this 16-byte magic header.
SQLITE_MAGIC = b"SQLite format 3\x00"

# Every ciphertext block is 16 bytes; the IV is 16 bytes, so a valid
# encrypted blob is always IV + at least one AES block.
MIN_ENCRYPTED_LENGTH = IV_LENGTH + 16


class DecryptionError(ValueError):
    """Raised when decryption fails.

    Covers wrong passphrase, truncated/corrupt data, invalid PKCS7
    padding, and payloads that are not SQLite databases.  Subclasses
    ValueError so existing ``except ValueError`` handlers keep working.
    """

    DEFAULT_MESSAGE = "Invalid passphrase or corrupt database file"


# Memoized derived keys.  PBKDF2 is deterministic and deliberately slow
# (256k HMAC-SHA512 iterations), and the engine re-encrypts on every
# close and re-decrypts on every open — recomputing the key each time is
# wasteful.  The cache is in-memory only and never persisted; the derived
# bytes are identical, so the on-disk format is unchanged.
_KEY_CACHE: dict[str, bytes] = {}
_KEY_CACHE_LOCK = threading.Lock()


def derive_key(passphrase: str) -> bytes:
    """Derive a 256-bit key from passphrase using PBKDF2-HMAC-SHA512."""
    cached = _KEY_CACHE.get(passphrase)
    if cached is not None:
        return cached
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=KEY_LENGTH,
        salt=SALT,
        iterations=KDF_ITERATIONS,
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    with _KEY_CACHE_LOCK:
        _KEY_CACHE[passphrase] = key
    return key


def encrypt_file(data: bytes, passphrase: str) -> bytes:
    """Encrypt bytes with AES-256-CBC. Returns IV + ciphertext."""
    key = derive_key(passphrase)
    iv = os.urandom(IV_LENGTH)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    # PKCS7 padding
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len] * pad_len)
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return iv + ciphertext


def decrypt_file(data: bytes, passphrase: str) -> bytes:
    """Decrypt AES-256-CBC data (IV + ciphertext). Returns original bytes.

    Raises :class:`DecryptionError` when the data cannot be decrypted:
    wrong passphrase, truncated/corrupt input, invalid PKCS7 padding, or
    a payload that does not start with the SQLite magic header.
    """
    key = derive_key(passphrase)
    if len(data) < MIN_ENCRYPTED_LENGTH:
        raise DecryptionError(
            f"{DecryptionError.DEFAULT_MESSAGE}: data too short "
            f"({len(data)} bytes, need at least {MIN_ENCRYPTED_LENGTH})"
        )
    iv = data[:IV_LENGTH]
    ciphertext = data[IV_LENGTH:]
    if len(ciphertext) % 16 != 0:
        raise DecryptionError(
            f"{DecryptionError.DEFAULT_MESSAGE}: ciphertext is not "
            "block-aligned"
        )
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    # Strict PKCS7 validation: pad_len must be 1..16 and every trailing
    # byte must equal pad_len.
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16:
        raise DecryptionError(
            f"{DecryptionError.DEFAULT_MESSAGE}: invalid PKCS7 padding "
            f"(pad byte {pad_len})"
        )
    if padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise DecryptionError(
            f"{DecryptionError.DEFAULT_MESSAGE}: invalid PKCS7 padding"
        )
    plaintext = padded[:-pad_len]
    if not plaintext.startswith(SQLITE_MAGIC):
        raise DecryptionError(
            f"{DecryptionError.DEFAULT_MESSAGE}: not a SQLite database"
        )
    return plaintext


def validate_passphrase(data: bytes, passphrase: str) -> bool:
    """Return True if *data* decrypts with *passphrase*, False otherwise.

    Never raises — useful for a ``status``/``ping`` style command that
    just wants a yes/no answer about whether a passphrase matches.
    """
    try:
        decrypt_file(data, passphrase)
        return True
    except (DecryptionError, ValueError, IndexError, OSError):
        return False
