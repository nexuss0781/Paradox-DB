import { describe, it, expect } from 'vitest';
import { RetryManager, DEFAULT_RETRY_CONFIG } from '../src/retry.js';

describe('RetryManager', () => {
  it('canRetry() returns true when under max attempts', () => {
    const rm = new RetryManager();
    expect(rm.canRetry()).toBe(true);
    rm.recordAttempt();
    rm.recordAttempt();
    expect(rm.canRetry()).toBe(true);
  });

  it('canRetry() returns false at max attempts', () => {
    const rm = new RetryManager({ maxAttempts: 3, delays: [0, 100, 200] });
    rm.recordAttempt();
    rm.recordAttempt();
    rm.recordAttempt();
    expect(rm.canRetry()).toBe(false);
  });

  it('getNextDelay() returns correct delays per attempt', () => {
    const rm = new RetryManager();
    expect(rm.getNextDelay()).toBe(0);
    rm.recordAttempt();
    expect(rm.getNextDelay()).toBe(5000);
    rm.recordAttempt();
    expect(rm.getNextDelay()).toBe(30000);
    rm.recordAttempt();
    expect(rm.getNextDelay()).toBe(120000);
    rm.recordAttempt();
    expect(rm.getNextDelay()).toBe(600000);
    rm.recordAttempt();
    expect(rm.getNextDelay()).toBe(3600000);
  });

  it('getNextDelay() returns last delay when past delays array', () => {
    const rm = new RetryManager({ maxAttempts: 10, delays: [0, 100] });
    rm.recordAttempt();
    rm.recordAttempt();
    rm.recordAttempt();
    expect(rm.getNextDelay()).toBe(100);
  });

  it('recordAttempt() increments attempt count', () => {
    const rm = new RetryManager();
    expect(rm.currentAttempt).toBe(0);
    rm.recordAttempt();
    expect(rm.currentAttempt).toBe(1);
    rm.recordAttempt();
    expect(rm.currentAttempt).toBe(2);
  });

  it('reset() resets all state', () => {
    const rm = new RetryManager();
    rm.recordAttempt();
    rm.recordAttempt();
    rm.recordAttempt();
    expect(rm.currentAttempt).toBe(3);
    expect(rm.isFailed).toBe(false);
    rm.reset();
    expect(rm.currentAttempt).toBe(0);
    expect(rm.isFailed).toBe(false);
    expect(rm.canRetry()).toBe(true);
  });

  it('isFailed becomes true at max attempts', () => {
    const rm = new RetryManager({ maxAttempts: 2, delays: [0, 100] });
    expect(rm.isFailed).toBe(false);
    rm.recordAttempt();
    expect(rm.isFailed).toBe(false);
    rm.recordAttempt();
    expect(rm.isFailed).toBe(true);
  });

  it('delays match default spec: 0, 5s, 30s, 2m, 10m, 1h', () => {
    const expected = [0, 5000, 30000, 120000, 600000, 3600000];
    expect(DEFAULT_RETRY_CONFIG.delays).toEqual(expected);
    const rm = new RetryManager();
    for (let i = 0; i < expected.length; i++) {
      expect(rm.getNextDelay()).toBe(expected[i]);
      rm.recordAttempt();
    }
  });

  it('totalAttempts reflects config', () => {
    const rm = new RetryManager({ maxAttempts: 5, delays: [] });
    expect(rm.totalAttempts).toBe(5);
  });
});
