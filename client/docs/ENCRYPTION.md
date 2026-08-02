# Encryption

Every `parad` database file is a single **AES-256-CBC** ciphertext blob. The
plaintext SQLite database only ever exists decrypted in process memory (an
in-memory `sql.js` database) — it is never written to disk in plaintext.

The scheme is **byte-compatible with the Python `parad` SDK**, so a database
created by either SDK can be opened by the other.

## Parameters

| Parameter | Value |
| --- | --- |
| Cipher | AES-256-CBC |
| Key derivation | PBKDF2-HMAC-SHA512 |
| Salt | `paradox-salt` (UTF-8, fixed) |
| Iterations | `256 000` |
| Key length | 32 bytes (256 bits) |
| IV | 16 random bytes, generated per encryption |
| Padding | PKCS7 (16-byte block) |

## File format

```
+-------------------------------------------+
|  IV  (16 bytes)                            |
+-------------------------------------------+
|  ciphertext = AES-256-CBC(padded plaintext)|
|  (always a multiple of 16 bytes)           |
+-------------------------------------------+
```

## Key derivation

```ts
key = PBKDF2(password, salt = "paradox-salt", iterations = 256000, dklen = 32, hash = "sha512")
```

Derivation is deliberately slow (256k HMAC-SHA512 iterations). To avoid paying
that cost on every open/close, derived keys are memoized **in memory** for the
process lifetime (keyed by passphrase). The cache is never persisted.

## Encryption details

- The plaintext is PKCS7-padded manually to a 16-byte boundary.
- Padding is applied **before** encryption and the cipher runs with
  `autoPadding` disabled, so the ciphertext is exactly
  `16 + ceil(len/16)*16` bytes — never more.
- The IV is prepended to the ciphertext, making each encryption of the same
  database produce different bytes (no IV reuse).

```ts
import { encryptFile, decryptFile, validatePassphrase, deriveKey } from 'parad';

const ciphertext = encryptFile(plaintextBytes, 'passphrase');
const roundtrip  = decryptFile(ciphertext, 'passphrase'); // === plaintextBytes

const ok = validatePassphrase(ciphertext, 'passphrase');  // true
```

## Decryption validation

`decryptFile` is strict. It throws `DecryptionError` when:

- the input is shorter than `IV + one AES block` (32 bytes);
- the ciphertext is not block-aligned;
- the PKCS7 padding byte is out of range (1..16) or the padding bytes don't
  match;
- the decrypted payload doesn't start with the SQLite magic header
  (`SQLite format 3\0`).

That last check is what catches a **wrong passphrase or a corrupt/foreign file**:
A wrong key produces garbage after unpadding, and garbage almost never begins
with the SQLite header.

## Engine lifecycle

`ClientEngine` wraps this end-to-end:

1. `open()` — read `dbPath`, `decryptFile`, load the plaintext bytes into an
   in-memory **`sql.js`** database (`SQLite compiled to WASM`).
2. All SQL runs against the in-memory database; nothing is ever written to disk
   in plaintext.
3. `close()` — `db.export()` the bytes, `encryptFile`, write the ciphertext
   back to `dbPath`.

This means a crash before `close()` loses only un-encrypted changes (they were
only in memory), never corrupts the on-disk ciphertext, and the encrypted file
on disk is always a complete, valid database snapshot.

## Security notes

- **Passphrases stay client-side.** Only ciphertext is ever sent to the
  gateway.
- The default passphrase is the string `default` when none is configured — set
  `PARADOX_PASSPHRASE`, a URL `?passphrase=`, or `options.passphrase` for real
  deployments.
- The PBKDF2 salt is public and fixed by design so both SDKs interoperate;
  security rests on the passphrase entropy and iteration count, not the salt.
- Memoized keys live only in-process; they do not touch disk.
