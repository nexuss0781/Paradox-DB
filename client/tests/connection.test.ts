import { describe, it, expect } from 'vitest';
import { parseUrl, generateUrl, dbStateKey } from '../src/connection.js';
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
      email: '',
      password: '',
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

  it('parses email:password in userinfo', () => {
    const p = parseUrl('parad://alice@example.com:secretpw@local/myproj/mydb?passphrase=secret');
    expect(p.email).toBe('alice@example.com');
    expect(p.password).toBe('secretpw');
    expect(p.token).toBe('');
  });

  it('parses token in userinfo', () => {
    const p = parseUrl('parad://tok-abc@local/myproj/mydb?passphrase=secret');
    expect(p.token).toBe('tok-abc');
    expect(p.email).toBe('');
    expect(p.password).toBe('');
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

  it('round-trips an email:password form', () => {
    const url = generateUrl('mydb', 'secret', 'https://g/v1', 'proj', '', 'alice@example.com', 'pw');
    const p = parseUrl(url);
    expect(p.email).toBe('alice@example.com');
    expect(p.password).toBe('pw');
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
