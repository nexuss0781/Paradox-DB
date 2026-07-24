import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { ClientEngine } from '../src/engine.js';
import { DatabaseNotOpenError, EncryptionError, SQLiteError } from '../src/errors.js';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const TEST_DB = path.join(os.tmpdir(), `test-paradox-${Date.now()}.sqlcipher`);
const PASSPHRASE = 'test-passphrase-123';

function makeConfig(dbPath?: string) {
  return {
    database_path: dbPath || TEST_DB,
    encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256000, page_size: 4096 },
    sync: {
      gateway_url: '',
      api_key: '',
      trigger_timer_seconds: 30,
      trigger_ops_threshold: 50,
      max_file_size_mb: 50,
      auto_sync_on_shutdown: true,
    },
    conflict: { strategy: 'last-write-wins' as const, log_conflicts: true },
    logging: { level: 'info' as const, path: '/tmp' },
  };
}

describe('ClientEngine', () => {
  let engine: ClientEngine;

  beforeEach(() => {
    engine = new ClientEngine(makeConfig());
  });

  afterEach(() => {
    try { engine.close(); } catch {}
    try { fs.unlinkSync(TEST_DB); } catch {}
  });

  describe('open/close', () => {
    it('creates new .sqlcipher file', () => {
      engine.open(PASSPHRASE);
      expect(fs.existsSync(TEST_DB)).toBe(true);
    });

    it('opens existing DB', () => {
      engine.open(PASSPHRASE);
      engine.close();
      engine.open(PASSPHRASE);
      expect(engine.isOpen).toBe(true);
    });

    it('rejects wrong passphrase', () => {
      engine.open(PASSPHRASE);
      engine.close();
      const e2 = new ClientEngine(makeConfig());
      expect(() => e2.open('wrong')).toThrow(EncryptionError);
    });

    it('close() is idempotent', () => {
      engine.open(PASSPHRASE);
      engine.close();
      engine.close();
    });

    it('throws DatabaseNotOpenError when not open', () => {
      expect(() => engine.execute('SELECT 1')).toThrow(DatabaseNotOpenError);
    });
  });

  describe('CRUD', () => {
    beforeEach(() => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, age INTEGER)');
    });

    it('execute() runs raw SQL', () => {
      engine.execute("INSERT INTO users (name, age) VALUES ('Alice', 30)");
      const rows = engine.select('users');
      expect(rows.length).toBe(1);
      expect(rows[0].name).toBe('Alice');
    });

    it('execute() parameterized', () => {
      engine.execute("INSERT INTO users (name, age) VALUES (?, ?)", ['Bob', 25]);
      expect(engine.select('users')[0].name).toBe('Bob');
    });

    it('execute() invalid SQL throws SQLiteError', () => {
      expect(() => engine.execute('INVALID SQL')).toThrow(SQLiteError);
    });

    it('insert() returns ID', () => {
      const id = engine.insert('users', { name: 'Charlie', age: 35 });
      expect(id).toBeGreaterThan(0);
    });

    it('select() returns all rows', () => {
      engine.insert('users', { name: 'A', age: 1 });
      engine.insert('users', { name: 'B', age: 2 });
      expect(engine.select('users').length).toBe(2);
    });

    it('select() with where', () => {
      engine.insert('users', { name: 'A', age: 1 });
      engine.insert('users', { name: 'B', age: 2 });
      expect(engine.select('users', { age: 1 }).length).toBe(1);
    });

    it('select() empty table', () => {
      expect(engine.select('users')).toEqual([]);
    });

    it('update() modifies rows', () => {
      engine.insert('users', { name: 'A', age: 1 });
      const changed = engine.update('users', { age: 99 }, { name: 'A' });
      expect(changed).toBe(1);
      expect(engine.select('users')[0].age).toBe(99);
    });

    it('update() no match', () => {
      expect(engine.update('users', { age: 99 }, { name: 'nope' })).toBe(0);
    });

    it('delete() removes rows', () => {
      engine.insert('users', { name: 'A', age: 1 });
      const changed = engine.delete('users', { name: 'A' });
      expect(changed).toBe(1);
      expect(engine.select('users').length).toBe(0);
    });

    it('delete() no match', () => {
      expect(engine.delete('users', { name: 'nope' })).toBe(0);
    });
  });

  describe('WAL mode', () => {
    it('enabled after open', () => {
      engine.open(PASSPHRASE);
      const result = engine.execute('PRAGMA journal_mode');
      expect(result.rows[0].journal_mode).toBe('wal');
    });
  });

  describe('operation count', () => {
    it('increments on each execute', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE cnt (id INTEGER PRIMARY KEY)');
      expect(engine.operationCount).toBe(1);
      engine.execute('INSERT INTO cnt DEFAULT VALUES');
      expect(engine.operationCount).toBe(2);
    });

    it('resetOperationCount resets to zero', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE cnt (id INTEGER PRIMARY KEY)');
      engine.resetOperationCount();
      expect(engine.operationCount).toBe(0);
    });
  });

  describe('performance', () => {
    it('single insert < 1ms p95', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE perf (id INTEGER PRIMARY KEY, val TEXT)');
      const times: number[] = [];
      for (let i = 0; i < 100; i++) {
        const s = performance.now();
        engine.execute('INSERT INTO perf (val) VALUES (?)', [`v${i}`]);
        times.push(performance.now() - s);
      }
      times.sort((a, b) => a - b);
      expect(times[94]).toBeLessThan(1);
    });

    it('single select < 1ms p95', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE perf (id INTEGER PRIMARY KEY, val TEXT)');
      for (let i = 0; i < 100; i++) {
        engine.execute('INSERT INTO perf (val) VALUES (?)', [`v${i}`]);
      }
      const times: number[] = [];
      for (let i = 0; i < 100; i++) {
        const s = performance.now();
        engine.execute('SELECT * FROM perf WHERE id = ?', [i + 1]);
        times.push(performance.now() - s);
      }
      times.sort((a, b) => a - b);
      expect(times[94]).toBeLessThan(1);
    });

    it('10k inserts < 2s', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE bulk (id INTEGER PRIMARY KEY, val TEXT)');
      const start = performance.now();
      for (let i = 0; i < 10000; i++) {
        engine.execute('INSERT INTO bulk (val) VALUES (?)', [`v${i}`]);
      }
      expect(performance.now() - start).toBeLessThan(2000);
    });

    it('1k selects < 1s', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE bulk (id INTEGER PRIMARY KEY, val TEXT)');
      for (let i = 0; i < 1000; i++) {
        engine.execute('INSERT INTO bulk (val) VALUES (?)', [`v${i}`]);
      }
      const start = performance.now();
      for (let i = 0; i < 1000; i++) {
        engine.execute('SELECT * FROM bulk WHERE id = ?', [(i % 1000) + 1]);
      }
      expect(performance.now() - start).toBeLessThan(1000);
    });

    it('open existing DB < 50ms', () => {
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE warm (id INTEGER PRIMARY KEY)');
      engine.close();
      const start = performance.now();
      engine.open(PASSPHRASE);
      expect(performance.now() - start).toBeLessThan(50);
    });
  });
});
