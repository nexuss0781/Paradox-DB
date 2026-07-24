import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import * as http from 'http';
import { SyncManager } from '../src/sync-manager.js';
import { ClientConfig } from '../src/types.js';

const TEST_DB = path.join(os.tmpdir(), `test-sync-${Date.now()}.sqlcipher`);
const GATEWAY_PORT = 19876;
const GATEWAY_URL = `http://localhost:${GATEWAY_PORT}/v1`;

function makeConfig(overrides?: Partial<ClientConfig>): ClientConfig {
  return {
    database_path: TEST_DB,
    encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256000, page_size: 4096 },
    sync: {
      gateway_url: GATEWAY_URL,
      api_key: 'pk_test_key_123',
      trigger_timer_seconds: 30,
      trigger_ops_threshold: 50,
      max_file_size_mb: 50,
      auto_sync_on_shutdown: true,
    },
    conflict: { strategy: 'last-write-wins', log_conflicts: true },
    logging: { level: 'info', path: '/tmp' },
    ...overrides,
  };
}

const MOCK_DB_BYTES = Buffer.from('FAKE_SQLCIPHER_DATABASE_BYTES_HERE');

function createMockServer(
  handler: (req: http.IncomingMessage, res: http.ServerResponse) => void,
): Promise<http.Server> {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(GATEWAY_PORT, () => resolve(server));
  });
}

function closeServer(server: http.Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
}

describe('SyncManager', () => {
  let server: http.Server;
  let manager: SyncManager;

  afterEach(async () => {
    if (server) {
      try { await closeServer(server); } catch {}
      server = undefined!;
    }
    try { fs.unlinkSync(TEST_DB); } catch {}
    try { fs.unlinkSync(TEST_DB + '-wal'); } catch {}
    try { fs.unlinkSync(TEST_DB + '-shm'); } catch {}
  });

  describe('isLocalStale', () => {
    it('returns true when no local file exists', () => {
      manager = new SyncManager(makeConfig());
      expect(manager.isLocalStale()).toBe(true);
    });

    it('returns true when localVersion is 0', () => {
      manager = new SyncManager(makeConfig());
      fs.writeFileSync(TEST_DB, 'some data');
      expect(manager.isLocalStale()).toBe(true);
    });

    it('returns false when local file exists and version > 0', () => {
      manager = new SyncManager(makeConfig());
      fs.writeFileSync(TEST_DB, 'some data');
      manager.setLocalVersion(5);
      expect(manager.isLocalStale()).toBe(false);
    });
  });

  describe('pullLatest', () => {
    it('downloads and replaces local DB', async () => {
      server = await createMockServer((req, res) => {
        if (req.url?.startsWith('/v1/download')) {
          res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
          res.end(MOCK_DB_BYTES);
        } else if (req.url?.startsWith('/v1/versions')) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            database_name: 'test.sqlcipher',
            versions: [{ version: 3, message_id: '99', uploaded_at: '2026-01-01T00:00:00Z', size_bytes: 100 }],
          }));
        } else {
          res.writeHead(404);
          res.end();
        }
      });

      manager = new SyncManager(makeConfig());
      const result = await manager.pullLatest();

      expect(result).toBe(true);
      expect(fs.existsSync(TEST_DB)).toBe(true);
      expect(fs.readFileSync(TEST_DB)).toEqual(MOCK_DB_BYTES);
      expect(manager.getLocalVersion()).toBe(3);
    });

    it('handles download failure gracefully', async () => {
      server = await createMockServer((req, res) => {
        res.writeHead(500);
        res.end('Internal Server Error');
      });

      manager = new SyncManager(makeConfig());
      const result = await manager.pullLatest();

      expect(result).toBe(false);
    });

    it('handles 404 (database not found) gracefully', async () => {
      server = await createMockServer((req, res) => {
        res.writeHead(404);
        res.end(JSON.stringify({ detail: 'Database not found' }));
      });

      manager = new SyncManager(makeConfig());
      const result = await manager.pullLatest();

      expect(result).toBe(false);
    });
  });

  describe('pullVersion', () => {
    it('downloads specific version', async () => {
      server = await createMockServer((req, res) => {
        const url = new URL(req.url || '/', `http://localhost:${GATEWAY_PORT}`);
        const version = url.searchParams.get('version');
        if (req.url?.startsWith('/v1/download') && version === '5') {
          res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
          res.end(MOCK_DB_BYTES);
        } else {
          res.writeHead(404);
          res.end();
        }
      });

      manager = new SyncManager(makeConfig());
      const result = await manager.pullVersion(5);

      expect(result).toBe(true);
      expect(fs.readFileSync(TEST_DB)).toEqual(MOCK_DB_BYTES);
      expect(manager.getLocalVersion()).toBe(5);
    });

    it('handles version not found gracefully', async () => {
      server = await createMockServer((req, res) => {
        res.writeHead(404);
        res.end(JSON.stringify({ detail: 'Version not found' }));
      });

      manager = new SyncManager(makeConfig());
      const result = await manager.pullVersion(999);

      expect(result).toBe(false);
    });
  });

  describe('pullOnStart', () => {
    it('triggers pull when local is stale', async () => {
      server = await createMockServer((req, res) => {
        if (req.url?.startsWith('/v1/download')) {
          res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
          res.end(MOCK_DB_BYTES);
        } else if (req.url?.startsWith('/v1/versions')) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            database_name: 'test.sqlcipher',
            versions: [{ version: 1, message_id: '1', uploaded_at: '2026-01-01T00:00:00Z', size_bytes: 50 }],
          }));
        } else {
          res.writeHead(404);
          res.end();
        }
      });

      manager = new SyncManager(makeConfig());
      await manager.pullOnStart();

      expect(fs.existsSync(TEST_DB)).toBe(true);
      expect(fs.readFileSync(TEST_DB)).toEqual(MOCK_DB_BYTES);
    });

    it('skips when local is current', async () => {
      let downloadHit = false;
      server = await createMockServer((req, res) => {
        if (req.url?.startsWith('/v1/download')) {
          downloadHit = true;
          res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
          res.end(MOCK_DB_BYTES);
        } else {
          res.writeHead(404);
          res.end();
        }
      });

      manager = new SyncManager(makeConfig());
      fs.writeFileSync(TEST_DB, 'existing data');
      manager.setLocalVersion(10);

      await manager.pullOnStart();
      expect(downloadHit).toBe(false);
    });
  });

  describe('HTTP error handling', () => {
    it('returns false on connection refused', async () => {
      manager = new SyncManager(makeConfig({ sync: { ...makeConfig().sync, gateway_url: 'http://127.0.0.1:47321/v1' } }));
      let result = false;
      try {
        result = await manager.pullLatest();
      } catch {
        result = false;
      }
      expect(result).toBe(false);
    });
  });
});
