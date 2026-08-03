import * as fs from 'node:fs';
import * as path from 'node:path';
import { configDir } from './config.js';

// Characters unsafe in a filename segment. Multi-part keys like
// "myproject/mydb" are flattened to "myproject__mydb".
// eslint-disable-next-line no-control-regex -- U+0000–U+001F are intentionally blocked as filename-unsafe
const UNSAFE_STATE_CHARS = /[/\\:*?"<>|\u0000-\u001f]/g;

export function sanitizeStateKey(dbKey: string): string {
  const key = dbKey.replace(UNSAFE_STATE_CHARS, '__').trim().replace(/^\.+|\.+$/g, '');
  if (!key || !/[a-zA-Z0-9]/.test(key)) {
    throw new Error(`db_key must not be empty after sanitizing: ${JSON.stringify(dbKey)}`);
  }
  return key;
}

export interface SyncState {
  database_name: string;
  remote_version: number | null;
  remote_hash: string | null;
  last_sync: string | null;
  last_local_hash: string | null;
  dirty: boolean;
  offline: boolean;
}

function defaultState(dbName: string): SyncState {
  return {
    database_name: dbName,
    remote_version: null,
    remote_hash: null,
    last_sync: null,
    last_local_hash: null,
    dirty: false,
    offline: false,
  };
}

function statePath(dbName: string): string {
  return path.join(configDir(), `${sanitizeStateKey(dbName)}.sync.json`);
}

export function loadState(dbName: string): SyncState {
  const p = statePath(dbName);
  try {
    if (fs.existsSync(p)) {
      const parsed = JSON.parse(fs.readFileSync(p, 'utf-8')) as Partial<SyncState>;
      return { ...defaultState(dbName), ...parsed };
    }
  } catch {
    // corrupt/partial state file -> defaults
  }
  return defaultState(dbName);
}

export function saveState(dbName: string, state: SyncState): void {
  const p = statePath(dbName);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(state, null, 2), 'utf-8');
}

export function getRemoteVersion(dbName: string): number | null {
  const s = loadState(dbName);
  const v = s.remote_version;
  return v === null || v === undefined ? null : Number(v);
}

export function setRemoteVersion(dbName: string, version: number, fileHash = ''): void {
  const s = loadState(dbName);
  s.remote_version = version;
  s.remote_hash = fileHash;
  s.last_sync = new Date().toISOString();
  saveState(dbName, s);
}

export function getLastLocalHash(dbName: string): string | null {
  return loadState(dbName).last_local_hash;
}

export function setLastLocalHash(dbName: string, fileHash: string): void {
  const s = loadState(dbName);
  s.last_local_hash = fileHash;
  saveState(dbName, s);
}

export function markDirty(dbKey: string): void {
  const s = loadState(dbKey);
  s.dirty = true;
  saveState(dbKey, s);
}

export function clearDirty(dbKey: string): void {
  const s = loadState(dbKey);
  s.dirty = false;
  saveState(dbKey, s);
}

export function isDirty(dbKey: string): boolean {
  return Boolean(loadState(dbKey).dirty);
}

export function setOffline(dbKey: string, offline: boolean): void {
  const s = loadState(dbKey);
  s.offline = Boolean(offline);
  saveState(dbKey, s);
}

export function isOffline(dbKey: string): boolean {
  return Boolean(loadState(dbKey).offline);
}

export function getSyncStatus(dbKey: string): SyncState {
  return loadState(dbKey);
}
