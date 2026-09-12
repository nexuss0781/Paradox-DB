# Paradox-DB ROADMAP

Remaining work after the server-side SQL session engine + Python SDK work.
Status as of the last session: gateway SQL engine and Python SQLAlchemy/DB-API
support implemented and passing locally (gateway: 12 tests, parad: 82 tests).
Nothing below is done yet.

## 1. TS SDK — expose the new SQL / session API

The Python SDK exposes `sql()`, `sql_session_status()`, `sql_session_close()`.
The TypeScript SDK (`client/`) is untouched and needs the same surface.

- [ ] Add `sql()`, `sql_session_status()`, `sql_session_close()` to `client/src/gateway.ts`
- [ ] Add result types (`SqlResult`, session status/info, `SqlParams`) to `client/src/types.ts` and export from `client/src/index.ts`
- [ ] Mirror the JSON wire format: `columns`, `rows`, `rowcount`, `lastrowid`, `changes`, `in_transaction`, `persisted_version`, `session`; the `__parad_bytes__` BLOB marker
- [ ] Vitest coverage for the three methods + blob encode/decode (model on `tests/` + the Python `test_dbapi.py`)
- [ ] Version bump + publish to npm (see SKILL/ship)

## 2. Python SDK — close SQLAlchemy gaps

- [ ] ORM integration test: declarative base, relationships, `sessionmaker`, bulk ops (only raw SQL + reflection is currently tested)
- [ ] Async: `create_async_engine` — currently unsupported (sync-only DBAPI); decide greenlet adapter or document as unsupported
- [ ] `executemany` batching — currently one HTTP round-trip per row
- [ ] Session eviction handling on the client: detect "session closed/not found" and transparently re-warm + retry
- [ ] Verify Alembic migrations against the dialect (or document unsupported features)
- [ ] Verify savepoints (`SAVEPOINT`/`RELEASE`), `VACUUM`, `ATTACH`, `PRAGMA`-heavy paths
- [ ] Document the dialect and `parad://` URL usage in `docs/api.md` and update `SKILL/parad-db/SKILL.md`

## 3. Gateway — session engine hardening

- [ ] Graceful shutdown: persist dirty sessions on process exit (wire `SqlSessionStore.stop()` into the FastAPI lifespan)
- [ ] Multi-worker story: `session_store` is in-memory per worker; decide sticky sessions (single worker for `/v1/.../sql`) or a shared store (Redis), else SQL state can diverge across workers
- [ ] Request limits: cap SQL statement size and `params` size; guard against unbounded result sets (currently `fetchall` into memory)
- [ ] Observability: active-session count / eviction metrics endpoint; don't log passphrases
- [ ] Per-user session quota + abuse protection on session creation

## 4. End-to-end verification

- [ ] Live test: SDK (Python, later TS) ↔ running gateway over real HTTP with a real Telegram channel — not mocked
- [ ] Verify commit creates a real new version and reconnects (client reopen after gateway restart / session eviction)

## 5. Release & deploy

- [ ] Deploy gateway with the new `/v1/databases/{id}/sql*` router
- [ ] Publish `parad` 2.3.0 to PyPI (with `[alchemy]` extra)
- [ ] Publish TS SDK after section 1
- [ ] Post-deploy smoke test on the live URL
