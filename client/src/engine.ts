import initSqlJs, { type Database, type SqlJsStatic } from 'sql.js';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { decryptFile, encryptFile, DecryptionError } from './crypto.js';
import { DatabaseNotOpenError, SQLiteError } from './errors.js';

let sqlPromise: Promise<SqlJsStatic> | null = null;

async function getSql(): Promise<SqlJsStatic> {
  if (!sqlPromise) {
    sqlPromise = initSqlJs();
  }
  return sqlPromise;
}

export class ClientEngine {
  private db: Database | null = null;
  private _passphrase: string;
  dbPath: string;
  private _opCount = 0;

  constructor(dbPath: string, passphrase: string) {
    this.dbPath = dbPath.replace(/^~/, os.homedir());
    this._passphrase = passphrase;
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
          throw new Error(`Database not found: ${this.dbPath}`);
        }
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
      this.db = bytes ? new SQL.Database(bytes) : new SQL.Database();
    } catch (err) {
      this.db = null;
      if (err instanceof DecryptionError) throw err;
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
    }
  }

  close(): void {
    if (this.db) {
      let bytes: Buffer;
      try {
        bytes = Buffer.from(this.db.export());
      } catch {
        bytes = Buffer.alloc(0);
      }
      try {
        this.db.close();
      } catch {
        // already closed
      }
      this.db = null;
      if (bytes.length > 0) this.writeEncrypted(bytes);
    }
  }

  private writeEncrypted(bytes: Buffer): void {
    const encrypted = encryptFile(bytes, this._passphrase);
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    fs.writeFileSync(this.dbPath, encrypted);
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

  execute(sql: string, params?: any[]): { rows: any[]; changes: number; lastInsertRowid: number } {
    if (!this.db) throw new DatabaseNotOpenError();
    try {
      const trimmed = sql.trim().toUpperCase();
      if (trimmed.startsWith('SELECT') || trimmed.startsWith('PRAGMA') || trimmed.startsWith('EXPLAIN')) {
        const rows = this.queryAll(sql, params);
        this._opCount++;
        return { rows, changes: 0, lastInsertRowid: 0 };
      }
      this.db.run(sql, params ?? []);
      const changes = this.db.getRowsModified();
      let lastInsertRowid = 0;
      if (trimmed.startsWith('INSERT')) {
        const res = this.queryAll('SELECT last_insert_rowid() AS id');
        lastInsertRowid = Number(res[0]?.id ?? 0);
      }
      this._opCount++;
      return { rows: [], changes, lastInsertRowid };
    } catch (err) {
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
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
