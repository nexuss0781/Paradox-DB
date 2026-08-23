import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { ClientEngine } from './engine.js';
import { GatewayClient, GatewayError, isConnectivityError } from './gateway.js';
import * as syncState from './state.js';
import { configDir, loadConfig, saveConfig } from './config.js';


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

/** Remove credentials from a Parad URL before displaying it in normal CLI output. */
export function redactUrl(url: string): string {
  const parsed = new URL(url);
  parsed.username = parsed.username ? '<redacted>' : '';
  parsed.password = '';
  parsed.searchParams.delete('token');
  parsed.searchParams.delete('passphrase');
  return parsed.toString();
}

/**
 * Resolve the canonical single-value database URL.
 *
 * Precedence is explicit at the call site, then DATABASE_URL, then the
 * persisted config.database_url. Legacy split fields are used only as a
 * compatibility fallback and the reconstructed URL is persisted immediately.
 */
export function getCanonicalDatabaseUrl(name?: string): string {
  const config = loadConfig();
  const configured = process.env.DATABASE_URL?.trim() || config.database_url?.trim() || '';
  if (configured) {
    const parsed = parseUrl(configured);
    if (name && parsed.name !== name) {
      throw new Error(`Canonical DATABASE_URL points to '${parsed.name}', not '${name}'`);
    }
    return configured;
  }

  const inferredName = name || path.basename(config.database_path).replace(/\.db$/, '');
  if (!inferredName) throw new Error('No database name is configured; run parad init <name> first');
  const passphrase = process.env.PARADOX_PASSPHRASE || config.encryption.passphrase || '';
  const gatewayUrl = process.env.PARADOX_GATEWAY || config.sync.gateway_url || '';
  const apiKey = process.env.PARADOX_API_KEY || config.sync.api_key || '';
  if (!passphrase && gatewayUrl) {
    throw new Error(`No passphrase is configured for '${inferredName}'. Set PARADOX_PASSPHRASE or recover DATABASE_URL from the original provisioning output.`);
  }

  const canonical = generateUrl(inferredName, passphrase, gatewayUrl, config.project_name || null, apiKey);
  config.database_url = canonical;
  saveConfig(config);
  return canonical;
}

/**
 * Recover a canonical URL from the owner-authenticated gateway when it is not
 * available locally. The server stores it encrypted and reveals it only via
 * the explicit recovery endpoint.
 */
export async function recoverCanonicalDatabaseUrl(name?: string): Promise<string> {
  const config = loadConfig();
  const local = process.env.DATABASE_URL?.trim() || config.database_url?.trim() || '';
  if (local) {
    const parsed = parseUrl(local);
    if (name && parsed.name !== name) {
      throw new Error(`Canonical DATABASE_URL points to '${parsed.name}', not '${name}'`);
    }
    return local;
  }

  const inferredName = name || path.basename(config.database_path).replace(/\.db$/, '');
  const gatewayUrl = process.env.PARADOX_GATEWAY || config.sync.gateway_url || '';
  const apiKey = process.env.PARADOX_API_KEY || config.sync.api_key || '';
  if (!gatewayUrl || !apiKey) return getCanonicalDatabaseUrl(name);

  const gateway = new GatewayClient(gatewayUrl, apiKey);
  let databaseId = config.database_id || '';
  if (!databaseId) {
    const projects = (await gateway.listProjects()) as { id: string; name: string }[];
    const project = projects.find((entry) => !config.project_name || entry.name === config.project_name);
    if (!project) return getCanonicalDatabaseUrl(name);
    const databases = (await gateway.listDatabases(project.id)) as { id: string; name: string }[];
    databaseId = databases.find((entry) => entry.name === inferredName)?.id || '';
    if (!databaseId) return getCanonicalDatabaseUrl(name);
    config.project_id = project.id;
    config.project_name = project.name;
  }

  try {
    const response = await gateway.getDatabaseUrl(databaseId, true);
    if (!response.database_url) return getCanonicalDatabaseUrl(name);
    const parsed = parseUrl(response.database_url);
    if (name && parsed.name !== name) {
      throw new Error(`Recovered DATABASE_URL points to '${parsed.name}', not '${name}'`);
    }
    config.database_url = response.database_url;
    config.database_id = databaseId;
    saveConfig(config);
    return response.database_url;
  } catch (error) {
    if (error instanceof GatewayError && [404, 405, 501].includes(error.statusCode)) {
      return getCanonicalDatabaseUrl(name);
    }
    throw error;
  }
}

/**
 * Explicitly register a locally known canonical URL on the owner’s gateway.
 * This is the safe migration path for databases created before server URL
 * storage existed; it performs no init, push, pull, or snapshot mutation.
 */
export async function registerCanonicalDatabaseUrl(databaseUrl: string): Promise<string> {
  const config = loadConfig();
  const parsed = parseUrl(databaseUrl);
  const gatewayUrl = parsed.gateway_url || process.env.PARADOX_GATEWAY || config.sync.gateway_url || '';
  const apiKey = process.env.PARADOX_API_KEY || config.sync.api_key || parsed.token || '';
  if (!gatewayUrl || !apiKey) {
    throw new Error('A gateway URL and owner API key are required to register DATABASE_URL');
  }
  const gateway = new GatewayClient(gatewayUrl, apiKey);
  let projectId = config.project_id || '';
  let projectName = parsed.project || config.project_name || '';
  if (!projectId) {
    const projects = (await gateway.listProjects()) as { id: string; name: string }[];
    const project = projects.find((entry) => !projectName || entry.name === projectName);
    if (!project) throw new Error(`Could not find project '${projectName || '(unspecified)'}'`);
    projectId = project.id;
    projectName = project.name;
  }
  const databases = (await gateway.listDatabases(projectId)) as { id: string; name: string }[];
  const database = databases.find((entry) => entry.name === parsed.name);
  if (!database) throw new Error(`Could not find database '${parsed.name}' in project '${projectName || projectId}'`);
  await gateway.setDatabaseUrl(database.id, databaseUrl);
  config.database_url = databaseUrl;
  config.database_id = database.id;
  config.project_id = projectId;
  if (projectName) config.project_name = projectName;
  saveConfig(config);
  return databaseUrl;
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
  storageChannel?: string;
  logChannel?: string;
}

export class SyncDaemon {
  PUSH_INTERVAL = 2000;
  PULL_INTERVAL = 30_000;

  private engine: ClientEngine;
  private dbName: string;
  private dbKey: string;
  private databaseId: string;
  private projectId: string;
  private storageChannel: string;
  private logChannel: string;
  private gateway: GatewayClient;
  private timer: ReturnType<typeof setInterval> | null = null;
  lastSync: number | null = null;

  private _offline: boolean;
  private _consecutiveFailures = 0;
  private _lastError: string | null = null;
  private _ticking = false;

  constructor(opts: SyncDaemonOptions) {
    this.engine = opts.engine;
    this.dbName = opts.dbName;
    this.dbKey = dbStateKey(opts.dbName, opts.project || null);
    this.databaseId = opts.databaseId || '';
    this.projectId = opts.projectId || '';
    this.storageChannel = opts.storageChannel || '';
    this.logChannel = opts.logChannel || '';
    this.gateway = new GatewayClient(opts.gatewayUrl, opts.apiKey || '');
    if (opts.pushIntervalMs) this.PUSH_INTERVAL = opts.pushIntervalMs;
    if (opts.pullIntervalMs) this.PULL_INTERVAL = opts.pullIntervalMs;
    this._offline = Boolean(syncState.isOffline(this.dbKey));
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => this._onTick(), 500);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private _onTick(): void {
    // Never run ticks concurrently: a slow push (large upload, retries,
    // offline backoff) must not let a second tick capture stale version
    // state and start a duplicate push.
    if (this._ticking) return;
    this._ticking = true;
    this._tick()
      .catch(() => {
        // never crash the host
      })
      .finally(() => {
        this._ticking = false;
      });
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
        storage_channel: this.storageChannel,
        log_channel: this.logChannel,
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
            storage_channel: this.storageChannel,
            log_channel: this.logChannel,
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
      const result = await this.gateway.download(this.dbName, undefined, this.databaseId, this.projectId, this.storageChannel);
      if (!result.bytes || result.bytes.length === 0) return false;
      const remoteHash = crypto.createHash('sha256').update(result.bytes).digest('hex');
      let currentHash = '';
      try {
        currentHash = crypto.createHash('sha256').update(this.engine.getRawBytes()).digest('hex');
      } catch {
        // ignore
      }
      if (remoteHash === currentHash) return false;
      // Replace the local snapshot atomically and start clean (journal reset).
      await this.engine.replaceBytes(result.bytes);
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
  private storageChannel: string;
  private logChannel: string;
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
    storageChannel?: string;
    logChannel?: string;
  }) {
    this.passphrase = opts.passphrase;
    this.gatewayUrl = opts.gatewayUrl;
    this.apiKey = opts.apiKey || '';
    this.project = opts.project || null;
    this.databaseId = opts.databaseId || '';
    this.projectId = opts.projectId || '';
    this.storageChannel = opts.storageChannel || '';
    this.logChannel = opts.logChannel || '';
    this.dbName = path.basename(opts.dbPath).replace(/\.db$/, '');
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
        storageChannel: this.storageChannel,
        logChannel: this.logChannel,
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

  /** Canonical connection URL for this successfully resolved database. */
  get databaseUrl(): string {
    return generateUrl(this.dbName, this.passphrase, this.gatewayUrl, this.project, this.apiKey);
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
        storage_channel: this.storageChannel,
        log_channel: this.logChannel,
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
          storage_channel: this.storageChannel,
          log_channel: this.logChannel,
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
    const result = await gw.download(this.dbName, undefined, this.databaseId, this.projectId, this.storageChannel);
    if (!result.bytes || result.bytes.length === 0) return false;
    const remoteHash = crypto.createHash('sha256').update(result.bytes).digest('hex');
    let currentHash = '';
    try {
      currentHash = crypto.createHash('sha256').update(this.engine.getRawBytes()).digest('hex');
    } catch {
      // ignore
    }
    if (remoteHash === currentHash) return false;
    await this.engine.replaceBytes(result.bytes);
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
    const result = await gw.download(this.dbName, version, this.databaseId, this.projectId, this.storageChannel);
    if (!result.bytes || result.bytes.length === 0) return false;
    const remoteHash = crypto.createHash('sha256').update(result.bytes).digest('hex');
    await this.engine.replaceBytes(result.bytes);
    syncState.setRemoteVersion(this.dbKey, result.version ?? version, remoteHash);
    syncState.setLastLocalHash(this.dbKey, remoteHash);
    syncState.clearDirty(this.dbKey);
    return true;
  }
}

// ── Convenience factory ─────────────────────────────────────────

export function generatePassphrase(): string {
  return crypto.randomBytes(32).toString('base64url');
}

function announcePassphrase(passphrase: string, dbPath: string): void {
  const msg =
    `[parad] Generated a new encryption passphrase for '${dbPath}': ${passphrase}\n` +
    '[parad] It was saved to ~/.paradox/config.json and ~/.paradox/.env. ' +
    'Keep it safe — it is NOT recoverable if lost, and it must match on every ' +
    'machine sharing this database.\n';
  try {
    process.stderr.write(msg);
  } catch {
    // non-fatal
  }
  try {
    fs.mkdirSync(configDir(), { recursive: true });
    const envFile = path.join(configDir(), '.env');
    const line = `export PARADOX_PASSPHRASE="${passphrase}"\n`;
    if (fs.existsSync(envFile)) {
      const content = fs.readFileSync(envFile, 'utf-8');
      if (!content.includes('PARADOX_PASSPHRASE=')) {
        fs.writeFileSync(envFile, content.replace(/\s*$/, '\n') + line, 'utf-8');
      }
    } else {
      fs.writeFileSync(envFile, '# parad auto-generated encryption passphrase\n' + line, 'utf-8');
    }
  } catch {
    // non-fatal
  }
}

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
  storageChannel?: string;
  logChannel?: string;
  allowLegacyDefault?: boolean;
}

export async function connect(opts: ConnectOptions | string): Promise<ParadConnection> {
  let options: ConnectOptions;
  if (typeof opts === 'string') {
    options = { url: opts };
  } else {
    options = opts;
  }

  const cfg = loadConfig();
  const configuredUrl = options.url || ((!options.name && !options.dbPath) ? process.env.DATABASE_URL || cfg.database_url || '' : '');
  let parsedUrl: ParsedUrl | null = null;
  if (configuredUrl) {
    parsedUrl = parseUrl(configuredUrl);
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
  if (!resolvedPassphrase) resolvedPassphrase = cfg.encryption.passphrase || '';
  if (!resolvedPassphrase) {
    // First-time connect: generate a strong passphrase, persist it, and
    // surface it (CLI notice + ~/.paradox/.env) so it can be reused on other
    // machines. Never auto-generate for an existing DB file — that keeps
    // legacy 'default'-encrypted databases readable.
    if (!fs.existsSync(resolvedPath)) {
      resolvedPassphrase = generatePassphrase();
      try {
        const c = loadConfig();
        c.encryption.passphrase = resolvedPassphrase;
        saveConfig(c);
      } catch {
        // non-fatal
      }
      announcePassphrase(resolvedPassphrase, resolvedPath);
    } else if (options.allowLegacyDefault) {
      resolvedPassphrase = 'default';
    } else {
      throw new Error(
        `No passphrase configured for existing database '${resolvedPath}'. ` +
        `Set PARADOX_PASSPHRASE or passphrase explicitly. ` +
        `Use allowLegacyDefault: true only for legacy databases encrypted with 'default'.`,
      );
    }
  }

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
      resolvedApiKey = result.api_key;
    } catch (exc) {
      throw new Error(`Login to gateway failed: ${exc instanceof Error ? exc.message : String(exc)}`);
    }
    if (!resolvedApiKey) {
      throw new Error('Login succeeded but no API key was returned');
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
    storageChannel: options.storageChannel || process.env.PARADOX_STORAGE_CHANNEL || '',
    logChannel: options.logChannel || process.env.PARADOX_LOG_CHANNEL || '',
  });
  await conn.init();

  // Register the canonical URL server-side after provisioning. Older
  // gateways may not have this endpoint yet, so retain legacy connectivity
  // when they return 404/405.
  if (resolvedGateway && databaseId) {
    try {
      await new GatewayClient(resolvedGateway, resolvedApiKey).setDatabaseUrl(databaseId, conn.databaseUrl);
    } catch (error) {
      if (!(error instanceof GatewayError) || ![404, 405, 501].includes(error.statusCode)) {
        throw new Error(`Could not store canonical database_url on gateway: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
  }

  try {
    const c = loadConfig();
    c.database_url = conn.databaseUrl;
    saveConfig(c);
  } catch {
    // non-fatal: the connection itself is ready even if config persistence fails
  }
  return conn;
}
