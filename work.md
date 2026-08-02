# Paradox-DB — SDK Hardening Status (work.md)

Last updated: 2026-08-02. This file is the single source of truth so any agent
(or the same agent in a fresh session) can resume the work exactly where it
left off.

## Objective
Harden the **TypeScript SDK** (`client/`, `@paradox/client`) and finish the
**Python SDK** (`parad/`) to parity with the already-hardened Python engine
core, implementing the SAME confirmed principles:

- Frozen encryption format, byte-compatible across TS and Python.
- Postgres-like connection string → project/db auto-provisioning → auto-login.
- Build SQL offline; background daemon auto-pushes **one push = one new
  version** (full-file snapshot, hash-diff detected).
- Offline → dirty flag → batch push on reconnect.
- Conflict 409 = **local-wins** (pull remote, re-push local bytes as new
  version; local data NEVER silently dropped).
- Manual `push`/`pull`/`sync`/`status`/`versions`/`rollback`/`shell`/`config`
  CLI commands intact.
- Web-server use = `auto_sync=False` + manual push/pull (+ `pull_on_startup`).

## DONE (Python engine core + SDK — previous phase, verified green)
- `parad/parad/` hardened: `crypto.py` (AES-256-CBC, PBKDF2-HMAC-SHA512
  salt=`b"paradox-salt"` 256k iters, 16-byte IV, PKCS7, SQLite-magic),
  `engine.py`, `state.py`, `config.py` (`config_dir()` call-time), `connection.py`,
  `gateway.py`, `watcher.py`, `types.py`, CLI commands.
- `parad/tests/`: **36/36 pytest pass** (crypto, engine, state, workflow T1-T4).
  Run: `cd parad && python3 -m pytest tests/ -q`.
- `parad/README.md` Quick Start rewritten (SDK-first).
- All work in `parad/` is uncommitted (git modified); do NOT lose.

## DONE (TS SDK — THIS phase, verified green)
Old broken modules DELETED (`sync-manager.ts`, `change-tracker.ts`,
`conflict-handler.ts`, `retry.ts`) with their tests.

New/rewritten in `client/src/`:
1. `crypto.ts` — byte-compatible AES-256-CBC (same salt/iterations/IV/PKCS7/
   magic as Python). `encryptFile/decryptFile/deriveKey/validatePassphrase`.
   **IMPORTANT:** uses `.setAutoPadding(false)` on both cipher and decipher —
   Node's default `autoPadding` appends an extra block and broke the
   Python-encrypted fixture (fixed).
2. `errors.ts` — `DecryptionError`, `GatewayError(statusCode, message, detail)`.
   Single `DecryptionError` class (crypto.ts re-exports it from errors.ts so
   `instanceof` works).
3. `gateway.ts` — `GatewayClient` (API-key `X-API-Key`, projects/databases CRUD +
   `ensureProject`/`ensureDatabase`, `upload` base64 file_data, `download` with
   x-version/x-message-id, `status`/`versions`/`rollback`),
   `isConnectivityError` (5xx/network=offline; 409=conflict not offline).
4. `state.ts` — project-scoped `<configDir>/<project>__<name>.sync.json`,
   dirty/offline/remote_version/remote_hash/last_local_hash/getSyncStatus.
5. `config.ts` — `configDir()` (call-time PARADOX_HOME), default gateway
   `https://paradox-db.onrender.com/v1`, db path `.db`.
6. `types.ts` — `ClientConfig` project_id/project_name/database_id.
7. `engine.ts` — encrypted blob ↔ plaintext temp ↔ better-sqlite3; `open(create)`,
   wrong passphrase → DecryptionError, idempotent `close()`, `getRawBytes()`,
   CRUD helpers. `execute()` uses prepared statements for BOTH reads and writes
   (parameterless `SELECT` now returns rows — previously `db.exec()` dropped
   them). **`get(table, where?) → any|null` (first row), `insertMany(table, rows)
   → number[]` (single better-sqlite3 transaction, atomic), `upsert(table, row,
   conflictColumns) → number` (changes: 1 insert/update, 0 no-op; conflict
   target must match a PK/UNIQUE constraint; non-conflict columns merged via
   `excluded.`; all-conflict row → DO NOTHING).** Note: better-sqlite3 returns a
   STALE `lastInsertRowid` after `ON CONFLICT DO UPDATE`, so `upsert` returns
   `.changes`, not rowid.
8. `connection.ts` — `parseUrl`/`generateUrl`/`dbStateKey`, `connect()`
   (auth order: explicit api_key > URL token > userinfo token > email:password
   login > config api_key; auto-provisioning + persist ids). Honors
   `options.project`/`options.name` (were ignored). `ParadConnection`
   (execute/commit/rollback/close/push/pull/pullVersion), `SyncDaemon` (hash-diff
   push ~2s, pull ~30s, offline tracking, 409 local-wins, pull before start).
9. `index.ts` — public API exports.
10. `cli.ts` — ESM-safe (`fileURLToPath(import.meta.url)`, `readline/promises`),
    no double `/v1`.

Tests (all in `client/tests/`, all green):
- `crypto.test.ts` (11) — incl. Python-encrypted fixture byte-compat.
- `engine.test.ts` (12), `state.test.ts` (8), `connection.test.ts` (18),
  `scaffold.test.ts` (4).
- `workflow.test.ts` (4) — in-process `MockGateway` (async `listen`, `/v1`
  prefix strip, `GET/POST /projects/{id}/databases`): T1 email:password
  auto-login+provisioning; T2 offline→dirty→recovery one-version push; T3 409
  local-wins loser preserved; T4 manual push/pullVersion revert.

**VERIFIED:** `npx tsc --noEmit` clean; `npx tsc` build OK; `npm run test`
(= `npx vitest run`) **57/57 pass**; `node dist/cli.js --help` works;
`package.json` has `"bin": { "parad": "dist/cli.js" }`.

## DONE (TS SDK 2.0.0 — swap better-sqlite3 → sql.js WASM, 2026-08-02)
- **Why:** `better-sqlite3` is a native module — its `node-gyp` build hangs
  `npm install` for end users; `node:sqlite` (Node built-in) was tried and
  rejected: requires Node >= 22 (most users on 18) and vitest 2.0.5 / vite
  5.4.21 cannot load it (`Failed to load url sqlite`). `sql.js` = real SQLite
  compiled to WASM → full SQL, byte-compatible files, Node >= 18, zero native
  deps, no build step, vitest bundles it.
- `client/src/engine.ts` rewritten: `open()` is now **async** (module-level
  `initSqlJs()` once, `new SQL.Database(bytes)`); DB lives **in WASM memory** —
  no temp file; `close()` = `db.export()` → `encryptFile` → write `dbPath`;
  `changes` = `getRowsModified()`; `lastInsertRowid` = `SELECT
  last_insert_rowid()` after INSERT; SELECT/PRAGMA/EXPLAIN via
  prepare/step/getAsObject; transactions via `db.run('BEGIN'/'COMMIT'/'ROLLBACK')`.
  `get`/`insertMany`/`upsert` helpers preserved. **Same encrypted on-disk
  format — v1.x files open unchanged, no migration.**
- `client/src/connection.ts`: `ParadConnection` constructor no longer opens the
  engine synchronously — added `async init()` (awaits `engine.open(true)`, then
  starts the sync daemon / fire-and-forget `pullOnStartup`); `connect()` now
  `await`s `init()` before returning. `SyncDaemon.pull`, `pull`, `pullVersion`
  `await` the re-open after writing new bytes.
- `client/package.json`: version **2.0.0**, `dependencies: { "sql.js": "^1.14.1" }`,
  `engines: { "node": ">=18" }`; removed `@types/better-sqlite3`; added
  `@types/sql.js`.
- `vitest.config.ts`: removed the `server.deps.external` node:sqlite shim.
- Docs updated to sql.js/Node >= 18: `client/README.md`, `docs/API.md`
  (`await engine.open(true)` example, async `open` row), `docs/ENCRYPTION.md`
  (in-memory lifecycle), `docs/ERRORS.md`, `docs/setup.md`, `docs/troubleshooting.md`
  (replaced "better-sqlite3 fails to install" with a WASM-loading note), and the
  skill gotcha in all 3 SKILL copies.
- **VERIFIED:** `npx tsc --noEmit` clean; `npx tsc` build OK; vitest **57/57**.

## NEXT (in order — resume here)
1. **KNOWN GAP — TS config path ignores PARADOX_HOME** (`client/src/config.ts`):
   ~~`loadConfig`/`saveConfig`/`getDefaultConfigPath` use module-level `DEFAULT_CONFIG_PATH`~~ 
   **FIXED**: now `defaultConfigPath()` resolves through call-time `configDir()`; all
   `DEFAULT_CONFIG_PATH` references replaced. TS suite re-verified 54/54 after the fix.
2. **`parad/tests/test_connection.py`** ~~(NOT written)~~ **DONE**: 20 tests —
   9 parse_url (all 6 forms + nested project + reject missing-name + unsupported
   scheme + paradox scheme), 4 generate_url round-trips, db_state_key scoping,
   and 6 auth-resolution order tests via monkeypatched `pc.GatewayClient` /
   `pc.ParadConnection` (explicit api_key > URL token > userinfo token >
   email:password fake login > config api_key; email/password without gateway
   raises). No network.
3. **Extend `parad/tests/test_workflow.py`** ~~(not done)~~ **DONE**: appended
   `test_manual_push_pull_creates_versions_and_reverts_local` (push v1/v2,
   download version=1 via real GatewayClient, `conn._apply_local`, SELECT reverts)
   and `test_manual_rollback_pull_interop` (emulates gateway-side rollback by
   re-publishing v1 bytes as v3 on the mock Store, then `conn.pull()` and SELECT
   reverts). Existing 4 tests untouched.
4. **Final verification** **DONE (2026-08-01)**: TS `npx tsc --noEmit` clean,
   `npx tsc` build OK, vitest **54/54**; Python pytest **58/58** (36 + 22 new);
   both CLIs `--help` exit 0.
5. **get/insertMany/upsert helpers** **DONE (2026-08-02)**: added to
   `client/src/engine.ts` (get→any|null, insertMany→number[] atomic,
   upsert→changes count; upsert conflict target must match PK/UNIQUE; stale
   lastInsertRowid on DO UPDATE → return `.changes`). 3 new engine tests,
   suite now **57/57**. Skill updated (removed "no helpers" warning, added the
   three shapes) and synced to all 3 copies (`SKILL/`, `.opencode/skills/`,
   `~/.config/opencode/skills/`). Committed + pushed.
6. Commit is NOT to be done unless explicitly requested (git status: 44 changed/untracked).

## Known gotchas / constraints
- `importlib.reload` in Python conftest swaps module `__dict__` in place —
  tests must read config via module object (`import parad.config as _cfg`;
  `_cfg.config_dir()`), NOT `from parad.config import CONFIG_DIR`.
- SyncDaemon offline→recovery: `_onSuccess` clears `offline` BEFORE logging
  "Sync back online" — wait_for must include the log condition.
- `sql.js` (SQLite WASM) — no native module, no build step. `open()` is async;
  DB is in memory until `close()` persists (encrypt `export()` to `dbPath`).
  In Node, crypto disables `autoPadding` for byte-compat with Python's explicit PKCS7.
- `client/package.json` `type: module`; all imports use `.js` suffixes.
- Live gateway `https://paradox-db.onrender.com/v1`; mock-gateway tests use
  `http://127.0.0.1:<port>/v1` and strip the `/v1` prefix in the router.

## Repo layout
- `client/` — TS SDK (this phase; done, green).
- `parad/` — Python SDK (done, hardened; 36 tests green).
- `gateway/` — FastAPI gateway v2.0.0 (API-key-only auth; live on Render).
- `shared/` — legacy shared TS types (out of date vs. live gateway; ignore).

## AUTH PHASE — API keys + enforce cloud auth (2026-08-02)
- **API-key-only (JWT dropped entirely)** per user decision. `get_current_user`
  requires `X-API-Key: pk_...` only; `Authorization: Bearer` is rejected 401.
  OpenAPI security scheme is just `APIKeyHeader` (no `HTTPBearer`).
- `generate_api_key()` issues `pk_<token_urlsafe(32)>`, shown once at issue
  time; only SHA-256 hash stored (`api_key_hash`, unique index, idempotent
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `init_db`).
- `POST /v1/auth/register` → `{user_id, email, username, api_key}` (no
  `access_token`). `POST /v1/auth/login` issues a **fresh** key, invalidating
  the previous one. `POST /v1/auth/api-key` mints/rotates (old invalidated).
  Register/login are rate-limited (429).
- **Enforcement**: every data endpoint requires auth, ownership scoped by
  `user_id`. Public only: `/`, `/health*`, `/metrics`, `/docs`, register/login.
- **Deployed + verified live on Render** (`85ea5cf`): 12/12 live checks pass —
  register returns only api_key, Bearer rejected 401, no-header 401, invalid
  key 401, login rotates + invalidates old key, projects/databases CRUD,
  upload+download round-trip (by id and by name), versions, mint rotates +
  invalidates, user scoping, notifications/test 401 without key.
- Local: Postgres down + no docker here, so DB-backed tests can't run
  locally; unit suite green (31 passed, incl. API-key hashing, no-JWT,
  rate limiter, health, auth-required stub checks).
- TS SDK (57/57) + Python SDK (58/58) both send `X-API-Key`, return
  `{api_key}` from login/register.
- Next (not done): TS SDK `PARADOX_API_KEY` env support (Python has it),
  web frontend.
