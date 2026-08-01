import { describe, it, expect } from 'vitest';
import { ClientEngine, loadConfig, getDefaultConfigPath, configDir } from '../src/index';

describe('ClientEngine', () => {
  it('should instantiate with explicit db path + passphrase', () => {
    const engine = new ClientEngine('/tmp/test-scaffold.db', 'secret');
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
    expect(config.sync.gateway_url).toBe('https://paradox-db.onrender.com/v1');
    expect(config.project_id).toBe('');
  });
});

describe('getDefaultConfigPath', () => {
  it('should return a path ending with config.json', () => {
    const p = getDefaultConfigPath();
    expect(p).toMatch(/config\.json$/);
  });
});

describe('configDir', () => {
  it('honors PARADOX_HOME at call time', () => {
    const saved = process.env.PARADOX_HOME;
    try {
      process.env.PARADOX_HOME = '/tmp/paradox-custom-home';
      expect(configDir()).toBe('/tmp/paradox-custom-home');
    } finally {
      if (saved === undefined) delete process.env.PARADOX_HOME;
      else process.env.PARADOX_HOME = saved;
    }
  });
});
