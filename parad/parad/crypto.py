"""AES-256-CBC file encryption for parad local database."""

import os
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SALT = b"paradox-salt"
KDF_ITERATIONS = 256_000
KEY_LENGTH = 32  # 256 bits
IV_LENGTH = 16  # 128 bits


def derive_key(passphrase: str) -> bytes:
    """Derive a 256-bit key from passphrase using PBKDF2-HMAC-SHA512."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=KEY_LENGTH,
        salt=SALT,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


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
    """Decrypt AES-256-CBC data (IV + ciphertext). Returns original bytes."""
    key = derive_key(passphrase)
    iv = data[:IV_LENGTH]
    ciphertext = data[IV_LENGTH:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    # Remove PKCS7 padding
    pad_len = padded[-1]
    return padded[:-pad_len]
