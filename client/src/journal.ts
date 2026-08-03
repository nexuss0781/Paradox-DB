// Append-only durable write journal for the encrypted SQLite engine.
//
// Every write (statement + bound params) is serialized into a compact binary
// record and stored in the journal file BEFORE it is applied to the in-memory
// engine. On the next open() the journal is replayed on top of the snapshot,
// so a crash between writes loses nothing. A monotonically increasing `seq` is
// stored per record and mirrored into the snapshot's `PRAGMA user_version` at
// checkpoint time, making replay idempotent even if a crash lands between the
// snapshot write and the journal truncation.

export const JOURNAL_MAGIC = Buffer.from('PJRN', 'latin1');
export const JOURNAL_VERSION = 1;

export interface JournalEntry {
  seq: number;
  sql: string;
  params: unknown[];
}

const T_NULL = 0;
const T_NUMBER = 1;
const T_STRING = 2;
const T_BLOB = 3;
const T_BOOL = 4;
const T_BIGINT = 5;

/**
 * Serialize a journal record. The layout is:
 *
 *   [4B 'PJRN'][1B version]
 *   [u32be seq]
 *   [u32be sqlLen][sql utf8]
 *   [u16be paramCount]
 *   [per param: 1B type + payload]
 *     T_NULL   0           (no payload)
 *     T_NUMBER 1 8B f64be
 *     T_STRING 2 [u32 len][utf8]
 *     T_BLOB   3 [u32 len][bytes]
 *     T_BOOL   4 1B
 *     T_BIGINT 5 8B i64be
 */
export function encodeEntry(seq: number, sql: string, params: unknown[]): Buffer {
  const sqlBuf = Buffer.from(sql, 'utf-8');
  const parts: Buffer[] = [];
  const header = Buffer.alloc(9);
  JOURNAL_MAGIC.copy(header, 0);
  header[4] = JOURNAL_VERSION;
  header.writeUInt32BE(seq, 5);
  parts.push(header);
  const sqlLen = Buffer.alloc(4);
  sqlLen.writeUInt32BE(sqlBuf.length, 0);
  parts.push(sqlLen, sqlBuf);
  const count = Buffer.alloc(2);
  count.writeUInt16BE(params.length, 0);
  parts.push(count);
  for (const p of params) {
    parts.push(encodeParam(p));
  }
  return Buffer.concat(parts);
}

function encodeParam(p: unknown): Buffer {
  if (p === null || p === undefined) {
    return Buffer.from([T_NULL]);
  }
  if (typeof p === 'number') {
    const b = Buffer.alloc(9);
    b[0] = T_NUMBER;
    b.writeDoubleBE(p, 1);
    return b;
  }
  if (typeof p === 'boolean') {
    const b = Buffer.alloc(2);
    b[0] = T_BOOL;
    b[1] = p ? 1 : 0;
    return b;
  }
  if (typeof p === 'bigint') {
    const b = Buffer.alloc(9);
    b[0] = T_BIGINT;
    b.writeBigInt64BE(p, 1);
    return b;
  }
  if (typeof p === 'string') {
    const s = Buffer.from(p, 'utf-8');
    const b = Buffer.alloc(5 + s.length);
    b[0] = T_STRING;
    b.writeUInt32BE(s.length, 1);
    s.copy(b, 5);
    return b;
  }
  if (p instanceof Uint8Array) {
    const b = Buffer.alloc(5 + p.length);
    b[0] = T_BLOB;
    b.writeUInt32BE(p.length, 1);
    Buffer.from(p).copy(b, 5);
    return b;
  }
  // Fallback: serialize as string (best-effort for exotic values).
  const s = Buffer.from(String(p), 'utf-8');
  const b = Buffer.alloc(5 + s.length);
  b[0] = T_STRING;
  b.writeUInt32BE(s.length, 1);
  s.copy(b, 5);
  return b;
}

/** Deserialize a journal record. Throws on malformed/unknown-format input. */
export function decodeEntry(buf: Buffer): JournalEntry {
  if (buf.length < 9 || !buf.subarray(0, 4).equals(JOURNAL_MAGIC)) {
    throw new Error('journal: bad record magic');
  }
  if (buf[4] !== JOURNAL_VERSION) {
    throw new Error(`journal: unsupported version ${buf[4]}`);
  }
  const seq = buf.readUInt32BE(5);
  let offset = 9;
  if (offset + 4 > buf.length) throw new Error('journal: truncated sql length');
  const sqlLen = buf.readUInt32BE(offset);
  offset += 4;
  if (offset + sqlLen > buf.length) throw new Error('journal: truncated sql');
  const sql = buf.subarray(offset, offset + sqlLen).toString('utf-8');
  offset += sqlLen;
  if (offset + 2 > buf.length) throw new Error('journal: truncated param count');
  const paramCount = buf.readUInt16BE(offset);
  offset += 2;
  const params: unknown[] = [];
  for (let i = 0; i < paramCount; i++) {
    if (offset >= buf.length) throw new Error('journal: truncated param');
    const type = buf[offset];
    offset += 1;
    switch (type) {
      case T_NULL:
        params.push(null);
        break;
      case T_NUMBER: {
        if (offset + 8 > buf.length) throw new Error('journal: truncated number');
        params.push(buf.readDoubleBE(offset));
        offset += 8;
        break;
      }
      case T_BOOL: {
        if (offset + 1 > buf.length) throw new Error('journal: truncated bool');
        params.push(buf[offset] !== 0);
        offset += 1;
        break;
      }
      case T_BIGINT: {
        if (offset + 8 > buf.length) throw new Error('journal: truncated bigint');
        params.push(buf.readBigInt64BE(offset));
        offset += 8;
        break;
      }
      case T_STRING: {
        if (offset + 4 > buf.length) throw new Error('journal: truncated string len');
        const len = buf.readUInt32BE(offset);
        offset += 4;
        if (offset + len > buf.length) throw new Error('journal: truncated string');
        params.push(buf.subarray(offset, offset + len).toString('utf-8'));
        offset += len;
        break;
      }
      case T_BLOB: {
        if (offset + 4 > buf.length) throw new Error('journal: truncated blob len');
        const len = buf.readUInt32BE(offset);
        offset += 4;
        if (offset + len > buf.length) throw new Error('journal: truncated blob');
        params.push(Buffer.from(buf.subarray(offset, offset + len)));
        offset += len;
        break;
      }
      default:
        throw new Error(`journal: unknown param type ${type}`);
    }
  }
  return { seq, sql, params };
}

/** Bytes of a length-prefixed encrypted entry stored in the journal file. */
export function wrapEntry(cipher: Buffer): Buffer {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(cipher.length, 0);
  return Buffer.concat([head, cipher]);
}
