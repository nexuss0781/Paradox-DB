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
3. `gateway.ts` — `GatewayClient` (Bearer JWT, projects/databases CRUD +
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
- `better-sqlite3` is a native module; no SQLCipher. In Node, disable
  `autoPadding` for byte-compat with Python's explicit PKCS7.
- `client/package.json` `type: module`; all imports use `.js` suffixes.
- Live gateway `https://paradox-db.onrender.com/v1`; mock-gateway tests use
  `http://127.0.0.1:<port>/v1` and strip the `/v1` prefix in the router.

## Repo layout
- `client/` — TS SDK (this phase; done, green).
- `parad/` — Python SDK (done, hardened; 36 tests green).
- `gateway/` — FastAPI gateway v2.0.0 (unchanged; JWT-only contract).
- `shared/` — legacy shared TS types (out of date vs. live gateway; ignore).
