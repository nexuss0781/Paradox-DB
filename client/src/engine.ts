import initSqlJs, { type Database, type SqlJsStatic } from 'sql.js';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { decryptBytes, decryptFile, encryptBytes, DecryptionError } from './crypto.js';
import { DatabaseNotOpenError, SQLiteError } from './errors.js';
import { decodeEntry, encodeEntry, wrapEntry, type JournalEntry } from './journal.js';

let sqlPromise: Promise<SqlJsStatic> | null = null;

async function getSql(): Promise<SqlJsStatic> {
  if (!sqlPromise) {
    sqlPromise = initSqlJs();
  }
  return sqlPromise;
}

// When the journal grows past this many bytes it is folded into the snapshot
// during the session (keeps journal replay fast and the file bounded).
const JOURNAL_CHECKPOINT_BYTES = 1024 * 1024;

export class ClientEngine {
  private db: Database | null = null;
  private _passphrase: string;
  dbPath: string;
  private _opCount = 0;

  // ── journal state ─────────────────────────────────────────────
  private journalPath: string;
  private journalFd: number | null = null;
  private journalLen = 0;
  // `baseSeq` is the write watermark folded into the snapshot (mirrored to the
  // SQLite file header via PRAGMA user_version). `seq` is the next sequence to
  // assign. Replay applies only records with seq > baseSeq, which makes replay
  // idempotent even if a crash lands between snapshot write and journal clear.
  private baseSeq = 0;
  private seq = 0;
  private inTx = 0;

  constructor(dbPath: string, passphrase: string) {
    this.dbPath = dbPath.replace(/^~/, os.homedir());
    this._passphrase = passphrase;
    this.journalPath = `${this.dbPath}.journal`;
  }

  async open(create = false): Promise<void> {
    if (this.db) return;
    const SQL = await getSql();
    try {
      let bytes: Uint8Array | null = null;
      if (create && (!fs.existsSync(this.dbPath) || fs.statSync(this.dbPath).size === 0)) {
        bytes = null;
      } else {
        if (!fs.existsSync(this.dbPath)) {
          // Crash before the first snapshot: rebuild entirely from the journal.
          if (!fs.existsSync(this.journalPath)) {
            throw new Error(`Database not found: ${this.dbPath}`);
          }
          bytes = null;
        } else {
          const encrypted = fs.readFileSync(this.dbPath);
          let decrypted: Buffer;
          try {
            decrypted = decryptFile(encrypted, this._passphrase);
          } catch (err) {
            if (err instanceof DecryptionError) {
              throw new DecryptionError(`Cannot open ${this.dbPath}: ${err.message}`);
            }
            throw err;
          }
          bytes = new Uint8Array(decrypted);
        }
      }
      this.db = bytes ? new SQL.Database(bytes) : new SQL.Database();
      this.baseSeq = this.readUserVersion();
      this.seq = this.baseSeq;
      this.replayJournal();
      this.ensureJournalFd();
    } catch (err) {
      this.db = null;
      this.closeJournalFd();
      if (err instanceof DecryptionError) throw err;
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
    }
  }

  close(): void {
    if (this.db) {
      let wrote = false;
      // Abort any open user transaction (SQL semantics: uncommitted work is lost).
      if (this.inTx > 0) {
        try {
          this.db.run('ROLLBACK');
        } catch {
          // best-effort
        }
        this.inTx = 0;
      }
      try {
        this.writeSnapshot();
        wrote = true;
      } catch {
        // Journal stays intact so the next open() can recover.
      }
      try {
        this.db.close();
      } catch {
        // already closed
      }
      this.db = null;
      if (wrote) {
        try {
          this.clearJournal();
        } catch {
          // best-effort
        }
      }
    }
    this.closeJournalFd();
  }

  /** Replace the whole database from plaintext SQLite bytes (used by pull). */
  async replaceBytes(bytes: Buffer): Promise<void> {
    const SQL = await getSql();
    if (this.db) {
      try {
        this.db.close();
      } catch {
        // already closed
      }
      this.db = null;
    }
    this.baseSeq = 0;
    this.seq = 0;
    this.inTx = 0;
    this.clearJournal();
    this.closeJournalFd();
    this.db = new SQL.Database(new Uint8Array(bytes));
    this.baseSeq = this.readUserVersion();
    this.seq = this.baseSeq;
    try {
      this.writeSnapshot();
    } catch {
      // best-effort
    }
    this.ensureJournalFd();
  }

  // ── snapshot persistence ───────────────────────────────────────

  private readUserVersion(): number {
    try {
      const res = this.db!.exec('PRAGMA user_version');
      const v = res?.[0]?.values?.[0]?.[0];
      return typeof v === 'number' && Number.isFinite(v) ? Math.floor(v) : 0;
    } catch {
      return 0;
    }
  }

  /** Mirror the write watermark into the SQLite header, but only when it
   *  actually changed (a redundant PRAGMA write bumps the file change counter
   *  and would make exports non-deterministic). */
  private stampUserVersion(): void {
    try {
      const current = this.readUserVersion();
      const target = Math.floor(this.seq);
      if (current !== target) {
        this.db!.exec(`PRAGMA user_version = ${target}`);
      }
    } catch {
      // watermark is best-effort
    }
  }

  /** Export the in-memory DB, stamp the write watermark, and atomically
   *  rewrite the encrypted snapshot file. Does NOT clear the journal. */
  private writeSnapshot(): void {
    if (!this.db) return;
    this.stampUserVersion();
    let bytes: Buffer;
    try {
      bytes = Buffer.from(this.db.export());
    } catch {
      return;
    }
    if (bytes.length === 0) return;
    this.atomicWriteSnapshot(bytes);
  }

  /** Write snapshot then clear the journal. */
  private checkpoint(): void {
    this.writeSnapshot();
    this.clearJournal();
  }

  private atomicWriteSnapshot(dbBytes: Buffer): void {
    const encrypted = encryptBytes(dbBytes, this._passphrase);
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    const tmp = `${this.dbPath}.tmp`;
    const fd = fs.openSync(tmp, 'w');
    try {
      fs.writeFileSync(fd, encrypted);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, this.dbPath);
  }

  // ── journal ────────────────────────────────────────────────────

  private ensureJournalFd(): void {
    if (this.journalFd != null) return;
    fs.mkdirSync(path.dirname(this.journalPath), { recursive: true });
    this.journalFd = fs.openSync(this.journalPath, 'a');
    try {
      this.journalLen = fs.statSync(this.journalPath).size;
    } catch {
      this.journalLen = 0;
    }
  }

  private closeJournalFd(): void {
    if (this.journalFd != null) {
      try {
        fs.fsyncSync(this.journalFd);
      } catch {
        // best-effort
      }
      try {
        fs.closeSync(this.journalFd);
      } catch {
        // best-effort
      }
      this.journalFd = null;
    }
  }

  private clearJournal(): void {
    if (this.journalFd != null) {
      try {
        fs.ftruncateSync(this.journalFd, 0);
      } catch {
        // best-effort
      }
    } else if (fs.existsSync(this.journalPath)) {
      try {
        fs.truncateSync(this.journalPath, 0);
      } catch {
        // best-effort
      }
    }
    this.journalLen = 0;
  }

  private truncateJournalTo(len: number): void {
    if (this.journalFd != null) {
      try {
        fs.ftruncateSync(this.journalFd, len);
      } catch {
        // best-effort
      }
    } else if (fs.existsSync(this.journalPath)) {
      try {
        fs.truncateSync(this.journalPath, len);
      } catch {
        // best-effort
      }
    }
    this.journalLen = len;
  }

  private syncJournal(): void {
    if (this.journalFd != null) {
      try {
        fs.fsyncSync(this.journalFd);
      } catch {
        // best-effort
      }
    }
  }

  private appendJournal(sql: string, params: unknown[]): void {
    this.ensureJournalFd();
    const plain = encodeEntry(this.seq, sql, params);
    const cipher = encryptBytes(plain, this._passphrase);
    const record = wrapEntry(cipher);
    fs.writeSync(this.journalFd!, record);
    this.journalLen += record.length;
  }

  /** Track transaction depth for durable fsync boundaries. */
  private updateTxnDepth(trimmed: string): number {
    const before = this.inTx;
    if (/^BEGIN(\b|$)/.test(trimmed) || /^START TRANSACTION/.test(trimmed) || /^SAVEPOINT\b/.test(trimmed)) {
      this.inTx += 1;
    } else if (/^(COMMIT|END|ROLLBACK|RELEASE)\b/.test(trimmed)) {
      this.inTx = Math.max(0, this.inTx - 1);
    }
    return before;
  }

  /**
   * Apply every journal record with seq > baseSeq onto the in-memory DB.
   * Records already folded into the snapshot are skipped, making replay
   * idempotent. On a corrupt/failed record the clean prefix is folded into a
   * snapshot and the bad tail is dropped so the next open starts clean.
   */
  private replayJournal(): void {
    if (!fs.existsSync(this.journalPath)) return;
    let data: Buffer;
    try {
      data = fs.readFileSync(this.journalPath);
    } catch {
      return;
    }
    if (data.length === 0) return;
    let offset = 0;
    let lastApplied = this.baseSeq;
    let stopAt = -1;
    while (offset < data.length) {
      if (offset + 4 > data.length) break;
      const entryLen = data.readUInt32BE(offset);
      offset += 4;
      if (offset + entryLen > data.length) break;
      const cipher = data.subarray(offset, offset + entryLen);
      offset += entryLen;
      let entry: JournalEntry;
      try {
        entry = decodeEntry(decryptBytes(cipher, this._passphrase));
      } catch {
        stopAt = offset;
        break;
      }
      if (entry.seq <= this.baseSeq) continue;
      try {
        this.db!.run(entry.sql, (entry.params ?? []) as any[]);
        lastApplied = entry.seq;
      } catch {
        stopAt = offset;
        break;
      }
    }
    this.seq = Math.max(lastApplied, this.baseSeq);
    // A crash can leave a transaction open mid-replay — abort it.
    try {
      this.db!.run('ROLLBACK');
    } catch {
      // no open transaction
    }
    if (stopAt >= 0) {
      try {
        this.writeSnapshot();
      } catch {
        // keep old snapshot
      }
      this.truncateJournalTo(stopAt);
    }
  }

  // ── public API ─────────────────────────────────────────────────

  /** Execute SQL and return rows for queries and DML with RETURNING. */
  executeRaw(sql: string, params?: any[]): any[] {
    if (!this.db) throw new DatabaseNotOpenError();
    const trimmed = sql.trim().toUpperCase();
    if (trimmed.startsWith('SELECT') || trimmed.startsWith('PRAGMA') || trimmed.startsWith('EXPLAIN')) {
      return this.queryAll(sql, params);
    }
    if (!/\bRETURNING\b/.test(trimmed)) {
      return this.execute(sql, params).rows;
    }

    const beforeLen = this.journalLen;
    const txnBefore = this.updateTxnDepth(trimmed);
    this.seq += 1;
    try {
      this.appendJournal(sql, params ?? []);
      const stmt = this.db.prepare(sql);
      const rows: any[] = [];
      try {
        stmt.bind(params ?? []);
        while (stmt.step()) rows.push(stmt.getAsObject());
      } finally {
        stmt.free();
      }
      if (this.inTx === 0) this.syncJournal();
      this._opCount++;
      if (this.journalLen > JOURNAL_CHECKPOINT_BYTES && this.inTx === 0) this.checkpoint();
      return rows;
    } catch (err) {
      this.seq -= 1;
      this.inTx = txnBefore;
      this.truncateJournalTo(beforeLen);
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
    }
  }

  /** Execute SQL and return positional SQLite rows for Drizzle and proxy adapters. */
  executeRawValues(sql: string, params?: any[]): any[][] {
    if (!this.db) throw new DatabaseNotOpenError();
    const trimmed = sql.trim().toUpperCase();
    if (trimmed.startsWith('SELECT') || trimmed.startsWith('PRAGMA') || trimmed.startsWith('EXPLAIN')) {
      return this.queryValues(sql, params);
    }
    if (!/\bRETURNING\b/.test(trimmed)) {
      this.execute(sql, params);
      return [];
    }

    const beforeLen = this.journalLen;
    const txnBefore = this.updateTxnDepth(trimmed);
    this.seq += 1;
    try {
      this.appendJournal(sql, params ?? []);
      const stmt = this.db.prepare(sql);
      const rows: any[][] = [];
      try {
        stmt.bind(params ?? []);
        while (stmt.step()) rows.push(stmt.get() as any[]);
      } finally {
        stmt.free();
      }
      if (this.inTx === 0) this.syncJournal();
      this._opCount++;
      if (this.journalLen > JOURNAL_CHECKPOINT_BYTES && this.inTx === 0) this.checkpoint();
      return rows;
    } catch (err) {
      this.seq -= 1;
      this.inTx = txnBefore;
      this.truncateJournalTo(beforeLen);
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
    }
  }

  execute(sql: string, params?: any[]): { rows: any[]; changes: number; lastInsertRowid: number } {
    if (!this.db) throw new DatabaseNotOpenError();
    const trimmed = sql.trim().toUpperCase();
    try {
      if (trimmed.startsWith('SELECT') || trimmed.startsWith('PRAGMA') || trimmed.startsWith('EXPLAIN')) {
        const rows = this.queryAll(sql, params);
        this._opCount++;
        return { rows, changes: 0, lastInsertRowid: 0 };
      }
      const beforeLen = this.journalLen;
      const txnBefore = this.updateTxnDepth(trimmed);
      this.seq += 1;
      try {
        this.appendJournal(sql, params ?? []);
      } catch (err) {
        this.seq -= 1;
        this.inTx = txnBefore;
        this.truncateJournalTo(beforeLen);
        throw err;
      }
      try {
        this.db.run(sql, params ?? []);
      } catch (err) {
        this.seq -= 1;
        this.inTx = txnBefore;
        this.truncateJournalTo(beforeLen);
        throw err;
      }
      if (this.inTx === 0) this.syncJournal();
      const changes = this.db.getRowsModified();
      let lastInsertRowid = 0;
      if (trimmed.startsWith('INSERT')) {
        const res = this.queryAll('SELECT last_insert_rowid() AS id');
        lastInsertRowid = Number(res[0]?.id ?? 0);
      }
      this._opCount++;
      if (this.journalLen > JOURNAL_CHECKPOINT_BYTES && this.inTx === 0) {
        this.checkpoint();
      }
      return { rows: [], changes, lastInsertRowid };
    } catch (err) {
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
    }
  }

  private queryValues(sql: string, params?: any[]): any[][] {
    const stmt = this.db!.prepare(sql);
    try {
      if (params && params.length > 0) stmt.bind(params);
      const rows: any[][] = [];
      while (stmt.step()) rows.push(stmt.get() as any[]);
      return rows;
    } finally {
      stmt.free();
    }
  }

  private queryAll(sql: string, params?: any[]): any[] {
    const stmt = this.db!.prepare(sql);
    try {
      if (params && params.length > 0) stmt.bind(params);
      const rows: any[] = [];
      while (stmt.step()) {
        rows.push(stmt.getAsObject());
      }
      return rows;
    } finally {
      stmt.free();
    }
  }

  insert(table: string, row: Record<string, any>): number {
    const keys = Object.keys(row);
    const values = Object.values(row);
    const placeholders = keys.map(() => '?').join(', ');
    const result = this.execute(
      `INSERT INTO ${table} (${keys.join(', ')}) VALUES (${placeholders})`,
      values,
    );
    return result.lastInsertRowid;
  }

  select(table: string, where?: Record<string, any>, options?: { orderBy?: string; limit?: number; offset?: number }): any[] {
    let sql = `SELECT * FROM ${table}`;
    const params: any[] = [];
    if (where && Object.keys(where).length > 0) {
      const conditions = Object.entries(where).map(([k, v]) => {
        params.push(v);
        return `${k} = ?`;
      });
      sql += ` WHERE ${conditions.join(' AND ')}`;
    }
    if (options?.orderBy) sql += ` ORDER BY ${options.orderBy}`;
    if (options?.limit) sql += ` LIMIT ${options.limit}`;
    if (options?.offset) sql += ` OFFSET ${options.offset}`;
    return this.execute(sql, params).rows;
  }

  update(table: string, set: Record<string, any>, where: Record<string, any>): number {
    const setClauses = Object.keys(set).map((k) => `${k} = ?`);
    const setValues = Object.values(set);
    const whereClauses = Object.keys(where).map((k) => `${k} = ?`);
    const whereValues = Object.values(where);
    return this.execute(
      `UPDATE ${table} SET ${setClauses.join(', ')} WHERE ${whereClauses.join(' AND ')}`,
      [...setValues, ...whereValues],
    ).changes;
  }

  delete(table: string, where: Record<string, any>): number {
    const clauses = Object.keys(where).map((k) => `${k} = ?`);
    const values = Object.values(where);
    return this.execute(`DELETE FROM ${table} WHERE ${clauses.join(' AND ')}`, values).changes;
  }

  /** Fetch the first row matching `where`, or null when nothing matches. */
  get(table: string, where: Record<string, any>): any | null {
    const rows = this.select(table, where, { limit: 1 });
    return rows[0] ?? null;
  }

  /** Insert many rows atomically (single transaction). Returns each rowid. */
  insertMany(table: string, rows: Record<string, any>[]): number[] {
    if (!this.db) throw new DatabaseNotOpenError();
    if (rows.length === 0) return [];
    this.execute('BEGIN');
    try {
      const ids = rows.map((row) => this.insert(table, row));
      this.execute('COMMIT');
      return ids;
    } catch (err) {
      try {
        this.execute('ROLLBACK');
      } catch {
        // transaction already aborted
      }
      throw err;
    }
  }

  /**
   * Insert `row`, or when a conflict on `conflictColumns` occurs, update the
   * existing row with every non-conflict column (`col = excluded.col`).
   * Returns the number of rows affected (1 inserted or 1 updated, 0 on
   * DO NOTHING).
   */
  upsert(table: string, row: Record<string, any>, conflictColumns: string | string[]): number {
    const conflict = Array.isArray(conflictColumns) ? conflictColumns : [conflictColumns];
    if (conflict.length === 0) throw new Error('upsert requires at least one conflict column');
    const keys = Object.keys(row);
    const values = Object.values(row);
    const placeholders = keys.map(() => '?').join(', ');
    const conflictList = conflict.join(', ');
    const setClauses = keys.filter((k) => !conflict.includes(k)).map((k) => `${k} = excluded.${k}`);
    const sql =
      setClauses.length > 0
        ? `INSERT INTO ${table} (${keys.join(', ')}) VALUES (${placeholders}) ON CONFLICT(${conflictList}) DO UPDATE SET ${setClauses.join(', ')}`
        : `INSERT INTO ${table} (${keys.join(', ')}) VALUES (${placeholders}) ON CONFLICT(${conflictList}) DO NOTHING`;
    return this.execute(sql, values).changes;
  }

  /** Return the current PLAINTEXT SQLite bytes (not encrypted). */
  getRawBytes(): Buffer {
    if (this.db) {
      this.stampUserVersion();
      return Buffer.from(this.db.export());
    }
    if (fs.existsSync(this.dbPath)) {
      const encrypted = fs.readFileSync(this.dbPath);
      return decryptFile(encrypted, this._passphrase);
    }
    throw new Error(`Database not found: ${this.dbPath}`);
  }

  get isOpen(): boolean {
    return this.db !== null;
  }

  get passphrase(): string {
    return this._passphrase;
  }

  get operationCount(): number {
    return this._opCount;
  }

  resetOperationCount(): void {
    this._opCount = 0;
  }
}
