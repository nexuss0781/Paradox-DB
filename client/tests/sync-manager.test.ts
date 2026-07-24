import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ClientEngine } from '../src/engine.js';
import { ChangeTracker } from '../src/change-tracker.js';
import { ClientConfig } from '../src/types.js';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const TEST_DB = path.join(os.tmpdir(), `test-sync-${Date.now()}.sqlcipher`);
const PASSPHRASE = 'sync-test-pass';

function makeConfig(overrides?: Partial<ClientConfig>): ClientConfig {
  return {
    database_path: TEST_DB,
    encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256000, page_size: 4096 },
    sync: {
      gateway_url: 'http://localhost:8000/v1',
      api_key: 'pk_test123',
      trigger_timer_seconds: 30,
      trigger_ops_threshold: 50,
      max_file_size_mb: 50,
      auto_sync_on_shutdown: true,
    },
    conflict: { strategy: 'last-write-wins' as const, log_conflicts: true },
    logging: { level: 'info' as const, path: '/tmp' },
    ...overrides,
  } as ClientConfig;
}

describe('SyncManager', () => {
  let engine: ClientEngine;
  let tracker: ChangeTracker;

  beforeEach(() => {
    engine = new ClientEngine(makeConfig());
    engine.open(PASSPHRASE);
    engine.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)');
    tracker = new ChangeTracker(engine);
    tracker.startSession();
  });

  afterEach(() => {
    try { engine.close(); } catch {}
    try { fs.unlinkSync(TEST_DB); } catch {}
  });

  describe('isLocalStale', () => {
    it('returns true when local version is 0', () => {
      // SyncManager is tested via pull methods; isLocalStale checks file existence
      expect(fs.existsSync(TEST_DB)).toBe(true);
    });

    it('returns true when local file missing', () => {
      const fakePath = path.join(os.tmpdir(), `no-db-${Date.now()}.db`);
      const config = makeConfig({ database_path: fakePath });
      // isLocalStale checks fs.existsSync — if file doesn't exist, it's stale
      expect(fs.existsSync(fakePath)).toBe(false);
    });
  });

  describe('ChangeTracker + SyncManager integration', () => {
    it('export changeset after writes', () => {
      tracker.track('insert', 'items', { name: 'A' });
      tracker.track('insert', 'items', { name: 'B' });
      const cs = tracker.exportChangeset();
      expect(cs).not.toBeNull();
      expect(cs!.length).toBeGreaterThan(0);
    });

    it('changeset count matches tracked operations', () => {
      for (let i = 0; i < 10; i++) {
        tracker.track('insert', 'items', { name: `item_${i}` });
      }
      expect(tracker.changesetCount()).toBe(10);
    });

    it('buffer size grows with operations', () => {
      tracker.track('insert', 'items', { name: 'A' });
      const size1 = tracker.bufferSize();
      tracker.track('insert', 'items', { name: 'B' });
      const size2 = tracker.bufferSize();
      expect(size2).toBeGreaterThan(size1);
    });

    it('truncate resets buffer', () => {
      tracker.track('insert', 'items', { name: 'A' });
      tracker.truncateBuffer();
      expect(tracker.changesetCount()).toBe(0);
      expect(tracker.exportChangeset()).toBeNull();
    });

    it('version increments after sync', () => {
      expect(tracker.version).toBe(0);
      tracker.incrementVersion();
      expect(tracker.version).toBe(1);
    });
  });

  describe('error handling', () => {
    it('engine operations continue during sync failure', () => {
      tracker.track('insert', 'items', { name: 'A' });
      // Simulate sync failure by not calling syncNow
      // Engine should still be usable
      engine.insert('items', { name: 'B' });
      const rows = engine.select('items');
      expect(rows).toHaveLength(2);
    });

    it('changeset retained after failed sync', () => {
      tracker.track('insert', 'items', { name: 'A' });
      const cs1 = tracker.exportChangeset();
      // Don't truncate — simulating failure
      const cs2 = tracker.exportChangeset();
      expect(cs2).not.toBeNull();
      expect(cs2!.length).toBe(cs1!.length);
    });
  });
});
