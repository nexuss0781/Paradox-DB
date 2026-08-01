import { createCipheriv, createDecipheriv, pbkdf2Sync, randomBytes } from 'node:crypto';
import { DecryptionError } from './errors.js';

export { DecryptionError } from './errors.js';

export const SALT = Buffer.from('paradox-salt', 'utf-8');
export const KDF_ITERATIONS = 256_000;
export const KEY_LENGTH = 32; // 256 bits
export const IV_LENGTH = 16; // 128 bits

// Every SQLite database starts with this 16-byte magic header.
export const SQLITE_MAGIC = Buffer.from('SQLite format 3\x00', 'utf-8');

// Every ciphertext block is 16 bytes; the IV is 16 bytes, so a valid
// encrypted blob is always IV + at least one AES block.
export const MIN_ENCRYPTED_LENGTH = IV_LENGTH + 16;

// Memoized derived keys. PBKDF2 is deterministic and deliberately slow
// (256k HMAC-SHA512 iterations), and the engine re-encrypts on every
// close and re-decrypts on every open. The cache is in-memory only.
const keyCache = new Map<string, Buffer>();

export function deriveKey(passphrase: string): Buffer {
  const cached = keyCache.get(passphrase);
  if (cached) return cached;
  const key = pbkdf2Sync(passphrase, SALT, KDF_ITERATIONS, KEY_LENGTH, 'sha512');
  keyCache.set(passphrase, key);
  return key;
}

/** Encrypt bytes with AES-256-CBC. Returns IV + ciphertext. */
export function encryptFile(data: Buffer, passphrase: string): Buffer {
  const key = deriveKey(passphrase);
  const iv = randomBytes(IV_LENGTH);
  const padLen = 16 - (data.length % 16);
  const padded = Buffer.concat([data, Buffer.alloc(padLen, padLen)]);
  const cipher = createCipheriv('aes-256-cbc', key, iv).setAutoPadding(false);
  const ciphertext = Buffer.concat([cipher.update(padded), cipher.final()]);
  return Buffer.concat([iv, ciphertext]);
}

/**
 * Decrypt AES-256-CBC data (IV + ciphertext). Returns the original bytes.
 *
 * Throws DecryptionError on wrong passphrase, truncated/corrupt input,
 * invalid PKCS7 padding, or a payload that does not start with the
 * SQLite magic header.
 */
export function decryptFile(data: Buffer, passphrase: string): Buffer {
  const key = deriveKey(passphrase);
  if (data.length < MIN_ENCRYPTED_LENGTH) {
    throw new DecryptionError(
      `${new DecryptionError().message}: data too short (${data.length} bytes, need at least ${MIN_ENCRYPTED_LENGTH})`,
    );
  }
  const iv = data.subarray(0, IV_LENGTH);
  const ciphertext = data.subarray(IV_LENGTH);
  if (ciphertext.length % 16 !== 0) {
    throw new DecryptionError(
      `${new DecryptionError().message}: ciphertext is not block-aligned`,
    );
  }
  const decipher = createDecipheriv('aes-256-cbc', key, iv).setAutoPadding(false);
  const padded = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  // Strict PKCS7 validation: padLen must be 1..16 and every trailing
  // byte must equal padLen.
  const padLen = padded[padded.length - 1];
  if (padLen < 1 || padLen > 16) {
    throw new DecryptionError(
      `${new DecryptionError().message}: invalid PKCS7 padding (pad byte ${padLen})`,
    );
  }
  const padStart = padded.length - padLen;
  for (let i = padStart; i < padded.length; i++) {
    if (padded[i] !== padLen) {
      throw new DecryptionError(
        `${new DecryptionError().message}: invalid PKCS7 padding`,
      );
    }
  }
  const plaintext = padded.subarray(0, padStart);
  if (plaintext.length < SQLITE_MAGIC.length || !plaintext.subarray(0, SQLITE_MAGIC.length).equals(SQLITE_MAGIC)) {
    throw new DecryptionError(
      `${new DecryptionError().message}: not a SQLite database`,
    );
  }
  return Buffer.from(plaintext);
}

/** Return true if data decrypts with passphrase, false otherwise. Never throws. */
export function validatePassphrase(data: Buffer, passphrase: string): boolean {
  try {
    decryptFile(data, passphrase);
    return true;
  } catch {
    return false;
  }
}
