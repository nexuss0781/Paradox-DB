import { afterEach, describe, it, expect } from 'vitest';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { vi } from 'vitest';
import { join } from 'node:path';
import { parseUrl, generateUrl, redactUrl, getCanonicalDatabaseUrl, recoverCanonicalDatabaseUrl, registerCanonicalDatabaseUrl, dbStateKey } from '../src/connection.js';
import { GatewayError, isConnectivityError } from '../src/gateway.js';

describe('parseUrl', () => {
  it('parses local-only form', () => {
    const p = parseUrl('parad://local/mydb?passphrase=secret');
    expect(p).toEqual({
      name: 'mydb',
      project: null,
      passphrase: 'secret',
      gateway_url: '',
      token: '',
    });
  });

  it('parses project-scoped with gateway', () => {
    const p = parseUrl('parad://local/myproj/mydb?passphrase=secret&gateway=https://paradox-db.onrender.com/v1');
    expect(p.name).toBe('mydb');
    expect(p.project).toBe('myproj');
    expect(p.gateway_url).toBe('https://paradox-db.onrender.com/v1');
  });

  it('parses explicit token query param', () => {
    const p = parseUrl('parad://local/myproj/mydb?passphrase=secret&gateway=https://g/v1&token=tok-abc');
    expect(p.token).toBe('tok-abc');
  });

  it('rejects email:password in userinfo', () => {
    expect(() => parseUrl('parad://alice@example.com:secretpw@local/myproj/mydb?passphrase=secret')).toThrow(/retired/);
  });

  it('parses token in userinfo', () => {
    const p = parseUrl('parad://tok-abc@local/myproj/mydb?passphrase=secret');
    expect(p.token).toBe('tok-abc');
  });

  it('parses nested project path', () => {
    const p = parseUrl('parad://local/myproj/sub/mydb?gateway=https://g/v1');
    expect(p.project).toBe('myproj/sub');
  });

  it('rejects missing db name', () => {
    expect(() => parseUrl('parad://local/')).toThrow();
  });

  it('rejects unsupported scheme', () => {
    expect(() => parseUrl('postgres://local/mydb')).toThrow();
  });
});

describe('generateUrl', () => {
  it('round-trips a token form', () => {
    const url = generateUrl('mydb', 'secret', 'https://g/v1', 'proj', 't1');
    const p = parseUrl(url);
    expect(p.name).toBe('mydb');
    expect(p.project).toBe('proj');
    expect(p.token).toBe('t1');
    expect(p.passphrase).toBe('secret');
    expect(p.gateway_url).toBe('https://g/v1');
  });

  it('produces a local-only URL', () => {
    const url = generateUrl('mydb', 'secret');
    expect(url).toBe('parad://local/mydb?passphrase=secret');
  });

  it('omits query params that are empty', () => {
    const url = generateUrl('mydb');
    expect(url).toBe('parad://local/mydb');
  });
});

describe('redactUrl', () => {
  it('removes credentials while preserving the database target', () => {
    const redacted = redactUrl('parad://token-abc@local/proj/mydb?gateway=https://g/v1&passphrase=secret');
    expect(redacted).toBe('parad://%3Credacted%3E@local/proj/mydb?gateway=https%3A%2F%2Fg%2Fv1');
    expect(redacted).not.toContain('token-abc');
    expect(redacted).not.toContain('secret');
  });
});

describe('getCanonicalDatabaseUrl', () => {
  const originalHome = process.env.PARADOX_HOME;
  const originalDatabaseUrl = process.env.DATABASE_URL;

  afterEach(() => {
    if (originalHome === undefined) delete process.env.PARADOX_HOME;
    else process.env.PARADOX_HOME = originalHome;
    if (originalDatabaseUrl === undefined) delete process.env.DATABASE_URL;
    else process.env.DATABASE_URL = originalDatabaseUrl;
    vi.restoreAllMocks();
  });

  it('prefers ambient DATABASE_URL', () => {
    const home = mkdtempSync('/tmp/parad-ts-url-');
    process.env.PARADOX_HOME = home;
    process.env.DATABASE_URL = 'parad://local/proj/newdb?passphrase=secret&gateway=https://g/v1';
    expect(getCanonicalDatabaseUrl()).toBe(process.env.DATABASE_URL);
    rmSync(home, { recursive: true, force: true });
  });

  it('returns the persisted canonical URL before legacy fields', () => {
    const home = mkdtempSync('/tmp/parad-ts-url-');
    process.env.PARADOX_HOME = home;
    delete process.env.DATABASE_URL;
    writeFileSync(join(home, 'config.json'), JSON.stringify({ database_url: 'parad://local/proj/db?passphrase=secret' }));
    expect(getCanonicalDatabaseUrl()).toBe('parad://local/proj/db?passphrase=secret');
    rmSync(home, { recursive: true, force: true });
  });

  it('recovers and persists the canonical URL from the owner gateway', async () => {
    const home = mkdtempSync('/tmp/parad-ts-url-');
    process.env.PARADOX_HOME = home;
    delete process.env.DATABASE_URL;
    writeFileSync(join(home, 'config.json'), JSON.stringify({
      database_path: '~/remote.db',
      project_name: 'proj',
      sync: { gateway_url: 'https://g/v1', api_key: 'owner-key' },
    }));
    const gatewayModule = await import('../src/gateway.js');
    vi.spyOn(gatewayModule.GatewayClient.prototype, 'listProjects').mockResolvedValue([{ id: 'p1', name: 'proj' }] as any);
    vi.spyOn(gatewayModule.GatewayClient.prototype, 'listDatabases').mockResolvedValue([{ id: 'd1', name: 'remote' }] as any);
    const reveal = vi.spyOn(gatewayModule.GatewayClient.prototype, 'getDatabaseUrl').mockResolvedValue({
      database_id: 'd1',
      database_url: 'parad://owner-secret@local/proj/remote?passphrase=secret&gateway=https://g/v1',
      configured: true,
      redacted: false,
    });
    const recovered = await recoverCanonicalDatabaseUrl();
    expect(recovered).toContain('parad://owner-secret@');
    expect(reveal).toHaveBeenCalledWith('d1', true);
    expect(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).database_url).toBe(recovered);
    rmSync(home, { recursive: true, force: true });
  });

  it('falls back safely when the gateway has no recovery endpoint', async () => {
    const home = mkdtempSync('/tmp/parad-ts-url-');
    process.env.PARADOX_HOME = home;
    delete process.env.DATABASE_URL;
    writeFileSync(join(home, 'config.json'), JSON.stringify({
      database_path: '~/legacy.db',
      project_name: 'proj',
      encryption: { passphrase: 'secret' },
      sync: { gateway_url: 'https://g/v1', api_key: 'token' },
    }));
    const gatewayModule = await import('../src/gateway.js');
    vi.spyOn(gatewayModule.GatewayClient.prototype, 'listProjects').mockResolvedValue([{ id: 'p1', name: 'proj' }] as any);
    vi.spyOn(gatewayModule.GatewayClient.prototype, 'listDatabases').mockResolvedValue([{ id: 'd1', name: 'legacy' }] as any);
    vi.spyOn(gatewayModule.GatewayClient.prototype, 'getDatabaseUrl').mockRejectedValue(new GatewayError(404, 'not found'));
    expect(parseUrl(await recoverCanonicalDatabaseUrl())).toMatchObject({ name: 'legacy', passphrase: 'secret' });
    rmSync(home, { recursive: true, force: true });
  });

  it('explicitly registers a known URL without opening the database', async () => {
    const home = mkdtempSync('/tmp/parad-ts-url-');
    process.env.PARADOX_HOME = home;
    delete process.env.DATABASE_URL;
    writeFileSync(join(home, 'config.json'), JSON.stringify({
      project_id: 'p1',
      project_name: 'proj',
      sync: { gateway_url: 'https://g/v1', api_key: 'owner-key' },
    }));
    const gatewayModule = await import('../src/gateway.js');
    vi.spyOn(gatewayModule.GatewayClient.prototype, 'listDatabases').mockResolvedValue([{ id: 'd1', name: 'remote' }] as any);
    const store = vi.spyOn(gatewayModule.GatewayClient.prototype, 'setDatabaseUrl').mockResolvedValue({
      database_id: 'd1', database_url: 'parad://<redacted>@local/proj/remote?gateway=https%3A%2F%2Fg%2Fv1', configured: true, redacted: true,
    });
    const url = 'parad://owner-secret@local/proj/remote?passphrase=secret&gateway=https://g/v1';
    expect(await registerCanonicalDatabaseUrl(url)).toBe(url);
    expect(store).toHaveBeenCalledWith('d1', url);
    expect(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).database_id).toBe('d1');
    rmSync(home, { recursive: true, force: true });
  });

  it('reconstructs and persists the canonical URL from legacy fields', () => {
    const home = mkdtempSync('/tmp/parad-ts-url-');
    process.env.PARADOX_HOME = home;
    delete process.env.DATABASE_URL;
    writeFileSync(join(home, 'config.json'), JSON.stringify({
      database_path: '~/legacy.db',
      project_name: 'proj',
      encryption: { passphrase: 'secret' },
      sync: { gateway_url: 'https://g/v1', api_key: 'token' },
    }));
    const url = getCanonicalDatabaseUrl();
    expect(parseUrl(url)).toMatchObject({ name: 'legacy', project: 'proj', passphrase: 'secret', token: 'token' });
    expect(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).database_url).toBe(url);
    rmSync(home, { recursive: true, force: true });
  });
});

describe('dbStateKey', () => {
  it('is project-scoped', () => {
    expect(dbStateKey('mydb', 'myproj')).toBe('myproj/mydb');
    expect(dbStateKey('mydb')).toBe('mydb');
    expect(dbStateKey('mydb', null)).toBe('mydb');
  });
});

describe('isConnectivityError', () => {
  it('classifies 5xx as offline', () => {
    expect(isConnectivityError(new GatewayError(503, 'x'))).toBe(true);
    expect(isConnectivityError(new GatewayError(500, 'x'))).toBe(true);
  });

  it('classifies 409 as NOT offline', () => {
    expect(isConnectivityError(new GatewayError(409, 'conflict'))).toBe(false);
  });

  it('classifies 4xx as NOT offline', () => {
    expect(isConnectivityError(new GatewayError(404, 'nf'))).toBe(false);
    expect(isConnectivityError(new GatewayError(401, 'auth'))).toBe(false);
  });

  it('classifies network error codes as offline', () => {
    const err = new Error('ECONNREFUSED');
    (err as any).code = 'ECONNREFUSED';
    expect(isConnectivityError(err)).toBe(true);
  });

  it('does not classify unknown errors as offline', () => {
    expect(isConnectivityError(new Error('boom'))).toBe(false);
  });
});
