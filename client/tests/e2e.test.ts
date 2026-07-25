import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { ClientEngine } from '../src/engine.js';
import { SyncManager } from '../src/sync-manager.js';
import { ClientConfig } from '../src/types.js';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import http from 'http';

const PASSPHRASE = 'e2e-test-pass';
const MOCK_DB_BYTES = Buffer.from('FAKE_SQLCIPHER_DATABASE_BYTES_E2E');

function makeDbPath(label: string): string {
  return path.join(os.tmpdir(), `e2e-${label}-${Date.now()}.sqlcipher`);
}

function makeConfig(dbPath: string, gatewayUrl: string): ClientConfig {
  return {
    database_path: dbPath,
    encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256000, page_size: 4096 },
    sync: {
      gateway_url: gatewayUrl,
      api_key: 'pk_e2e_test_key',
      trigger_timer_seconds: 1,
      trigger_ops_threshold: 50,
      max_file_size_mb: 50,
      auto_sync_on_shutdown: true,
    },
    conflict: { strategy: 'last-write-wins' as const, log_conflicts: true },
    logging: { level: 'info' as const, path: '/tmp' },
  };
}

interface MockGatewayHandlers {
  upload?: (body: any, apiKey: string) => { status: number; data: any };
  download?: (req: http.IncomingMessage, apiKey: string) => { status: number; data: Buffer };
  status?: (apiKey: string) => { status: number; data: any };
}

function createMockGateway(
  handlers: MockGatewayHandlers,
): Promise<{ server: http.Server; port: number }> {
  return new Promise((resolve) => {
    const server = http.createServer(async (req, res) => {
      const apiKey = (req.headers['x-api-key'] as string) || '';
      const url = new URL(req.url || '/', `http://localhost`);

      try {
        if (req.method === 'POST' && url.pathname === '/v1/upload') {
          const body = await readJsonBody(req);
          const result = handlers.upload
            ? handlers.upload(body, apiKey)
            : { status: 200, data: { version: 1 } };
          res.writeHead(result.status, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify(result.data));
          return;
        }

        if (req.method === 'GET' && url.pathname === '/v1/download') {
          const result = handlers.download
            ? handlers.download(req, apiKey)
            : { status: 200, data: MOCK_DB_BYTES };
          if (result.status === 200) {
            res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
            res.end(result.data);
          } else {
            res.writeHead(result.status, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result.data));
          }
          return;
        }

        if (req.method === 'GET' && url.pathname === '/v1/versions') {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({
              database_name: path.basename('test.sqlcipher'),
              versions: [
                { version: 1, message_id: '1', uploaded_at: '2026-01-01T00:00:00Z', size_bytes: 100 },
              ],
            }),
          );
          return;
        }

        if (req.method === 'GET' && url.pathname === '/v1/status') {
          const result = handlers.status
            ? handlers.status(apiKey)
            : { status: 200, data: { user_id: 'u1', databases: [] } };
          res.writeHead(result.status, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify(result.data));
          return;
        }

        res.writeHead(404);
        res.end();
      } catch (err) {
        res.writeHead(500);
        res.end(JSON.stringify({ error: 'internal_error' }));
      }
    });

    server.listen(0, () => {
      const addr = server.address();
      resolve({ server, port: typeof addr === 'object' ? addr!.port! : 0 });
    });
  });
}

function closeServer(server: http.Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
}

function readJsonBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf-8')));
      } catch {
        resolve(null);
      }
    });
  });
}

function cleanup(dbPath: string) {
  try { fs.unlinkSync(dbPath); } catch {}
  try { fs.unlinkSync(dbPath + '-wal'); } catch {}
  try { fs.unlinkSync(dbPath + '-shm'); } catch {}
}

describe('E2E: Client Sync Flow', () => {
  describe('test_push_changeset', () => {
    it('tracks changes, exports changeset, pushes to mock gateway, verifies data received', async () => {
      let receivedUploadBody: any = null;

      const { server, port } = await createMockGateway({
        upload: (body, apiKey) => {
          receivedUploadBody = body;
          expect(apiKey).toBe('pk_e2e_test_key');
          return { status: 200, data: { version: 1 } };
        },
      });

      try {
        const dbPath = makeDbPath('push-cs');
        const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
        const engine = new ClientEngine(config);
        engine.open(PASSPHRASE);
        engine.execute(
          'CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)',
        );

        engine.startTracking();
        engine.insert('items', { name: 'alpha', value: 10 });
        engine.insert('items', { name: 'beta', value: 20 });
        engine.insert('items', { name: 'gamma', value: 30 });

        const changeset = engine.exportChangeset();
        expect(changeset).not.toBeNull();

        const syncManager = new SyncManager(config);
        const result = await syncManager.push(changeset!);

        expect(result.success).toBe(true);
        expect(result.version).toBe(1);

        expect(receivedUploadBody).not.toBeNull();
        expect(receivedUploadBody.database_name).toBe(path.basename(dbPath));
        expect(receivedUploadBody.version_type).toBe('changeset');
        const decoded = Buffer.from(receivedUploadBody.changeset_data, 'base64');
        const parsed = JSON.parse(decoded.toString('utf-8'));
        expect(parsed.operations).toHaveLength(3);
        expect(parsed.operations[0].type).toBe('insert');
        expect(parsed.operations[0].table).toBe('items');
        expect(parsed.operations[0].data.name).toBe('alpha');
        expect(parsed.operations[1].data.name).toBe('beta');
        expect(parsed.operations[2].data.name).toBe('gamma');

        engine.close();
        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    });
  });

  describe('test_push_full_database', () => {
    it('reads local DB file and sends it as full upload', async () => {
      let receivedUploadBody: any = null;

      const { server, port } = await createMockGateway({
        upload: (body) => {
          receivedUploadBody = body;
          return { status: 200, data: { version: 5 } };
        },
      });

      try {
        const dbPath = makeDbPath('push-full');
        const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
        const engine = new ClientEngine(config);
        engine.open(PASSPHRASE);
        engine.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)');
        engine.insert('items', { name: 'full_db_item' });
        engine.close();

        const syncManager = new SyncManager(config);
        const result = await syncManager.pushFullDatabase();

        expect(result.success).toBe(true);
        expect(result.version).toBe(5);
        expect(syncManager.getLocalVersion()).toBe(5);

        expect(receivedUploadBody).not.toBeNull();
        expect(receivedUploadBody.version_type).toBe('full');
        expect(receivedUploadBody.file_data).toBeDefined();
        const sentFile = Buffer.from(receivedUploadBody.file_data, 'base64');
        expect(sentFile.length).toBeGreaterThan(0);
        expect(Buffer.compare(sentFile, fs.readFileSync(dbPath))).toBe(0);

        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    });
  });

  describe('test_push_handles_409_conflict', () => {
    it('returns conflict error when server responds with 409', async () => {
      const { server, port } = await createMockGateway({
        upload: () => {
          return { status: 409, data: { remote_version: 3, error: 'conflict' } };
        },
      });

      try {
        const dbPath = makeDbPath('push-409');
        const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
        const engine = new ClientEngine(config);
        engine.open(PASSPHRASE);
        engine.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)');

        engine.startTracking();
        engine.insert('items', { name: 'conflict_item' });
        const changeset = engine.exportChangeset()!;

        const syncManager = new SyncManager(config);
        const result = await syncManager.push(changeset);

        expect(result.success).toBe(false);
        expect(result.error).toBe('conflict');

        engine.close();
        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    });
  });

  describe('test_push_handles_429_rate_limit', () => {
    it('retries after 429 and succeeds on second attempt', async () => {
      let callCount = 0;

      const { server, port } = await createMockGateway({
        upload: () => {
          callCount++;
          if (callCount === 1) {
            return {
              status: 429,
              data: { error: '429 rate limit retry_after=0' },
            };
          }
          return { status: 200, data: { version: 2 } };
        },
      });

      const dbPath = makeDbPath('push-429');
      const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
      const engine = new ClientEngine(config);
      engine.open(PASSPHRASE);
      engine.execute('CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)');

      engine.startTracking();
      engine.insert('items', { name: 'rate_limited' });
      const changeset = engine.exportChangeset()!;

      try {
        const syncManager = new SyncManager(config);
        const result = await syncManager.push(changeset);

        expect(result.success).toBe(true);
        expect(result.version).toBe(2);
        expect(callCount).toBe(2);

        engine.close();
        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    });
  });

  describe('test_pull_latest', () => {
    it('downloads file from mock gateway and writes to local DB path', async () => {
      const fileBytes = Buffer.from('PULLED_DATABASE_CONTENT_123');

      const { server, port } = await createMockGateway({
        download: () => {
          return { status: 200, data: fileBytes };
        },
      });

      try {
        const dbPath = makeDbPath('pull-latest');
        const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
        const syncManager = new SyncManager(config);

        const result = await syncManager.pullLatest();

        expect(result).toBe(true);
        expect(fs.existsSync(dbPath)).toBe(true);
        expect(fs.readFileSync(dbPath)).toEqual(fileBytes);
        expect(syncManager.getLocalVersion()).toBe(1);

        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    });
  });

  describe('test_auto_sync', () => {
    it('triggers syncNow on timer tick via startAutoSync', async () => {
      let statusHits = 0;

      const { server, port } = await createMockGateway({
        status: () => {
          statusHits++;
          return {
            status: 200,
            data: {
              user_id: 'u1',
              databases: [],
            },
          };
        },
      });

      try {
        const dbPath = makeDbPath('auto-sync');
        const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
        const syncManager = new SyncManager(config);

        const syncCallback = vi.fn();
        syncManager.startAutoSync(syncCallback);

        expect(syncManager.isAutoSyncRunning).toBe(true);

        await new Promise((resolve) => setTimeout(resolve, 2500));

        syncManager.stopAutoSync();
        expect(syncManager.isAutoSyncRunning).toBe(false);
        expect(statusHits).toBeGreaterThanOrEqual(1);
        expect(syncCallback).toHaveBeenCalled();

        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    }, 10000);
  });

  describe('test_sync_push_then_pull', () => {
    it('full round-trip: push changeset then pull latest', async () => {
      let lastUploadBody: any = null;
      let downloadHit = false;

      const pulledContent = Buffer.from('SYNCED_DB_AFTER_PUSH_AND_PULL');

      const { server, port } = await createMockGateway({
        upload: (body) => {
          lastUploadBody = body;
          return { status: 200, data: { version: 2 } };
        },
        download: () => {
          downloadHit = true;
          return { status: 200, data: pulledContent };
        },
      });

      try {
        const dbPath = makeDbPath('round-trip');
        const config = makeConfig(dbPath, `http://localhost:${port}/v1`);
        const engine = new ClientEngine(config);
        engine.open(PASSPHRASE);
        engine.execute(
          'CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER)',
        );

        engine.startTracking();
        engine.insert('items', { name: 'round_trip_a', value: 1 });
        engine.insert('items', { name: 'round_trip_b', value: 2 });

        const changeset = engine.exportChangeset();
        expect(changeset).not.toBeNull();

        const syncManager = new SyncManager(config);

        // Push phase
        const pushResult = await syncManager.push(changeset!);
        expect(pushResult.success).toBe(true);
        expect(pushResult.version).toBe(2);
        expect(lastUploadBody).not.toBeNull();
        expect(lastUploadBody.version_type).toBe('changeset');

        // Pull phase
        const pullResult = await syncManager.pullLatest();
        expect(pullResult).toBe(true);
        expect(downloadHit).toBe(true);
        expect(fs.readFileSync(dbPath)).toEqual(pulledContent);
        expect(syncManager.getLocalVersion()).toBe(1);

        engine.close();
        cleanup(dbPath);
      } finally {
        await closeServer(server);
      }
    });
  });
});
