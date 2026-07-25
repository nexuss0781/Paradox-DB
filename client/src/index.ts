export { ClientEngine } from './engine.js';
export { SyncManager } from './sync-manager.js';
export { loadConfig, getDefaultConfigPath } from './config.js';
export {
  DatabaseNotOpenError,
  SQLiteError,
  EncryptionError,
  ConfigError,
  ConflictError,
  RateLimitError,
  AuthenticationError,
  NetworkError,
} from './errors.js';
export type { ClientConfig, QueryResult, SelectOptions } from './types.js';
