export interface RetryConfig {
  maxAttempts: number;
  delays: number[];
}

export const DEFAULT_RETRY_CONFIG: RetryConfig = {
  maxAttempts: 6,
  delays: [0, 5000, 30000, 120000, 600000, 3600000],
};

export class RetryManager {
  private config: RetryConfig;
  private attempt: number = 0;
  private lastAttemptTime: number = 0;
  private _failed: boolean = false;

  constructor(config: RetryConfig = DEFAULT_RETRY_CONFIG) {
    this.config = config;
  }

  canRetry(): boolean {
    return this.attempt < this.config.maxAttempts;
  }

  getNextDelay(): number {
    if (this.attempt >= this.config.delays.length) {
      return this.config.delays[this.config.delays.length - 1];
    }
    return this.config.delays[this.attempt];
  }

  recordAttempt(): void {
    this.attempt++;
    this.lastAttemptTime = Date.now();
    if (this.attempt >= this.config.maxAttempts) {
      this._failed = true;
    }
  }

  reset(): void {
    this.attempt = 0;
    this.lastAttemptTime = 0;
    this._failed = false;
  }

  get isFailed(): boolean {
    return this._failed;
  }

  get currentAttempt(): number {
    return this.attempt;
  }

  get totalAttempts(): number {
    return this.config.maxAttempts;
  }
}
