# Sync Workflows

Paradox-DB's sync model is designed to be **zero-config by default** and
**fully controllable when you need it**. This guide walks through the default
automatic workflow, what happens when things go wrong, and the optional manual
push/pull operations.

- [The model in one paragraph](#the-model-in-one-paragraph)
- [Default workflow: automatic sync](#default-workflow-automatic-sync)
- [What the daemon actually does](#what-the-daemon-actually-does)
- [Offline handling](#offline-handling)
- [Conflict resolution](#conflict-resolution)
- [Manual workflow: push / pull / pullVersion](#manual-workflow-push--pull--pullversion)
- [Choosing auto vs manual](#choosing-auto-vs-manual)

---

## The model in one paragraph

The gateway stores one canonical, **versioned** copy of each database. Every
successful upload becomes a new immutable version. Your local file is just
another copy. Sync is a two-step handshake: **push** sends your local bytes at
the version you last knew about, and **pull** fetches the latest remote bytes
and replaces your local file. Because the server serializes versions, two
writers can still race — and that race is resolved by a `409 Conflict` response,
which this SDK handles for you.

---

## Default workflow: automatic sync

Turn it on by simply connecting with a gateway:

```ts
import { connect } from 'parad';

const db = await connect({
  name: 'app',
  gatewayUrl: 'https://paradox-db.onrender.com/v1',
  apiKey: 'token',        // or use a connection string with auto-login
});
```

`autoSync` defaults to `true` (and only runs when a gateway is resolved), so a
background daemon starts immediately:

```
your process ── writes ──▶ encrypted SQLite (local)
                                │
            SyncDaemon (every ~2s)│  push when the local hash changed
                                ▼
                        gateway: version N+1
                                │
            SyncDaemon (every ~30s)│  pull the latest remote snapshot
                                ▼
                        local file replaced if remote changed
```

You keep writing SQL exactly as if it were a local database:

```ts
db.execute(`CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  payload TEXT,
  at TEXT DEFAULT CURRENT_TIMESTAMP
)`);

for (const evt of events) db.insert('events', { name: evt.name, payload: JSON.stringify(evt) });

await db.close(); // checkpoint + re-encrypt + stop daemon
```

No explicit `push()`/`pull()` calls are needed. The daemon also pushes on a
**change-detection** basis: it hashes the local file and only uploads when the
hash differs from the last one it pushed, so idle connections don't spam the
gateway.

> **Startup pull:** pass `pullOnStartup: true` to `connect` to fetch the latest
> remote snapshot before your app starts reading. A failure here is non-fatal —
> the daemon will catch up on its regular pull interval.

---

## What the daemon actually does

Timing (configurable via `pushIntervalMs` / `pullIntervalMs`):

| Loop | Default | What happens |
| --- | --- | --- |
| Tick | 500 ms | Evaluation loop; decides whether to act. |
| Push | 2 000 ms | If local hash ≠ last pushed hash, upload local bytes as a new version. |
| Pull | 30 000 ms | Download the latest remote version; apply it if it differs. |

Push bookkeeping lives in the per-database sync state file
(`<configDir>/<dbKey>.sync.json`):

- `last_local_hash` — the SHA-256 of the local file the last time it was pushed;
- `remote_version` — the remote version the local file is based on;
- `dirty` / `offline` — set when a push could not complete.

---

## Offline handling

The SDK distinguishes **connectivity errors** (DNS, refused, reset, timeout,
gateway `5xx`) from everything else via `isConnectivityError`.

1. A push fails with a connectivity error → the daemon marks the database
   **dirty** and **offline** (persisted to the state file), logs a single
   warning, and keeps trying on every push tick. It will not spam logs — the
   transition online→offline is logged once.
2. While offline, your app keeps writing normally. Local writes are never lost
   — the file keeps growing locally.
3. The next push that succeeds clears the offline/dirty flags and logs
   `Sync back online for <key> — pushing pending changes`. Your accumulated
   local edits are uploaded as one new version.

```ts
db.daemon?.offline;             // true while unreachable
db.daemon?.consecutiveFailures; // count of consecutive failures
db.daemon?.lastError;           // last error message
```

If the process is killed while offline, no data is lost: the next time the
daemon runs it detects the hash change and pushes everything.

---

## Conflict resolution

Two writers racing is the only case a versioned push protocol can't silently
paper over. The server rejects the loser with **`409 Conflict`** (carrying the
remote version). This SDK's policy is **local-wins**:

```
writer A: local bytes at v3 ──▶ upload v4     ✔
writer B: local bytes at v3 ──▶ upload ──▶ 409 Conflict
        B pulls remote (now at v4) as its new base
        B re-uploads ITS bytes as v5          ✔  (B's writes survive)
```

Concretely, on `409`:

1. `pull()` downloads the winning remote snapshot and applies it locally —
   this advances the local base version;
2. the **original local bytes are re-pushed as a brand-new version**;

so **local writes are never silently dropped**. Both writers' data ends up on
the server as successive versions; the gateway keeps the full version history,
and you can `rollback` or `pullVersion` at any time.

> The same local-wins logic runs inside `push()`, so manual and automatic
> pushes behave identically.

---

## Manual workflow: push / pull / pullVersion

If your application prefers explicit control — batch sync, backups, time-travel,
or sync-on-shutdown — disable the daemon and drive it yourself.

### Disable the daemon

```ts
const db = await connect({
  name: 'app',
  gatewayUrl: '…',
  apiKey: '…',
  autoSync: false,          // no background daemon
  pullOnStartup: true,      // optional: hydrate once at boot
});
```

### Push local changes

```ts
const version = await db.push();
// version = new remote version number, or null when no gateway is configured
```

`push()` uploads the current local file at the version the state file records
and, on `409`, automatically performs the pull-then-re-push local-wins sequence.
Resolves to the new remote version on success.

### Pull latest changes

```ts
const changed = await db.pull();
// true  → the remote snapshot was applied (local file replaced)
// false → nothing to do (identical bytes, empty download, or error)
```

`pull()` downloads the latest version, compares hashes, and only replaces your
local file when the remote actually differs. The engine is closed, the new
snapshot is re-encrypted to `dbPath`, and it is reopened — transparently.

### Pull a specific version (time-travel / restore)

```ts
const restored = await db.pullVersion(12);
// true  → the local file is now a copy of remote version 12
// false → download or apply failed
```

Useful for restoring from a backup point, auditing, or comparing historical
states. `pullVersion` applies unconditionally (no hash short-circuit) so you can
re-apply the same version repeatedly.

### Sync = push then pull

```ts
await db.push();
await db.pull();
```

### Server-side rollback

The gateway can also roll a database back itself; combined with a pull, you can
reset both ends:

```ts
import { GatewayClient } from 'parad';

const gw = new GatewayClient('https://paradox-db.onrender.com/v1', apiKey);
await gw.rollback('app', 8);   // server now points at version 8
await db.pull();               // apply version 8 locally
```

---

## Choosing auto vs manual

| Situation | Recommendation |
| --- | --- |
| Most apps; "it just syncs" | Default `autoSync: true` |
| Batch/cron jobs that sync once and exit | `autoSync: false`, explicit `push()`/`pull()` |
| Backups / restore / migrations | `pullVersion()` + `GatewayClient.rollback()` |
| Hot standby / replica hydration | `pullOnStartup: true` + daemon |
| Offline field devices | Default daemon; offline marking keeps you safe |
