---
name: parad-db
description: Use when working with Paradox-DB or the `parad` package (TypeScript SDK on npm; Python twin `parad` on PyPI) — an encrypted SQLite database with cloud sync. Covers connect, the default auto-sync workflow, offline/conflict handling, and the manual push/pull fallback when sync fails.
---

# Paradox-DB / `parad`

## Mandatory Drizzle routing

When a task mentions **Drizzle**, `drizzle-orm`, `parad/drizzle`, Drizzle schemas, Drizzle migrations, Drizzle queries, or TypeScript ORM compatibility, **read `Drizzle.md` in this directory before inspecting or modifying code**. Treat `Drizzle.md` as the AI execution guide for the Phase 2 adapter; use `client/docs/DRIZZLE.md` for the implementation reference and examples. Follow the guide’s connection, schema, transaction, lifecycle, testing, and reporting workflow exactly. Do not redesign the adapter or invent a second connection path when the requested work fits the documented API.

## Mandatory SQLAlchemy routing

When a task mentions **SQLAlchemy**, `create_engine`, `Session`, SQLAlchemy models, Python ORM compatibility, DB-API, `parad.dbapi`, or the `parad://` SQLAlchemy dialect, **read `SQLAlchemy.md` in this directory before inspecting or modifying code**. Treat `SQLAlchemy.md` as the AI execution guide for the Phase 3 Python adapter; use `parad/docs/SQLALCHEMY.md` for the implementation reference and examples. Follow the guide’s authentication, `DATABASE_URL`, DB-API, engine, ORM, transaction, lifecycle, testing, and reporting workflow exactly. Do not create a second Python connection format or bypass the Parad engine when the documented API fits the task.

Encrypted-at-rest SQLite with cloud sync. On-disk file is a single AES-256-CBC
ciphertext blob (SQLite only exists decrypted in a temp file). `parad` is the
TypeScript SDK; the Python `parad` on PyPI is byte-compatible (same KDF/salt).

- Package: `npm install parad` (`import { connect } from 'parad'`), Node >= 18
- Gateway default: `https://paradox-db.onrender.com/v1`
- Versioned sync; every upload = new immutable version; **local-wins on conflict**

## Active gateway resolver

ParadoxDB can move between deployment domains. Integrations should first read
`https://paradox-domain.onrender.com/active-domain.json`, then use its
`gatewayUrl` value as the active gateway base and cache it for `ttlSeconds`.
The resolver is a public static discovery document only; it never contains an
API key or passphrase. Configure its `gatewayUrl` as the gateway API base,
including `/v1` when the deployment requires that path.

## Canonical database_url-first workflow

Prefer one canonical connection value everywhere. For a new project, create the database once, print the canonical URL deliberately, and store only that URL in the deployment secret `DATABASE_URL`:

```bash
parad auth login
parad init <database-name> --project <project-name> --print-database-url
```

The command creates or resolves the project/database, initializes the encrypted local file, pushes the first snapshot, persists `database_url` in `~/.paradox/config.json`, and prints the complete secret-bearing URL only when `--print-database-url` is present. Do not use `init` as a recovery command for a database whose original passphrase is unavailable: generated passphrases are not recoverable and a new init can replace the remote snapshot.

For an already provisioned database, retrieve the canonical value with the new owner-authenticated server recovery path:

```bash
parad url --print-database-url
# Alias:
parad database-url --print-database-url
```

When no local canonical value is available, the CLI resolves the owned project/database through the gateway and calls the explicit reveal endpoint. The gateway stores `database_url` encrypted at rest and never includes it in ordinary project/database responses. The revealed value is persisted locally as `config.database_url` so later processes can use one value without another lookup. Normal `parad url` output is redacted.

Local precedence remains `DATABASE_URL` environment variable, then persisted `config.database_url`, then server recovery, then a URL reconstructed from legacy split fields (`database_path`, `project_name`, `encryption.passphrase`, `sync.gateway_url`, and `sync.api_key`). A successful legacy reconstruction is immediately persisted as `config.database_url`. If neither the server nor local configuration has enough material, the command stops rather than inventing a value or running `init`.

Use the canonical URL directly in new applications:

```ts
const db = await connect(process.env.DATABASE_URL!);
```

```python
from parad import connect
import os

db = connect(url=os.environ["DATABASE_URL"])
```

The equivalent SDK helper is available in both languages:

```ts
import { getCanonicalDatabaseUrl } from 'parad';
const url = getCanonicalDatabaseUrl();
```

```python
from parad import get_canonical_database_url
url = get_canonical_database_url()
```

The active gateway resolver remains required only for provisioning or sync operations. Resolve `https://paradox-domain.onrender.com/active-domain.json` before authenticated gateway calls, then use its `gatewayUrl` value.

## Use an existing database after provisioning

Auto-sync is on by default whenever a gateway is resolved. Don't hand-drive
sync unless it fails.

```ts
import { connect } from 'parad';

// Quickest: local encrypted DB, no gateway.
const db = await connect({ name: 'myapp' });

// Or connect to a synced database (project-scoped, auto-login, auto-provisioned):
const db = await connect('parad://me@example.com:secret@local/acme/myapp?passphrase=hunter2');

// Just write SQL. The daemon pushes (~2s) and pulls (~30s) automatically.
db.execute('CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, task TEXT, done INTEGER DEFAULT 0)');
db.insert('todos', { task: 'ship it' });
const rows = db.select('todos', { done: 0 }, { orderBy: 'id DESC', limit: 10 });

await db.close(); // REQUIRED before exit — re-encrypts to disk, stops daemon
```

Return shapes (exact — don't guess):

```ts
db.execute(sql, params?)    // → { rows: any[], changes: number, lastInsertRowid: number }
db.insert(table, row)       // → number (lastInsertRowid)
db.insertMany(table, rows)  // → number[] (rowids; one transaction, atomic)
db.get(table, where?)       // → any | null (first matching row)
db.select(table, where?, options?)  // → any[] (array of row objects)
db.update(table, set, where)// → number (rows changed)
db.upsert(table, row, conflictColumns)  // → number (1 insert or update, 0 no-op)
db.delete(table, where)     // → number (rows deleted)
db.push()                   // → Promise<number | null> (remote version)
db.pull()                   // → Promise<boolean> (true if local file replaced)
```

`upsert` updates every non-conflict column on conflict; its `conflictColumns`
(must match a PRIMARY KEY/UNIQUE constraint) can be a string or array.

Options: `{ name, project, passphrase, url, dbPath, gatewayUrl, apiKey, autoSync,
pullOnStartup, pushIntervalMs, pullIntervalMs }`. `pullOnStartup` hydrates the
latest snapshot at boot (failures non-fatal).

Config/state live at `~/.paradox/` (`$PARADOX_HOME` overrides): `config.json`
plus one `<dbKey>.sync.json` per database — where to look when debugging sync.

## Connect resolution (canonical first, legacy preserved)

An explicit `url` argument is strongest. Otherwise, when no explicit legacy target (`name` or `dbPath`) is supplied, `DATABASE_URL` is checked before persisted `config.database_url`. Only when no canonical URL is available do split legacy settings participate.

| Concern | Canonical-first order |
| --- | --- |
| database URL | explicit `url` → `DATABASE_URL` → `config.database_url` → legacy reconstruction |
| dbPath | option → canonical URL name → `<configDir>/<name>.db` → `config.database_path` |
| passphrase | option → canonical URL query → `PARADOX_PASSPHRASE` → config → generated only for a new local DB |
| gateway | option → canonical URL query → `PARADOX_GATEWAY` → `config.sync.gateway_url` |
| apiKey | option → canonical URL token → `PARADOX_API_KEY` → auto-login credentials → `config.sync.api_key` |

Explicit `name`, `project`, and `dbPath` options remain supported for legacy applications and intentionally select a target instead of silently overriding it with an unrelated canonical URL.

Connection strings: `parad://[token@ | email:pass@]local[/project]/<name>[?passphrase=&gateway=&token=]`.
URL with a project ⇒ `connect` auto-provisions (ensureProject + ensureDatabase, idempotent)
and persists resolved ids/credentials to `config.json`.

## Recommended deployment setup

The gateway stores the canonical URL encrypted at rest in its database. Configure a dedicated `DATABASE_URL_ENCRYPTION_KEY` Fernet key on the gateway and keep it stable across restarts and deployments.

Use the canonical URL as the only deployment database secret:

```text
DATABASE_URL=parad://...
```

Do not set separate gateway/API-key/passphrase variables for a new deployment unless a legacy integration requires them. Keep the URL in a secret manager, never in source control or logs. If it is exposed, rotate the underlying API key and encryption passphrase as appropriate and issue a new canonical URL.

### Canonical URL retrieval and migration

Use the SDK or CLI recovery helper before attempting any database initialization. Recovery checks the ambient `DATABASE_URL` and local `config.database_url`, then calls the owner-authenticated gateway reveal endpoint for the resolved project/database, and only then falls back to legacy local reconstruction. The gateway stores the canonical value encrypted at rest and never reveals it through ordinary list/detail metadata.

```bash
parad url
parad url --print-database-url
```

For a database created before server URL storage, an owner must provide the already-known canonical value once:

```bash
parad url register 'parad://...'
```

Registration updates only the encrypted URL field. It must not run `init`, create a replacement database, pull a snapshot, push a snapshot, or overwrite remote data. A server cannot recover a historical URL or passphrase that was never stored; do not claim recovery is possible until registration has succeeded.

## Sync model

- Daemon: tick 500 ms; push if local sha256 hash changed (min 2 s apart); pull at most every 30 s.
- **Offline**: connectivity error (DNS/refused/reset/timeout, gateway 5xx, status 0) ⇒ mark
  dirty + offline in state, retry each tick, log transition once. Local writes never lost.
- **Conflict**: `409 Conflict` ⇒ local-wins: pull remote (advance base), re-push local bytes
  as a new version. Local writes are never silently dropped. Manual `push()` does the same.
- State per DB: `<configDir>/<dbKey>.sync.json` (`remote_version`, `last_local_hash`, `dirty`, `offline`).

```ts
db.daemon?.offline;                 // gateway unreachable?
db.daemon?.lastError;               // last failure message
db.daemon?.consecutiveFailures;
```

## Manual sync — only when auto-sync is off or failing

Turn the daemon off (`autoSync: false`) for batch/cron/one-shot jobs, or reach
for these when the daemon can't recover.

```ts
const db = await connect({ name: 'myapp', autoSync: false, pullOnStartup: true });

// CRUD (no sync involved)
const id = db.insert('todos', { task: 'manual flow' });
db.update('todos', { done: 1 }, { id });
const rows = db.select('todos', undefined, { orderBy: 'created_at DESC' });
db.delete('todos', { id });
const r = db.execute('SELECT COUNT(*) AS n FROM todos'); // r.rows[0].n

// Sync
const version = await db.push();        // → new remote version | null (no gateway)
const changed = await db.pull();        // → true if local file was replaced
await db.pullVersion(12);               // restore historical version (time-travel/backup)
await db.push(); await db.pull();       // full sync
```

Server-side rollback (reset both ends):

```ts
import { GatewayClient } from 'parad';
const gw = new GatewayClient('https://paradox-db.onrender.com/v1', apiKey);
await gw.rollback('myapp', 8);          // server points at v8
await db.pull();                        // hydrate it locally
```

CLI fallback (same operations, config-driven): `parad url [--print-database-url]`,
`parad database-url`, `parad push`, `parad pull [version]`, `parad sync`,
`parad status --json`, `parad versions`, `parad rollback <v>`,
`parad exec/insert/select/update/delete`, `parad shell`, `parad config show|set`.
`parad config show` redacts secret-bearing fields; use the explicit URL command only when intentionally copying the canonical secret.

## Errors (quick map)

| Error / status | Meaning | Fix |
| --- | --- | --- |
| `DecryptionError` | wrong passphrase / corrupt file | pass `passphrase` option, URL param, or `PARADOX_PASSPHRASE` |
| `SQLiteError` | SQL failed (has `.originalError`) | fix the statement |
| `GatewayError` 401 | bad/expired token | reconnect with `email:password@` to re-login |
| `GatewayError` 409 | version conflict | auto-resolved by `push()` (local-wins) |
| `GatewayError` 5xx / status 0 | gateway down / network | treated as offline; retry later |
| `push()` → `null` | no gateway configured | add `gatewayUrl`/`apiKey` or use a URL |

Use `isConnectivityError(err)` to distinguish offline from deterministic errors.

## Gotchas

- **Always `await db.close()`** before the process exits — the encrypted file on
  disk is only rewritten on close.
- `commit()`/`rollback()` are **no-ops** (each statement autocommits). For
  transactions: `db.execute('BEGIN') … db.execute('COMMIT')`.
- `select(table, where)` interpolates object **keys as column names** — never
  pass untrusted identifiers there; values are always parameterized.
- Default passphrase is literally `default`; set one for real deployments.
- Sync works cross-language: a DB written by TS `parad` opens in Python `parad`
  (PyPI package `parad`; `connect` takes a `url=` **keyword**, not positional):

  ```python
  from parad import connect
  db = connect(url="parad://me@example.com:secret@local/acme/myapp?passphrase=hunter2")
  db.execute("INSERT INTO todos (task) VALUES (?)", ("ship it",))
  print(db.execute("SELECT * FROM todos"))   # list[dict]
  db.push(); db.close()                       # auto_sync default True
  ```

- Files on the gateway are full-file versions — every push sends the whole DB
  (default size cap 50 MB).

## Security and credential rules

- Registration and login are gateway operations: `POST /v1/auth/register` and `POST /v1/auth/login`. Passwords must be at least 12 characters and are bcrypt-hashed by the gateway.
- API keys are generated by the gateway, not by the SDK. Send them in `X-API-Key`; do not use `Authorization: Bearer`.
- Users may have multiple named API keys. Keys are returned in plaintext only at creation time, stored as hashes, may expire, and can be revoked with `DELETE /v1/auth/api-keys/{key_id}`.
- Do not put passwords, API keys, or passphrases in production connection URLs. Prefer `PARADOX_API_KEY`, `PARADOX_PASSPHRASE`, or a secret manager.
- `generatePassphrase()` is exported by both SDKs and returns a cryptographically random 256-bit passphrase. `connect()` generates one automatically only for a new database when no passphrase is configured.
- Existing databases require an explicit passphrase. Set `allowLegacyDefault: true` (TypeScript) or `allow_legacy_default=True` (Python) only for a known legacy database encrypted with `default`.
- Generated passphrases are not recoverable by the gateway. Back them up securely before moving a database to another machine.
- See `SECURITY.md` for deployment requirements, key-management guidance, and limitations that remain outside the SDK.
