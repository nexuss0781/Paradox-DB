import Database from 'better-sqlite3';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { ClientConfig, QueryResult, SelectOptions } from './types.js';
import { DatabaseNotOpenError, SQLiteError, EncryptionError } from './errors.js';
import { ChangeTracker, ConflictInfo } from './change-tracker.js';

export class ClientEngine {
  private db: Database.Database | null = null;
  private config: ClientConfig;
  private dbPath: string;
  private _opCount = 0;
  private _changeTracker: ChangeTracker | null = null;

  constructor(config: ClientConfig) {
    this.config = config;
    this.dbPath = config.database_path;
  }

  open(passphrase: string, dbPath?: string): void {
    const target = dbPath || this.dbPath;
    const resolved = target.replace(/^~/, os.homedir());
    const dir = path.dirname(resolved);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    try {
      this.db = new Database(resolved);

      const salt = crypto.createHash('sha256').update('paradox-salt').digest();
      const key = crypto.pbkdf2Sync(passphrase, salt, this.config.encryption.kdf_iterations || 256000, 32, 'sha512');
      const hexKey = key.toString('hex');

      this.db.pragma(`cipher_key = "x'${hexKey}"`);
      this.db.pragma(`cipher_page_size = ${this.config.encryption.page_size || 4096}`);
      this.db.pragma('cipher_hmac_algorithm = HMAC_SHA512');
      this.db.pragma('cipher_kdf_algorithm = PBKDF2_HMAC_SHA512');
      this.db.pragma('journal_mode = WAL');
    } catch (err: any) {
      this.db = null;
      if (
        err.message?.includes('cipher') ||
        err.message?.includes('key') ||
        err.message?.includes('not a database') ||
        err.message?.includes('file is not a database')
      ) {
        throw new EncryptionError();
      }
      throw new SQLiteError(err.message, err);
    }
  }

  close(): void {
    if (!this.db) return;
    try {
      this.db.pragma('wal_checkpoint(FULL)');
    } catch {
      // checkpoint may fail on encrypted DBs without proper cipher; non-fatal
    }
    try {
      this.db.close();
    } catch {
      // already closed
    }
    this.db = null;
  }

  execute(sql: string, params?: any[]): QueryResult {
    if (!this.db) throw new DatabaseNotOpenError();
    try {
      if (params && params.length > 0) {
        const stmt = this.db.prepare(sql);
        const result = stmt.run(...params);
        const trimmed = sql.trim().toUpperCase();
        const rows =
          trimmed.startsWith('SELECT') || trimmed.startsWith('PRAGMA')
            ? stmt.all(...params)
            : [];
        this._opCount++;
        return {
          rows,
          changes: result.changes,
          lastInsertRowid: Number(result.lastInsertRowid),
        };
      } else {
        this.db.exec(sql);
        this._opCount++;
        return { rows: [], changes: 0, lastInsertRowid: 0 };
      }
    } catch (err: any) {
      throw new SQLiteError(err.message, err);
    }
  }

  insert(table: string, row: Record<string, any>): number {
    const keys = Object.keys(row);
    const values = Object.values(row);
    const placeholders = keys.map(() => '?').join(', ');
    const result = this.execute(
      `INSERT INTO ${table} (${keys.join(', ')}) VALUES (${placeholders})`,
      values
    );
    this._changeTracker?.track('insert', table, row);
    return result.lastInsertRowid;
  }

  select(table: string, where?: Record<string, any>, options?: SelectOptions): any[] {
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
    const setClauses = Object.entries(set).map(([k]) => `${k} = ?`);
    const setValues = Object.values(set);
    const whereClauses = Object.entries(where).map(([k]) => `${k} = ?`);
    const whereValues = Object.values(where);
    const changes = this.execute(
      `UPDATE ${table} SET ${setClauses.join(', ')} WHERE ${whereClauses.join(' AND ')}`,
      [...setValues, ...whereValues]
    ).changes;
    if (changes > 0) this._changeTracker?.track('update', table, undefined, where, set);
    return changes;
  }

  delete(table: string, where: Record<string, any>): number {
    const clauses = Object.entries(where).map(([k]) => `${k} = ?`);
    const values = Object.values(where);
    const changes = this.execute(
      `DELETE FROM ${table} WHERE ${clauses.join(' AND ')}`,
      values
    ).changes;
    if (changes > 0) this._changeTracker?.track('delete', table, undefined, where);
    return changes;
  }

  get isOpen(): boolean {
    return this.db !== null;
  }

  get operationCount(): number {
    return this._opCount;
  }

  resetOperationCount(): void {
    this._opCount = 0;
  }

  get changeTracker(): ChangeTracker | null {
    return this._changeTracker;
  }

  startTracking(): void {
    this._changeTracker = new ChangeTracker(this);
    this._changeTracker.startSession();
  }

  exportChangeset(): Buffer | null {
    return this._changeTracker?.exportChangeset() ?? null;
  }

  importChangeset(patch: Buffer): { success: boolean; conflicts?: ConflictInfo } {
    if (!this._changeTracker) {
      this.startTracking();
    }
    return this._changeTracker!.importChangeset(patch);
  }
}
