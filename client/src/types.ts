export interface ClientConfig {
  database_path: string;
  project_id: string;
  project_name: string;
  database_id: string;
  encryption: {
    cipher: string;
    kdf_iterations: number;
    page_size: number;
    passphrase: string;
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
