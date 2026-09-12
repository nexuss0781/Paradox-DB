import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { homedir } from 'node:os';
import type { ClientConfig } from './types.js';

/** Current config directory, read at call time (PARADOX_HOME override). */
export function configDir(): string {
  const home = process.env.PARADOX_HOME || '~/.paradox';
  return home === '~' ? homedir() : home.replace(/^~/, homedir());
}

function defaultConfigPath(): string {
  return join(configDir(), 'config.json');
}

const DEFAULT_CONFIG: ClientConfig = {
  database_url: '',
  database_path: join(homedir(), '.paradox', 'data.db'),
  project_id: '',
  project_name: '',
  database_id: '',
  encryption: {
    cipher: 'aes-256-cbc',
    kdf_iterations: 256_000,
    page_size: 4096,
    passphrase: '',
  },
  sync: {
    gateway_url: 'https://paradox-db.onrender.com/v1',
    api_key: '',
    trigger_timer_seconds: 30,
    trigger_ops_threshold: 50,
    max_file_size_mb: 50,
    auto_sync_on_shutdown: true,
  },
  conflict: {
    strategy: 'last-write-wins',
    log_conflicts: true,
  },
  logging: {
    level: 'info',
    path: join(homedir(), '.paradox', 'logs'),
  },
};

function resolveHomePaths(obj: Record<string, any>): Record<string, any> {
  const result: Record<string, any> = {};
  for (const key of Object.keys(obj)) {
    const val = obj[key];
    if (typeof val === 'string' && (val.startsWith('~/') || val === '~')) {
      result[key] = val.replace(/^~/, homedir());
    } else if (val !== null && typeof val === 'object' && !Array.isArray(val)) {
      result[key] = resolveHomePaths(val);
    } else {
      result[key] = val;
    }
  }
  return result;
}

function deepMerge(target: Record<string, unknown>, source: Record<string, unknown>): Record<string, unknown> {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    const sourceVal = source[key];
    const targetVal = result[key];
    if (
      sourceVal !== null &&
      typeof sourceVal === 'object' &&
      !Array.isArray(sourceVal) &&
      targetVal !== null &&
      typeof targetVal === 'object' &&
      !Array.isArray(targetVal)
    ) {
      result[key] = deepMerge(targetVal as Record<string, unknown>, sourceVal as Record<string, unknown>);
    } else if (sourceVal !== undefined) {
      result[key] = sourceVal;
    }
  }
  return result;
}

export function loadConfig(configPath?: string): ClientConfig {
  const resolvedPath = configPath ?? defaultConfigPath();

  if (!existsSync(resolvedPath)) {
    return { ...DEFAULT_CONFIG };
  }

  const raw = readFileSync(resolvedPath, 'utf-8');
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const resolved = resolveHomePaths(parsed);
  return deepMerge(DEFAULT_CONFIG as unknown as Record<string, unknown>, resolved) as unknown as ClientConfig;
}

export function getDefaultConfigPath(): string {
  return defaultConfigPath();
}

export function saveConfig(config: ClientConfig, configPath?: string): void {
  const resolvedPath = configPath ?? defaultConfigPath();
  const dir = dirname(resolvedPath);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  writeFileSync(resolvedPath, JSON.stringify(config, null, 2), 'utf-8');
}
