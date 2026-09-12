---
name: parad-db
description: Use when working with Paradox-DB or the `parad` package (TypeScript SDK on npm; Python twin `parad` on PyPI) — an encrypted SQLite database with cloud sync. Covers connect, the recommended autoSync workflow, the complete API, passphrase handling, sync behavior, manual push/pull, channels, CLI, and errors.
---

# Paradox-DB / `parad`

Paradox-DB is an encrypted SQLite database with automatic cloud sync.

- The database is a normal SQLite database. You write normal SQL.
- Every write is saved locally and automatically synced to the cloud gateway.
- Sync is handled for you. You do not manage it. You just write code.

**Package:** `npm install parad`, Node >= 18.
**Python:** `pip install parad` (same engine, same gateway, same data).
**Gateway:** `https://paradox-db.onrender.com/v1` (routers are under `/v1`).

This document has two parts:

1. **THE SIMPLE PATH** — the one recommended way to use `parad`. Follow this.
2. **The complete API reference** — everything `parad` can do, for when you need more.

---

# THE SIMPLE PATH

Use `autoSync` (it is on by default). The daemon syncs for you:

- Push (upload your changes) about every **2 seconds**.
- Pull (download remote changes) about every **30 seconds**.

You do three things and nothing else:

1. `connect(...)`
2. Write SQL
3. `await db.close()` when the process ends

## The full workflow, in order

```ts
import { connect } from 'parad';

// STEP 1 — Connect.
// Use the same options on every machine that shares this database.
const db = await connect({
  name: 'myapp',                       // database name (your choice)
  project: 'my-project',               // group your databases (your choice)
  gatewayUrl: 'https://paradox-db.onrender.com/v1',
  apiKey: process.env.PARADOX_TOKEN,   // your gateway API key
  passphrase: process.env.PARADOX_PASSPHRASE,  // your encryption secret
  autoSync: true,                      // <-- recommended. This is the default.
  pullOnStartup: true,                 // load the latest cloud snapshot at boot
});

// STEP 2 — Write SQL. That is all.
// Auto-sync makes these changes available on the gateway within seconds.
db.execute('CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, task TEXT, done INTEGER DEFAULT 0)');
db.engine.insert('todos', { task: 'ship it' });
db.engine.update('todos', { done: 1 }, { id: 1 });

// Read with plain SQL.
const { rows } = db.execute('SELECT * FROM todos WHERE done = 0 ORDER BY id DESC LIMIT 10');
console.log(rows);

// STEP 3 — Close when the process is done.
// This saves a clean snapshot and stops the sync daemon.
await db.close();
```

That is the entire simple path. Auto-sync handles the rest.

> **If you only remember one thing:** connect with `autoSync: true`, write SQL,
> call `db.close()` at the end. Never call `push`/`pull` yourself in this mode.

## A complete example (server or long-running process)

```ts
import { connect } from 'parad';

let db = await connect({
  name: 'nexuss-cronjob',
  project: 'nexuss',
  gatewayUrl: process.env.PARADOX_GATEWAY || 'https://paradox-db.onrender.com/v1',
  apiKey: process.env.PARADOX_TOKEN,
  passphrase: process.env.PARADOX_PASSPHRASE,
  autoSync: true,
  pullOnStartup: true,
});

// Any other module in the same process can share this connection.
// For separate processes: connect with the SAME name + passphrase + gateway,
// and the daemon keeps them all in sync through the gateway.

async function saveMonitor(m: any) {
  db.engine.upsert('monitors', m, 'id');          // insert or update
}

async function getMonitors(userId: string) {
  const { rows } = db.execute(
    'SELECT * FROM monitors WHERE "userId" = ? ORDER BY "createdAt" DESC',
    [userId]
  );
  return rows;
}

// Graceful shutdown: always close so the snapshot is written.
async function shutdown() {
  await db.close();
  process.exit(0);
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
```

---

# THE COMPLETE API

## connect()

The only entry point. Returns `Promise<ParadConnection>`.

```ts
const db = await connect({
  name: 'myapp',                    // database name
  project: 'acme',                  // optional. groups databases. auto-provisions on the gateway.
  passphrase: '...',                // encryption secret. see Passphrase below.
  url: 'parad://...',               // alternative: a connection string instead of options
  dbPath: '/abs/path/db',           // optional. where the local file lives.
  gatewayUrl: 'https://paradox-db.onrender.com/v1',
  apiKey: 'pk_...',                 // gateway API key
  autoSync: true,                   // default. sync daemon on.
  pullOnStartup: true,              // pull latest snapshot at boot (needs autoSync on)
  pushIntervalMs: 2000,             // default. min time between pushes.
  pullIntervalMs: 30000,            // default. min time between pulls.
  storageChannel: '-100...',        // optional. see Channels.
  logChannel: '-100...',            // optional. see Channels.
});

// Or a connection string (auto-provisions project + database):
const db = await connect('parad://me@example.com:secret@local/acme/myapp?passphrase=hunter2');
```

`project` + a gateway means the SDK creates the project and database on the
gateway automatically (`ensureProject` + `ensureDatabase`). This is idempotent
and safe to run on every boot.

## What `connect` returns: `db`

```ts
db.execute(sql, params?)            // → { rows: any[], changes: number, lastInsertRowid: number }
db.engine.get(table, where)         // → any | null   (first matching row)
db.engine.insert(table, row)        // → number       (lastInsertRowid)
db.engine.insertMany(table, rows)   // → number[]     (rowids, one transaction, atomic)
db.engine.update(table, set, where) // → number       (rows changed)
db.engine.delete(table, where)      // → number       (rows deleted)
db.engine.upsert(table, row, conflictColumns) // → number (1 insert-or-update, 0 no-op)
db.push()                           // → Promise<number | null>  (remote version)
db.pull()                           // → Promise<boolean> (true if local file was replaced)
db.pullVersion(v)                   // → restore a historical version (time travel)
db.close()                          // → save snapshot + stop daemon. ALWAYS call before exit.
db.daemon?.offline                  // → gateway unreachable right now?
db.daemon?.lastError                // → last failure message
db.daemon?.consecutiveFailures      // → number
```

### Reading rows: `db.execute` is the reliable way

There is no typed `select()` method. Use `db.execute` with `?` placeholders.

```ts
const { rows } = db.execute('SELECT * FROM users WHERE email = ?', ['a@b.com']);
const one = rows[0] ?? null;

// You may also see db.engine.select(table, where, options) in the source.
// It exists at runtime and accepts { orderBy, limit, offset }, but it is not
// in the public type declarations. Prefer db.execute for selects.
```

### Writing rows

```ts
const id = db.engine.insert('todos', { task: 'write docs', done: 0 });
db.engine.insertMany('todos', [{ task: 'a' }, { task: 'b' }]);
db.engine.update('todos', { done: 1 }, { id });
db.engine.upsert('todos', { id: 1, task: 'x' }, 'id'); // matches PRIMARY KEY/UNIQUE
db.engine.delete('todos', { id });
```

- `upsert` updates every non-conflict column when a conflict happens.
- `conflictColumns` must match a `PRIMARY KEY` or `UNIQUE` constraint.
- Values are always parameterized. Object **keys become column names** — never
  pass untrusted identifiers there.

## Passphrase resolution (first match wins)

| Concern | Order |
| --- | --- |
| passphrase | option → URL `?passphrase=` → `PARADOX_PASSPHRASE` env → `config.encryption.passphrase` → auto-generate (new DB) or `'default'` (existing DB) |
| gateway | option → URL `?gateway=` → `config.sync.gateway_url` |
| apiKey | option → URL token → `email:password@` auto-login (token saved to config) → `config.sync.api_key` |
| local file path | option `dbPath` → `<configDir>/<name>.db` → URL name → `config.database_path` |

Rules that matter:

- First connect with no passphrase and no existing file **auto-generates** a
  strong passphrase (32 random bytes), saves it to `~/.paradox/.env` and
  `config.json`, and prints it once. Save it. It is **not recoverable**.
  Reuse it on every machine that shares the database.
- An existing local DB file with no configured passphrase uses `'default'`
  (legacy compatibility).
- Gateway auth: an `apiKey` (starts with `pk_`) is the normal way. You can also
  put `email:password@` in a URL; the SDK logs in and saves the returned token.

## Where state lives

- `~/.paradox/config.json` — connection settings. `PARADOX_HOME` overrides the folder.
- `~/.paradox/<dbKey>.sync.json` — sync state per database
  (`remote_version`, `remote_hash`, `last_sync`, `last_local_hash`, `dirty`, `offline`).
  `dbKey` is `name`, or `project/name` flattened to `project__name`.
- `~/.paradox/.env` — the auto-generated passphrase, if one was created.

## How auto-sync works (so you can trust it)

- A daemon ticks every 500 ms. It pushes when your local data changed
  (min 2 s between pushes) and pulls at most every 30 s.
- With `pullOnStartup: true`, the latest cloud snapshot is loaded before the
  daemon starts. Boot failures here are non-fatal.
- **Offline:** if the gateway is unreachable (DNS, refused, timeout, 5xx),
  the daemon marks the state dirty and retries on every tick. Your local writes
  are never lost.
- **Conflicts:** a `409 Conflict` is resolved automatically as **local-wins** —
  pull the remote, then re-push your bytes as a new version. Local writes are
  never silently dropped.
- **Rate limits:** a `429` is retried by the daemon. It honors `retry_after`.
- Every gateway request has a 120 s timeout and retries transient failures
  (up to 3 attempts, backoff 1 s / 4 s). Deterministic 4xx errors are not retried.
- Gateway files are full-file versions: every push sends the complete snapshot
  (files up to 50 MB). Every upload creates a new immutable version number.

## Channels (optional, default is fine)

Snapshots and operation logs go to default gateway channels. You normally do
**not** need to change this.

```ts
const db = await connect({
  name: 'myapp',
  project: 'acme',
  gatewayUrl: 'https://paradox-db.onrender.com/v1',
  apiKey: 'pk_...',
  storageChannel: '-1004470017903',  // managed channel id for snapshots
  logChannel: '-1001234567890',      // optional. keep default if unsure.
});
```

`storageChannel` and `logChannel` also read the env vars
`PARADOX_STORAGE_CHANNEL` / `PARADOX_LOG_CHANNEL`. Without them, behavior is
unchanged (the default channels are used).

## Manual sync (use only when asked)

Auto-sync is the recommended mode. Manual sync exists for batch jobs, cron
runs, or when you specifically want `autoSync: false`.

```ts
const db = await connect({ name: 'myapp', autoSync: false, pullOnStartup: true });

// CRUD — no sync involved
db.engine.insert('todos', { task: 'manual flow' });
db.engine.update('todos', { done: 1 }, { id: 1 });

// Sync — you drive it
const version = await db.push();       // → new remote version | null
const changed = await db.pull();       // → true if local file was replaced
await db.pullVersion(12);              // restore a historical version
```

Server-side rollback (resets both ends):

```ts
import { GatewayClient } from 'parad';
const gw = new GatewayClient('https://paradox-db.onrender.com/v1', apiKey);
await gw.rollback('myapp', 8);         // server now points at v8
await db.pull();                       // hydrate it locally
```

## CLI

The same operations, from a terminal. Config-driven.

```
parad init <name>                  Create a new encrypted database
parad connect <url>                Connect via a connection string
parad exec <sql>                   Run raw SQL
parad insert <table> <json>        Insert a row
parad select <table> [where]       Query rows
parad update <table> <set> <where> Update rows
parad delete <table> <where>       Delete rows
parad push                         Push local changes to the gateway
parad pull [version]               Pull latest (or a specific version)
parad sync                         Push then pull
parad status                       Show sync status
parad versions                     List remote versions
parad rollback <version>           Roll back to a version
parad config show|set              Show / update config
parad shell                        Interactive REPL
```

## Errors (quick map)

| Error / status | Meaning | What to do |
| --- | --- | --- |
| `DecryptionError` | wrong passphrase or corrupt file | pass the correct `passphrase`, URL param, or `PARADOX_PASSPHRASE` |
| `SQLiteError` | the SQL failed (has `.originalError`) | fix the statement |
| `GatewayError` 401 | bad or rotated API key | re-login with `email:password@` (old key is invalidated) |
| `GatewayError` 409 | version conflict | auto-resolved by `push()` (local-wins) |
| `GatewayError` 429 | rate limited | wait `retry_after`; the daemon retries |
| `GatewayError` 5xx / status 0 | gateway down or network issue | treated as offline; it retries later |
| `push()` → `null` | no gateway configured | add `gatewayUrl` / `apiKey`, or use a URL |

Use `isConnectivityError(err)` to tell offline problems apart from real errors.

---

# Things to keep in mind

These are friendly notes, not restrictions. They prevent small surprises.

1. **Always `await db.close()` before the process exits.**
   `close()` writes a clean snapshot and stops the daemon. A hard
   `process.exit()` without `close()` can drop changes that have not been
   pushed yet (the daemon pushes every ~2 s).

2. **`undefined` values break inserts.**
   sql.js rejects `undefined` bindings with `tried to bind a value of an
   unknown type (undefined)`. Coerce missing values to `null` before writing:

   ```ts
   function toRow(obj: any) {
     const row: Record<string, any> = {};
     for (const [k, v] of Object.entries(obj)) row[k] = v === undefined ? null : v;
     return row;
   }
   db.engine.insert('monitors', toRow(monitor));
   ```

3. **A cold gateway can reject the very first `connect()`.**
   The gateway is a free Render service and can hibernate. Its first response
   after wake-up may be a `502`, and `connect()` provisioning throws without
   retrying. Wrap `connect` in a small retry loop for resilience:

   ```ts
   async function connectWithRetry() {
     let lastErr: unknown;
     for (let attempt = 1; attempt <= 6; attempt++) {
       try {
         return await connect({ /* your options */ });
       } catch (err) {
         lastErr = err;
         await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** attempt, 30000)));
       }
     }
     throw lastErr;
   }
   ```

4. **`pullOnStartup` only works when `autoSync` is on.**
   The startup pull lives inside the auto-sync path. If you disable autoSync,
   call `await db.pull()` yourself when you want the latest snapshot.

5. **Set the passphrase env var before importing `parad`.**
   ESM imports are hoisted. If `connect()` runs before your `process.env`
   assignment, `parad` auto-generates a different passphrase. Load `.env`
   first, or use `await import('parad')` after setting env.

6. **`commit()` / `rollback()` are no-ops** (statements autocommit).
   For transactions: `db.execute('BEGIN') ... db.execute('COMMIT')`.

7. **Sync works across languages.**
   A database written by TypeScript `parad` opens in Python `parad`
   (PyPI package `parad`). Pass the URL as the `url=` keyword — the first
   positional argument is `name`, not the URL.

   ```python
   from parad import connect
   db = connect(url="parad://me@example.com:secret@local/acme/myapp?passphrase=hunter2")
   db.execute("INSERT INTO todos (task) VALUES (?)", ("ship it",))
   print(db.execute("SELECT * FROM todos"))   # list[dict]
   db.push(); db.close()
   ```

   Python surface: `execute/insert/select/update/delete/get_raw_bytes` on the
   engine; `push`/`pull`/`close` on the connection. Python has no
   `get`/`upsert`/`insertMany`/`pullVersion`.
