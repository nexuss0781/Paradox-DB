export interface PushRequest {
  database_name: string;
  file_data?: string;
  changeset_data?: string;
  version_type: 'full' | 'changeset' | 'auto';
  version?: number;
}

export interface PushResponse {
  request_id: string;
  message_id: string;
  version: number;
  uploaded_at: string;
}

export interface UploadRequest {
  database_name: string;
  version_type: 'full' | 'changeset' | 'auto';
  version?: number;
}

export interface UploadResponse {
  request_id: string;
  message_id: string;
  version: number;
  uploaded_at: string;
}

export interface DownloadResponse {
  database_name: string;
  version: number;
  message_id: string;
  uploaded_at: string;
  size_bytes: number;
}

export interface VersionInfo {
  version: number;
  message_id: string;
  uploaded_at: string;
  size_bytes: number;
}

export interface VersionsResponse {
  database_name: string;
  versions: VersionInfo[];
}

export interface StatusResponse {
  user_id: string;
  databases: DatabaseStatus[];
}

export interface DatabaseStatus {
  name: string;
  latest_version: number;
  latest_message_id: string;
  pending_changesets: number;
  last_sync_at: string | null;
}

export interface RollbackRequest {
  database_name: string;
  target_version: number;
}

export interface RollbackResponse {
  request_id: string;
  rolled_back_to: number;
  new_message_id: string;
}

export interface ConflictResponse {
  error: 'conflict_detected';
  remote_version: number;
  remote_message_id: string;
  your_version: number;
  resolution: 'pull_before_push';
}

export interface RateLimitResponse {
  error: 'rate_limited';
  retry_after_seconds: number;
  queue_depth: number;
}

export interface NotFoundResponse {
  error: 'not_found';
  database_name: string;
}

export interface ErrorResponse {
  error: string;
  detail?: string;
}

export interface RegisterRequest {
  username: string;
}

export interface RegisterResponse {
  user_id: string;
  api_key: string;
  channel_id: string;
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'error';
}

export interface ConflictLogEntry {
  conflict_id: string;
  user_id: string;
  database_name: string;
  local_version: number;
  remote_version: number;
  resolution: 'lww' | 'merge' | 'manual';
  local_hash: string;
  remote_hash: string;
  timestamp: string;
}

export interface SyncStatus {
  user_id: string;
  databases: SyncStatusDatabase[];
}

export interface SyncStatusDatabase {
  name: string;
  latest_version: number;
  latest_message_id: string;
  pending_changesets: number;
  last_sync_at: string | null;
}

export interface ClientConfig {
  database_path: string;
  encryption: {
    cipher: string;
    kdf_iterations: number;
    page_size: number;
  };
  sync: {
    gateway_url: string;
    api_key: string;
    trigger_timer_seconds: number;
    trigger_ops_threshold: number;
    max_file_size_mb: number;
    auto_sync_on_shutdown: boolean;
  };
  conflict: {
    strategy: 'last-write-wins' | 'first-write-wins' | 'manual';
    log_conflicts: boolean;
  };
  logging: {
    level: 'debug' | 'info' | 'warn' | 'error';
    path: string;
  };
}

export interface QueryResult {
  rows: any[];
  changes: number;
  lastInsertRowid: number;
}

export interface SelectOptions {
  limit?: number;
  offset?: number;
  orderBy?: string;
}
