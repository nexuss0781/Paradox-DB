import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { ClientEngine } from '../src/engine.js';
import { encryptBytes } from '../src/crypto.js';

let tmpDir: string;

function freshPath(): string {
  return path.join(tmpDir, `db-${Math.random().toString(36).slice(2)}.db`);
}

function journalPath(dbPath: string): string {
  return `${dbPath}.journal`;
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'parad-journal-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe('journal crash recovery', () => {
  it('recovers writes after a crash (no close)', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('INSERT INTO t VALUES (?)', ['hello']);
    // simulate crash: never close eng
    expect(fs.existsSync(journalPath(p))).toBe(true);

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    const rows = eng2.execute('SELECT v FROM t').rows;
    expect(rows).toEqual([{ v: 'hello' }]);
    eng2.close();
  });

  it('replay is idempotent across repeated opens', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    for (let i = 0; i < 5; i++) eng.execute('INSERT INTO t VALUES (?)', [`row${i}`]);

    let eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    expect(eng2.execute('SELECT COUNT(*) AS n FROM t').rows[0].n).toBe(5);
    eng2.close(); // checkpoint folds the journal

    eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    expect(eng2.execute('SELECT COUNT(*) AS n FROM t').rows[0].n).toBe(5);
    eng2.close();

    // journal cleared after clean close
    expect(fs.statSync(journalPath(p)).size).toBe(0);
  });

  it('drops statements that failed originally (no phantom data)', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT UNIQUE)');
    eng.execute('INSERT INTO t VALUES (1, ?)', ['kept']);
    expect(() => eng.execute('INSERT INTO t VALUES (2, ?)', ['kept'])).toThrow(); // unique violation
    // crash

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    const rows = eng2.execute('SELECT * FROM t').rows;
    expect(rows).toEqual([{ id: 1, v: 'kept' }]);
    eng2.close();
  });

  it('aborts an uncommitted transaction on crash', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('BEGIN');
    eng.execute('INSERT INTO t VALUES (?)', ['never-committed']);
    // crash before COMMIT

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    expect(eng2.execute('SELECT COUNT(*) AS n FROM t').rows[0].n).toBe(0);
    eng2.close();
  });

  it('preserves a committed transaction across a crash', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('BEGIN');
    eng.execute('INSERT INTO t VALUES (?)', ['committed']);
    eng.execute('COMMIT');
    // crash

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    const rows = eng2.execute('SELECT v FROM t').rows;
    expect(rows).toEqual([{ v: 'committed' }]);
    eng2.close();
  });

  it('round-trips typed params (null, number, blob, bool, bigint, string)', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (n REAL, s TEXT, b BLOB, bl INTEGER, bi INTEGER, nu TEXT)');
    const blob = Buffer.from([0, 1, 2, 250, 255]);
    eng.execute('INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)', [3.25, 'str', blob, true, 9007199254740992n, null]);
    // crash

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    const row = eng2.execute('SELECT * FROM t').rows[0];
    expect(row.n).toBe(3.25);
    expect(row.s).toBe('str');
    expect(Buffer.from(row.b)).toEqual(blob);
    expect(row.bl).toBe(1);
    expect(String(row.bi)).toBe('9007199254740992');
    expect(row.nu).toBeNull();
    eng2.close();
  });

  it('opens legacy bare-encrypted-SQLite snapshots', async () => {
    const p = freshPath();
    // Build a legacy file the old way: bare SQLite bytes, encrypted, no journal.
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('INSERT INTO t VALUES (?)', ['legacy']);
    const bytes = eng.getRawBytes();
    // remove journal + rewrite as bare encrypted blob (legacy layout)
    fs.rmSync(journalPath(p), { force: true });
    fs.writeFileSync(p, encryptBytes(bytes, 'secret'));

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    const rows = eng2.execute('SELECT v FROM t').rows;
    expect(rows).toEqual([{ v: 'legacy' }]);
    eng2.close();
  });

  it('survives crash-during-checkpoint: replay skips already-folded records', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)');
    eng.execute('INSERT INTO t VALUES (1, ?)', ['a']);
    // capture the journal BEFORE the checkpoint clears it
    const journal = journalPath(p);
    const savedJournal = fs.readFileSync(journal);
    eng.close(); // checkpoint folds seq 1..2 into the snapshot, clears journal

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    expect(eng2.execute('SELECT COUNT(*) AS n FROM t').rows[0].n).toBe(1);
    eng2.close();
    // simulate a crash between snapshot write and journal truncation:
    // restore the stale journal on top of the up-to-date snapshot
    fs.writeFileSync(journal, savedJournal);
    const eng3 = new ClientEngine(p, 'secret');
    await eng3.open();
    expect(eng3.execute('SELECT COUNT(*) AS n FROM t').rows[0].n).toBe(1);
    eng3.close();
  });

  it('replaceBytes resets the journal and persists pulled bytes', async () => {
    const p = freshPath();
    const eng = new ClientEngine(p, 'secret');
    await eng.open(true);
    eng.execute('CREATE TABLE t (v TEXT)');
    eng.execute('INSERT INTO t VALUES (?)', ['old']);
    // build replacement bytes: a fresh db with different content
    const engTmp = new ClientEngine(path.join(tmpDir, 'tmp-other.db'), 'secret');
    await engTmp.open(true);
    engTmp.execute('CREATE TABLE t (v TEXT)');
    engTmp.execute('INSERT INTO t VALUES (?)', ['new-remote']);
    const remoteBytes = engTmp.getRawBytes();
    engTmp.close();

    await eng.replaceBytes(remoteBytes);
    expect(eng.execute('SELECT v FROM t').rows).toEqual([{ v: 'new-remote' }]);
    expect(fs.statSync(journalPath(p)).size).toBe(0);
    eng.close();

    const eng2 = new ClientEngine(p, 'secret');
    await eng2.open();
    expect(eng2.execute('SELECT v FROM t').rows).toEqual([{ v: 'new-remote' }]);
    eng2.close();
  });
});
