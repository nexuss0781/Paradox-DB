# Phase Contracts: Paradox-DB

**Version:** 1.0
**Status:** Draft
**Source:** Architecture.md

Each phase is a self-contained, deliverable unit with defined inputs, outputs, acceptance criteria, and exit conditions. Phases are ordered by dependency; parallelisable work is noted in §13.

---

## Phase 0: Project Scaffold

**Goal:** Establish repository structure, tooling, and CI baseline. No functional code.

**Inputs:**
- Architecture.md, Design.md, Phase.md (this document)

**Deliverables:**
- Monorepo layout with `client/`, `gateway/`, `shared/` top-level directories
- Language/framework selection locked:
  - Client: TypeScript (Node.js or Bun)
  - Gateway: Python (FastAPI)
  - Shared types: JSON schema files or shared TypeScript types
- Linter, formatter, and type-checker configured for each language
- CI pipeline (GitHub Actions or equivalent): lint, typecheck, test on PR
- README with setup instructions, architecture diagram link, and phase roadmap
- `.env.example` documenting all required environment variables

**Acceptance Criteria:**
- `make lint` passes in all directories
- `make typecheck` passes in all directories
- CI runs green on empty commit
- All team members can `git clone && make dev` and get a working (empty) project

**Exit Condition:** Repo is bootable, CI is green, phase tracking begins.

**Estimated effort:** 1–2 days

---

## Phase 1: Local SQLite Engine (Client Core)

**Goal:** Client can open an encrypted SQLite database, execute CRUD operations, and return results in < 1ms on local disk.

**Depends on:** Phase 0

**Inputs:**
- SQLCipher build/binary available in client environment
- Config schema from Architecture.md §10.1

**Deliverables:**
- `ClientEngine` class with:
  - `open(passphrase, db_path)` → opens/creates SQLCipher database
  - `close()` → clean shutdown, WAL checkpoint
  - `execute(sql, params?)` → generic query executor
  - `insert(table, row)` → convenience method
  - `select(table, where?)` → convenience method
  - `update(table, set, where)` → convenience method
  - `delete(table, where)` → convenience method
- WAL mode enabled by default
- SQLCipher configuration: AES-256-CBC, page size 4096, KDF 256k iterations, PBKDF2-HMAC-SHA512
- Config loader reading `~/.paradox/config.json`
- Unit tests for all CRUD operations
- Performance baseline: confirm < 1ms local read/write on reference hardware

**Acceptance Criteria:**
- Can create a new encrypted database from scratch
- Can open existing encrypted database with correct passphrase
- Rejects wrong passphrase with clear error
- All CRUD operations return correct results
- WAL mode confirmed via `PRAGMA journal_mode`
- Unit test coverage > 80%
- Benchmark: 10,000 inserts complete in < 2 seconds

**Exit Condition:** Client is a fully functional local encrypted SQLite engine with no sync capability.

**Estimated effort:** 3–5 days

---

## Phase 2: Change Tracking (Session Extension)

**Goal:** Client tracks all mutations as binary diffs using SQLite Session Extension, and can export/import changesets.

**Depends on:** Phase 1

**Inputs:**
- `ClientEngine` from Phase 1
- SQLite Session Extension compiled into SQLCipher build

**Deliverables:**
- `ChangeTracker` class integrated into `ClientEngine`:
  - `startSession()` → begin tracking changes
  - `exportChangeset()` → returns binary patch (changeset blob)
  - `importChangeset(patch)` → applies changeset to database
  - `truncateBuffer()` → clears committed changeset buffer
  - `bufferSize()` → returns current pending changeset size in bytes
  - `changesetCount()` → number of operations in buffer
- Automatic session start on `ClientEngine.open()`
- Changeset format: SQLite Session Extension binary format
- Conflict detection on `importChangeset()`: returns conflict info if changeset cannot apply cleanly
- Unit tests for export, import, truncate, conflict detection
- Integration test: write 100 rows → export changeset → open new DB → import changeset → verify all rows present

**Acceptance Criteria:**
- After N writes, `exportChangeset()` returns a valid binary patch
- `importChangeset()` on a fresh DB reproduces all N writes
- `truncateBuffer()` resets the session without data loss
- Conflicting changesets are detected and reported (not silently dropped)
- Changeset size is proportional to number of mutations, not total DB size

**Exit Condition:** Client can produce and consume binary diffs. Ready for sync transport.

**Estimated effort:** 3–5 days

---

## Phase 3: Web Gateway Foundation

**Goal:** Deploy a working HTTP API scaffold with health checks, Registry DB, and Docker Compose stack.

**Depends on:** Phase 0

**Inputs:**
- Architecture.md §2.2 (Gateway), §2.3 (Registry DB), §9.2 (Deployment)

**Deliverables:**
- FastAPI application skeleton with:
  - `GET /health` → 200 OK
  - `GET /health/ready` → checks PostgreSQL + Redis connectivity
  - `GET /health/telegram` → checks Telegram Bot API reachability
  - `GET /v1/status` → stub (returns empty)
  - `POST /v1/upload` → stub (returns 501)
  - `GET /v1/download` → stub (returns 501)
  - `GET /v1/versions` → stub (returns 501)
  - `POST /v1/rollback` → stub (returns 501)
- PostgreSQL setup with migration tool (Alembic or equivalent):
  - `user_channels` table
  - `database_versions` table
  - `sync_log` table
- Redis connection pool for distributed locks
- Docker Compose stack: Gateway, PostgreSQL, Redis, Nginx (TLS termination)
- `.env.example` with all required variables
- Unit tests for health endpoints
- Integration test: Docker Compose up → all health checks pass

**Acceptance Criteria:**
- `docker compose up` brings up all 4 services
- `GET /health` returns 200
- `GET /health/ready` returns 200 when all deps are reachable
- Database migrations run cleanly on fresh PostgreSQL
- All stub endpoints return 501 with descriptive message
- CI integration test passes

**Exit Condition:** Gateway infrastructure is live and ready for business logic.

**Estimated effort:** 3–4 days

---

## Phase 4: Telegram Integration Layer

**Goal:** Gateway can create private channels, upload files, and download files via Telegram Bot API.

**Depends on:** Phase 3

**Inputs:**
- Telethon (Python) or GramJS (JS) library
- Telegram Bot token and API credentials
- Architecture.md §2.4 (Telegram Channel)

**Deliverables:**
- `TelegramClient` wrapper class:
  - `createPrivateChannel(user_id)` → creates channel, adds bot as admin, returns `channel_id`
  - `uploadFile(channel_id, file_bytes, caption)` → calls `sendDocument`, returns `message_id`
  - `downloadFile(channel_id, message_id)` → calls `getFile`, returns file bytes
  - `getFileMetadata(channel_id, message_id)` → returns file info without downloading
- Rate limit middleware:
  - Per-channel: max 15 uploads/min (configurable)
  - Global: max 15 uploads/sec (configurable)
  - Queue with retry-after handling
- File naming convention: `{db_name}_v{version}.sqlcipher` / `{db_name}_cs{id}.patch`
- Caption JSON metadata: `{ db_name, version, type, timestamp, hash }`
- Error handling: retry 3x on transient Telegram errors, surface permanent failures
- Unit tests for wrapper methods (mocked Telegram API)
- Integration test with real Telegram bot (dev channel)

**Acceptance Criteria:**
- Can create a private channel and retrieve its ID
- Can upload a file and receive a valid `message_id`
- Can download a file by `message_id` and verify content matches upload
- Rate limiter correctly throttles uploads
- Transient errors trigger automatic retry
- File metadata is correctly embedded in message caption

**Exit Condition:** Gateway has a reliable Telegram storage backend.

**Estimated effort:** 4–6 days

---

## Phase 5: Auth & Authorization

**Goal:** All Gateway endpoints are protected. Only authenticated users can access their own data.

**Depends on:** Phase 3

**Inputs:**
- Architecture.md §4.2 (Authentication), §4.3 (Authorization)

**Deliverables:**
- Authentication middleware:
  - API key validation (static, stored hashed in Registry DB)
  - JWT token validation (short-lived, signed with server secret)
  - Both methods supported; API key for CLI, JWT for web clients
- Authorization enforcement:
  - `user_id` extracted from auth context (never from request body)
  - All queries scoped to authenticated `user_id`
  - 401 Unauthorized for missing/invalid auth
  - 403 Forbidden for cross-user access attempts
- User registration flow:
  - `POST /v1/auth/register` → creates user, generates API key, provisions Telegram channel
  - Returns API key (shown once, not stored plaintext)
- Rate limiting per user (configurable, default: 60 req/min)
- Unit tests for auth middleware, token validation, user scoping
- Integration test: unauthenticated request → 401; wrong user → 403

**Acceptance Criteria:**
- Unauthenticated requests receive 401
- Authenticated user cannot access another user's data (403)
- API key and JWT both work as auth mechanisms
- User registration creates channel and returns API key
- Rate limiter blocks excessive requests with 429

**Exit Condition:** Gateway is secure. No business logic endpoints are unprotected.

**Estimated effort:** 3–4 days

---

## Phase 6: Sync Protocol — Push Path

**Goal:** Client can push local changes to Gateway, which uploads them to Telegram and updates the Registry.

**Depends on:** Phase 2, Phase 4, Phase 5

**Inputs:**
- `ChangeTracker` from Phase 2
- `TelegramClient` from Phase 4
- Auth from Phase 5
- Architecture.md §3.2 (Sync Push), §6 (Sync Protocol)

**Deliverables:**
- **Client side — `SyncManager` class:**
  - `start()` → begins background sync loop
  - `stop()` → graceful shutdown, final sync
  - `syncNow()` → manual trigger
  - Sync triggers:
    - Timer: configurable interval (default 30s)
    - Operation count: configurable threshold (default 50 ops)
    - Graceful shutdown: always sync on exit
  - Flow: export changeset → POST `/upload` → on success, truncate buffer
  - On failure: retain buffer, log error, retry on next trigger
  - Exponential backoff on 429: 30s → 60s → 120s → 300s (cap)
- **Gateway side — `POST /v1/upload` implementation:**
  - Parse multipart upload (database_name, file, version_type)
  - Authenticate user via middleware
  - Acquire distributed lock (Redis) on `(user_id, database_name)`
  - Validate file format (`.sqlcipher` or `.patch`)
  - Compute SHA-256 hash of file
  - Upload to Telegram via `TelegramClient.uploadFile()`
  - Update `database_versions` in Registry DB
  - Append to `sync_log`
  - Release lock
  - Return `{ request_id, message_id, version, uploaded_at }`
  - On conflict (version mismatch): return 409 with remote version info
- Rate limit queue on Gateway side (prevents burst to Telegram)
- Unit tests for SyncManager triggers, Gateway upload handler
- Integration test: client writes → sync fires → Gateway confirms → Registry updated

**Acceptance Criteria:**
- Client local write completes in < 1ms (sync is async)
- Sync trigger fires at configured interval
- Upload reaches Telegram and returns valid `message_id`
- Registry DB is updated with new version
- Session buffer is truncated after successful upload
- Failed upload retains buffer and retries
- 409 returned when client version is behind
- Lock prevents concurrent uploads for same database

**Exit Condition:** Write path is end-to-end functional. Data flows from client to Telegram.

**Estimated effort:** 5–7 days

---

## Phase 7: Sync Protocol — Pull Path

**Goal:** Client can pull the latest (or specific) version from Telegram to restore or update local database.

**Depends on:** Phase 4, Phase 5

**Inputs:**
- `TelegramClient` from Phase 4
- Auth from Phase 5
- Architecture.md §3.3 (Sync Pull)

**Deliverables:**
- **Gateway side — `GET /v1/download` implementation:**
  - Authenticate user
  - Lookup `latest_message_id` from Registry DB (or specific version)
  - Download file from Telegram via `TelegramClient.downloadFile()`
  - Stream file bytes to client
  - Append to `sync_log`
  - Return 404 if database not found
- **Gateway side — `GET /v1/versions` implementation:**
  - Return list of available versions for a database from Registry DB
- **Client side — pull integration into `SyncManager`:**
  - `pullLatest()` → downloads latest version, replaces local DB
  - `pullVersion(version)` → downloads specific version
  - `pullOnStart()` → configurable: pull before first read if local is stale
- Cold start handling:
  - New device detects no local DB → triggers full pull
  - Block reads until pull completes
  - Show progress indicator
- **Gateway side — `POST /v1/rollback` implementation:**
  - Download target version from Telegram
  - Re-upload as new version (preserves version chain)
  - Update Registry DB
- Unit tests for download handler, version listing, rollback
- Integration test: upload file → download → verify content matches

**Acceptance Criteria:**
- Download returns exact file bytes that were uploaded
- Version listing returns all uploaded versions in correct order
- Rollback re-creates database at target version
- Cold start pulls full database before allowing reads
- 404 returned for nonexistent database
- `sync_log` records all download operations

**Exit Condition:** Read/recovery path is end-to-end functional. Data flows from Telegram to client.

**Estimated effort:** 4–6 days

---

## Phase 8: Conflict Detection & Resolution

**Goal:** System detects version mismatches between client and Registry, and resolves via last-write-wins with audit logging.

**Depends on:** Phase 6, Phase 7

**Inputs:**
- Push and pull paths functional
- Architecture.md §7 (Conflict Resolution)

**Deliverables:**
- **Conflict detection on upload:**
  - Gateway compares client's declared base version against Registry's latest
  - If mismatch → return 409 Conflict with `{ remote_version, remote_message_id, your_version }`
- **Client conflict handler:**
  - On receiving 409: pull latest version from Gateway
  - Apply local changes on top of pulled version (best-effort merge)
  - If merge fails: LWW overwrite, log both versions
  - Retry push with updated base version
- **Conflict audit log:**
  - New table `conflict_log`: `{ conflict_id, user_id, database_name, local_version, remote_version, resolution, timestamp }`
  - Both versions' file hashes stored for manual inspection
- **Conflict notification:**
  - `tgdb logs` surfaces conflict events with both version hashes
  - Optional: webhook callback for programmatic conflict handling (v2 placeholder)
- Unit tests for conflict detection, LWW resolution
- Integration test: two clients write independently → one gets 409 → pulls → overwrites → syncs clean

**Acceptance Criteria:**
- 409 returned when client version does not match Registry
- Client automatically pulls and retries on 409
- Losing writer's changes are overwritten (LWW)
- Both versions are logged in `conflict_log` with hashes
- No silent data loss without audit trail
- After conflict resolution, both clients converge to same state

**Exit Condition:** Conflicts are detectable, resolvable, and auditable.

**Estimated effort:** 3–4 days

---

## Phase 9: Error Handling & Resilience

**Goal:** All failure modes are handled gracefully with retries, backpressure, and user-visible status.

**Depends on:** Phase 6, Phase 7

**Inputs:**
- All sync paths functional
- Architecture.md §8 (Error Handling)

**Deliverables:**
- **Client retry policy:**
  - Attempt 1: immediate
  - Attempt 2: +5s
  - Attempt 3: +30s
  - Attempt 4: +2min
  - Attempt 5: +10min
  - Attempt 6: +1hr (max)
  - After max: mark as failed, surface via `tgdb logs`
- **Gateway retry for Telegram errors:**
  - 3 retries with exponential backoff on Telegram API failures
  - Transient errors (timeout, 5xx): retry
  - Permanent errors (400, 403): fail immediately, log
- **Backpressure:**
  - Client: if pending changesets > 10, surface `QUEUE_LAG` warning
  - Gateway: if Telegram rate limit hit, return 429 with `retry_after_seconds` and `queue_depth`
  - Client respects `retry_after_seconds` before next attempt
- **Error surfacing:**
  - `tgdb logs` shows all failed syncs with error type, timestamp, retry count
  - `GET /v1/status` shows `pending_changesets` count per database
  - Gateway request log records all failures with `error_message`
- Unit tests for retry logic, backpressure thresholds
- Integration test: simulate Telegram outage → client queues → outage ends → sync drains

**Acceptance Criteria:**
- Retry policy matches spec (timing, max attempts)
- Backpressure warning triggers at correct threshold
- 429 response includes retry-after and queue depth
- All failures are logged and visible to user
- No infinite retry loops
- Client survives Gateway downtime without data loss (buffer retained)

**Exit Condition:** System degrades gracefully under all failure conditions.

**Estimated effort:** 3–4 days

---

## Phase 10: CLI & User Interface

**Goal:** Users can interact with Paradox-DB via `tgdb` CLI for all operations.

**Depends on:** Phase 1, Phase 6, Phase 7

**Inputs:**
- `ClientEngine`, `SyncManager` from earlier phases
- Architecture.md §10.1 (Client Config)

**Deliverables:**
- `tgdb` CLI binary with commands:

| Command | Description |
|---------|-------------|
| `tgdb init <name>` | Create new encrypted database |
| `tgdb open <name>` | Open existing database |
| `tgdb exec <sql>` | Execute raw SQL |
| `tgdb insert <table> <json>` | Insert row |
| `tgdb select <table> [where]` | Query rows |
| `tgdb update <table> <set> <where>` | Update rows |
| `tgdb delete <table> <where>` | Delete rows |
| `tgdb sync` | Manual sync trigger |
| `tgdb pull` | Pull latest from Telegram |
| `tgdb status` | Show sync status, queue depth |
| `tgdb logs` | Show sync history and errors |
| `tgdb versions` | List available remote versions |
| `tgdb rollback <version>` | Rollback to specific version |
| `tgdb config show` | Show current config |
| `tgdb config set <key> <value>` | Update config |

- Interactive mode: `tgdb shell` opens REPL for continuous queries
- Config management: read/write `~/.paradox/config.json`
- Logging: structured logs to `~/.paradox/logs/`
- `--json` flag on all commands for machine-readable output
- `--help` on all commands

**Acceptance Criteria:**
- All commands execute correctly against local DB
- `tgdb sync` triggers background sync
- `tgdb pull` downloads and restores from Telegram
- `tgdb status` shows accurate sync state
- `tgdb logs` shows sync history with timestamps
- `--json` output is valid JSON
- `--help` is informative for every command

**Exit Condition:** Complete CLI for all user-facing operations.

**Estimated effort:** 4–6 days

---

## Phase 11: Monitoring & Observability

**Goal:** Gateway exposes Prometheus metrics, structured logging, and audit trail for all operations.

**Depends on:** Phase 3

**Inputs:**
- Architecture.md §11 (Monitoring & Observability)

**Deliverables:**
- Prometheus metrics endpoint (`GET /metrics`):

| Metric | Type | Description |
|--------|------|-------------|
| `sync_uploads_total` | Counter | Total upload attempts |
| `sync_uploads_success` | Counter | Successful uploads |
| `sync_uploads_failed` | Counter | Failed uploads |
| `sync_upload_latency_ms` | Histogram | Upload round-trip time |
| `sync_queue_depth` | Gauge | Pending uploads in queue |
| `sync_lock_wait_ms` | Histogram | Time waiting for distributed lock |
| `telegram_api_errors` | Counter | Telegram API failures by error type |
| `registry_operations` | Counter | Registry DB read/write operations |

- Structured JSON logging (all requests, errors, retries)
- Request logging middleware: every API call logged with `request_id`, `user_id`, `operation`, `status`, `duration_ms`
- Audit trail: `sync_log` table records all upload/download/pull operations
- Health check endpoints refined:
  - `GET /health` → 200 OK (basic liveness)
  - `GET /health/ready` → checks PostgreSQL + Redis
  - `GET /health/telegram` → checks Telegram API
- Grafana dashboard template (optional, recommended)

**Acceptance Criteria:**
- `GET /metrics` returns valid Prometheus format
- All metrics increment correctly under load
- Structured logs are parseable (JSON format)
- `sync_log` records every sync operation
- Health checks accurately reflect component status
- No metric cardinality explosion (label values bounded)

**Exit Condition:** System is observable. Failures are traceable from client to Telegram.

**Estimated effort:** 3–4 days

---

## Phase 12: Deployment & Production Readiness

**Goal:** System is deployable, documented, and ready for real use.

**Depends on:** Phase 3, Phase 10, Phase 11

**Inputs:**
- All prior phases complete
- Architecture.md §9 (Deployment)

**Deliverables:**
- Docker Compose stack finalized with:
  - Gateway (FastAPI, Gunicorn/Uvicorn workers)
  - PostgreSQL (with persistent volume)
  - Redis
  - Nginx (TLS termination via Let's Encrypt)
- Production `.env` template with all required variables
- TLS certificate automation (certbot or equivalent)
- Client distribution:
  - CLI binary packaged (cross-compile for Linux/macOS/Windows)
  - Install script: `curl -sSL https://get.paradox-db.dev | bash`
- Documentation:
  - `docs/setup.md` — installation and first-run guide
  - `docs/configuration.md` — all config options explained
  - `docs/api.md` — Gateway API reference
  - `docs/cli.md` — CLI command reference
  - `docs/troubleshooting.md` — common errors and fixes
- Load testing: confirm Gateway handles 100 concurrent users
- Security audit checklist:
  - No secrets in code or logs
  - SQLCipher key never transmitted
  - All transit encrypted (TLS 1.2+)
  - Rate limiting active

**Acceptance Criteria:**
- `docker compose up --build` brings up production-like stack
- TLS certificate auto-renews
- Client installs on Linux, macOS, and Windows (WSL)
- All documentation is accurate and complete
- Load test passes at 100 concurrent users
- Security checklist is green

**Exit Condition:** System is production-ready and documented.

**Estimated effort:** 4–6 days

---

## Phase 13: Dependency Graph & Parallelisation

### Dependency Map

```
Phase 0: Project Scaffold
  │
  ├──► Phase 1: Local SQLite Engine ──► Phase 2: Change Tracking ──┐
  │                                                                   │
  ├──► Phase 3: Web Gateway Foundation ──┬───────────────────────────┤
  │                                       │                           │
  │                                       ├──► Phase 4: Telegram ────┤
  │                                       │                          │
  │                                       └──► Phase 11: Monitoring  │
  │                                                                   │
  ├──► Phase 5: Auth & Authorization ────────────────────────────────┤
  │                                                                   │
  │                          Phase 6: Sync Push ◄────────────────────┘
  │                               │
  │                               ▼
  │                          Phase 7: Sync Pull
  │                               │
  │                               ▼
  │                          Phase 8: Conflicts
  │                               │
  │                               ▼
  │                          Phase 9: Error Handling
  │                               │
  │                               ▼
  │                          Phase 10: CLI
  │                               │
  │                               ▼
  └───────────────────────────── Phase 12: Deployment
```

### Parallelisable Tasks (Can Run Simultaneously)

| Group | Phases | Rationale |
|-------|--------|-----------|
| **A** | Phase 1 (SQLite Engine) | Core client, no deps beyond scaffold |
| **B** | Phase 3 (Gateway Foundation) | Server infra, no deps beyond scaffold |
| **C** | Phase 5 (Auth) | Can start with mock endpoints, only needs Gateway scaffold |
| **D** | Phase 11 (Monitoring) | Metrics/logging middleware, only needs Gateway scaffold |
| **E** | Phase 4 (Telegram) | Needs Gateway scaffold, but independent of auth/client |

**Optimal parallel schedule:**

```
Week 1:    [Phase 0]
Week 2:    [Phase 1] ───────────── [Phase 3] ───────────── [Phase 11]
Week 3:    [Phase 1] (cont.)      [Phase 3] (cont.)       [Phase 11] (cont.)
                                    [Phase 5] ───────────── [Phase 4]
Week 4:    [Phase 2]               [Phase 5] (cont.)       [Phase 4] (cont.)
Week 5:    [Phase 2] (cont.)
Week 6:    [Phase 6] ◄── needs Phase 2 + Phase 4 + Phase 5
Week 7:    [Phase 6] (cont.)
Week 8:    [Phase 7] ◄── needs Phase 4 + Phase 5
            [Phase 8] ◄── needs Phase 6 + Phase 7
Week 9:    [Phase 8] (cont.)
            [Phase 9] ◄── needs Phase 6 + Phase 7
            [Phase 10] ◄── needs Phase 1 + Phase 6 + Phase 7
Week 10:   [Phase 9] (cont.)
            [Phase 10] (cont.)
Week 11:   [Phase 12] ◄── needs Phase 3 + Phase 10 + Phase 11
Week 12:   [Phase 12] (cont.)
```

### Non-Parallelisable Tasks (Sequential Dependencies)

| Phase | Blocked By | Cannot Start Until |
|-------|-----------|-------------------|
| Phase 1 | Phase 0 | Scaffold complete |
| Phase 2 | Phase 1 | SQLite engine working |
| Phase 3 | Phase 0 | Scaffold complete |
| Phase 4 | Phase 3 | Gateway scaffold + Docker running |
| Phase 5 | Phase 3 | Gateway scaffold + Registry DB live |
| Phase 6 | Phase 2 + Phase 4 + Phase 5 | Change tracker + Telegram + Auth all functional |
| Phase 7 | Phase 4 + Phase 5 | Telegram + Auth functional |
| Phase 8 | Phase 6 + Phase 7 | Full push + pull paths working |
| Phase 9 | Phase 6 + Phase 7 | Full sync paths working |
| Phase 10 | Phase 1 + Phase 6 + Phase 7 | Client engine + sync working |
| Phase 11 | Phase 3 | Gateway scaffold ready |
| Phase 12 | Phase 3 + Phase 10 + Phase 11 | Everything deployed and observable |

### Critical Path

```
Phase 0 → Phase 3 → Phase 4 → Phase 6 → Phase 8 → Phase 10 → Phase 12
```

**Estimated total duration:** 10–12 weeks with 2–3 developers working in parallel.

**Minimum viable duration (single developer):** ~16–20 weeks.

---

## Phase 14: Verification Checklist

After all phases complete, verify end-to-end:

- [ ] Client creates encrypted database, CRUD works in < 1ms
- [ ] Changeset export/import reproduces all mutations
- [ ] Sync push uploads changeset to Telegram via Gateway
- [ ] Registry DB tracks latest version per database
- [ ] Sync pull downloads and restores database from Telegram
- [ ] Cold start pulls full database before allowing reads
- [ ] Conflict detection returns 409 on version mismatch
- [ ] LWW resolution overwrites losing changes, logs both versions
- [ ] All endpoints authenticated, unauthorized requests rejected
- [ ] Retry policy matches spec (timing, backoff, max attempts)
- [ ] Backpressure surfaces queue depth to client
- [ ] CLI `tgdb` handles all user operations
- [ ] Prometheus metrics are accurate and complete
- [ ] Docker Compose stack runs in production-like mode
- [ ] TLS certificates are valid and auto-renewing
- [ ] Documentation covers setup, config, API, CLI, and troubleshooting
- [ ] Load test passes at 100 concurrent users
- [ ] Security checklist is green (no leaked secrets, all transit encrypted)
