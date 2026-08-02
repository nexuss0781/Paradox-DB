# API Reference

All symbols are exported from the package root (`import { … } from 'parad'`).
SQL runs on **SQLite via `sql.js`** (SQLite compiled to WASM), so the package has
**no native dependencies** and works on Node >= 18.

## Exports

```ts
// Engine / connection
connect, ParadConnection, SyncDaemon
parseUrl, generateUrl, dbStateKey

// Low-level gateway HTTP client
GatewayClient, isConnectivityError

// Config + persistent sync state
loadConfig, saveConfig, getDefaultConfigPath, configDir
state  // namespace: { loadState, saveState, getRemoteVersion, setRemoteVersion,
       //   getLastLocalHash, setLastLocalHash, markDirty, clearDirty, isDirty,
       //   setOffline, isOffline, getSyncStatus, sanitizeStateKey }

// Errors
DecryptionError, DatabaseNotOpenError, SQLiteError, EncryptionError,
ConfigError, ConflictError, RateLimitError, AuthenticationError,
NetworkError, GatewayError

// Types
ClientConfig, QueryResult, SelectOptions, ParsedUrl, ConnectOptions
```

---

## `connect(options | url)`

The single entry point. Opens (or creates) an encrypted database, resolves
credentials, provisions the remote project/database when needed, and optionally
starts background syncing.

```ts
async function connect(opts: ConnectOptions | string): Promise<ParadConnection>
```

Passing a string is shorthand for `connect({ url: string })`.

### Options

```ts
interface ConnectOptions {
  /** Database name. Used to derive the default local path: <configDir>/<name>.db */
  name?: string;
  /** Project (folder) on the gateway. Scoped databases sync to a project. */
  project?: string;
  /** Encryption passphrase. Default: 'default' (see resolution order below). */
  passphrase?: string;
  /** Full `parad://…` connection string (takes precedence over name/project). */
  url?: string;
  /** Explicit local path to the encrypted database file. */
  dbPath?: string;
  /** Gateway base URL, e.g. https://paradox-db.onrender.com/v1 */
  gatewayUrl?: string;
  /** Bearer token for the gateway. */
  apiKey?: string;
  /** Start the background sync daemon. Default: true (only when a gateway is set). */
  autoSync?: boolean;
  /** Pull the latest remote snapshot immediately after connecting. Default: false. */
  pullOnStartup?: boolean;
  /** Daemon push cadence in ms. Default: 2000. */
  pushIntervalMs?: number;
  /** Daemon pull cadence in ms. Default: 30000. */
  pullIntervalMs?: number;
}
```

### Resolution order

| Concern | Sources, first match wins |
| --- | --- |
| `dbPath` | `options.dbPath` → `<configDir>/<name>.db` → `<configDir>/<url name>.db` → `config.database_path` |
| `passphrase` | `options.passphrase` → `url ?passphrase=` → `PARADOX_PASSPHRASE` env → `'default'` |
| `gatewayUrl` | `options.gatewayUrl` → `url ?gateway=` → `config.sync.gateway_url` |
| `apiKey` | `options.apiKey` → `url ?token=` / `token@` → **email:password auto-login** → `config.sync.api_key` |

When credentials come from an `email:password` pair, `connect` calls
`POST /auth/login`, uses the returned `access_token`, and persists it into
`config.json` so later connections are seamless.

### Provisioning

If the connection string includes a project path (`parad://…@local/<project>/<name>`)
and a gateway is resolved, `connect` calls `ensureProject(project)` and
`ensureDatabase(projectId, name)` (create-if-missing), then saves the resulting
`project_id`, `database_id`, and resolved gateway/credentials to `config.json`.

### Example

```ts
const db = await connect({
  name: 'inventory',
  project: 'acme',
  passphrase: 's3cret',
  gatewayUrl: 'https://paradox-db.onrender.com/v1',
  apiKey: 'your-bearer-token',
  autoSync: true,
  pullOnStartup: true,
});
```

---

## `class ParadConnection`

The user-facing connection. Wraps a `ClientEngine` and optionally a `SyncDaemon`.

### Properties

| Property | Type | Description |
| --- | --- | --- |
| `engine` | `ClientEngine` | The underlying encrypted SQLite engine. |
| `daemon` | `SyncDaemon \| null` | The background sync daemon, or `null` when `autoSync` is off / no gateway. |
| `isConnected` | `boolean` | Whether the SQLite database is currently open. |
| `dbKey` | `string` | Stable sync key (`<project>/<name>` or `<name>`). |

### Methods

#### `execute(sql, params?)`
```ts
execute(sql: string, params?: any[]): { rows: any[]; changes: number; lastInsertRowid: number }
```
Run raw SQL. `SELECT`/`PRAGMA`/`EXPLAIN` return rows; everything else returns
`changes` and `lastInsertRowid`. Parameterized statements use `?` placeholders.

#### `push()`
```ts
push(): Promise<number | null>
```
Manually push the current local database to the gateway. Resolves to the new
remote version number, or `null` when no gateway is configured. If the gateway
rejects with a 409 conflict, the local snapshot is preserved and re-pushed as a
new version (local-wins — see [SYNC.md](SYNC.md#conflict-resolution)).

#### `pull()`
```ts
pull(): Promise<boolean>
```
Manually pull the latest remote snapshot and apply it locally. Returns `true`
when the local database was replaced, `false` when there was nothing new (bytes
identical), the download was empty, or it failed.

#### `pullVersion(version)`
```ts
pullVersion(version: number): Promise<boolean>
```
Pull and apply a **specific historical version**. Returns `true` on success.
Useful for time-travel, restore-from-backup, or auditing.

#### `close()`
```ts
close(): void
```
Stops the sync daemon and closes the engine. The engine re-encrypts the
in-memory database and writes it back to `dbPath` — call this before your
process exits so local changes are persisted to disk.

#### `commit()` / `rollback()`
```ts
commit(): void
rollback(): void
```
No-ops (API parity with the Python SDK). Each statement auto-commits. Use SQL
transactions explicitly if needed: `execute('BEGIN') … execute('COMMIT')`.

---

## `class SyncDaemon`

Background sync loop created by `ParadConnection` when `autoSync` is enabled and
a gateway is configured. It ticks every **500 ms** and:

- pushes when the local database hash changed and at least `PUSH_INTERVAL`
  (default **2000 ms**) has elapsed since the last push attempt;
- pulls at most once per `PULL_INTERVAL` (default **30 000 ms**).

You normally never construct one directly, but you can inspect a running
connection's daemon:

```ts
const db = await connect({ name: 'app' }); // autoSync default true

db.daemon?.offline;                 // boolean
db.daemon?.consecutiveFailures;     // number
db.daemon?.lastError;               // string | null
db.daemon?.lastSync;                // timestamp | null
db.daemon?.isRunning;               // boolean
```

| Constructor option | Type | Default | Description |
| --- | --- | --- | --- |
| `engine` | `ClientEngine` | — | Engine to push/pull. |
| `dbName` | `string` | — | Database name for the gateway. |
| `gatewayUrl` | `string` | — | Gateway base URL. |
| `apiKey` | `string` | `''` | Bearer token. |
| `project` | `string \| null` | `null` | Project scope. |
| `databaseId` / `projectId` | `string` | `''` | Pre-resolved remote IDs (fallback to names). |
| `pushIntervalMs` | `number` | `2000` | Min interval between push attempts. |
| `pullIntervalMs` | `number` | `30_000` | Min interval between pull attempts. |

---

## `class ClientEngine`

Low-level encrypted SQLite engine. Owns the encryption/decryption lifecycle:

1. `open()` (async) decrypts `dbPath` into an in-memory `sql.js` database (or
   creates a fresh empty database when `create` is set);
2. all SQL operates on the in-memory database;
3. `close()` exports the bytes, re-encrypts, and writes the ciphertext back to
   `dbPath`.

```ts
const engine = new ClientEngine('/path/to/app.db', 'passphrase');
await engine.open(true);                 // create if missing
engine.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)');
engine.insert('t', { v: 'hello' });
const rows = engine.select('t', { v: 'hello' });
engine.close();                          // encrypt + write to disk
```

| Member | Description |
| --- | --- |
| `open(create = false)` | **Async.** Open the database. `create` makes a new empty DB when the file is missing/empty. |
| `close()` | Export, encrypt the in-memory DB, write to `dbPath`. |
| `isOpen` | Whether a SQLite handle is open. |
| `execute(sql, params?)` | Raw SQL → `{ rows, changes, lastInsertRowid }`. |
| `insert(table, row)` | Insert an object → `lastInsertRowid`. |
| `select(table, where?, options?)` | Query → `any[]`. `where` is an equality map; `options` = `{ orderBy, limit, offset }`. |
| `update(table, set, where)` | Update rows → number of changed rows. |
| `delete(table, where)` | Delete rows → number of deleted rows. |
| `getRawBytes()` | The **plaintext** SQLite bytes (used for hashing/upload). |
| `operationCount` / `resetOperationCount()` | Op counter for instrumentation. |
| `passphrase` | The passphrase the engine was constructed with. |

> **Warning:** `select()` builds `WHERE k = ?` from object keys. Keys are
> interpolated directly into SQL, so never pass untrusted identifiers as column
> names. Values are always parameterized.

---

## `class GatewayClient`

Thin HTTP client over the Paradox-DB gateway REST API. `GatewayClient` is also
exported so advanced users can drive the gateway directly.

```ts
const gw = new GatewayClient('https://paradox-db.onrender.com/v1', apiKey);
```

| Method | HTTP | Description |
| --- | --- | --- |
| `login(email, password)` | `POST /auth/login` | Exchange credentials → `{ access_token }`. |
| `registerEmail(email, username, password)` | `POST /auth/register` | Create an account → `{ access_token }`. |
| `authMe()` | `GET /auth/me` | Current user info. |
| `listProjects()` / `createProject(name, desc?)` | `GET/POST /projects` | Project management. |
| `ensureProject(name, desc?)` | — | Find or create a project by name. |
| `listDatabases(projectId)` / `createDatabase(projectId, name, desc?)` | `GET/POST /projects/:id/databases` | Database management. |
| `ensureDatabase(projectId, name, desc?)` | — | Find or create a database by name. |
| `upload(params)` | `POST /upload` | Push `file_bytes` (base64) at a `version`. Returns `{ request_id, message_id, version, uploaded_at }`. |
| `download(name?, version?, dbId?, projId?)` | `GET /download` | Returns `{ bytes, version, message_id }` (version from `x-version` header). |
| `status()` | `GET /status` | `{ user_id, databases: [{ name, latest_version, latest_message_id, pending_changesets, last_sync_at }] }`. |
| `versions(database_name)` | `GET /versions` | `{ database_name, versions: [{ version, message_id, uploaded_at, size_bytes }] }`. |
| `rollback(database_name, target_version)` | `POST /rollback` | Roll the database back server-side. |

```ts
interface UploadParams {
  database_name?: string;
  database_id?: string;
  project_id?: string;
  file_bytes: Buffer;       // plaintext SQLite bytes (engine.getRawBytes())
  version: number;          // expected remote version (0 for first push)
  version_type?: string;    // default 'full'
}
```

### `isConnectivityError(err)`

Returns `true` for network failures (DNS, refused, reset, timeout), transport
errors, and gateway `5xx`/status-`0` responses. `409` conflicts are **not**
connectivity errors — that distinction is what lets the sync layer treat
conflicts and outages differently.

---

## URL helpers

```ts
parseUrl(url: string): ParsedUrl
generateUrl(name, passphrase?, gatewayUrl?, project?, token?, email?, password?): string
dbStateKey(name: string, project?: string | null): string
```

- `parseUrl` validates the scheme (`parad:` or `paradox:`), requires a database
  name in the path, and extracts `passphrase`, `gateway`, `token`, `email`,
  `password`, and the project prefix.
- `generateUrl` builds a connection string from parts.
- `dbStateKey` produces the stable sync key (`<project>/<name>` or `<name>`).

See [CONNECTION_STRINGS.md](CONNECTION_STRINGS.md) for the full URL grammar.

---

## Config & state modules

```ts
configDir(): string                    // process.env.PARADOX_HOME || '~/.paradox'
getDefaultConfigPath(): string         // <configDir>/config.json
loadConfig(path?): ClientConfig        // deep-merged over defaults
saveConfig(config, path?): void        // writes pretty-printed JSON
```

```ts
state.getSyncStatus(dbKey): SyncState  // { database_name, remote_version,
                                       //   remote_hash, last_sync,
                                       //   last_local_hash, dirty, offline }
state.isDirty(dbKey) / state.markDirty(dbKey) / state.clearDirty(dbKey)
state.isOffline(dbKey) / state.setOffline(dbKey, offline)
state.getRemoteVersion(dbKey) / state.setRemoteVersion(dbKey, version, hash?)
state.getLastLocalHash(dbKey) / state.setLastLocalHash(dbKey, hash)
```

Sync state is persisted per database as `<sanitizedKey>.sync.json` inside the
config directory. See [CONFIGURATION.md](CONFIGURATION.md).

---

## Types

```ts
interface QueryResult {
  rows: any[];
  changes: number;
  lastInsertRowid: number;
}

interface SelectOptions {
  limit?: number;
  offset?: number;
  orderBy?: string;
}

interface ParsedUrl {
  name: string;
  project: string | null;
  passphrase: string;
  gateway_url: string;
  token: string;
  email: string;
  password: string;
}
```

For `ClientConfig`, see [CONFIGURATION.md](CONFIGURATION.md#clientconfig).
