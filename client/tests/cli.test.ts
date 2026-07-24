import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const TEST_DB_DIR = path.join(os.tmpdir(), `test-cli-${Date.now()}`);
const TEST_DB = path.join(TEST_DB_DIR, 'testdb.sqlcipher');
const PASSPHRASE = 'cli-test-pass';
const CLI = path.join(__dirname, '..', 'src', 'cli.ts');

function run(args: string, env?: Record<string, string>): string {
  const result = execSync(
    `npx tsx ${CLI} ${args}`,
    {
      env: { ...process.env, PARADOX_PASSPHRASE: PASSPHRASE, ...env },
      cwd: path.join(__dirname, '..'),
      encoding: 'utf-8',
      timeout: 10000,
    }
  );
  return result;
}

beforeEach(() => {
  if (!fs.existsSync(TEST_DB_DIR)) fs.mkdirSync(TEST_DB_DIR, { recursive: true });
});

afterEach(() => {
  try { fs.rmSync(TEST_DB_DIR, { recursive: true, force: true }); } catch {}
  try { fs.rmSync(path.join(TEST_DB_DIR, '..'), { recursive: true, force: true }); } catch {}
});

describe('tgdb CLI', () => {
  it('--help shows usage', () => {
    const out = run('--help');
    expect(out).toContain('tgdb');
    expect(out).toContain('init');
    expect(out).toContain('select');
    expect(out).toContain('sync');
  });

  it('--version shows version', () => {
    const out = run('--version');
    expect(out.trim()).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it('init creates database', () => {
    const out = run(`init clitest --json`, { PARADOX_DB_PATH: TEST_DB });
    expect(out).toContain('"status": "created"');
  });

  it('init fails if db exists', () => {
    run(`init clitest2`, { PARADOX_DB_PATH: TEST_DB });
    try {
      run(`init clitest2`, { PARADOX_DB_PATH: TEST_DB });
      fail('Should have thrown');
    } catch (e: any) {
      expect(e.stderr || e.message).toContain('already exists');
    }
  });

  it('exec runs SQL', () => {
    run(`init execdb`, { PARADOX_DB_PATH: TEST_DB });
    const out = run(`exec "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"`, { PARADOX_DB_PATH: TEST_DB });
    expect(out).toContain('changes');
  });

  it('insert adds row', () => {
    run(`init insertdb`, { PARADOX_DB_PATH: TEST_DB });
    run(`exec "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"`, { PARADOX_DB_PATH: TEST_DB });
    const out = run(`insert users '{"name":"Alice"}' --json`, { PARADOX_DB_PATH: TEST_DB });
    expect(out).toContain('inserted_id');
  });

  it('select returns rows', () => {
    run(`init selectdb`, { PARADOX_DB_PATH: TEST_DB });
    run(`exec "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"`, { PARADOX_DB_PATH: TEST_DB });
    run(`insert users '{"name":"Bob"}'`, { PARADOX_DB_PATH: TEST_DB });
    const out = run(`select users --json`, { PARADOX_DB_PATH: TEST_DB });
    expect(out).toContain('Bob');
  });

  it('status returns object', async () => {
    const out = run(`status --json`, { PARADOX_DB_PATH: TEST_DB });
    expect(() => JSON.parse(out)).not.toThrow();
  });

  it('unknown command exits with error', () => {
    try {
      run('nonexistent');
      fail('Should have thrown');
    } catch (e: any) {
      expect(e.status).not.toBe(0);
    }
  });
});
