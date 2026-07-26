"""Optional encryption layer for gateway storage.

When enabled, clients encrypt data before upload and decrypt after download.
The gateway stores encrypted bytes — it never sees plaintext.
"""

import os
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SALT = b"paradox-gateway-salt"
KDF_ITERATIONS = 100_000
KEY_LENGTH = 32
IV_LENGTH = 16


def derive_key(passphrase: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA512(),
        length=KEY_LENGTH,
        salt=SALT,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_data(data: bytes, passphrase: str) -> bytes:
    """Encrypt bytes with AES-256-CBC. Returns IV + ciphertext."""
    key = derive_key(passphrase)
    iv = os.urandom(IV_LENGTH)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len] * pad_len)
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return iv + ciphertext


def decrypt_data(data: bytes, passphrase: str) -> bytes:
    """Decrypt AES-256-CBC data (IV + ciphertext)."""
    key = derive_key(passphrase)
    iv = data[:IV_LENGTH]
    ciphertext = data[IV_LENGTH:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad_len = padded[-1]
    return padded[:-pad_len]
