---
name: parad-db
description: Use when working with Paradox-DB or the `parad` package (TypeScript SDK on npm; Python twin `parad` on PyPI) — an encrypted SQLite database with cloud sync. Covers connect, the default auto-sync workflow, offline/conflict handling, and the manual push/pull fallback when sync fails.
---

# Paradox-DB / `parad`

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

## Default workflow — use this first

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

## Connect resolution (first match wins)

| Concern | Order |
| --- | --- |
| dbPath | option → `<configDir>/<name>.db` → URL name → `config.database_path` |
| passphrase | option → URL `?passphrase=` → `PARADOX_PASSPHRASE` → `'default'` |
| gateway | option → URL `?gateway=` → `config.sync.gateway_url` |
| apiKey | option → URL token → `email:password@` auto-login (token saved to config) → `config.sync.api_key` |

Connection strings: `parad://[token@ | email:pass@]local[/project]/<name>[?passphrase=&gateway=&token=]`.
URL with a project ⇒ `connect` auto-provisions (ensureProject + ensureDatabase, idempotent)
and persists resolved ids/credentials to `config.json`.

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

CLI fallback (same operations, config-driven): `parad push`, `parad pull [version]`,
`parad sync`, `parad status --json`, `parad versions`, `parad rollback <v>`,
`parad exec/insert/select/update/delete`, `parad shell`, `parad config show|set`.

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
