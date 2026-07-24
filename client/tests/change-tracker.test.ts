import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { ClientEngine } from '../src/engine.js';
import { ChangeTracker } from '../src/change-tracker.js';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const TEST_DB = path.join(os.tmpdir(), `test-ct-${Date.now()}.sqlcipher`);
const PASSPHRASE = 'ct-test-pass';

function makeConfig(dbPath?: string) {
  return {
    database_path: dbPath || TEST_DB,
    encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256000, page_size: 4096 },
    sync: { gateway_url: '', api_key: '', trigger_timer_seconds: 30, trigger_ops_threshold: 50, max_file_size_mb: 50, auto_sync_on_shutdown: true },
    conflict: { strategy: 'last-write-wins' as const, log_conflicts: true },
    logging: { level: 'info' as const, path: '/tmp' },
  } as any;
}

describe('ChangeTracker', () => {
  let engine: ClientEngine;
  let tracker: ChangeTracker;

  beforeEach(() => {
    engine = new ClientEngine(makeConfig());
    engine.open(PASSPHRASE);
    engine.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)');
    tracker = new ChangeTracker(engine);
    tracker.startSession();
  });

  afterEach(() => {
    try { engine.close(); } catch {}
    try { fs.unlinkSync(TEST_DB); } catch {}
  });

  describe('session management', () => {
    it('startSession initializes session', () => {
      expect(tracker.active).toBe(true);
      expect(tracker.changesetCount()).toBe(0);
    });

    it('exports null with no writes', () => {
      expect(tracker.exportChangeset()).toBeNull();
    });
  });

  describe('tracking', () => {
    it('track() adds operation to buffer', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      expect(tracker.changesetCount()).toBe(1);
    });

    it('multiple tracks accumulate', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      tracker.track('insert', 'items', { name: 'B', value: 2 });
      tracker.track('update', 'items', undefined, { name: 'A' }, { value: 99 });
      expect(tracker.changesetCount()).toBe(3);
    });

    it('bufferSize returns byte count', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      expect(tracker.bufferSize()).toBeGreaterThan(0);
    });

    it('bufferSize returns 0 when empty', () => {
      expect(tracker.bufferSize()).toBe(0);
    });
  });

  describe('exportChangeset', () => {
    it('returns Buffer after writes', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      const cs = tracker.exportChangeset();
      expect(cs).toBeInstanceOf(Buffer);
      expect(cs!.length).toBeGreaterThan(0);
    });

    it('exported changeset is valid JSON', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      const cs = tracker.exportChangeset()!;
      const parsed = JSON.parse(cs.toString('utf-8'));
      expect(parsed.id).toBeDefined();
      expect(parsed.operations).toHaveLength(1);
      expect(parsed.baseVersion).toBe(0);
    });

    it('operations preserve type, table, data', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      const cs = JSON.parse(tracker.exportChangeset()!.toString('utf-8'));
      expect(cs.operations[0].type).toBe('insert');
      expect(cs.operations[0].table).toBe('items');
      expect(cs.operations[0].data).toEqual({ name: 'A', value: 1 });
    });
  });

  describe('importChangeset', () => {
    it('applies patch to fresh DB', () => {
      const db2Path = path.join(os.tmpdir(), `test-ct-import-${Date.now()}.sqlcipher`);
      try {
        const engine2 = new ClientEngine(makeConfig(db2Path));
        engine2.open(PASSPHRASE);
        engine2.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)');
        const tracker2 = new ChangeTracker(engine2);
        tracker2.startSession();

        tracker.track('insert', 'items', { name: 'A', value: 1 });
        tracker.track('insert', 'items', { name: 'B', value: 2 });
        const cs = tracker.exportChangeset()!;

        const result = tracker2.importChangeset(cs);
        expect(result.success).toBe(true);

        const rows = engine2.select('items');
        expect(rows).toHaveLength(2);
        expect(rows[0].name).toBe('A');
        expect(rows[1].name).toBe('B');
        engine2.close();
      } finally {
        try { fs.unlinkSync(db2Path); } catch {}
      }
    });

    it('returns { success: false } for corrupted patch', () => {
      const result = tracker.importChangeset(Buffer.from('not-json'));
      expect(result.success).toBe(false);
    });

    it('detects version conflict', () => {
      const db2Path = path.join(os.tmpdir(), `test-ct-conflict-${Date.now()}.sqlcipher`);
      try {
        const engine2 = new ClientEngine(makeConfig(db2Path));
        engine2.open(PASSPHRASE);
        engine2.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)');
        const tracker2 = new ChangeTracker(engine2);
        tracker2.startSession();
        tracker2.incrementVersion(); // version = 1

        tracker.track('insert', 'items', { name: 'A', value: 1 });
        const cs = tracker.exportChangeset()!; // baseVersion = 0

        const result = tracker2.importChangeset(cs);
        expect(result.success).toBe(false);
        expect(result.conflicts).toBeDefined();
        expect(result.conflicts!.localVersion).toBe(1);
        expect(result.conflicts!.remoteVersion).toBe(0);
        engine2.close();
      } finally {
        try { fs.unlinkSync(db2Path); } catch {}
      }
    });
  });

  describe('truncateBuffer', () => {
    it('clears buffer', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      tracker.truncateBuffer();
      expect(tracker.changesetCount()).toBe(0);
      expect(tracker.bufferSize()).toBe(0);
    });

    it('does not lose committed data', () => {
      engine.insert('items', { name: 'X', value: 100 });
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      tracker.truncateBuffer();
      const rows = engine.select('items');
      expect(rows).toHaveLength(1);
      expect(rows[0].name).toBe('X');
    });
  });

  describe('version', () => {
    it('starts at 0', () => {
      expect(tracker.version).toBe(0);
    });

    it('incrementVersion increases', () => {
      tracker.incrementVersion();
      expect(tracker.version).toBe(1);
    });

    it('import increments version', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      const cs = tracker.exportChangeset()!;
      const db2Path = path.join(os.tmpdir(), `test-ct-ver-${Date.now()}.sqlcipher`);
      try {
        const engine2 = new ClientEngine(makeConfig(db2Path));
        engine2.open(PASSPHRASE);
        engine2.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)');
        const tracker2 = new ChangeTracker(engine2);
        tracker2.startSession();
        tracker2.importChangeset(cs);
        expect(tracker2.version).toBe(1);
        engine2.close();
      } finally {
        try { fs.unlinkSync(db2Path); } catch {}
      }
    });
  });

  describe('integration: round-trip', () => {
    it('write 100 rows → export → new DB → import → verify 100 rows', () => {
      const db2Path = path.join(os.tmpdir(), `test-ct-roundtrip-${Date.now()}.sqlcipher`);
      try {
        for (let i = 0; i < 100; i++) {
          engine.insert('items', { name: `item_${i}`, value: i });
          tracker.track('insert', 'items', { name: `item_${i}`, value: i });
        }
        const cs = tracker.exportChangeset()!;
        expect(cs).not.toBeNull();

        const engine2 = new ClientEngine(makeConfig(db2Path));
        engine2.open(PASSPHRASE);
        engine2.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)');
        const tracker2 = new ChangeTracker(engine2);
        tracker2.startSession();
        const result = tracker2.importChangeset(cs);
        expect(result.success).toBe(true);

        const rows = engine2.select('items');
        expect(rows).toHaveLength(100);
        expect(rows[0].name).toBe('item_0');
        expect(rows[99].name).toBe('item_99');
        engine2.close();
      } finally {
        try { fs.unlinkSync(db2Path); } catch {}
      }
    });

    it('export → truncate → export → second empty', () => {
      tracker.track('insert', 'items', { name: 'A', value: 1 });
      const cs1 = tracker.exportChangeset();
      expect(cs1).not.toBeNull();

      tracker.truncateBuffer();
      const cs2 = tracker.exportChangeset();
      expect(cs2).toBeNull();
    });

    it('chain: export A → import to B → track C on B → export → import back', () => {
      const db2Path = path.join(os.tmpdir(), `test-ct-chain-${Date.now()}.sqlcipher`);
      try {
        tracker.track('insert', 'items', { name: 'A', value: 1 });
        const csA = tracker.exportChangeset()!;

        const engine2 = new ClientEngine(makeConfig(db2Path));
        engine2.open(PASSPHRASE);
        engine2.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)');
        const tracker2 = new ChangeTracker(engine2);
        tracker2.startSession();
        tracker2.importChangeset(csA);

        // Track C on engine2
        tracker2.track('insert', 'items', { name: 'C', value: 3 });
        const csC = tracker2.exportChangeset()!;

        // Import csC back to engine1
        const result = tracker.importChangeset(csC);
        expect(result.success).toBe(true);

        const rows = engine.select('items');
        expect(rows).toHaveLength(2);
        expect(rows.map((r: any) => r.name).sort()).toEqual(['A', 'C']);
        engine2.close();
      } finally {
        try { fs.unlinkSync(db2Path); } catch {}
      }
    });
  });
});
