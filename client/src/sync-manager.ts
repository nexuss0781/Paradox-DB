import { ClientConfig } from './types.js';
import { RetryManager, DEFAULT_RETRY_CONFIG } from './retry.js';
import * as fs from 'fs';
import * as path from 'path';
import * as http from 'http';
import * as https from 'https';

interface VersionInfo {
  version: number;
  message_id: string;
  uploaded_at: string;
  size_bytes: number;
}

interface DownloadResponse {
  database_name: string;
  versions: VersionInfo[];
}

interface StatusDatabase {
  name: string;
  latest_version: number;
  latest_message_id: string;
  pending_changesets: number;
  last_sync_at: string | null;
}

interface StatusResponse {
  user_id: string;
  databases: StatusDatabase[];
}

export class SyncManager {
  private config: ClientConfig;
  private localVersion: number = 0;
  private syncRetry: RetryManager;

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

  private httpGetJSON(url: string, apiKey: string): Promise<unknown> {
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
}
