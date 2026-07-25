export class DatabaseNotOpenError extends Error {
  constructor(message = 'Database not open') {
    super(message);
    this.name = 'DatabaseNotOpenError';
  }
}

export class SQLiteError extends Error {
  originalError: Error;
  constructor(message: string, originalError: Error) {
    super(message);
    this.name = 'SQLiteError';
    this.originalError = originalError;
  }
}

export class EncryptionError extends Error {
  constructor(message = 'Wrong passphrase or corrupted database') {
    super(message);
    this.name = 'EncryptionError';
  }
}

export class ConfigError extends Error {
  constructor(message = 'Invalid configuration') {
    super(message);
    this.name = 'ConfigError';
  }
}

export class ConflictError extends Error {
  remoteVersion: number;
  yourVersion: number;
  remoteMessageId: string;

  constructor(remoteVersion: number, yourVersion: number, remoteMessageId: string) {
    super(`Conflict: remote v${remoteVersion} vs your v${yourVersion}`);
    this.name = 'ConflictError';
    this.remoteVersion = remoteVersion;
    this.yourVersion = yourVersion;
    this.remoteMessageId = remoteMessageId;
  }
}

export class RateLimitError extends Error {
  retryAfterSeconds: number;

  constructor(retryAfterSeconds: number) {
    super(`Rate limited: retry after ${retryAfterSeconds}s`);
    this.name = 'RateLimitError';
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export class AuthenticationError extends Error {
  constructor(message = 'Authentication failed') {
    super(message);
    this.name = 'AuthenticationError';
  }
}

export class NetworkError extends Error {
  constructor(message = 'Network error') {
    super(message);
    this.name = 'NetworkError';
  }
}
