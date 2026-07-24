import { describe, it, expect } from 'vitest';
import { ClientEngine, loadConfig, getDefaultConfigPath } from '../src/index';
import type { ClientConfig } from '../src/types';

const TEST_CONFIG: ClientConfig = {
  database_path: '/tmp/test-scaffold.db',
  encryption: { cipher: 'aes-256-cbc', kdf_iterations: 256_000, page_size: 4096 },
  sync: {
    gateway_url: 'http://localhost:8000/v1',
    api_key: 'test-key',
    trigger_timer_seconds: 30,
    trigger_ops_threshold: 50,
    max_file_size_mb: 50,
    auto_sync_on_shutdown: true,
  },
  conflict: { strategy: 'last-write-wins', log_conflicts: true },
  logging: { level: 'info', path: '/tmp/logs' },
};

describe('ClientEngine', () => {
  it('should instantiate with explicit config', () => {
    const engine = new ClientEngine(TEST_CONFIG);
    expect(engine).toBeInstanceOf(ClientEngine);
    expect(engine.isOpen).toBe(false);
  });
});

describe('loadConfig', () => {
  it('should return default config when no file exists', () => {
    const config = loadConfig('/nonexistent/path/config.json');
    expect(config.database_path).toContain('.paradox');
    expect(config.encryption.cipher).toBe('aes-256-cbc');
    expect(config.sync.trigger_timer_seconds).toBe(30);
  });
});

describe('getDefaultConfigPath', () => {
  it('should return a path ending with config.json', () => {
    const p = getDefaultConfigPath();
    expect(p).toMatch(/config\.json$/);
  });
});
