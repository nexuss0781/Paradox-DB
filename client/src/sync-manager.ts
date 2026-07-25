import { ClientConfig } from './types.js';
import { RetryManager, DEFAULT_RETRY_CONFIG } from './retry.js';
import * as fs from 'fs';
import * as path from 'path';
import * as http from 'http';
import * as https from 'https';

// Mirrors VersionInfo from shared/src/types.ts
interface VersionInfo {
  version: number;
  message_id: string;
  uploaded_at: string;
  size_bytes: number;
}

// Mirrors VersionsResponse from shared/src/types.ts
interface DownloadResponse {
  database_name: string;
  versions: VersionInfo[];
}

// Mirrors DatabaseStatus (SyncStatusDatabase) from shared/src/types.ts
interface StatusDatabase {
  name: string;
  latest_version: number;
  latest_message_id: string;
  pending_changesets: number;
  last_sync_at: string | null;
}

// Mirrors StatusResponse (SyncStatus) from shared/src/types.ts
interface StatusResponse {
  user_id: string;
  databases: StatusDatabase[];
}

export interface PushResult {
  success: boolean;
  error?: string;
  version?: number;
}

export class SyncManager {
  private config: ClientConfig;
  private localVersion: number = 0;
  private syncRetry: RetryManager;
  private autoSyncTimer: NodeJS.Timeout | null = null;
  private autoSyncRunning: boolean = false;

  constructor(config: ClientConfig) {
    this.config = config;
    this.syncRetry = new RetryManager(DEFAULT_RETRY_CONFIG);
  }

  getLocalVersion(): number {
    return this.localVersion;
  }

  setLocalVersion(v: number): void {
    this.localVersion = v;
  }

  isLocalStale(): boolean {
    const dbPath = this.config.database_path.replace(/^~/, process.env.HOME || '~');
    if (!fs.existsSync(dbPath)) {
      return true;
    }
    if (this.localVersion === 0) {
      return true;
    }
    return false;
  }

  async pullLatest(): Promise<boolean> {
    return this.pullVersion(undefined);
  }

  async pullVersion(version?: number): Promise<boolean> {
    const baseUrl = this.config.sync.gateway_url.replace(/\/+$/, '');
    const params = new URLSearchParams();
    params.set('database_name', path.basename(this.config.database_path));
    if (version !== undefined) {
      params.set('version', String(version));
    }

    const url = `${baseUrl}/download?${params.toString()}`;
    try {
      const fileBytes = await this.httpGetBinary(url, this.config.sync.api_key);
      if (!fileBytes || fileBytes.length === 0) {
        return false;
      }
      const dbPath = this.config.database_path.replace(/^~/, process.env.HOME || '~');
      const dir = path.dirname(dbPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(dbPath, fileBytes);

      if (version !== undefined) {
        this.localVersion = version;
      } else {
        await this.fetchAndCacheVersion();
      }
      return true;
    } catch {
      return false;
    }
  }

  async pullOnStart(): Promise<void> {
    if (!this.isLocalStale()) {
      return;
    }
    await this.pullLatest();
  }

  async syncNow(): Promise<{ success: boolean; error?: string }> {
    if (!this.syncRetry.canRetry()) {
      return { success: false, error: 'max_retries_exhausted' };
    }

    const delay = this.syncRetry.getNextDelay();
    if (delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, delay));
    }

    this.syncRetry.recordAttempt();

    const baseUrl = this.config.sync.gateway_url.replace(/\/+$/, '');
    const params = new URLSearchParams();
    params.set('database_name', path.basename(this.config.database_path));
    const url = `${baseUrl}/status?${params.toString()}`;

    try {
      const status = await this.httpGetJSON(url, this.config.sync.api_key) as StatusResponse;
      if (!status) {
        return { success: false, error: 'empty_status_response' };
      }

      const dbEntry = status.databases.find(
        (d) => d.name === path.basename(this.config.database_path)
      );

      if (dbEntry && dbEntry.latest_version > this.localVersion) {
        const pulled = await this.pullVersion(dbEntry.latest_version);
        if (pulled) {
          this.syncRetry.reset();
          return { success: true };
        }
        return { success: false, error: 'pull_failed' };
      }

      this.syncRetry.reset();
      return { success: true };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('429')) {
        const match = msg.match(/retry_after[=:](\d+)/i);
        const retryAfter = match ? parseInt(match[1], 10) * 1000 : 30000;
        await new Promise((resolve) => setTimeout(resolve, retryAfter));
      }
      return { success: false, error: msg };
    }
  }

  async push(changesetData: Buffer): Promise<PushResult> {
    const baseUrl = this.config.sync.gateway_url.replace(/\/+$/, '');
    const url = `${baseUrl}/upload`;
    const body = {
      database_name: path.basename(this.config.database_path),
      changeset_data: changesetData.toString('base64'),
      version_type: 'changeset',
      version: this.localVersion,
    };

    try {
      const resp = await this.httpPostJSON(url, body, this.config.sync.api_key);
      if (resp.status === 200 && resp.data) {
        if (resp.data.version !== undefined) {
          this.localVersion = resp.data.version;
        }
        return { success: true, version: this.localVersion };
      }
      if (resp.status === 409) {
        return { success: false, error: 'conflict', version: resp.data?.remote_version };
      }
      return { success: false, error: `http_${resp.status}` };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('429')) {
        const match = msg.match(/retry_after[=:](\d+)/i);
        const retryAfter = match ? parseInt(match[1], 10) * 1000 : 30000;
        await new Promise((resolve) => setTimeout(resolve, retryAfter));
        try {
          const retryResp = await this.httpPostJSON(url, body, this.config.sync.api_key);
          if (retryResp.status === 200 && retryResp.data) {
            if (retryResp.data.version !== undefined) {
              this.localVersion = retryResp.data.version;
            }
            return { success: true, version: this.localVersion };
          }
          return { success: false, error: `retry_http_${retryResp.status}` };
        } catch (retryErr: unknown) {
          return { success: false, error: `retry_${retryErr instanceof Error ? retryErr.message : String(retryErr)}` };
        }
      }
      return { success: false, error: msg };
    }
  }

  async pushFullDatabase(): Promise<PushResult> {
    const dbPath = this.config.database_path.replace(/^~/, process.env.HOME || '~');
    if (!fs.existsSync(dbPath)) {
      return { success: false, error: 'database_file_not_found' };
    }

    const fileData = fs.readFileSync(dbPath);
    const maxBytes = this.config.sync.max_file_size_mb * 1024 * 1024;
    if (fileData.length > maxBytes) {
      return { success: false, error: `database_exceeds_max_size_${this.config.sync.max_file_size_mb}MB` };
    }

    const baseUrl = this.config.sync.gateway_url.replace(/\/+$/, '');
    const url = `${baseUrl}/upload`;
    const body = {
      database_name: path.basename(this.config.database_path),
      file_data: fileData.toString('base64'),
      version_type: 'full',
      version: this.localVersion,
    };

    try {
      const resp = await this.httpPostJSON(url, body, this.config.sync.api_key);
      if (resp.status === 200 && resp.data) {
        if (resp.data.version !== undefined) {
          this.localVersion = resp.data.version;
        }
        return { success: true, version: this.localVersion };
      }
      if (resp.status === 409) {
        return { success: false, error: 'conflict', version: resp.data?.remote_version };
      }
      return { success: false, error: `http_${resp.status}` };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('429')) {
        const match = msg.match(/retry_after[=:](\d+)/i);
        const retryAfter = match ? parseInt(match[1], 10) * 1000 : 30000;
        await new Promise((resolve) => setTimeout(resolve, retryAfter));
        try {
          const retryResp = await this.httpPostJSON(url, body, this.config.sync.api_key);
          if (retryResp.status === 200 && retryResp.data) {
            if (retryResp.data.version !== undefined) {
              this.localVersion = retryResp.data.version;
            }
            return { success: true, version: this.localVersion };
          }
          return { success: false, error: `retry_http_${retryResp.status}` };
        } catch (retryErr: unknown) {
          return { success: false, error: `retry_${retryErr instanceof Error ? retryErr.message : String(retryErr)}` };
        }
      }
      return { success: false, error: msg };
    }
  }

  startAutoSync(onSync?: (result: { pushed: boolean; pulled: boolean }) => void): void {
    if (this.autoSyncTimer) return;

    const intervalMs = (this.config.sync.trigger_timer_seconds || 30) * 1000;
    const threshold = this.config.sync.trigger_ops_threshold || 50;

    this.autoSyncTimer = setInterval(async () => {
      if (this.autoSyncRunning) return;
      this.autoSyncRunning = true;

      try {
        let pushed = false;
        let pulled = false;

        // Push phase: check for pending changes (caller provides changeset externally in CLI flow,
        // but for background auto-sync we check the change-tracker via a callback pattern)
        // For now, auto-sync only pulls unless explicitly told to push.
        const pullResult = await this.syncNow();
        if (pullResult.success) {
          pulled = true;
        }

        if (onSync) {
          onSync({ pushed, pulled });
        }
      } finally {
        this.autoSyncRunning = false;
      }
    }, intervalMs);
  }

  stopAutoSync(): void {
    if (this.autoSyncTimer) {
      clearInterval(this.autoSyncTimer);
      this.autoSyncTimer = null;
    }
    this.autoSyncRunning = false;
  }

  get isAutoSyncRunning(): boolean {
    return this.autoSyncTimer !== null;
  }

  resetSyncRetry(): void {
    this.syncRetry.reset();
  }

  get syncRetryState(): { attempt: number; failed: boolean; canRetry: boolean } {
    return {
      attempt: this.syncRetry.currentAttempt,
      failed: this.syncRetry.isFailed,
      canRetry: this.syncRetry.canRetry(),
    };
  }

  private async fetchAndCacheVersion(): Promise<void> {
    const baseUrl = this.config.sync.gateway_url.replace(/\/+$/, '');
    const params = new URLSearchParams();
    params.set('database_name', path.basename(this.config.database_path));
    const url = `${baseUrl}/versions?${params.toString()}`;

    try {
      const body = await this.httpGetJSON(url, this.config.sync.api_key) as DownloadResponse;
      if (body && body.versions && body.versions.length > 0) {
        const latest = body.versions.reduce((a, b) => (a.version > b.version ? a : b));
        this.localVersion = latest.version;
      }
    } catch {
      // non-fatal: leave localVersion unchanged
    }
  }

  async getStatus(): Promise<StatusResponse | null> {
    const baseUrl = this.config.sync.gateway_url.replace(/\/+$/, '');
    const url = `${baseUrl}/status`;
    try {
      return await this.httpGetJSON(url, this.config.sync.api_key) as StatusResponse;
    } catch {
      return null;
    }
  }

  private httpGetBinary(url: string, apiKey: string): Promise<Buffer> {
    return new Promise((resolve, reject) => {
      const parsed = new URL(url);
      const transport = parsed.protocol === 'https:' ? https : http;
      const req = transport.get(url, {
        headers: {
          'X-API-Key': apiKey,
        },
        timeout: 30_000,
      }, (res) => {
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => resolve(Buffer.concat(chunks)));
        res.on('error', reject);
      });
      req.on('error', reject);
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timed out'));
      });
    });
  }

  httpGetJSON(url: string, apiKey: string): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const parsed = new URL(url);
      const transport = parsed.protocol === 'https:' ? https : http;
      const req = transport.get(url, {
        headers: {
          'X-API-Key': apiKey,
        },
        timeout: 30_000,
      }, (res) => {
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString('utf-8')));
          } catch (e) {
            reject(e);
          }
        });
        res.on('error', reject);
      });
      req.on('error', reject);
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timed out'));
      });
    });
  }

  httpPostJSON(url: string, body: Record<string, any>, apiKey: string): Promise<{ status: number; data: any }> {
    return new Promise((resolve, reject) => {
      const parsed = new URL(url);
      const transport = parsed.protocol === 'https:' ? https : http;
      const payload = JSON.stringify(body);
      const req = transport.request(url, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
        },
        timeout: 60_000,
      }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          const raw = Buffer.concat(chunks).toString('utf-8');
          let data: any;
          try {
            data = JSON.parse(raw);
          } catch {
            data = null;
          }
          if (res.statusCode && res.statusCode >= 400) {
            const errMsg = data?.error || data?.message || `HTTP ${res.statusCode}`;
            const err = new Error(errMsg);
            (err as any).status = res.statusCode;
            (err as any).retryAfter = res.headers?.['retry-after'];
            reject(err);
            return;
          }
          resolve({ status: res.statusCode || 200, data });
        });
        res.on('error', reject);
      });
      req.on('error', reject);
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timed out'));
      });
      req.write(payload);
      req.end();
    });
  }
}
