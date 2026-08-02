import Database from 'better-sqlite3';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { decryptFile, encryptFile, DecryptionError } from './crypto.js';
import { DatabaseNotOpenError, SQLiteError } from './errors.js';

export class ClientEngine {
  private db: Database.Database | null = null;
  private _passphrase: string;
  dbPath: string;
  private tmpPath: string | null = null;
  private _opCount = 0;

  constructor(dbPath: string, passphrase: string) {
    this.dbPath = dbPath.replace(/^~/, os.homedir());
    this._passphrase = passphrase;
  }

  private cleanupTmp(): void {
    if (this.tmpPath) {
      try {
        fs.unlinkSync(this.tmpPath);
      } catch {
        // already gone
      }
      this.tmpPath = null;
    }
  }

  private decryptToTemp(): string {
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
    const tmp = path.join(
      os.tmpdir(),
      `parad-tmp-${process.pid}-${Math.random().toString(36).slice(2)}.db`,
    );
    fs.writeFileSync(tmp, decrypted);
    this.tmpPath = tmp;
    return tmp;
  }

  private encryptFromTemp(): void {
    if (!this.tmpPath) return;
    const decrypted = fs.readFileSync(this.tmpPath);
    const encrypted = encryptFile(decrypted, this._passphrase);
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    fs.writeFileSync(this.dbPath, encrypted);
  }

  open(create = false): void {
    if (this.db) return;
    this.cleanupTmp();
    try {
      let target: string;
      if (create && (!fs.existsSync(this.dbPath) || fs.statSync(this.dbPath).size === 0)) {
        fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
        const tmp = path.join(
          os.tmpdir(),
          `parad-tmp-${process.pid}-${Math.random().toString(36).slice(2)}.db`,
        );
        fs.writeFileSync(tmp, Buffer.alloc(0));
        this.tmpPath = tmp;
        target = tmp;
      } else {
        target = this.decryptToTemp();
      }
      this.db = new Database(target);
    } catch (err) {
      this.db = null;
      this.cleanupTmp();
      if (err instanceof DecryptionError) throw err;
      throw new SQLiteError(err instanceof Error ? err.message : String(err), err as Error);
    }
  }

  close(): void {
    if (this.db) {
      try {
        this.db.pragma('wal_checkpoint(FULL)');
      } catch {
        // non-fatal
      }
      try {
        this.db.close();
      } catch {
        // already closed
      }
      this.db = null;
    }
    if (this.tmpPath) {
      this.encryptFromTemp();
      this.cleanupTmp();
    }
  }

  execute(sql: string, params?: any[]): { rows: any[]; changes: number; lastInsertRowid: number } {
    if (!this.db) throw new DatabaseNotOpenError();
    try {
      const stmt = this.db.prepare(sql);
      const trimmed = sql.trim().toUpperCase();
      if (trimmed.startsWith('SELECT') || trimmed.startsWith('PRAGMA') || trimmed.startsWith('EXPLAIN')) {
        const rows = params && params.length > 0 ? stmt.all(...params) : stmt.all();
        this._opCount++;
        return { rows, changes: 0, lastInsertRowid: 0 };
      }
      const result = params && params.length > 0 ? stmt.run(...params) : stmt.run();
      this._opCount++;
      return {
        rows: [],
        changes: result.changes,
        lastInsertRowid: Number(result.lastInsertRowid),
      };
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
    const insertAll = this.db.transaction((all: Record<string, any>[]) => all.map((row) => this.insert(table, row)));
    return insertAll(rows);
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
    if (this.tmpPath) {
      return fs.readFileSync(this.tmpPath);
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
