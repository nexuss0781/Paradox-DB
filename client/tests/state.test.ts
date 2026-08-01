import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as state from '../src/state.js';

let tmpHome: string;
const saved = process.env.PARADOX_HOME;

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'parad-state-'));
  process.env.PARADOX_HOME = tmpHome;
});

afterEach(() => {
  if (saved === undefined) delete process.env.PARADOX_HOME;
  else process.env.PARADOX_HOME = saved;
  fs.rmSync(tmpHome, { recursive: true, force: true });
});

describe('sanitizeStateKey', () => {
  it('flattens multi-part keys', () => {
    expect(state.sanitizeStateKey('myproject/mydb')).toBe('myproject__mydb');
    expect(state.sanitizeStateKey('a/b')).toBe('a__b');
    expect(state.sanitizeStateKey('myproject__mydb')).toBe('myproject__mydb');
    expect(state.sanitizeStateKey('main')).toBe('main');
  });

  it('rejects empty/unsafe keys', () => {
    expect(() => state.sanitizeStateKey('')).toThrow();
    expect(() => state.sanitizeStateKey('///')).toThrow();
    expect(() => state.sanitizeStateKey('..')).toThrow();
  });
});

describe('state roundtrips under PARADOX_HOME', () => {
  it('saves/loads remote version + hash', () => {
    const key = 'proj/db';
    state.setRemoteVersion(key, 7, 'abc123');
    const s = state.loadState(key);
    expect(s.remote_version).toBe(7);
    expect(s.remote_hash).toBe('abc123');
    expect(s.last_sync).toBeTruthy();
    const file = path.join(tmpHome, 'proj__db.sync.json');
    expect(fs.existsSync(file)).toBe(true);
  });

  it('default state has new fields', () => {
    const s = state.loadState('never-seen/db');
    expect(s.dirty).toBe(false);
    expect(s.offline).toBe(false);
    expect(s.remote_version).toBeNull();
    expect(s.last_local_hash).toBeNull();
  });

  it('dirty flag roundtrip', () => {
    const key = 'proj/db';
    expect(state.isDirty(key)).toBe(false);
    state.markDirty(key);
    expect(state.isDirty(key)).toBe(true);
    state.clearDirty(key);
    expect(state.isDirty(key)).toBe(false);
  });

  it('offline flag roundtrip + persists', () => {
    const key = 'proj/db';
    state.setOffline(key, true);
    expect(state.isOffline(key)).toBe(true);
    state.setOffline(key, false);
    expect(state.isOffline(key)).toBe(false);
  });

  it('last_local_hash roundtrip', () => {
    const key = 'proj/db';
    state.setLastLocalHash(key, 'hashX');
    expect(state.getLastLocalHash(key)).toBe('hashX');
    expect(state.getRemoteVersion(key)).toBeNull();
  });

  it('getSyncStatus shape', () => {
    const key = 'proj/db';
    state.setRemoteVersion(key, 3, 'r3');
    state.setLastLocalHash(key, 'l3');
    state.markDirty(key);
    const s = state.getSyncStatus(key);
    expect(s.remote_version).toBe(3);
    expect(s.remote_hash).toBe('r3');
    expect(s.last_local_hash).toBe('l3');
    expect(s.dirty).toBe(true);
  });
});
