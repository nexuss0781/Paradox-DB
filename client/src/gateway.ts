import { GatewayError } from './errors.js';
export { GatewayError } from './errors.js';

const COLD_START_TIMEOUT_MS = 30_000;

export interface UploadParams {
  database_name?: string;
  database_id?: string;
  project_id?: string;
  file_bytes: Buffer;
  version: number;
  version_type?: string;
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

  private async request<T>(method: 'GET' | 'POST', path: string, params?: URLSearchParams, body?: unknown): Promise<T> {
    const url = params ? `${this.gatewayUrl}${path}?${params.toString()}` : `${this.gatewayUrl}${path}`;
    let resp: Response;
    try {
      resp = await fetch(url, {
        method,
        headers: this.headers(),
        body: body !== undefined ? JSON.stringify(body) : undefined,
        redirect: 'follow',
      });
    } catch (err) {
      throw new GatewayError(0, err instanceof Error ? err.message : String(err));
    }
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

  /** Login issues a fresh cloud API key (the previous key is invalidated). */
  async login(email: string, password: string): Promise<AuthResult> {
    return this.request<AuthResult>('POST', '/auth/login', undefined, { email, password });
  }

  /** Register creates the account and returns the first cloud API key. */
  async registerEmail(email: string, username: string, password: string): Promise<AuthResult> {
    return this.request<AuthResult>('POST', '/auth/register', undefined, {
      email,
      username,
      password,
    });
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

  async upload(params: UploadParams): Promise<UploadResult> {
    const payload: Record<string, string | number> = {
      file_data: params.file_bytes.toString('base64'),
      version_type: params.version_type || 'full',
      version: params.version,
    };
    if (params.database_name) payload.database_name = params.database_name;
    if (params.database_id) payload.database_id = params.database_id;
    if (params.project_id) payload.project_id = params.project_id;
    return this.request<UploadResult>('POST', '/upload', undefined, payload);
  }

  async download(database_name = '', version?: number, database_id = '', project_id = ''): Promise<DownloadResult> {
    const params = new URLSearchParams();
    if (database_id) params.set('database_id', database_id);
    else if (database_name) params.set('database_name', database_name);
    if (project_id) params.set('project_id', project_id);
    if (version !== undefined) params.set('version', String(version));

    const url = `${this.gatewayUrl}/download?${params.toString()}`;
    let resp: Response;
    try {
      resp = await fetch(url, { method: 'GET', headers: this.headers(), redirect: 'follow' });
    } catch (err) {
      throw new GatewayError(0, err instanceof Error ? err.message : String(err));
    }
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
