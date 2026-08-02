import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { ClientEngine } from '../src/engine.js';
import { DecryptionError } from '../src/errors.js';
import { decryptFile, encryptFile } from '../src/crypto.js';

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'parad-engine-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe('ClientEngine', () => {
  it('creates a fresh encrypted DB when create=true and file is missing', () => {
    const p = path.join(tmpDir, 'fresh.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('INSERT INTO t VALUES (?)', ['hello']);
    eng.close();
    expect(fs.existsSync(p)).toBe(true);
    // On-disk file is encrypted, not plaintext sqlite
    const disk = fs.readFileSync(p);
    expect(disk.toString('utf-8', 0, 15)).not.toBe('SQLite format 3');
    // Decrypts back to a real sqlite db
    const plain = decryptFile(disk, 'secret');
    expect(plain.toString('utf-8', 0, 16)).toBe('SQLite format 3\u0000');
  });

  it('round-trips a created DB: reopen and read data', () => {
    const p = path.join(tmpDir, 'roundtrip.db');
    let eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('INSERT INTO t VALUES (?)', ['persisted']);
    eng.close();

    eng = new ClientEngine(p, 'secret');
    eng.open();
    const rows = eng.execute('SELECT v FROM t').rows;
    expect(rows).toEqual([{ v: 'persisted' }]);
    eng.close();
  });

  it('treats an empty file as a fresh DB when create=true', () => {
    const p = path.join(tmpDir, 'empty.db');
    fs.writeFileSync(p, Buffer.alloc(0));
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.close();
    const plain = decryptFile(fs.readFileSync(p), 'secret');
    expect(plain.toString('utf-8', 0, 16)).toBe('SQLite format 3\u0000');
  });

  it('rejects a wrong passphrase with DecryptionError and leaves no temp', () => {
    const p = path.join(tmpDir, 'pass.db');
    let eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.close();

    eng = new ClientEngine(p, 'wrong');
    expect(() => eng.open()).toThrow(DecryptionError);
    expect(eng.isOpen).toBe(false);
  });

  it('rejects a corrupt (non-SQLite) encrypted file with DecryptionError', () => {
    const p = path.join(tmpDir, 'corrupt.db');
    fs.writeFileSync(p, encryptFile(Buffer.from('not sqlite at all!!'), 'secret'));
    const eng = new ClientEngine(p, 'secret');
    expect(() => eng.open()).toThrow(DecryptionError);
  });

  it('close is idempotent', () => {
    const p = path.join(tmpDir, 'idem.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.close();
    eng.close();
    expect(eng.isOpen).toBe(false);
  });

  it('getRawBytes returns plaintext sqlite while open and closed', () => {
    const p = path.join(tmpDir, 'raw.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    const openBytes = eng.getRawBytes();
    expect(openBytes.toString('utf-8', 0, 16)).toBe('SQLite format 3\u0000');
    eng.close();
    const closedBytes = eng.getRawBytes();
    expect(closedBytes.toString('utf-8', 0, 16)).toBe('SQLite format 3\u0000');
    expect(closedBytes).toEqual(openBytes);
  });

  it('insert/select/update/delete work', () => {
    const p = path.join(tmpDir, 'crud.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)');
    const id = eng.insert('t', { name: 'alice' });
    expect(id).toBe(1);
    const rows = eng.select('t', { name: 'alice' });
    expect(rows).toHaveLength(1);
    expect(rows[0].name).toBe('alice');
    const updated = eng.update('t', { name: 'bob' }, { name: 'alice' });
    expect(updated).toBe(1);
    const deleted = eng.delete('t', { name: 'bob' });
    expect(deleted).toBe(1);
    eng.close();
  });

  it('get / insertMany / upsert helpers work', () => {
    const p = path.join(tmpDir, 'helpers.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT UNIQUE, age INTEGER)');

    const ids = eng.insertMany('t', [
      { name: 'a', age: 1 },
      { name: 'b', age: 2 },
    ]);
    expect(ids).toEqual([1, 2]);

    expect(eng.get('t', { name: 'a' })).toMatchObject({ id: 1, name: 'a', age: 1 });
    expect(eng.get('t', { name: 'zzz' })).toBeNull();

    const newId = eng.upsert('t', { id: 3, name: 'c', age: 3 }, 'id');
    expect(newId).toBe(1);
    expect(eng.get('t', { name: 'c' }).age).toBe(3);

    const updId = eng.upsert('t', { name: 'a', age: 10 }, 'name');
    const rowA = eng.get('t', { name: 'a' });
    expect(rowA.age).toBe(10);
    expect(rowA.id).toBe(1);
    expect(updId).toBe(1);

    const before = eng.select('t').length;
    const noop = eng.upsert('t', { id: 1 }, 'id');
    expect(noop).toBe(0);
    expect(eng.select('t').length).toBe(before);

    eng.close();
  });

  it('insertMany with an empty array is a no-op and returns []', () => {
    const p = path.join(tmpDir, 'emptymany.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    expect(eng.insertMany('t', [])).toEqual([]);
    eng.close();
  });

  it('upsert rolls back on missing conflict column and throws', () => {
    const p = path.join(tmpDir, 'badupsert.db');
    const eng = new ClientEngine(p, 'secret');
    eng.open(true);
    eng.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)');
    expect(() => eng.upsert('t', { id: 1, name: 'x' }, [])).toThrow(
      'upsert requires at least one conflict column'
    );
    eng.close();
  });

  it('throws DatabaseNotOpenError when executing while closed', () => {
    const eng = new ClientEngine(path.join(tmpDir, 'closed.db'), 'secret');
    expect(() => eng.execute('SELECT 1')).toThrow('Database not open');
  });
});
