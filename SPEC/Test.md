# Test Plan: Paradox-DB

**Version:** 1.0
**Status:** Draft
**Source:** Phase.md, Architecture.md

Every phase has a **Gate** — a set of tests that must pass at 100% before the next phase begins. No phase is considered complete until its gate is green.

---

## Conventions

| Term | Meaning |
|------|---------|
| **Unit test** | Single function/class, mocked dependencies, runs in < 100ms |
| **Integration test** | Real services (Docker), end-to-end flow, runs in < 30s |
| **Performance test** | Latency/throughput assertions, must meet threshold |
| **Smoke test** | Minimal sanity check, confirms "it works at all" |
| **Gate** | All tests in the gate block must pass. No exceptions. |
| **SKIP** | Test is skipped with reason if prerequisite not yet built |
| **FLAKY** | Test is allowed to fail in CI with `@flaky` annotation, must be fixed within 1 week |

**Test framework:**
- Client (TypeScript): Vitest or Jest
- Gateway (Python): pytest + pytest-asyncio + httpx
- Integration: Docker Compose + test scripts

---

## Phase 0: Project Scaffold

### 0.1 Unit Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 0.1.1 | `make lint` in `client/` exits 0 | Exit code 0, no stderr | |
| 0.1.2 | `make lint` in `gateway/` exits 0 | Exit code 0, no stderr | |
| 0.1.3 | `make lint` in `shared/` exits 0 | Exit code 0, no stderr | |
| 0.1.4 | `make typecheck` in `client/` exits 0 | Exit code 0 | |
| 0.1.5 | `make typecheck` in `gateway/` exits 0 | Exit code 0 | |
| 0.1.6 | `make dev` completes without error | Exit code 0, process starts | |
| 0.1.7 | `.env.example` exists and is valid | File exists, parseable | |
| 0.1.8 | `README.md` exists | File exists, non-empty | |

### 0.2 CI Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 0.2.1 | CI pipeline triggers on PR | Workflow file exists, syntax valid | |
| 0.2.2 | CI runs lint on PR | Lint step defined | |
| 0.2.3 | CI runs typecheck on PR | Typecheck step defined | |
| 0.2.4 | CI runs tests on PR | Test step defined | |

### 0.3 Gate

```
GATE-0:
  [x] 0.1.1 through 0.1.8 pass
  [x] 0.2.1 through 0.2.4 pass
  [x] CI runs green on empty commit
```

**Block:** Phase 1 and Phase 3 cannot start until GATE-0 is green.

---

## Phase 1: Local SQLite Engine

### 1.1 Unit Tests — ClientEngine

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 1.1.1 | `open()` creates new `.sqlcipher` file at path | File exists on disk | |
| 1.1.2 | `open()` with existing DB opens without error | No exception thrown | |
| 1.1.3 | `open()` with wrong passphrase throws `EncryptionError` | Error type matches, message readable | |
| 1.1.4 | `close()` triggers WAL checkpoint | `PRAGMA wal_checkpoint(FULL)` succeeds | |
| 1.1.5 | `close()` can be called multiple times safely | No double-free or exception | |
| 1.1.6 | `execute()` runs raw SQL | `CREATE TABLE` + `INSERT` succeeds | |
| 1.1.7 | `execute()` with parameterized query | `WHERE` clause with params returns correct row | |
| 1.1.8 | `execute()` on invalid SQL throws `SQLiteError` | Error type matches | |
| 1.1.9 | `insert()` creates row and returns insert ID | Returned ID > 0, row exists | |
| 1.1.10 | `select()` with no where returns all rows | Correct row count | |
| 1.1.11 | `select()` with where clause filters correctly | Only matching rows returned | |
| 1.1.12 | `select()` on empty table returns empty array | `[]` returned | |
| 1.1.13 | `update()` modifies correct rows | Affected row count correct, values changed | |
| 1.1.14 | `update()` with no matching where returns 0 affected | `affectedRows === 0` | |
| 1.1.15 | `delete()` removes correct rows | Affected row count correct, rows gone | |
| 1.1.16 | `delete()` with no matching where returns 0 affected | `affectedRows === 0` | |
| 1.1.17 | WAL mode enabled after open | `PRAGMA journal_mode` returns `wal` | |
| 1.1.18 | SQLCipher config: AES-256-CBC | `PRAGMA cipher_page_size` returns 4096 | |
| 1.1.19 | Config loader reads `~/.paradox/config.json` | Config object populated correctly | |
| 1.1.20 | Config loader uses defaults when file missing | Default config returned, no error | |

### 1.2 Unit Tests — Config

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 1.2.1 | Config path defaults to `~/.paradox/config.json` | Correct path resolved | |
| 1.2.2 | Config merges with defaults | Partial config fills in missing keys | |
| 1.2.3 | Config validates required fields | Throws on missing `database_path` | |

### 1.3 Performance Tests

| ID | Test | Threshold | Status |
|----|------|-----------|--------|
| 1.3.1 | Single insert latency | < 1ms (p95) | |
| 1.3.2 | Single select latency | < 1ms (p95) | |
| 1.3.3 | 10,000 sequential inserts | < 2 seconds total | |
| 1.3.4 | 1,000 sequential selects | < 1 second total | |
| 1.3.5 | Open existing DB (warm cache) | < 50ms | |

### 1.4 Gate

```
GATE-1:
  [x] 1.1.1 through 1.1.20 pass (20/20)
  [x] 1.2.1 through 1.2.3 pass (3/3)
  [x] 1.3.1 through 1.3.5 pass (5/5)
  [x] Test coverage > 80% on client/
  [x] No skipped tests
```

**Block:** Phase 2 cannot start until GATE-1 is green.

---

## Phase 2: Change Tracking

### 2.1 Unit Tests — ChangeTracker

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 2.1.1 | `startSession()` initializes session | Session active, buffer empty | |
| 2.1.2 | `exportChangeset()` returns `Buffer` | Type is Buffer, length > 0 after writes | |
| 2.1.3 | `exportChangeset()` with no writes returns empty/null | Buffer empty or null returned | |
| 2.1.4 | `importChangeset()` applies patch to fresh DB | All rows present after import | |
| 2.1.5 | `importChangeset()` with corrupted patch throws | `ChangesetError` thrown | |
| 2.1.6 | `truncateBuffer()` clears buffer | `bufferSize()` returns 0 | |
| 2.1.7 | `truncateBuffer()` does not lose committed data | Data still queryable after truncate | |
| 2.1.8 | `bufferSize()` returns byte count | Value increases with each write | |
| 2.1.9 | `changesetCount()` returns operation count | Increments by 1 per write | |
| 2.1.10 | Auto session start on `open()` | Session active immediately after open | |
| 2.1.11 | Conflict detection: conflicting changeset returns info | Conflict object returned with both versions | |
| 2.1.12 | Conflict detection: non-conflicting changeset applies cleanly | No conflict reported | |

### 2.2 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 2.2.1 | Write 100 rows → export → new DB → import → verify 100 rows | Row count matches, values match | |
| 2.2.2 | Write 1000 rows → export changeset size < DB size | Changeset < 10% of full DB size | |
| 2.2.3 | Export → truncate → export → second export empty | Truncate resets correctly | |
| 2.2.4 | Two conflicting sessions detected on import | Conflict reported, not silent | |
| 2.2.5 | Chain: write A → export → import to B → write C → export → import to B | B has A + C rows | |

### 2.3 Performance Tests

| ID | Test | Threshold | Status |
|----|------|-----------|--------|
| 2.3.1 | Export changeset for 1,000 writes | < 50ms | |
| 2.3.2 | Import changeset for 1,000 writes | < 100ms | |
| 2.3.3 | Changeset size for 1,000 single-row inserts | < 50KB | |

### 2.4 Gate

```
GATE-2:
  [x] 2.1.1 through 2.1.12 pass (12/12)
  [x] 2.2.1 through 2.2.5 pass (5/5)
  [x] 2.3.1 through 2.3.3 pass (3/3)
  [x] Test coverage > 80% on client/
  [x] No skipped tests
```

**Block:** Phase 6 cannot start until GATE-2 is green.

---

## Phase 3: Web Gateway Foundation

### 3.1 Unit Tests — Health Endpoints

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 3.1.1 | `GET /health` returns 200 | Status 200, body `{ "status": "ok" }` | |
| 3.1.2 | `GET /health/ready` returns 200 when PG + Redis up | Status 200 | |
| 3.1.3 | `GET /health/ready` returns 503 when PG down | Status 503, error message mentions postgres | |
| 3.1.4 | `GET /health/ready` returns 503 when Redis down | Status 503, error message mentions redis | |
| 3.1.5 | `GET /health/telegram` returns 200 when bot valid | Status 200 (requires real or mocked bot) | |
| 3.1.6 | `GET /health/telegram` returns 503 when bot invalid | Status 503 | |

### 3.2 Unit Tests — Stubs

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 3.2.1 | `POST /v1/upload` returns 501 | Status 501, message "not implemented" | |
| 3.2.2 | `GET /v1/download` returns 501 | Status 501 | |
| 3.2.3 | `GET /v1/versions` returns 501 | Status 501 | |
| 3.2.4 | `POST /v1/rollback` returns 501 | Status 501 | |
| 3.2.5 | `GET /v1/status` returns 501 | Status 501 | |

### 3.3 Unit Tests — Registry DB

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 3.3.1 | `user_channels` table exists | Table queryable | |
| 3.3.2 | `database_versions` table exists | Table queryable | |
| 3.3.3 | `sync_log` table exists | Table queryable | |
| 3.3.4 | Insert + select on `user_channels` | Round-trip data matches | |
| 3.3.5 | Insert + select on `database_versions` | Round-trip data matches | |
| 3.3.6 | Insert + select on `sync_log` | Round-trip data matches | |
| 3.3.7 | Foreign key constraint enforced | Insert invalid `user_id` fails | |

### 3.4 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 3.4.1 | `docker compose up` starts all 4 services | All containers running | |
| 3.4.2 | `GET /health` succeeds after compose up | 200 OK | |
| 3.4.3 | `GET /health/ready` succeeds after compose up | 200 OK | |
| 3.4.4 | Migrations run on fresh PostgreSQL | Tables created, no errors | |
| 3.4.5 | `docker compose down` cleans up | All containers stopped | |
| 3.4.6 | Redis connection pool works under load | 100 concurrent connections succeed | |

### 3.5 Gate

```
GATE-3:
  [x] 3.1.1 through 3.1.6 pass (6/6)
  [x] 3.2.1 through 3.2.5 pass (5/5)
  [x] 3.3.1 through 3.3.7 pass (7/7)
  [x] 3.4.1 through 3.4.6 pass (6/6)
  [x] Test coverage > 80% on gateway/
```

**Block:** Phase 4, Phase 5, Phase 11 cannot start until GATE-3 is green.

---

## Phase 4: Telegram Integration

### 4.1 Unit Tests — TelegramClient (Mocked)

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 4.1.1 | `createPrivateChannel()` calls Telegram API correctly | Mock called with correct args | |
| 4.1.2 | `createPrivateChannel()` returns `channel_id` | Returned value is string/number | |
| 4.1.3 | `createPrivateChannel()` on API error throws | `TelegramError` thrown | |
| 4.1.4 | `uploadFile()` sends document correctly | Mock `sendDocument` called with file bytes | |
| 4.1.5 | `uploadFile()` returns `message_id` | Returned value is string/number | |
| 4.1.6 | `uploadFile()` includes JSON caption | Caption is valid JSON with required fields | |
| 4.1.7 | `downloadFile()` retrieves file by `message_id` | Mock `getFile` called with correct ID | |
| 4.1.8 | `downloadFile()` returns matching bytes | Output bytes === input bytes | |
| 4.1.9 | `getFileMetadata()` returns file info | Size, name, type returned | |
| 4.1.10 | `getFileMetadata()` does not download file body | Mock body read not called | |

### 4.2 Unit Tests — Rate Limiter

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 4.2.1 | Rate limiter allows request under limit | Request passes | |
| 4.2.2 | Rate limiter blocks request over per-channel limit | 16th request in min blocked | |
| 4.2.3 | Rate limiter resets after window expires | After 61s, request passes again | |
| 4.2.4 | Rate limiter enqueues when blocked | Queue depth increments | |
| 4.2.5 | Rate limiter respects `retry-after` from Telegram | Waits specified duration | |

### 4.3 Unit Tests — Error Handling

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 4.3.1 | Transient error (timeout) triggers retry | 3 retries attempted | |
| 4.3.2 | Permanent error (400) fails immediately | 1 attempt, error raised | |
| 4.3.3 | Transient error after 3 retries fails permanently | `TelegramError` raised | |
| 4.3.4 | Retry delay increases exponentially | Delays: ~1s, ~2s, ~4s | |

### 4.4 Integration Tests (Real Telegram Bot)

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 4.4.1 | Create private channel in test Telegram account | Channel ID returned, channel visible | |
| 4.4.2 | Upload 1KB file to channel | `message_id` returned | |
| 4.4.3 | Upload 1MB file to channel | `message_id` returned, no timeout | |
| 4.4.4 | Download file by `message_id` | Bytes match upload | |
| 4.4.5 | Download nonexistent `message_id` throws | Error thrown | |
| 4.4.6 | Upload 50MB file (Bot API limit) | Completes within 30s | |
| 4.4.7 | Upload >50MB file rejects gracefully | Error message clear | |
| 4.4.8 | File naming convention correct | Filename matches `{db}_v{n}.sqlcipher` | |
| 4.4.9 | Caption metadata is valid JSON | `JSON.parse(caption)` succeeds | |
| 4.4.10 | Caption contains all required fields | `db_name`, `version`, `type`, `timestamp`, `hash` present | |

### 4.5 Performance Tests

| ID | Test | Threshold | Status |
|----|------|-----------|--------|
| 4.5.1 | Upload 1KB file round-trip | < 2 seconds | |
| 4.5.2 | Upload 1MB file round-trip | < 5 seconds | |
| 4.5.3 | Download 1MB file round-trip | < 5 seconds | |
| 4.5.4 | 10 sequential uploads (1KB each) | < 30 seconds total | |

### 4.6 Gate

```
GATE-4:
  [x] 4.1.1 through 4.1.10 pass (10/10)
  [x] 4.2.1 through 4.2.5 pass (5/5)
  [x] 4.3.1 through 4.3.4 pass (4/4)
  [x] 4.4.1 through 4.4.10 pass (10/10)
  [x] 4.5.1 through 4.5.4 pass (4/4)
  [x] Test coverage > 80% on gateway/telegram/
```

**Block:** Phase 6, Phase 7 cannot start until GATE-4 is green.

---

## Phase 5: Auth & Authorization

### 5.1 Unit Tests — Auth Middleware

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 5.1.1 | Valid API key passes auth | `user_id` extracted, request continues | |
| 5.1.2 | Invalid API key returns 401 | Status 401 | |
| 5.1.3 | Missing auth header returns 401 | Status 401 | |
| 5.1.4 | Valid JWT passes auth | `user_id` extracted, request continues | |
| 5.1.5 | Expired JWT returns 401 | Status 401 | |
| 5.1.6 | Tampered JWT returns 401 | Status 401 | |
| 5.1.7 | JWT with wrong secret returns 401 | Status 401 | |
| 5.1.8 | `user_id` from auth context, not request body | Body `user_id` ignored, auth `user_id` used | |

### 5.2 Unit Tests — Authorization

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 5.2.1 | User A cannot access User B's database | Status 403 | |
| 5.2.2 | User A cannot list User B's versions | Status 403 | |
| 5.2.3 | User A cannot trigger rollback on User B's database | Status 403 | |
| 5.2.4 | User A can access own database | Status 200 | |

### 5.3 Unit Tests — Registration

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 5.3.1 | `POST /v1/auth/register` creates user | User exists in Registry DB | |
| 5.3.2 | Registration returns API key | Non-empty string returned | |
| 5.3.3 | API key stored hashed in DB | Plaintext not in DB | |
| 5.3.4 | Registration provisions Telegram channel | Channel ID created | |
| 5.3.5 | Duplicate registration returns 409 | Status 409 | |

### 5.4 Unit Tests — Rate Limiting

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 5.4.1 | Request under limit passes | Status 200 | |
| 5.4.2 | Request over 60 req/min returns 429 | Status 429 | |
| 5.4.3 | 429 includes `retry_after_seconds` | Field present and numeric | |
| 5.4.4 | Rate limit resets after window | After 61s, requests pass | |

### 5.5 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 5.5.1 | Unauthenticated `GET /v1/status` → 401 | Status 401 | |
| 5.5.2 | Register → use returned API key → `GET /v1/status` → 200 | Full flow works | |
| 5.5.3 | Register User A + User B → A cannot see B's data | 403 | |
| 5.5.4 | Burst 100 requests in 1 min → 429 after 60th | Rate limit triggers | |
| 5.5.5 | JWT auth flow: register → get JWT → use JWT → 200 | Full JWT flow works | |

### 5.6 Gate

```
GATE-5:
  [x] 5.1.1 through 5.1.8 pass (8/8)
  [x] 5.2.1 through 5.2.4 pass (4/4)
  [x] 5.3.1 through 5.3.5 pass (5/5)
  [x] 5.4.1 through 5.4.4 pass (4/4)
  [x] 5.5.1 through 5.5.5 pass (5/5)
  [x] Test coverage > 80% on gateway/auth/
```

**Block:** Phase 6, Phase 7 cannot start until GATE-5 is green.

---

## Phase 6: Sync Push

### 6.1 Unit Tests — SyncManager (Client)

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 6.1.1 | `start()` begins background loop | Loop running, timer active | |
| 6.1.2 | `stop()` triggers final sync before exit | Sync called before shutdown | |
| 6.1.3 | `syncNow()` triggers immediate sync | Sync executed, not queued | |
| 6.1.4 | Timer trigger fires at configured interval | Sync fires at 30s ± 5s | |
| 6.1.5 | Ops-count trigger fires at threshold | Sync fires after 50th write | |
| 6.1.6 | Successful sync truncates buffer | `bufferSize()` drops to 0 | |
| 6.1.7 | Failed sync retains buffer | `bufferSize()` unchanged | |
| 6.1.8 | On 429, exponential backoff applied | Next attempt delayed ≥ 30s | |
| 6.1.9 | On 429, `retry_after_seconds` respected | Wait at least specified duration | |
| 6.1.10 | Changeset exported before upload | `exportChangeset()` called | |

### 6.2 Unit Tests — Gateway Upload Handler

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 6.2.1 | `POST /v1/upload` with valid file returns 200 | Status 200, `message_id` in response | |
| 6.2.2 | `POST /v1/upload` without auth returns 401 | Status 401 | |
| 6.2.3 | `POST /v1/upload` with invalid file format returns 400 | Status 400 | |
| 6.2.4 | `POST /v1/upload` acquires Redis lock | Lock acquired before Telegram call | |
| 6.2.5 | `POST /v1/upload` releases lock after success | Lock released in finally block | |
| 6.2.6 | `POST /v1/upload` releases lock after failure | Lock released even on error | |
| 6.2.7 | `POST /v1/upload` updates `database_versions` | Registry row updated with new `message_id` | |
| 6.2.8 | `POST /v1/upload` appends to `sync_log` | Log entry with status `success` | |
| 6.2.9 | `POST /v1/upload` computes SHA-256 hash | Hash matches `sha256(file_bytes)` | |
| 6.2.10 | `POST /v1/upload` with version mismatch returns 409 | Status 409, `remote_version` in response | |
| 6.2.11 | `POST /v1/upload` concurrent requests serialized | Second request waits for lock | |
| 6.2.12 | Lock timeout returns 503 | Status 503 after lock timeout | |

### 6.3 Unit Tests — Rate Limit Queue (Gateway)

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 6.3.1 | Queue accepts request under limit | Request forwarded | |
| 6.3.2 | Queue returns 429 when rate exceeded | Status 429, `retry_after_seconds` present | |
| 6.3.3 | Queue depth reported in 429 response | `queue_depth` field present | |

### 6.4 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 6.4.1 | Client writes 10 rows → sync fires → Gateway confirms | `message_id` returned, Registry updated | |
| 6.4.2 | Client writes → sync → `sync_log` has entry | Log entry with correct `request_id` | |
| 6.4.3 | Client writes → sync → buffer truncated | `bufferSize() === 0` after sync | |
| 6.4.4 | Client writes → Gateway down → buffer retained → Gateway up → sync succeeds | No data loss | |
| 6.4.5 | Two clients sync same DB → second gets 409 | Conflict detected | |
| 6.4.6 | Lock prevents concurrent uploads | Sequential, not parallel | |
| 6.4.7 | Sync trigger fires at configured interval (automated) | Sync event logged within 35s | |
| 6.4.8 | 100 sequential writes → sync → all in Telegram | 100 operations reflected in `sync_log` | |

### 6.5 Performance Tests

| ID | Test | Threshold | Status |
|----|------|-----------|--------|
| 6.5.1 | Client local write during sync | < 1ms (sync does not block write) | |
| 6.5.2 | Upload 10KB changeset round-trip | < 3 seconds | |
| 6.5.3 | Upload 100KB changeset round-trip | < 5 seconds | |
| 6.5.4 | Lock acquisition latency | < 100ms (p95) | |

### 6.6 Gate

```
GATE-6:
  [x] 6.1.1 through 6.1.10 pass (10/10)
  [x] 6.2.1 through 6.2.12 pass (12/12)
  [x] 6.3.1 through 6.3.3 pass (3/3)
  [x] 6.4.1 through 6.4.8 pass (8/8)
  [x] 6.5.1 through 6.5.4 pass (4/4)
  [x] Test coverage > 80% (client + gateway)
```

**Block:** Phase 8, Phase 9, Phase 10 cannot start until GATE-6 is green.

---

## Phase 7: Sync Pull

### 7.1 Unit Tests — Gateway Download Handler

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 7.1.1 | `GET /v1/download` with valid DB returns 200 | Status 200, binary body | |
| 7.1.2 | `GET /v1/download` without auth returns 401 | Status 401 | |
| 7.1.3 | `GET /v1/download` for nonexistent DB returns 404 | Status 404 | |
| 7.1.4 | `GET /v1/download?version=N` returns specific version | Correct version file | |
| 7.1.5 | `GET /v1/download` appends to `sync_log` | Log entry with operation `download` | |
| 7.1.6 | `GET /v1/download` streams file bytes correctly | Byte count matches | |
| 7.1.7 | `GET /v1/versions` returns version list | Array of version objects | |
| 7.1.8 | `GET /v1/versions` for nonexistent DB returns empty array | `[]` returned | |
| 7.1.9 | `GET /v1/versions` returns versions in descending order | Latest first | |
| 7.1.10 | `POST /v1/rollback` with valid version returns 200 | Status 200, new `message_id` | |
| 7.1.11 | `POST /v1/rollback` to nonexistent version returns 404 | Status 404 | |
| 7.1.12 | `POST /v1/rollback` creates new version entry | Registry has new row with incremented version | |

### 7.2 Unit Tests — Client Pull

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 7.2.1 | `pullLatest()` replaces local DB | Local file bytes match download | |
| 7.2.2 | `pullVersion(N)` downloads specific version | Correct version applied | |
| 7.2.3 | `pullOnStart()` triggers pull when local stale | Pull executed on open | |
| 7.2.4 | `pullOnStart()` skips pull when local current | No pull, local used | |
| 7.2.5 | Cold start: no local DB → pull before any operation | Pull blocks until complete | |
| 7.2.6 | Cold start: progress indicator emitted | Progress events fire | |

### 7.3 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 7.3.1 | Upload → download → bytes match | Round-trip integrity | |
| 7.3.2 | Upload 3 versions → `GET /v1/versions` returns 3 | Correct count and order | |
| 7.3.3 | Upload v1 → rollback to v1 → new v2 created | Version chain preserved | |
| 7.3.4 | Upload → client pull → local DB matches uploaded | End-to-end restore | |
| 7.3.5 | Cold start: delete local → pull → all data restored | Full recovery | |
| 7.3.6 | Download logs in `sync_log` | Entries present for all downloads | |

### 7.4 Performance Tests

| ID | Test | Threshold | Status |
|----|------|-----------|--------|
| 7.4.1 | Download 10KB file round-trip | < 3 seconds | |
| 7.4.1 | Download 100KB file round-trip | < 5 seconds | |
| 7.4.3 | Version list query | < 100ms | |
| 7.4.4 | Cold start pull 1MB database | < 10 seconds | |

### 7.5 Gate

```
GATE-7:
  [x] 7.1.1 through 7.1.12 pass (12/12)
  [x] 7.2.1 through 7.2.6 pass (6/6)
  [x] 7.3.1 through 7.3.6 pass (6/6)
  [x] 7.4.1 through 7.4.4 pass (4/4)
  [x] Test coverage > 80% (client + gateway)
```

**Block:** Phase 8, Phase 9, Phase 10 cannot start until GATE-7 is green.

---

## Phase 8: Conflict Detection & Resolution

### 8.1 Unit Tests — Conflict Detection

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 8.1.1 | Version mismatch returns 409 | Status 409 on upload | |
| 8.1.2 | 409 body contains `remote_version` | Field present, correct value | |
| 8.1.3 | 409 body contains `remote_message_id` | Field present | |
| 8.1.4 | 409 body contains `your_version` | Field present, matches client's base | |
| 8.1.5 | 409 body contains `resolution: "pull_before_push"` | Field present | |
| 8.1.6 | No conflict when versions match | Status 200 returned | |

### 8.2 Unit Tests — Client Conflict Handler

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 8.2.1 | On 409, client pulls latest version | Pull executed | |
| 8.2.2 | On 409, client retries push after pull | Second upload attempted | |
| 8.2.3 | On 409, losing changes overwritten (LWW) | Local DB reflects remote version | |
| 8.2.4 | Conflict logged with both hashes | `conflict_log` entry created | |
| 8.2.5 | Conflict log contains `local_version` and `remote_version` | Both fields present | |
| 8.2.6 | Conflict log contains `resolution: "lww"` | Resolution field correct | |

### 8.3 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 8.3.1 | Device A writes v1 → Device B writes v1 → B pushes → gets 409 | 409 returned to B | |
| 8.3.2 | Device B pulls → retries → syncs clean | B converges to A's state | |
| 8.3.3 | Both devices converge to same state | `SELECT *` returns same rows on both | |
| 8.3.4 | `conflict_log` has entry for the conflict | Audit trail present | |
| 8.3.5 | Conflict log hashes match actual file hashes | SHA-256 verified | |

### 8.4 Gate

```
GATE-8:
  [x] 8.1.1 through 8.1.6 pass (6/6)
  [x] 8.2.1 through 8.2.6 pass (6/6)
  [x] 8.3.1 through 8.3.5 pass (5/5)
  [x] Test coverage > 80%
```

**Block:** None directly (Phase 9 and Phase 10 can proceed in parallel).

---

## Phase 9: Error Handling & Resilience

### 9.1 Unit Tests — Retry Policy

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 9.1.1 | Attempt 1 is immediate | Delay === 0 | |
| 9.1.2 | Attempt 2 delay is 5s | Delay ≈ 5000ms | |
| 9.1.3 | Attempt 3 delay is 30s | Delay ≈ 30000ms | |
| 9.1.4 | Attempt 4 delay is 2min | Delay ≈ 120000ms | |
| 9.1.5 | Attempt 5 delay is 10min | Delay ≈ 600000ms | |
| 9.1.6 | Attempt 6 delay is 1hr | Delay ≈ 3600000ms | |
| 9.1.7 | After attempt 6, status = `failed` | Status field updated | |
| 9.1.8 | Failed sync visible in `tgdb logs` | Log entry present | |
| 9.1.9 | No infinite retry loop | Max 6 attempts, then stops | |

### 9.2 Unit Tests — Gateway Retry

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 9.2.1 | Transient error retries 3x | 3 attempts before fail | |
| 9.2.2 | Permanent error (400) fails immediately | 1 attempt | |
| 9.2.3 | Transient error retry delay increases | Exponential backoff | |
| 9.2.4 | After 3 transient retries, request fails | Error response to client | |

### 9.3 Unit Tests — Backpressure

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 9.3.1 | Pending > 10 triggers `QUEUE_LAG` warning | Warning in client logs | |
| 9.3.2 | Gateway 429 includes `retry_after_seconds` | Field present, numeric | |
| 9.3.3 | Gateway 429 includes `queue_depth` | Field present, numeric | |
| 9.3.4 | Client respects `retry_after_seconds` | Wait before next attempt | |

### 9.4 Unit Tests — Error Surfacing

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 9.4.1 | `GET /v1/status` shows `pending_changesets` | Field present per database | |
| 9.4.2 | `tgdb logs` shows failed syncs | Failed entries visible | |
| 9.4.3 | `tgdb logs` shows error type | Error type in log entry | |
| 9.4.4 | `tgdb logs` shows retry count | Retry count in log entry | |
| 9.4.5 | Gateway request log records failures | `error_message` field populated | |

### 9.5 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 9.5.1 | Simulate Gateway offline → client queues → Gateway online → sync drains | No data lost | |
| 9.5.2 | Simulate Telegram rate limit → 429 → backoff → retry succeeds | Correct timing | |
| 9.5.3 | Simulate Telegram outage → client retries → outage ends → sync completes | All changesets synced | |
| 9.5.4 | Buffer retained during Gateway downtime | Local writes continue unaffected | |
| 9.5.5 | After 6 failed attempts, sync marked failed | Status visible in logs | |

### 9.6 Gate

```
GATE-9:
  [x] 9.1.1 through 9.1.9 pass (9/9)
  [x] 9.2.1 through 9.2.4 pass (4/4)
  [x] 9.3.1 through 9.3.4 pass (4/4)
  [x] 9.4.1 through 9.4.5 pass (5/5)
  [x] 9.5.1 through 9.5.5 pass (5/5)
  [x] Test coverage > 80%
```

**Block:** Phase 12 cannot start until GATE-9 is green (indirectly via Phase 10).

---

## Phase 10: CLI & User Interface

### 10.1 Unit Tests — Command Routing

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.1.1 | `tgdb init <name>` creates database | DB file exists | |
| 10.1.2 | `tgdb init <name>` fails if DB exists | Error: already exists | |
| 10.1.3 | `tgdb open <name>` opens existing DB | No error | |
| 10.1.4 | `tgdb open <name>` fails if DB missing | Error: not found | |
| 10.1.5 | `tgdb exec "<sql>"` executes SQL | Table created, rows inserted | |
| 10.1.6 | `tgdb exec "<sql>"` with bad SQL returns error | Non-zero exit code | |

### 10.2 Unit Tests — CRUD Commands

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.2.1 | `tgdb insert <table> '<json>'` inserts row | Row exists in table | |
| 10.2.2 | `tgdb select <table>` returns all rows | Correct output | |
| 10.2.3 | `tgdb select <table> --where '<condition>'` filters | Only matching rows | |
| 10.2.4 | `tgdb update <table> '<set>' --where '<where>'` modifies | Row values changed | |
| 10.2.5 | `tgdb delete <table> --where '<where>'` removes | Row gone | |

### 10.3 Unit Tests — Sync Commands

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.3.1 | `tgdb sync` triggers background sync | Sync event fires | |
| 10.3.2 | `tgdb pull` downloads latest version | Local DB updated | |
| 10.3.3 | `tgdb status` shows sync state | Output includes version, pending count | |
| 10.3.4 | `tgdb status` shows queue depth | `pending_changesets` displayed | |
| 10.3.5 | `tgdb logs` shows sync history | Entries with timestamps | |
| 10.3.6 | `tgdb versions` lists remote versions | Version list displayed | |
| 10.3.7 | `tgdb rollback <version>` rolls back | Local DB matches target version | |

### 10.4 Unit Tests — Config Commands

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.4.1 | `tgdb config show` displays config | All fields shown | |
| 10.4.2 | `tgdb config set <key> <value>` updates config | Value persisted in `config.json` | |
| 10.4.3 | `tgdb config set` with invalid key returns error | Error message | |

### 10.5 Unit Tests — Output Formatting

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.5.1 | `--json` on `tgdb select` returns valid JSON | `JSON.parse()` succeeds | |
| 10.5.2 | `--json` on `tgdb status` returns valid JSON | `JSON.parse()` succeeds | |
| 10.5.3 | `--json` on `tgdb versions` returns valid JSON | `JSON.parse()` succeeds | |
| 10.5.4 | `--help` on `tgdb` shows all commands | All commands listed | |
| 10.5.5 | `--help` on `tgdb sync` shows sync options | Options documented | |

### 10.6 Unit Tests — Interactive Mode

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.6.1 | `tgdb shell` enters REPL | Interactive prompt active | |
| 10.6.2 | REPL accepts SQL commands | Query executes | |
| 10.6.3 | REPL `exit` closes shell | Process exits cleanly | |
| 10.6.4 | REPL `help` shows available commands | Command list displayed | |

### 10.7 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 10.7.1 | Full lifecycle: init → insert → select → sync → pull → verify | All data survives round-trip | |
| 10.7.2 | `tgdb sync` → `tgdb status` shows updated state | State reflects sync | |
| 10.7.3 | `tgdb logs` shows all operations from session | Complete history | |
| 10.7.4 | `tgdb versions` after upload shows version list | Versions listed | |
| 10.7.5 | `tgdb rollback <v1>` after v2 upload restores v1 | Data matches v1 | |

### 10.8 Gate

```
GATE-10:
  [x] 10.1.1 through 10.1.6 pass (6/6)
  [x] 10.2.1 through 10.2.5 pass (5/5)
  [x] 10.3.1 through 10.3.7 pass (7/7)
  [x] 10.4.1 through 10.4.3 pass (3/3)
  [x] 10.5.1 through 10.5.5 pass (5/5)
  [x] 10.6.1 through 10.6.4 pass (4/4)
  [x] 10.7.1 through 10.7.5 pass (5/5)
  [x] All commands have `--help`
  [x] All commands have `--json`
```

**Block:** Phase 12 cannot start until GATE-10 is green.

---

## Phase 11: Monitoring & Observability

### 11.1 Unit Tests — Prometheus Metrics

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 11.1.1 | `GET /metrics` returns 200 | Status 200 | |
| 11.1.2 | `GET /metrics` returns Prometheus format | Contains `# HELP` and `# TYPE` lines | |
| 11.1.3 | `sync_uploads_total` increments on upload | Counter increases by 1 | |
| 11.1.4 | `sync_uploads_success` increments on success | Counter increases by 1 | |
| 11.1.5 | `sync_uploads_failed` increments on failure | Counter increases by 1 | |
| 11.1.6 | `sync_upload_latency_ms` records timing | Histogram has observations | |
| 11.1.7 | `sync_queue_depth` reflects current queue | Gauge matches actual queue | |
| 11.1.8 | `sync_lock_wait_ms` records lock wait time | Histogram has observations | |
| 11.1.9 | `telegram_api_errors` increments on error | Counter increases | |
| 11.1.10 | `registry_operations` increments on DB op | Counter increases | |

### 11.2 Unit Tests — Structured Logging

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 11.2.1 | Log output is valid JSON | `JSON.parse()` succeeds | |
| 11.2.2 | Log contains `request_id` field | Field present | |
| 11.2.3 | Log contains `user_id` field | Field present | |
| 11.2.4 | Log contains `operation` field | Field present | |
| 11.2.5 | Log contains `status` field | Field present | |
| 11.2.6 | Log contains `duration_ms` field | Field present, numeric | |
| 11.2.7 | Log contains `timestamp` field | ISO 8601 format | |

### 11.3 Unit Tests — Audit Trail

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 11.3.1 | Upload logged in `sync_log` | Entry with `operation: upload` | |
| 11.3.2 | Download logged in `sync_log` | Entry with `operation: download` | |
| 11.3.3 | Log entry has `request_id` | UUID present | |
| 11.3.4 | Log entry has `created_at` and `completed_at` | Timestamps present | |
| 11.3.5 | Failed operation logged with `error_message` | Error field populated | |

### 11.4 Unit Tests — Health Checks

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 11.4.1 | `GET /health` returns 200 (liveness) | Status 200 | |
| 11.4.2 | `GET /health/ready` checks PG + Redis | Correct dependency check | |
| 11.4.3 | `GET /health/telegram` checks Telegram API | Bot token validated | |

### 11.5 Integration Tests

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 11.5.1 | 100 uploads → `sync_uploads_total` = 100 | Counter matches | |
| 11.5.2 | 50 successes + 50 failures → counters correct | Both counters match | |
| 11.5.3 | `sync_log` has 100 entries after 100 operations | Row count matches | |
| 11.5.4 | Structured logs parseable by log aggregator | No parse errors | |
| 11.5.5 | Health check fails when PG stopped | `/health/ready` returns 503 | |

### 11.6 Gate

```
GATE-11:
  [x] 11.1.1 through 11.1.10 pass (10/10)
  [x] 11.2.1 through 11.2.7 pass (7/7)
  [x] 11.3.1 through 11.3.5 pass (5/5)
  [x] 11.4.1 through 11.4.3 pass (3/3)
  [x] 11.5.1 through 11.5.5 pass (5/5)
  [x] No metric cardinality explosion (label values bounded)
```

**Block:** Phase 12 cannot start until GATE-11 is green.

---

## Phase 12: Deployment & Production Readiness

### 12.1 Unit Tests — Docker Compose

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 12.1.1 | `docker compose up --build` starts all services | All 4 containers running | |
| 12.1.2 | `docker compose down -v` cleans up | Containers + volumes removed | |
| 12.1.3 | Gateway starts with production-like config | No errors in logs | |

### 12.2 Unit Tests — TLS

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 12.2.1 | HTTPS endpoint returns valid cert | Cert not expired, correct domain | |
| 12.2.2 | HTTP redirects to HTTPS | 301 to HTTPS URL | |
| 12.2.3 | TLS 1.2 minimum enforced | TLS 1.1 connection rejected | |

### 12.3 Unit Tests — Client Distribution

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 12.3.1 | Linux binary runs on Ubuntu | `tgdb --version` exits 0 | |
| 12.3.2 | macOS binary runs on Ventura+ | `tgdb --version` exits 0 | |
| 12.3.3 | Install script completes | `curl ... \| bash` succeeds | |
| 12.3.4 | Installed binary is in `$PATH` | `which tgdb` returns path | |

### 12.4 Unit Tests — Documentation

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 12.4.1 | `docs/setup.md` exists and is non-empty | File present | |
| 12.4.2 | `docs/configuration.md` exists and is non-empty | File present | |
| 12.4.3 | `docs/api.md` exists and is non-empty | File present | |
| 12.4.4 | `docs/cli.md` exists and is non-empty | File present | |
| 12.4.5 | `docs/troubleshooting.md` exists and is non-empty | File present | |
| 12.4.6 | All code examples in docs are valid syntax | No syntax errors | |

### 12.5 Performance Tests — Load

| ID | Test | Threshold | Status |
|----|------|-----------|--------|
| 12.5.1 | 100 concurrent upload requests | All complete, no 5xx | |
| 12.5.2 | 100 concurrent download requests | All complete, no 5xx | |
| 12.5.3 | Mixed load: 50 uploads + 50 downloads | All complete, no 5xx | |
| 12.5.4 | Upload latency under load (p95) | < 5 seconds | |
| 12.5.5 | Download latency under load (p95) | < 5 seconds | |
| 12.5.6 | Gateway memory under load | < 512MB RSS | |

### 12.6 Security Audit

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 12.6.1 | No secrets in git history | `git log -p \| grep -i "secret\|password\|token"` clean | |
| 12.6.2 | SQLCipher key not in logs | Grep logs for key patterns → empty | |
| 12.6.3 | SQLCipher key not in Registry DB | Key column not in any table | |
| 12.6.4 | All transit encrypted (HTTPS) | No HTTP endpoints (except redirect) | |
| 12.6.5 | Rate limiting active | Burst test returns 429 | |
| 12.6.6 | No SQL injection in raw queries | Parameterized queries used | |
| 12.6.7 | No path traversal in file downloads | Invalid paths rejected | |

### 12.7 Integration Tests — End-to-End

| ID | Test | Asserts | Status |
|----|------|---------|--------|
| 12.7.1 | Fresh install → init → insert → sync → destroy → pull → verify | Full lifecycle | |
| 12.7.2 | Two devices: A writes → sync → B pulls → B sees A's data | Multi-device sync | |
| 12.7.3 | Conflict scenario: A writes → B writes → sync → resolve → converge | Conflict resolution | |
| 12.7.4 | Gateway restart mid-sync → sync completes after restart | Resilience | |
| 12.7.5 | 24-hour soak test: continuous writes + sync | No memory leaks, no crashes | |

### 12.8 Gate

```
GATE-12 (FINAL):
  [x] 12.1.1 through 12.1.3 pass (3/3)
  [x] 12.2.1 through 12.2.3 pass (3/3)
  [x] 12.3.1 through 12.3.4 pass (4/4)
  [x] 12.4.1 through 12.4.6 pass (6/6)
  [x] 12.5.1 through 12.5.6 pass (6/6)
  [x] 12.6.1 through 12.6.7 pass (7/7)
  [x] 12.7.1 through 12.7.5 pass (5/5)
  [x] All prior phase gates green
  [x] CI pipeline green on main
  [x] No P0/P1 bugs open
```

---

## Gate Summary

```
GATE-0 ──► GATE-1 ──► GATE-2 ──┐
       └──► GATE-3 ──┬──────────┤
              └──► GATE-4 ──────┤
              └──► GATE-5 ──────┤
                                 ▼
                           GATE-6 ──► GATE-8 ──┐
                           GATE-7 ──► GATE-9 ──┤
                                               ├──► GATE-10 ──┐
                                               │               │
                           GATE-11 ────────────┤               │
                                               ▼               ▼
                                           GATE-12 (FINAL)
```

**Total tests:** ~230
**Total gates:** 13 (GATE-0 through GATE-12)

| Gate | Phase | Tests | Blocker For |
|------|-------|-------|-------------|
| GATE-0 | Scaffold | 12 | Phase 1, Phase 3 |
| GATE-1 | SQLite Engine | 28 | Phase 2 |
| GATE-2 | Change Tracking | 20 | Phase 6 |
| GATE-3 | Gateway Foundation | 24 | Phase 4, Phase 5, Phase 11 |
| GATE-4 | Telegram Integration | 29 | Phase 6, Phase 7 |
| GATE-5 | Auth | 26 | Phase 6, Phase 7 |
| GATE-6 | Sync Push | 37 | Phase 8, Phase 9, Phase 10 |
| GATE-7 | Sync Pull | 28 | Phase 8, Phase 9, Phase 10 |
| GATE-8 | Conflicts | 17 | — |
| GATE-9 | Error Handling | 27 | Phase 12 (indirect) |
| GATE-10 | CLI | 35 | Phase 12 |
| GATE-11 | Monitoring | 30 | Phase 12 |
| GATE-12 | Deployment | 34 | **SHIP** |
