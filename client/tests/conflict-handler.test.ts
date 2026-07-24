import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ConflictHandler, ConflictResolution } from '../src/conflict-handler.js';
import { ClientEngine } from '../src/engine.js';
import { ChangeTracker } from '../src/change-tracker.js';
import { ConflictInfo } from '../src/change-tracker.js';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const TEST_DB = path.join(os.tmpdir(), `test-conflict-${Date.now()}.sqlcipher`);
const PASSPHRASE = 'conflict-test-pass';

function makeConfig(dbPath?: string) {
  return {
    database_path: dbPath || TEST_DB,
    encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256000, page_size: 4096 },
    sync: { gateway_url: 'http://localhost:8000/v1', api_key: 'pk_test', trigger_timer_seconds: 30, trigger_ops_threshold: 50, max_file_size_mb: 50, auto_sync_on_shutdown: true },
    conflict: { strategy: 'last-write-wins' as const, log_conflicts: true },
    logging: { level: 'info' as const, path: '/tmp' },
  } as any;
}

describe('ConflictHandler', () => {
  let engine: ClientEngine;
  let tracker: ChangeTracker;
  let handler: ConflictHandler;

  beforeEach(() => {
    engine = new ClientEngine(makeConfig());
    engine.open(PASSPHRASE);
    engine.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)');
    tracker = new ChangeTracker(engine);
    tracker.startSession();
    // Mock SyncManager — we can't actually call gateway in unit tests
    const mockSyncManager = { pullLatest: vi.fn().mockResolvedValue(true) } as any;
    handler = new ConflictHandler(engine, tracker, mockSyncManager);
  });

  afterEach(() => {
    try { engine.close(); } catch {}
    try { fs.unlinkSync(TEST_DB); } catch {}
  });

  it('handleConflict() calls pullLatest', async () => {
    const conflict: ConflictInfo = { localVersion: 1, remoteVersion: 2, localHash: 'a', remoteHash: 'b' };
    await handler.handleConflict(conflict);
    expect((handler as any).syncManager.pullLatest).toHaveBeenCalled();
  });

  it('handleConflict() returns LWW resolution', async () => {
    const conflict: ConflictInfo = { localVersion: 1, remoteVersion: 2, localHash: 'a', remoteHash: 'b' };
    const result = await handler.handleConflict(conflict);
    expect(result.strategy).toBe('lww');
    expect(result.resolved).toBe(true);
    expect(result.localVersion).toBe(1);
    expect(result.remoteVersion).toBe(2);
  });

  it('handleConflict() records in conflict log', async () => {
    const conflict: ConflictInfo = { localVersion: 1, remoteVersion: 2, localHash: 'a', remoteHash: 'b' };
    await handler.handleConflict(conflict);
    expect(handler.getConflictLog()).toHaveLength(1);
    expect(handler.getConflictLog()[0].strategy).toBe('lww');
  });

  it('conflictLog shows localVersion and remoteVersion', async () => {
    const conflict: ConflictInfo = { localVersion: 5, remoteVersion: 8, localHash: 'x', remoteHash: 'y' };
    await handler.handleConflict(conflict);
    const log = handler.getConflictLog();
    expect(log[0].localVersion).toBe(5);
    expect(log[0].remoteVersion).toBe(8);
  });

  it('clearLog() resets log', async () => {
    const conflict: ConflictInfo = { localVersion: 1, remoteVersion: 2, localHash: 'a', remoteHash: 'b' };
    await handler.handleConflict(conflict);
    handler.clearLog();
    expect(handler.getConflictLog()).toHaveLength(0);
  });

  it('handleConflict() returns resolved=false when pull fails', async () => {
    const mockSync = { pullLatest: vi.fn().mockResolvedValue(false) } as any;
    const h = new ConflictHandler(engine, tracker, mockSync);
    const conflict: ConflictInfo = { localVersion: 1, remoteVersion: 2, localHash: 'a', remoteHash: 'b' };
    const result = await h.handleConflict(conflict);
    expect(result.resolved).toBe(false);
  });

  it('multiple conflicts are logged independently', async () => {
    const c1: ConflictInfo = { localVersion: 1, remoteVersion: 2, localHash: 'a', remoteHash: 'b' };
    const c2: ConflictInfo = { localVersion: 3, remoteVersion: 4, localHash: 'c', remoteHash: 'd' };
    await handler.handleConflict(c1);
    await handler.handleConflict(c2);
    expect(handler.getConflictLog()).toHaveLength(2);
  });
});
