import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { ClientEngine } from './engine.js';
import { GatewayClient, GatewayError, isConnectivityError } from './gateway.js';
import * as syncState from './state.js';
import { configDir, loadConfig, saveConfig } from './config.js';
import { encryptFile } from './crypto.js';
import { DecryptionError } from './errors.js';

export interface ParsedUrl {
  name: string;
  project: string | null;
  passphrase: string;
  gateway_url: string;
  token: string;
  email: string;
  password: string;
}

// ── URL helpers ─────────────────────────────────────────────────

export function parseUrl(url: string): ParsedUrl {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error(`Invalid URL: ${url}`);
  }
  if (parsed.protocol !== 'parad:' && parsed.protocol !== 'paradox:') {
    throw new Error(`Unsupported URL scheme: ${parsed.protocol}`);
  }

  const qs = parsed.searchParams;
  const passphrase = qs.get('passphrase') || '';
  const gatewayUrl = qs.get('gateway') || '';
  const token = qs.get('token') || '';

  let userToken = token;
  let userEmail = '';
  let userPassword = '';
  if (parsed.username !== '') {
    const username = decodeURIComponent(parsed.username);
    const pw = parsed.password ? decodeURIComponent(parsed.password) : '';
    if (pw || username.includes('@')) {
      userEmail = username;
      userPassword = pw;
    } else if (!userToken) {
      userToken = username;
    }
  }

  const parts = parsed.pathname.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean);
  const name = parts.length ? parts[parts.length - 1] : '';
  if (!name) {
    throw new Error('URL must contain a database name in the path');
  }
  const project = parts.length > 1 ? parts.slice(0, -1).join('/') : null;

  return {
    name,
    project,
    passphrase,
    gateway_url: gatewayUrl,
    token: userToken,
    email: userEmail,
    password: userPassword,
  };
}

export function generateUrl(
  name: string,
  passphrase = '',
  gatewayUrl = '',
  project: string | null = null,
  token = '',
  email = '',
  password = '',
): string {
  let userinfo = '';
  if (email && password) {
    userinfo = `${encodeURIComponent(email).replace(/%40/g, '@')}:${encodeURIComponent(password)}@`;
  } else if (token) {
    userinfo = `${encodeURIComponent(token)}@`;
  }
  const pathname = project ? `local/${project}/${name}` : `local/${name}`;
  let url = `parad://${userinfo}${pathname}`;
  const qs: string[] = [];
  if (passphrase) qs.push(`passphrase=${encodeURIComponent(passphrase)}`);
  if (gatewayUrl) qs.push(`gateway=${encodeURIComponent(gatewayUrl).replace(/%3A/g, ':').replace(/%2F/g, '/')}`);
  if (token && email && password) qs.push(`token=${encodeURIComponent(token)}`);
  if (qs.length) url += `?${qs.join('&')}`;
  return url;
}

export function dbStateKey(name: string, project: string | null = null): string {
  if (project) return `${project}/${name}`;
  return name;
}

// ── Sync daemon ─────────────────────────────────────────────────

export interface SyncDaemonOptions {
  engine: ClientEngine;
  dbName: string;
  gatewayUrl: string;
  apiKey?: string;
  project?: string | null;
  databaseId?: string;
  projectId?: string;
  pushIntervalMs?: number;
  pullIntervalMs?: number;
}

export class SyncDaemon {
  PUSH_INTERVAL = 2000;
  PULL_INTERVAL = 30_000;

  private engine: ClientEngine;
  private dbName: string;
  private dbKey: string;
  private databaseId: string;
  private projectId: string;
  private gateway: GatewayClient;
  private timer: ReturnType<typeof setInterval> | null = null;
  lastSync: number | null = null;

  private _offline: boolean;
  private _consecutiveFailures = 0;
  private _lastError: string | null = null;

  constructor(opts: SyncDaemonOptions) {
    this.engine = opts.engine;
    this.dbName = opts.dbName;
    this.dbKey = dbStateKey(opts.dbName, opts.project || null);
    this.databaseId = opts.databaseId || '';
    this.projectId = opts.projectId || '';
    this.gateway = new GatewayClient(opts.gatewayUrl, opts.apiKey || '');
    if (opts.pushIntervalMs) this.PUSH_INTERVAL = opts.pushIntervalMs;
    if (opts.pullIntervalMs) this.PULL_INTERVAL = opts.pullIntervalMs;
    this._offline = Boolean(syncState.isOffline(this.dbKey));
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this._tick().catch(() => {
        // never crash the host
      });
    }, 500);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  get isRunning(): boolean {
    return this.timer !== null;
  }

  get offline(): boolean {
    return this._offline;
  }

  get consecutiveFailures(): number {
    return this._consecutiveFailures;
  }

  get lastError(): string | null {
    return this._lastError;
  }

  private _onSuccess(): void {
    const wasOffline = this._offline;
    this._offline = false;
    this._consecutiveFailures = 0;
    this._lastError = null;
    syncState.setOffline(this.dbKey, false);
    if (wasOffline) {
      console.info(`Sync back online for ${this.dbKey} — pushing pending changes`);
    }
  }

  private _onFailure(exc: unknown, conflict = false): void {
    if (conflict) return;
    this._lastError = exc instanceof Error ? exc.message : String(exc);
    if (isConnectivityError(exc)) {
      this._consecutiveFailures += 1;
      const wasOffline = this._offline;
      this._offline = true;
      syncState.setOffline(this.dbKey, true);
      syncState.markDirty(this.dbKey);
      if (!wasOffline) {
        console.warn(`Sync offline for ${this.dbKey}: ${this._lastError}`);
      }
    }
  }

  private async _maybePush(): Promise<void> {
    let currentHash: string | null = null;
    try {
      const raw = this.engine.getRawBytes();
      currentHash = crypto.createHash('sha256').update(raw).digest('hex');
    } catch {
      return;
    }
    const lastHash = syncState.getLastLocalHash(this.dbKey);
    if (currentHash === lastHash) return;
    await this._push(currentHash);
  }

  private async _push(currentHash: string): Promise<void> {
    let raw: Buffer;
    try {
      raw = this.engine.getRawBytes();
    } catch {
      return;
    }
    let remoteVer = syncState.getRemoteVersion(this.dbKey) || 0;
    try {
      const resp = await this.gateway.upload({
        database_name: this.dbName,
        database_id: this.databaseId,
        project_id: this.projectId,
        file_bytes: raw,
        version: remoteVer,
      });
      this._onSuccess();
      syncState.setRemoteVersion(this.dbKey, resp.version, currentHash);
      syncState.setLastLocalHash(this.dbKey, currentHash);
      syncState.clearDirty(this.dbKey);
      this.lastSync = Date.now();
      console.info(`Pushed ${this.dbKey} v${resp.version} (msg=${resp.message_id})`);
    } catch (exc) {
      if (exc instanceof GatewayError && exc.statusCode === 409) {
        // LOCAL-WINS: capture our bytes, pull the remote snapshot
        // (advances the base version), then re-push OUR bytes as a
        // brand new version. Local writes are never silently lost.
        console.info(`Conflict (409) on ${this.dbKey} — pulling remote, re-pushing local (local-wins)`);
        try {
          await this.pull();
          remoteVer = syncState.getRemoteVersion(this.dbKey) || 0;
          const resp = await this.gateway.upload({
            database_name: this.dbName,
            database_id: this.databaseId,
            project_id: this.projectId,
            file_bytes: raw,
            version: remoteVer,
          });
          syncState.setRemoteVersion(this.dbKey, resp.version, currentHash);
          syncState.setLastLocalHash(this.dbKey, currentHash);
          syncState.clearDirty(this.dbKey);
          this.lastSync = Date.now();
          this._onSuccess();
          console.info(`Pushed ${this.dbKey} v${resp.version} (msg=${resp.message_id})`);
        } catch (innerExc) {
          this._onFailure(innerExc);
        }
        return;
      }
      this._onFailure(exc);
    }
  }

  async pull(): Promise<boolean> {
    try {
      const result = await this.gateway.download(this.dbName, undefined, this.databaseId, this.projectId);
      if (!result.bytes || result.bytes.length === 0) return false;
      const remoteHash = crypto.createHash('sha256').update(result.bytes).digest('hex');
      let currentHash = '';
      try {
        currentHash = crypto.createHash('sha256').update(this.engine.getRawBytes()).digest('hex');
      } catch {
        // ignore
      }
      if (remoteHash === currentHash) return false;
      // close FIRST so the current in-memory state cannot re-encrypt over the new file
      this.engine.close();
      const encrypted = encryptFile(result.bytes, this.engine.passphrase);
      fs.mkdirSync(path.dirname(this.engine.dbPath), { recursive: true });
      fs.writeFileSync(this.engine.dbPath, encrypted);
      await this.engine.open();
      if (result.version !== null && result.version !== undefined) {
        syncState.setRemoteVersion(this.dbKey, result.version, remoteHash);
      }
      syncState.setLastLocalHash(this.dbKey, remoteHash);
      syncState.clearDirty(this.dbKey);
      return true;
    } catch (exc) {
      this._onFailure(exc);
      return false;
    }
  }

  private _pushCounter = 0;
  private _pullCounter = 0;

  private async _tick(): Promise<void> {
    this._pushCounter += 500;
    this._pullCounter += 500;
    if (this._pushCounter >= this.PUSH_INTERVAL) {
      this._pushCounter = 0;
      await this._maybePush();
    }
    if (this._pullCounter >= this.PULL_INTERVAL) {
      this._pullCounter = 0;
      await this.pull();
    }
  }
}

// ── Connection ──────────────────────────────────────────────────

export class ParadConnection {
  engine: ClientEngine;
  private passphrase: string;
  private gatewayUrl: string;
  private apiKey: string;
  private project: string | null;
  private databaseId: string;
  private projectId: string;
  private dbName: string;
  private _dbKey: string;
  private _daemon: SyncDaemon | null = null;
  private _autoSync = false;
  private _pullOnStartup = false;
  private _pushIntervalMs?: number;
  private _pullIntervalMs?: number;

  constructor(opts: {
    dbPath: string;
    passphrase: string;
    gatewayUrl: string;
    apiKey?: string;
    autoSync?: boolean;
    project?: string | null;
    databaseId?: string;
    projectId?: string;
    pullOnStartup?: boolean;
    pushIntervalMs?: number;
    pullIntervalMs?: number;
  }) {
    this.passphrase = opts.passphrase;
    this.gatewayUrl = opts.gatewayUrl;
    this.apiKey = opts.apiKey || '';
    this.project = opts.project || null;
    this.databaseId = opts.databaseId || '';
    this.projectId = opts.projectId || '';
    this.dbName = opts.gatewayUrl ? path.basename(opts.dbPath).replace(/\.db$/, '') : '';
    this._dbKey = dbStateKey(this.dbName, this.project);

    this.engine = new ClientEngine(opts.dbPath, opts.passphrase);
    this._autoSync = opts.autoSync || false;
    this._pullOnStartup = opts.pullOnStartup || false;
    this._pushIntervalMs = opts.pushIntervalMs;
    this._pullIntervalMs = opts.pullIntervalMs;
  }

  async init(): Promise<void> {
    await this.engine.open(true);
    if (this._autoSync && this.gatewayUrl) {
      this._daemon = new SyncDaemon({
        engine: this.engine,
        dbName: this.dbName,
        gatewayUrl: this.gatewayUrl,
        apiKey: this.apiKey,
        project: this.project,
        databaseId: this.databaseId,
        projectId: this.projectId,
        pushIntervalMs: this._pushIntervalMs,
        pullIntervalMs: this._pullIntervalMs,
      });
      if (this._pullOnStartup) {
        try {
          await this.pull();
        } catch {
          // pull_on_startup failure is non-fatal
        }
      }
      this._daemon.start();
    }
  }

  get daemon(): SyncDaemon | null {
    return this._daemon;
  }

  get isConnected(): boolean {
    return this.engine.isOpen;
  }

  get dbKey(): string {
    return this._dbKey;
  }

  execute(sql: string, params?: any[]): { rows: any[]; changes: number; lastInsertRowid: number } {
    return this.engine.execute(sql, params);
  }

  commit(): void {
    // each statement auto-commits; no-op for API parity
  }

  rollback(): void {
    // no open transaction persists across run() calls
  }

  close(): void {
    if (this._daemon) {
      this._daemon.stop();
      this._daemon = null;
    }
    this.engine.close();
  }

  push(): Promise<number | null> {
    if (!this.gatewayUrl) return Promise.resolve(null);
    const gw = new GatewayClient(this.gatewayUrl, this.apiKey);
    return this._pushManual(gw);
  }

  private async _pushManual(gw: GatewayClient): Promise<number | null> {
    const raw = this.engine.getRawBytes();
    const remoteVer = syncState.getRemoteVersion(this.dbKey) || 0;
    try {
      const resp = await gw.upload({
        database_name: this.dbName,
        database_id: this.databaseId,
        project_id: this.projectId,
        file_bytes: raw,
        version: remoteVer,
      });
      const currentHash = crypto.createHash('sha256').update(raw).digest('hex');
      syncState.setRemoteVersion(this.dbKey, resp.version, currentHash);
      syncState.setLastLocalHash(this.dbKey, currentHash);
      syncState.clearDirty(this.dbKey);
      return resp.version;
    } catch (exc) {
      if (exc instanceof GatewayError && exc.statusCode === 409) {
        const localRaw = raw;
        await this.pull();
        const newRemoteVer = syncState.getRemoteVersion(this.dbKey) || 0;
        const resp = await gw.upload({
          database_name: this.dbName,
          database_id: this.databaseId,
          project_id: this.projectId,
          file_bytes: localRaw,
          version: newRemoteVer,
        });
        const currentHash = crypto.createHash('sha256').update(localRaw).digest('hex');
        syncState.setRemoteVersion(this.dbKey, resp.version, currentHash);
        syncState.setLastLocalHash(this.dbKey, currentHash);
        syncState.clearDirty(this.dbKey);
        return resp.version;
      }
      throw exc;
    }
  }

  async pull(): Promise<boolean> {
    if (!this.gatewayUrl) return false;
    const gw = new GatewayClient(this.gatewayUrl, this.apiKey);
    const result = await gw.download(this.dbName, undefined, this.databaseId, this.projectId);
    if (!result.bytes || result.bytes.length === 0) return false;
    const remoteHash = crypto.createHash('sha256').update(result.bytes).digest('hex');
    let currentHash = '';
    try {
      currentHash = crypto.createHash('sha256').update(this.engine.getRawBytes()).digest('hex');
    } catch {
      // ignore
    }
    if (remoteHash === currentHash) return false;
    this.engine.close();
    const encrypted = encryptFile(result.bytes, this.passphrase);
    fs.mkdirSync(path.dirname(this.engine.dbPath), { recursive: true });
    fs.writeFileSync(this.engine.dbPath, encrypted);
    await this.engine.open();
    if (result.version !== null && result.version !== undefined) {
      syncState.setRemoteVersion(this.dbKey, result.version, remoteHash);
    }
    syncState.setLastLocalHash(this.dbKey, remoteHash);
    syncState.clearDirty(this.dbKey);
    return true;
  }

  async pullVersion(version: number): Promise<boolean> {
    if (!this.gatewayUrl) return false;
    const gw = new GatewayClient(this.gatewayUrl, this.apiKey);
    const result = await gw.download(this.dbName, version, this.databaseId, this.projectId);
    if (!result.bytes || result.bytes.length === 0) return false;
    const remoteHash = crypto.createHash('sha256').update(result.bytes).digest('hex');
    this.engine.close();
    const encrypted = encryptFile(result.bytes, this.passphrase);
    fs.mkdirSync(path.dirname(this.engine.dbPath), { recursive: true });
    fs.writeFileSync(this.engine.dbPath, encrypted);
    await this.engine.open();
    syncState.setRemoteVersion(this.dbKey, result.version ?? version, remoteHash);
    syncState.setLastLocalHash(this.dbKey, remoteHash);
    syncState.clearDirty(this.dbKey);
    return true;
  }
}

// ── Convenience factory ─────────────────────────────────────────

export interface ConnectOptions {
  name?: string;
  project?: string;
  passphrase?: string;
  url?: string;
  dbPath?: string;
  gatewayUrl?: string;
  apiKey?: string;
  autoSync?: boolean;
  pullOnStartup?: boolean;
  pushIntervalMs?: number;
  pullIntervalMs?: number;
}

export async function connect(opts: ConnectOptions | string): Promise<ParadConnection> {
  let options: ConnectOptions;
  if (typeof opts === 'string') {
    options = { url: opts };
  } else {
    options = opts;
  }

  const cfg = loadConfig();
  let parsedUrl: ParsedUrl | null = null;
  if (options.url) {
    parsedUrl = parseUrl(options.url);
  }

  const urlName = parsedUrl?.name || options.name || '';
  const urlProject = parsedUrl?.project || options.project || null;

  // resolve db_path
  let resolvedPath = options.dbPath || '';
  if (!resolvedPath && options.name) {
    resolvedPath = path.join(configDir(), `${options.name}.db`);
  }
  if (!resolvedPath && urlName) {
    resolvedPath = path.join(configDir(), `${urlName}.db`);
  }
  if (!resolvedPath) {
    resolvedPath = cfg.database_path;
  }

  // resolve passphrase
  let resolvedPassphrase = options.passphrase || '';
  if (!resolvedPassphrase) resolvedPassphrase = parsedUrl?.passphrase || '';
  if (!resolvedPassphrase) resolvedPassphrase = process.env.PARADOX_PASSPHRASE || '';
  if (!resolvedPassphrase) resolvedPassphrase = 'default';

  // resolve gateway
  let resolvedGateway = options.gatewayUrl || '';
  if (!resolvedGateway) resolvedGateway = parsedUrl?.gateway_url || '';
  if (!resolvedGateway) resolvedGateway = cfg.sync.gateway_url || '';

  // resolve auth
  let token = options.apiKey || '';
  if (!token) token = parsedUrl?.token || '';
  const email = parsedUrl?.email || '';
  const password = parsedUrl?.password || '';

  let resolvedApiKey = '';
  if (token) {
    resolvedApiKey = token;
  } else if (email && password) {
    if (!resolvedGateway) {
      throw new Error('email/password in URL require a gateway');
    }
    const gw = new GatewayClient(resolvedGateway);
    try {
      const result = await gw.login(email, password);
      resolvedApiKey = result.access_token;
    } catch (exc) {
      throw new Error(`Login to gateway failed: ${exc instanceof Error ? exc.message : String(exc)}`);
    }
    if (!resolvedApiKey) {
      throw new Error('Login succeeded but no token was returned');
    }
    try {
      const c = loadConfig();
      c.sync.api_key = resolvedApiKey;
      saveConfig(c);
    } catch {
      // non-fatal
    }
  } else {
    resolvedApiKey = resolvedGateway ? cfg.sync.api_key : '';
  }

  // project / database provisioning
  let projectId = '';
  let databaseId = '';
  if (resolvedGateway && urlProject) {
    const gw = new GatewayClient(resolvedGateway, resolvedApiKey);
    try {
      const proj = await gw.ensureProject(urlProject);
      projectId = proj.id || '';
      const dbs = await gw.ensureDatabase(projectId, urlName);
      databaseId = dbs.id || '';
      try {
        const c = loadConfig();
        c.database_path = resolvedPath;
        c.project_id = projectId;
        c.database_id = databaseId;
        c.project_name = urlProject;
        c.sync.gateway_url = resolvedGateway;
        c.sync.api_key = resolvedApiKey;
        saveConfig(c);
      } catch {
        // non-fatal
      }
    } catch (exc) {
      throw new Error(`Could not provision project/database on gateway: ${exc instanceof Error ? exc.message : String(exc)}`);
    }
  } else {
    try {
      const c = loadConfig();
      c.database_path = resolvedPath;
      if (resolvedGateway && resolvedApiKey) {
        c.sync.gateway_url = resolvedGateway;
        c.sync.api_key = resolvedApiKey;
      }
      saveConfig(c);
    } catch {
      // non-fatal
    }
  }

  const conn = new ParadConnection({
    dbPath: resolvedPath,
    passphrase: resolvedPassphrase,
    gatewayUrl: resolvedGateway,
    apiKey: resolvedApiKey,
    autoSync: (options.autoSync ?? true) && Boolean(resolvedGateway),
    project: urlProject,
    databaseId,
    projectId,
    pullOnStartup: (options.pullOnStartup ?? false) && Boolean(resolvedGateway),
    pushIntervalMs: options.pushIntervalMs,
    pullIntervalMs: options.pullIntervalMs,
  });
  await conn.init();
  return conn;
}
