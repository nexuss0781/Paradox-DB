export { ClientEngine } from './engine.js';
export { drizzle } from './drizzle.js';
export type { ParadDrizzleDatabase, ParadDrizzleSource } from './drizzle.js';
export { connect, ParadConnection, SyncDaemon, parseUrl, generateUrl, redactUrl, getCanonicalDatabaseUrl, recoverCanonicalDatabaseUrl, registerCanonicalDatabaseUrl, dbStateKey, generatePassphrase } from './connection.js';
export type { ParsedUrl, ConnectOptions } from './connection.js';
export { GatewayClient, isConnectivityError } from './gateway.js';
export type { UploadParams, UploadResult, DownloadResult, DatabaseUrlResponse, StatusResponse } from './gateway.js';
export { loadConfig, saveConfig, getDefaultConfigPath, configDir } from './config.js';
export * as state from './state.js';
export {
  DecryptionError,
  DatabaseNotOpenError,
  SQLiteError,
  EncryptionError,
  ConfigError,
  ConflictError,
  RateLimitError,
  AuthenticationError,
  NetworkError,
  GatewayError,
} from './errors.js';
export type { ClientConfig, QueryResult, SelectOptions } from './types.js';
