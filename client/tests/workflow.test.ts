import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as http from 'node:http';
import * as os from 'node:os';
import * as path from 'node:path';
import { connect, dbStateKey } from '../src/connection.js';
import { ClientEngine } from '../src/engine.js';
import * as state from '../src/state.js';
import { configDir } from '../src/config.js';
import { encryptFile } from '../src/crypto.js';

// ── in-process mock gateway (mirrors the live contract) ─────────

interface MockDb {
  id: string;
  name: string;
  project_id: string;
  versions: Map<number, Buffer>;
  latest: number;
}

class MockStore {
  projects: { id: string; name: string }[] = [];
  databases: MockDb[] = [];
  loginCalls = 0;
  uploadCalls = 0;
  private p = 0;
  private d = 0;

  projectByName(name: string) {
    return this.projects.find((x) => x.name === name);
  }
  createProject(name: string) {
    const p = { id: `p-${++this.p}`, name };
    this.projects.push(p);
    return p;
  }
  dbById(id: string) {
    return this.databases.find((x) => x.id === id);
  }
  dbByName(name: string) {
    return this.databases.find((x) => x.name === name);
  }
  createDatabase(projectId: string, name: string) {
    const d: MockDb = {
      id: `d-${++this.d}`,
      name,
      project_id: projectId,
      versions: new Map(),
      latest: 0,
    };
    this.databases.push(d);
    return d;
  }
}

class MockGateway {
  store: MockStore;
  server: http.Server | null = null;
  port = 0;
  down = false;

  constructor(store: MockStore) {
    this.store = store;
  }

  baseUrl(): string {
    return `http://127.0.0.1:${this.port}/v1`;
  }

  async start(port?: number): Promise<string> {
    this.down = false;
    this.server = http.createServer((req, res) => {
      if (this.down) {
        res.writeHead(503, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ detail: 'offline' }));
        return;
      }
      this.handle(req, res);
    });
    await new Promise<void>((resolve) => this.server!.listen(port || 0, '127.0.0.1', resolve));
    this.port = (this.server!.address() as any).port;
    return this.baseUrl();
  }

  stop(): void {
    if (this.server) {
      this.server.close();
      this.server = null;
    }
  }

  private send(res: http.ServerResponse, code: number, body: unknown, headers: Record<string, string> = {}): void {
    let data: Buffer;
    let ctype = 'application/json';
    if (Buffer.isBuffer(body)) {
      data = body;
      ctype = 'application/octet-stream';
    } else {
      data = Buffer.from(JSON.stringify(body));
    }
    res.writeHead(code, { 'Content-Type': ctype, 'Content-Length': data.length, ...headers });
    res.end(data);
  }

  private handle(req: http.IncomingMessage, res: http.ServerResponse): void {
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    let p = url.pathname;
    if (p.startsWith('/v1')) p = p.slice(3) || '/';
    const method = req.method || 'GET';
    const s = this.store;

    let body: any = {};
    if (method === 'POST') {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => {
        try {
          body = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
        } catch {
          body = {};
        }
        this.route(method, p, url, body, res);
      });
      return;
    }
    this.route(method, p, url, body, res);
  }

  private route(method: string, p: string, url: URL, body: any, res: http.ServerResponse): void {
    const s = this.store;
    if (method === 'GET' && p === '/auth/me') {
      this.send(res, 200, { id: 'u1', username: 'alice', email: 'alice@example.com' });
      return;
    }
    if (method === 'POST' && p === '/auth/login') {
      s.loginCalls += 1;
      this.send(res, 200, { access_token: 'tok-abc', token_type: 'bearer' });
      return;
    }
    if (method === 'POST' && p === '/auth/register') {
      this.send(res, 200, { access_token: 'tok-abc', user_id: 'u1' });
      return;
    }
    if (method === 'GET' && p === '/projects') {
      this.send(res, 200, s.projects);
      return;
    }
    if (method === 'POST' && p === '/projects') {
      this.send(res, 201, s.createProject(body.name));
      return;
    }
    if (method === 'GET' && p.match(/^\/projects\/[^/]+\/databases$/)) {
      const pid = p.split('/')[2];
      this.send(res, 200, s.databases.filter((d) => d.project_id === pid).map((d) => ({ id: d.id, name: d.name })));
      return;
    }
    if (method === 'POST' && p.match(/^\/projects\/[^/]+\/databases$/)) {
      const pid = p.split('/')[2];
      this.send(res, 201, s.createDatabase(pid, body.name));
      return;
    }
    if (method === 'GET' && p === '/status') {
      this.send(res, 200, {
        user_id: 'u1',
        databases: s.databases.map((d) => ({ name: d.name, latest_version: d.latest, latest_message_id: `m${d.latest}` })),
      });
      return;
    }
    if (method === 'GET' && p === '/download') {
      const db = url.searchParams.get('database_id') ? s.dbById(url.searchParams.get('database_id')!) : s.dbByName(url.searchParams.get('database_name') || '');
      if (!db) {
        this.send(res, 404, { detail: 'not found' });
        return;
      }
      const ver = Number(url.searchParams.get('version') || db.latest);
      const payload = db.versions.get(ver);
      if (!payload) {
        this.send(res, 404, { detail: 'version not found' });
        return;
      }
      this.send(res, 200, payload, { 'x-version': String(ver), 'x-message-id': `m${ver}` });
      return;
    }
    if (method === 'POST' && p === '/upload') {
      s.uploadCalls += 1;
      const db = body.database_id ? s.dbById(body.database_id) : s.dbByName(body.database_name || '');
      if (!db) {
        this.send(res, 404, { detail: 'database not found' });
        return;
      }
      const clientVer = Number(body.version || 0);
      if (clientVer < db.latest) {
        this.send(res, 409, { error: 'conflict_detected', remote_version: db.latest });
        return;
      }
      const payload = Buffer.from(body.file_data as string, 'base64');
      const newVer = db.latest + 1;
      db.versions.set(newVer, payload);
      db.latest = newVer;
      this.send(res, 200, {
        request_id: `r${newVer}`,
        database_id: db.id,
        message_id: `m${newVer}`,
        version: newVer,
        uploaded_at: new Date().toISOString(),
      });
      return;
    }
    this.send(res, 404, { detail: 'no route' });
  }
}

// ── helpers ─────────────────────────────────────────────────────

import * as crypto from 'node:crypto';

function sha(b: Buffer): string {
  return crypto.createHash('sha256').update(b).digest('hex');
}

function makeSqliteBytes(passphrase: string, rows: string[]): Buffer {
  const eng = new ClientEngine(path.join(os.tmpdir(), `mk-${Date.now()}-${Math.random().toString(36).slice(2)}.db`), passphrase);
  eng.open(true);
  eng.execute('CREATE TABLE IF NOT EXISTS t (v TEXT)');
  for (const r of rows) eng.execute('INSERT INTO t VALUES (?)', [r]);
  const blob = eng.getRawBytes();
  eng.close();
  fs.rmSync(eng.dbPath, { force: true });
  return blob;
}

function waitFor(pred: () => boolean, timeoutMs = 20000, step = 50): Promise<boolean> {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    const tick = () => {
      if (pred()) return resolve(true);
      if (Date.now() > deadline) return resolve(false);
      setTimeout(tick, step);
    };
    tick();
  });
}

let tmpHome: string;
const saved = process.env.PARADOX_HOME;

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'parad-workflow-'));
  process.env.PARADOX_HOME = tmpHome;
});

afterEach(() => {
  if (saved === undefined) delete process.env.PARADOX_HOME;
  else process.env.PARADOX_HOME = saved;
  fs.rmSync(tmpHome, { recursive: true, force: true });
});

const PASSPHRASE = 'secret';

describe('workflow T1: connect(email:password) auto-login + provisioning', () => {
  it('logs in, provisions project+db, persists project-scoped state', async () => {
    const store = new MockStore();
    const gw = new MockGateway(store);
    const base = await gw.start();
    const url = `parad://alice@example.com:secretpw@local/myproj/mydb?passphrase=${PASSPHRASE}&gateway=${base}`;

    const conn = await connect(url);
    expect(store.loginCalls).toBeGreaterThanOrEqual(1);
    expect(store.projectByName('myproj')).toBeTruthy();
    expect(store.dbByName('mydb')).toBeTruthy();
    expect(conn.dbKey).toBe('myproj/mydb');

    const key = dbStateKey('mydb', 'myproj');
    state.markDirty(key);
    const stateFile = path.join(configDir(), `${state.sanitizeStateKey(key)}.sync.json`);
    expect(fs.existsSync(stateFile)).toBe(true);
    expect(fs.existsSync(path.join(configDir(), 'mydb.sync.json'))).toBe(false);

    conn.close();
    gw.stop();
  });
});

describe('workflow T2: offline -> recovery batch push (one push = one version)', () => {
  it('goes offline, marks dirty, then pushes one new version on reconnect', async () => {
    const store = new MockStore();
    const gw = new MockGateway(store);
    const base = await gw.start();
    const url = `parad://tok-abc@local/offproj/offdb?passphrase=${PASSPHRASE}&gateway=${base}`;

    const conn = await connect({ url, pushIntervalMs: 200, pullIntervalMs: 1000 });
    const daemon = conn.daemon!;
    const key = conn.dbKey;

    expect(await waitFor(() => store.dbByName('offdb')?.latest >= 1)).toBe(true);

    gw.stop();
    conn.execute('CREATE TABLE IF NOT EXISTS t (v TEXT)');
    conn.execute('INSERT INTO t VALUES (?)', ['offline-change']);

    expect(await waitFor(() => daemon.offline === true, 10000)).toBe(true);
    expect(state.isOffline(key)).toBe(true);
    expect(state.isDirty(key)).toBe(true);
    expect(daemon.lastError).toBeTruthy();

    const uploadsBefore = store.uploadCalls;
    await gw.start(gw.port);

    const recovered = await waitFor(
      () => daemon.offline === false && store.dbByName('offdb')?.latest === 2,
    );
    expect(recovered).toBe(true);

    const remote = store.dbByName('offdb')!.versions.get(2)!.toString('utf-8');
    expect(remote).toContain('offline-change');
    expect(store.uploadCalls - uploadsBefore).toBe(1);
    expect(state.isOffline(key)).toBe(false);

    conn.close();
    gw.stop();
  });
});

describe('workflow T3: 409 conflict -> local-wins', () => {
  it('pull + re-push local bytes, loser version preserved, no spurious push', async () => {
    const store = new MockStore();
    const gw = new MockGateway(store);
    await gw.start();
    const proj = store.createProject('confproj');
    const db = store.createDatabase(proj.id, 'conflict');
    const remoteBytes = makeSqliteBytes(PASSPHRASE, ['remote-data']);
    db.versions.set(1, remoteBytes);
    db.latest = 1;

    const localPath = path.join(tmpHome, 'conflict_local.db');
    const localBytes = makeSqliteBytes(PASSPHRASE, ['local-data']);
    fs.writeFileSync(localPath, encryptFile(localBytes, PASSPHRASE));

    const conn = await connect({
      dbPath: localPath,
      name: 'conflict',
      project: 'confproj',
      passphrase: PASSPHRASE,
      gatewayUrl: gw.baseUrl(),
      apiKey: 'tok-abc',
      autoSync: false,
    });
    const key = conn.dbKey;
    const pushResult = await conn.push();
    expect(pushResult).not.toBeNull();

    expect(store.dbByName('conflict')!.latest).toBe(2);
    expect(store.dbByName('conflict')!.versions.get(2)!.equals(localBytes)).toBe(true);
    expect(store.dbByName('conflict')!.versions.get(1)!.equals(remoteBytes)).toBe(true);
    expect(state.isOffline(key)).toBe(false);
    expect(state.getLastLocalHash(key)).toBe(sha(localBytes));

    conn.close();
    gw.stop();
  });
});

describe('workflow T4: manual push / pull interop', () => {
  it('push after change creates a new version and pull restores it', async () => {
    const store = new MockStore();
    const gw = new MockGateway(store);
    await gw.start();
    const proj = store.createProject('p');
    store.createDatabase(proj.id, 'manual');

    const conn = await connect({
      dbPath: path.join(tmpHome, 'manual.db'),
      name: 'manual',
      project: 'p',
      passphrase: PASSPHRASE,
      gatewayUrl: gw.baseUrl(),
      apiKey: 'tok-abc',
      autoSync: false,
    });
    conn.execute('CREATE TABLE t (v TEXT)');
    conn.execute('INSERT INTO t VALUES (?)', ['one']);
    const v1 = await conn.push();
    expect(v1).toBe(1);
    conn.execute('INSERT INTO t VALUES (?)', ['two']);
    const v2 = await conn.push();
    expect(v2).toBe(2);
    expect(store.dbByName('manual')!.latest).toBe(2);

    // pull version 1 -> local file reverts to first snapshot
    const pulled = await conn.pullVersion(1);
    expect(pulled).toBe(true);
    const rows = conn.execute('SELECT v FROM t').rows;
    expect(rows).toEqual([{ v: 'one' }]);

    conn.close();
    gw.stop();
  });
});
