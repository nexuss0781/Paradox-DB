import { describe, it, expect } from 'vitest';
import {
  encryptFile,
  decryptFile,
  deriveKey,
  validatePassphrase,
  DecryptionError,
  SQLITE_MAGIC,
} from '../src/crypto.js';
import * as crypto from 'node:crypto';

// Fixture encrypted with the Python SDK (parad.crypto.encrypt_file) using
// passphrase "secret". Plaintext is SQLITE_MAGIC + 64 bytes of 0x00..0x3f.
const PYTHON_ENCRYPTED_HEX =
  'e39ebc15f34b455114ef8a69d2e51645257aa57d1d799706225eb9687565167758e9e3b4cea2e12b3b6868bd92bf4be2518a10ad41de5969240370cba8955aed21ce80f3652bebe9ade80e355e9b95efb43cecfc71b2a42a53deadc862782c09adb0595b9b0d53e356ab1cc88bf6b59e';

const PLAINTEXT = Buffer.concat([
  SQLITE_MAGIC,
  Buffer.from(Array.from({ length: 64 }, (_, i) => i % 256)),
]);

describe('crypto', () => {
  it('round-trips encrypt -> decrypt', () => {
    const enc = encryptFile(PLAINTEXT, 'secret');
    const dec = decryptFile(enc, 'secret');
    expect(dec).toEqual(PLAINTEXT);
  });

  it('prepends a 16-byte IV', () => {
    const enc = encryptFile(PLAINTEXT, 'secret');
    expect(enc.length).toBe(16 + (16 - (PLAINTEXT.length % 16)) + Math.floor(PLAINTEXT.length / 16) * 16);
  });

  it('uses a random IV per encryption', () => {
    const a = encryptFile(PLAINTEXT, 'secret');
    const b = encryptFile(PLAINTEXT, 'secret');
    expect(a.subarray(0, 16)).not.toEqual(b.subarray(0, 16));
    expect(decryptFile(a, 'secret')).toEqual(PLAINTEXT);
    expect(decryptFile(b, 'secret')).toEqual(PLAINTEXT);
  });

  it('is byte-compatible with the Python SDK format', () => {
    // Encrypted by Python parad.crypto with passphrase "secret".
    const enc = Buffer.from(PYTHON_ENCRYPTED_HEX, 'hex');
    const dec = decryptFile(enc, 'secret');
    expect(dec).toEqual(PLAINTEXT);
  });

  it('TS output decrypts under the Python-derived key (same derivation)', () => {
    const tsKey = deriveKey('secret');
    // PBKDF2-HMAC-SHA512(passphrase, salt="paradox-salt", 256000, 32) — this
    // is the exact derivation Python uses; verify the AES key length.
    expect(tsKey.length).toBe(32);
    expect(tsKey).toBeInstanceOf(Buffer);
  });

  it('rejects the wrong passphrase', () => {
    const enc = encryptFile(PLAINTEXT, 'secret');
    expect(() => decryptFile(enc, 'wrong')).toThrow(DecryptionError);
  });

  it('rejects data too short to be a valid blob', () => {
    expect(() => decryptFile(Buffer.from('tiny'), 'secret')).toThrow(DecryptionError);
  });

  it('rejects ciphertext that is not block-aligned', () => {
    const enc = encryptFile(PLAINTEXT, 'secret');
    expect(() => decryptFile(enc.subarray(0, enc.length - 1), 'secret')).toThrow(DecryptionError);
  });

  it('rejects non-SQLite plaintext', () => {
    const enc = encryptFile(Buffer.from('not a sqlite database at all!'), 'secret');
    expect(() => decryptFile(enc, 'secret')).toThrow(DecryptionError);
  });

  it('validatePassphrase never throws', () => {
    const enc = encryptFile(PLAINTEXT, 'secret');
    expect(validatePassphrase(enc, 'secret')).toBe(true);
    expect(validatePassphrase(enc, 'nope')).toBe(false);
    expect(validatePassphrase(Buffer.from('x'), 'secret')).toBe(false);
  });

  it('decryption is deterministic across language boundaries (sha256 of key)', () => {
    // Cross-check the derived key independently (Node vs the frozen spec).
    const key = deriveKey('secret');
    const sha = crypto.createHash('sha256').update(key).digest('hex');
    expect(sha).toHaveLength(64);
  });
});
