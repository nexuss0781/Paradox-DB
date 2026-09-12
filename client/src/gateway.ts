import { GatewayError } from './errors.js';
export { GatewayError } from './errors.js';

const COLD_START_TIMEOUT_MS = 120_000;
const MAX_REQUEST_ATTEMPTS = 3;
const RETRY_BACKOFF_MS = [1_000, 4_000];

function isRetryableNetworkError(err: unknown): boolean {
  if ((err as { name?: string })?.name === 'TimeoutError') return true;
  const code = (err as { code?: string })?.code;
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'ENOTFOUND' || code === 'ETIMEDOUT' || code === 'EAI_AGAIN') {
    return true;
  }
  if (err instanceof TypeError && err.message.includes('fetch')) {
    return true;
  }
  return false;
}

export interface UploadParams {
  database_name?: string;
  database_id?: string;
  project_id?: string;
  file_bytes: Buffer;
  version: number;
  version_type?: string;
  storage_channel?: string;
  log_channel?: string;
}

export interface UploadResult {
  request_id: string;
  message_id: string;
  version: number;
  uploaded_at: string;
}

export interface DownloadResult {
  bytes: Buffer;
  version: number | null;
  message_id: string | null;
}

export interface StatusDatabase {
  name: string;
  latest_version: number;
  latest_message_id: string;
  pending_changesets: number;
  last_sync_at: string | null;
}

export interface DatabaseUrlResponse {
  database_id: string;
  database_url: string | null;
  configured: boolean;
  redacted: boolean;
}
export interface StatusResponse {
  user_id: string;
  databases: StatusDatabase[];
}

export interface VersionEntry {
  version: number;
  message_id: string;
  uploaded_at: string;
  size_bytes: number | null;
}

export interface VersionsResponse {
  database_name: string;
  versions: VersionEntry[];
}

export interface RollbackResponse {
  request_id: string;
  rolled_back_to: number;
  new_message_id: string;
}

/** Cloud-issued credentials — the API key is shown once and hashed at rest. */
export interface AuthResult {
  user_id: string;
  email: string;
  username: string;
  api_key: string;
}

/** Network/5xx failures mean offline. 409 is a conflict, not offline. */
export function isConnectivityError(err: unknown): boolean {
  if (err instanceof GatewayError) {
    return err.statusCode >= 500 || err.statusCode === 0;
  }
  if (err instanceof TypeError && err.message.includes('fetch')) {
    return true;
  }
  const code = (err as { code?: string }).code;
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'ENOTFOUND' || code === 'ETIMEDOUT' || code === 'EAI_AGAIN') {
    return true;
  }
  return false;
}

export class GatewayClient {
  gatewayUrl: string;
  apiKey: string;

  constructor(gatewayUrl: string, apiKey: string = '') {
    this.gatewayUrl = gatewayUrl.replace(/\/+$/, '');
    this.apiKey = apiKey;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.apiKey) {
      h['X-API-Key'] = this.apiKey;
    }
    return h;
  }

  private async fetchWithRetry(url: string, init: Parameters<typeof fetch>[1]): Promise<Response> {
    let lastErr: unknown;
    for (let attempt = 0; attempt < MAX_REQUEST_ATTEMPTS; attempt++) {
      if (attempt > 0) {
        const delay = RETRY_BACKOFF_MS[attempt - 1] ?? RETRY_BACKOFF_MS[RETRY_BACKOFF_MS.length - 1];
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
      try {
        return await fetch(url, { ...init, signal: AbortSignal.timeout(COLD_START_TIMEOUT_MS) });
      } catch (err) {
        lastErr = err;
        if (!isRetryableNetworkError(err)) {
          throw new GatewayError(0, err instanceof Error ? err.message : String(err));
        }
      }
    }
    throw new GatewayError(0, lastErr instanceof Error ? lastErr.message : String(lastErr));
  }

  private async request<T>(method: 'GET' | 'POST' | 'PUT', path: string, params?: URLSearchParams, body?: unknown): Promise<T> {
    const url = params ? `${this.gatewayUrl}${path}?${params.toString()}` : `${this.gatewayUrl}${path}`;
    const resp = await this.fetchWithRetry(url, {
      method,
      headers: this.headers(),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      redirect: 'follow',
    });
    if (resp.status >= 400) {
      let detail: unknown = null;
      try {
        detail = await resp.json();
      } catch {
        // non-JSON error body
      }
      const msg =
        (detail as { error?: string })?.error ||
        (detail as { message?: string })?.message ||
        (detail as { detail?: string })?.detail ||
        `HTTP ${resp.status}`;
      throw new GatewayError(resp.status, msg, detail);
    }
    if (resp.status === 204 || (resp.headers.get('content-length') === '0' && !(resp.headers.get('content-type') || '').includes('json'))) {
      return undefined as unknown as T;
    }
    const ct = resp.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      return (await resp.json()) as T;
    }
    const buf = Buffer.from(await resp.arrayBuffer());
    return buf as unknown as T;
  }

  /** Exchange a Nexuss ``nxa_`` credential for a Paradox ``pk_`` key. */
  async exchangeNexussApiKey(apiKey: string): Promise<AuthResult> {
    return this.request<AuthResult>('POST', '/auth/nexuss/exchange', undefined, { api_key: apiKey });
  }

  /** Mint a fresh API key for the current user (the old key is invalidated). */
  async mintApiKey(): Promise<AuthResult> {
    return this.request<AuthResult>('POST', '/auth/api-key');
  }

  async authMe(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>('GET', '/auth/me');
  }

  async listProjects(): Promise<unknown[]> {
    return this.request<unknown[]>('GET', '/projects');
  }

  async createProject(name: string, description = ''): Promise<{ id: string; name: string }> {
    return this.request<{ id: string; name: string }>('POST', '/projects', undefined, { name, description });
  }

  async listDatabases(projectId: string): Promise<unknown[]> {
    return this.request<unknown[]>('GET', `/projects/${encodeURIComponent(projectId)}/databases`);
  }

  async getDatabase(databaseId: string): Promise<unknown> {
    return this.request<unknown>('GET', `/databases/${encodeURIComponent(databaseId)}`);
  }

  async createDatabase(projectId: string, name: string, description = ''): Promise<{ id: string; name: string }> {
    return this.request<{ id: string; name: string }>(
      'POST',
      `/projects/${encodeURIComponent(projectId)}/databases`,
      undefined,
      { name, description },
    );
  }

  async ensureProject(name: string, description = ''): Promise<{ id: string; name: string }> {
    const projects = (await this.listProjects()) as { id: string; name: string }[];
    const existing = projects.find((p) => p.name === name);
    if (existing) return existing;
    return this.createProject(name, description);
  }

  async ensureDatabase(projectId: string, name: string, description = ''): Promise<{ id: string; name: string }> {
    const dbs = (await this.listDatabases(projectId)) as { id: string; name: string }[];
    const existing = dbs.find((d) => d.name === name);
    if (existing) return existing;
    return this.createDatabase(projectId, name, description);
  }

  async getDatabaseUrl(databaseId: string, reveal = false): Promise<DatabaseUrlResponse> {
    if (reveal) {
      return this.request<DatabaseUrlResponse>('POST', `/databases/${encodeURIComponent(databaseId)}/connection-url/reveal`);
    }
    return this.request<DatabaseUrlResponse>('GET', `/databases/${encodeURIComponent(databaseId)}/connection-url`);
  }

  async setDatabaseUrl(databaseId: string, databaseUrl: string): Promise<DatabaseUrlResponse> {
    return this.request<DatabaseUrlResponse>('PUT', `/databases/${encodeURIComponent(databaseId)}/connection-url`, undefined, { database_url: databaseUrl });
  }

  async upload(params: UploadParams): Promise<UploadResult> {
    const payload: Record<string, string | number> = {
      file_data: params.file_bytes.toString('base64'),
      version_type: params.version_type || 'full',
      version: params.version,
    };
    if (params.database_name) payload.database_name = params.database_name;
    if (params.database_id) payload.database_id = params.database_id;
    if (params.project_id) payload.project_id = params.project_id;
    if (params.storage_channel) payload.storage_channel = params.storage_channel;
    if (params.log_channel) payload.log_channel = params.log_channel;
    return this.request<UploadResult>('POST', '/upload', undefined, payload);
  }

  async download(
    database_name = '',
    version?: number,
    database_id = '',
    project_id = '',
    storage_channel = '',
  ): Promise<DownloadResult> {
    const params = new URLSearchParams();
    if (database_id) params.set('database_id', database_id);
    else if (database_name) params.set('database_name', database_name);
    if (project_id) params.set('project_id', project_id);
    if (version !== undefined) params.set('version', String(version));
    if (storage_channel) params.set('storage_channel', storage_channel);

    const url = `${this.gatewayUrl}/download?${params.toString()}`;
    const resp = await this.fetchWithRetry(url, {
      method: 'GET',
      headers: this.headers(),
      redirect: 'follow',
    });
    if (resp.status >= 400) {
      let detail: unknown = null;
      try {
        detail = await resp.json();
      } catch {
        // ignore
      }
      throw new GatewayError(
        resp.status,
        (detail as { detail?: string })?.detail || (detail as { error?: string })?.error || `HTTP ${resp.status}`,
        detail,
      );
    }
    const bytes = Buffer.from(await resp.arrayBuffer());
    const versionHeader = resp.headers.get('x-version');
    return {
      bytes,
      version: versionHeader !== null ? Number(versionHeader) || null : null,
      message_id: resp.headers.get('x-message-id'),
    };
  }

  async status(): Promise<StatusResponse> {
    return this.request<StatusResponse>('GET', '/status');
  }

  async versions(database_name: string): Promise<VersionsResponse> {
    const params = new URLSearchParams();
    params.set('database_name', database_name);
    return this.request<VersionsResponse>('GET', '/versions', params);
  }

  async rollback(database_name: string, target_version: number): Promise<RollbackResponse> {
    return this.request<RollbackResponse>('POST', '/rollback', undefined, {
      database_name,
      target_version,
    });
  }
}
