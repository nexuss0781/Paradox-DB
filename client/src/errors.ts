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
