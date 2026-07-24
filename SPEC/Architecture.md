# Architecture: Paradox-DB

**Version:** 1.0
**Status:** Draft
**Pattern:** Local-first SQLite with asynchronous Telegram-backed cloud sync

---

## 1. System Overview

Paradox-DB is a local-first database system that provides sub-millisecond read/write performance via a local SQLite engine, with Telegram used as an asynchronous, versioned, durable object store for backup, recovery, and multi-device sync.

```
                         ┌──────────────────────────────────────┐
                         │           Telegram Channel            │
                         │      (private, bot = admin)          │
                         │   ┌──────────────────────────────┐   │
                         │   │  msg_1: data_v1.sqlcipher    │   │
                         │   │  msg_2: changeset_1.patch    │   │
                         │   │  msg_3: changeset_2.patch    │   │
                         │   │  ...                         │   │
                         │   └──────────────────────────────┘   │
                         └──────────────┬───────────────────────┘
                                        │ sendDocument / getFile
                                        │ (async, batched)
                         ┌──────────────▼───────────────────────┐
                         │          Web Gateway API              │
                         │     ┌───────────────────────┐        │
                         │     │   Registry Database    │        │
                         │     │  UserID → ChannelID    │        │
                         │     │  DBName → MessageID    │        │
                         │     └───────────────────────┘        │
                         │     ┌───────────────────────┐        │
                         │     │   Upload Queue / Lock  │        │
                         │     │  (Redis-backed)       │        │
                         │     └───────────────────────┘        │
                         └──────────────▲───────────────────────┘
                                        │ HTTP REST
                                        │ (auth, rate-limit)
                         ┌──────────────┴───────────────────────┐
                         │         Client Engine (CLI/Lib)       │
                         │  ┌─────────────────────────────────┐  │
                         │  │        local.sqlite              │  │
                         │  │  (SQLCipher, WAL mode)           │  │
                         │  │  ┌───────────────────────────┐  │  │
                         │  │  │  Session Extension (diffs) │  │  │
                         │  │  └───────────────────────────┘  │  │
                         │  └─────────────────────────────────┘  │
                         │  ┌─────────────────────────────────┐  │
                         │  │  Sync Manager (timer/ops trigger)│  │
                         │  └─────────────────────────────────┘  │
                         └──────────────────────────────────────┘
```

---

## 2. Components

### 2.1 Client Engine

The client engine is the primary interface for all database operations. It owns the local SQLite file and manages change tracking, sync scheduling, and conflict detection.

**Responsibilities:**
- Execute all reads and writes against local `data.db` (< 1ms)
- Track changes via SQLite Session Extension (binary diffs / changesets)
- Schedule background sync to Web Gateway on timer or operation-count trigger
- Pull latest version from Telegram before session start to reduce conflict window
- Encrypt local database with SQLCipher (client-held key only)
- Expose CLI commands and library API for user interaction

**Key behaviors:**

| Operation | Path | Latency | Durability |
|-----------|------|---------|------------|
| `INSERT` / `UPDATE` / `DELETE` | Local SQLite | < 1ms | Local disk only |
| `SELECT` | Local SQLite | < 1ms | N/A |
| Sync push | Client → Gateway → Telegram | 200–800ms+ | Async, eventual |
| Sync pull | Telegram → Gateway → Client | 200–800ms+ | N/A |
| Session start (cold) | Full pull from Telegram | Seconds | N/A |

**Change tracking mechanism:**

The SQLite Session Extension maintains an internal changeset buffer. Each write operation appends a binary diff to this buffer. When the sync trigger fires, the client:

1. Exports the accumulated changeset as a binary patch
2. Sends it to the Web Gateway `/upload` endpoint
3. On success, truncates the local session buffer
4. On failure, retains the buffer and retries on next trigger

```
Client Write Flow:
  INSERT/UPDATE/DELETE
       │
       ▼
  Execute on local.sqlite (WAL mode)
       │
       ▼
  Session Extension appends diff to buffer
       │
       ▼
  [Sync Trigger fires]
       │
       ▼
  Export changeset binary
       │
       ▼
  POST /upload {changeset, db_name, user_id}
       │
       ▼
  Gateway uploads to Telegram
       │
       ▼
  Truncate session buffer on 200 OK
```

---

### 2.2 Web Gateway

The Web Gateway is a stateless HTTP API that mediates between clients and Telegram. It handles authentication, rate-limit buffering, upload orchestration, and maintains a small registry database.

**Responsibilities:**
- Authenticate clients (API key / JWT)
- Buffer and serialize uploads to avoid Telegram rate-limit violations
- Call Telegram Bot API (`sendDocument` for upload, `getFile` for download)
- Maintain Registry DB: `UserID → ChannelID`, `DatabaseName → latest Telegram message_id`
- Acquire distributed lock per database to serialize concurrent uploads
- Log every request (type, timestamp, user, success/failure) for retry and audit
- Surface queue depth and lag to clients for backpressure visibility

**Framework options:**
- **FastAPI (Python)** — recommended if using Telethon for Telegram client
- **Elysia (Bun/JS)** — recommended if using GramJS for Telegram client

**Request logging schema:**

Every sync request is logged with:
- `request_id` (UUID)
- `user_id`
- `database_name`
- `operation` (upload | download | pull)
- `telegram_message_id` (on success)
- `status` (pending | success | failed | retrying)
- `error_message` (if failed)
- `created_at`
- `completed_at`

---

### 2.3 Registry Database

A small, high-availability datastore that maps users and databases to their Telegram storage locations.

**Data model:**

```sql
-- Maps each user to their private Telegram channel
CREATE TABLE user_channels (
    user_id       TEXT PRIMARY KEY,
    channel_id    TEXT NOT NULL,
    bot_token_id  TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tracks the latest synced version per database
CREATE TABLE database_versions (
    user_id           TEXT NOT NULL,
    database_name     TEXT NOT NULL,
    latest_message_id TEXT NOT NULL,   -- Telegram message ID
    latest_version    INTEGER NOT NULL DEFAULT 1,
    file_hash         TEXT NOT NULL,   -- SHA-256 of the uploaded file
    uploaded_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, database_name),
    FOREIGN KEY (user_id) REFERENCES user_channels(user_id)
);

-- Audit log for all sync operations
CREATE TABLE sync_log (
    request_id        TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    database_name     TEXT NOT NULL,
    operation         TEXT NOT NULL,   -- 'upload' | 'download' | 'pull'
    telegram_message_id TEXT,
    status            TEXT NOT NULL,   -- 'pending' | 'success' | 'failed' | 'retrying'
    error_message     TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at      TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES user_channels(user_id)
);
```

**Storage backend options:**
- **PostgreSQL** — recommended for production (ACID, rich querying)
- **Redis** — acceptable for simple deployments (key-value, fast, less durable)

---

### 2.4 Telegram Channel

Each user gets a private Telegram channel where the bot operates as admin. Telegram's message IDs serve as free, built-in version history.

**Channel structure:**
- **Channel type:** Private, one per user
- **Bot role:** Admin (can post, delete, pin)
- **File naming:** `{database_name}_v{version}.sqlcipher` for full snapshots, `{database_name}_cs{changeset_id}.patch` for changesets
- **Message caption:** JSON metadata `{ db_name, version, type, timestamp, hash }`

**Versioning via message IDs:**
- Each upload is a new Telegram message with an attached file
- The `message_id` returned by Telegram is the immutable version identifier
- The Registry DB tracks the latest `message_id` per database
- Rollback = re-downloading an older `message_id`'s file

**Rate limits (Telegram Bot API):**
- ~20–30 messages/sec per bot (global)
- ~20 messages/min per chat (per-channel)
- `sendDocument` file size limit: 50 MB (Bot API) / 2 GB (local Bot API / Telethon)
- Larger files should use Telethon or GramJS for streaming upload

---

## 3. Data Flow

### 3.1 Write Path (Fast, Local)

```
Client                    Local SQLite              Session Buffer
  │                            │                        │
  │  db.insert({name:"Alice"}) │                        │
  │ ──────────────────────────►│                        │
  │                            │  Execute on WAL        │
  │                            │───────────────────────►│
  │                            │                        │ Diff appended
  │  ◄─── < 1ms ──────────────│                        │
  │  (return to caller)        │                        │
```

The caller receives confirmation immediately. The write is durable on local disk in WAL mode. It is **not yet** durable in Telegram.

### 3.2 Sync Push (Async, Background)

```
Client                    Web Gateway              Telegram           Registry
  │                            │                      │                   │
  │  [Sync trigger fires]      │                      │                   │
  │  Export changeset          │                      │                   │
  │───────────────────────────►│                      │                   │
  │  POST /upload              │                      │                   │
  │                            │  Acquire lock        │                   │
  │                            │  (Redis)             │                   │
  │                            │─────────────────────►│                   │
  │                            │                      │                   │
  │                            │  sendDocument        │                   │
  │                            │─────────────────────►│                   │
  │                            │                      │  Return msg_id   │
  │                            │◄─────────────────────│                   │
  │                            │                      │                   │
  │                            │  UPDATE registry     │                   │
  │                            │─────────────────────────────────────────►│
  │                            │                      │                   │
  │                            │  Release lock        │                   │
  │                            │─────────────────────►│                   │
  │  ◄── 200 OK ──────────────│                      │                   │
  │  (truncate session buffer) │                      │                   │
```

**Failure handling:**
- On 5xx or timeout: client retains changeset, retries on next trigger
- On 429 (rate limit): exponential backoff, surface queue depth to client
- On lock acquisition timeout: skip this cycle, retry next trigger

### 3.3 Sync Pull (Recovery / New Device)

```
Client                    Web Gateway              Telegram           Registry
  │                            │                      │                   │
  │  [Session start / manual]  │                      │                   │
  │───────────────────────────►│                      │                   │
  │  GET /download?db=name     │  Lookup latest msg   │                   │
  │                            │─────────────────────────────────────────►│
  │                            │◄────────────────────────────────────────│
  │                            │                      │                   │
  │                            │  getFile(msg_id)     │                   │
  │                            │─────────────────────►│                   │
  │                            │  Return file bytes   │                   │
  │                            │◄─────────────────────│                   │
  │                            │                      │                   │
  │  ◄── File bytes ──────────│                      │                   │
  │  Replace local.sqlite     │                      │                   │
```

**Cold-start note:** A new device's first read is **not** millisecond-fast. It requires a full pull from Telegram (seconds-scale depending on file size). Subsequent reads are local and fast.

### 3.4 Conflict Detection

Conflicts arise when two devices modify the same database independently before syncing. The system detects conflicts but uses last-write-wins resolution (v1).

```
Device A writes locally          Device B writes locally
       │                                │
       ▼                                ▼
  Push changeset A                Push changeset B
       │                                │
       ▼                                ▼
  Web Gateway                      Web Gateway
  (may acquire lock first)         (waits for lock)
       │                                │
       ▼                                ▼
  Upload to Telegram               Upload to Telegram
       │                                │
       ▼                                ▼
  Registry: latest = A             Registry: latest = B
                                          │
                                    Device A pulls → gets B
                                    Device A's local changes
                                    are overwritten (LWW)
```

**v1 behavior:** Last-write-wins. The losing writer's changes are silently overwritten. Logging both versions is recommended for audit.

---

## 4. Security

### 4.1 Encryption

| Layer | Mechanism | Key Location |
|-------|-----------|--------------|
| Local database | SQLCipher (AES-256-CBC) | Client-held only |
| Data in transit (Client → Gateway) | TLS 1.2+ (HTTPS) | Standard cert |
| Data in transit (Gateway → Telegram) | Telegram MTProto | Telegram-managed |
| Data at rest (Telegram) | Telegram server-side encryption | Telegram-managed |

**SQLCipher configuration:**
- Cipher: AES-256-CBC
- Page size: 4096
- KDF iterations: 256,000 (OWASP recommended)
- HMAC: enabled
- Key derivation: PBKDF2-HMAC-SHA512

**Key management:**
- Encryption key is derived from user-provided passphrase
- Key never leaves the client device
- Key is **not** stored in Registry DB, Gateway, or Telegram
- Lost key = encrypted data is unrecoverable (by design)

### 4.2 Authentication

| Flow | Mechanism |
|------|-----------|
| Client → Gateway | API key (static) or JWT (short-lived) |
| Gateway → Telegram | Bot token (server-side, never exposed to client) |

### 4.3 Authorization

- Each user can only access their own channel and databases
- Gateway enforces `user_id` from auth context, ignores client-supplied `user_id` for authorization
- Bot is admin on private channels — no public access possible

---

## 5. API Specification (Web Gateway)

### 5.1 Endpoints

```
Base URL: https://gateway.paradox-db.example.com/v1
```

#### POST /upload

Upload a changeset or full snapshot to Telegram.

**Request:**
```
Content-Type: multipart/form-data

Fields:
  database_name  (string, required) — name of the database
  file           (binary, required) — .sqlcipher or .patch file
  version_type   (string, optional) — "full" | "changeset" (default: auto-detect)
  version        (integer, optional) — version number (default: latest + 1)
```

**Response (200):**
```json
{
  "request_id": "uuid",
  "message_id": "12345",
  "version": 7,
  "uploaded_at": "2026-07-24T12:00:00Z"
}
```

**Response (429):**
```json
{
  "error": "rate_limited",
  "retry_after_seconds": 30,
  "queue_depth": 5
}
```

**Response (409):**
```json
{
  "error": "conflict_detected",
  "remote_version": 8,
  "your_version": 7,
  "remote_message_id": "12346",
  "resolution": "pull_before_push"
}
```

#### GET /download

Download the latest (or specific) version of a database.

**Request:**
```
Query params:
  database_name  (string, required)
  version        (integer, optional) — specific version, default: latest
```

**Response (200):**
```
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="data_v7.sqlcipher"

<binary file>
```

**Response (404):**
```json
{
  "error": "not_found",
  "database_name": "nonexistent"
}
```

#### GET /versions

List available versions for a database.

**Request:**
```
Query params:
  database_name  (string, required)
```

**Response (200):**
```json
{
  "database_name": "mydb",
  "versions": [
    { "version": 7, "message_id": "12345", "uploaded_at": "2026-07-24T12:00:00Z", "size_bytes": 1048576 },
    { "version": 6, "message_id": "12300", "uploaded_at": "2026-07-23T18:30:00Z", "size_bytes": 1040000 }
  ]
}
```

#### GET /status

Check sync status and queue depth.

**Response (200):**
```json
{
  "user_id": "u_abc123",
  "databases": [
    {
      "name": "mydb",
      "latest_version": 7,
      "latest_message_id": "12345",
      "pending_changesets": 0,
      "last_sync_at": "2026-07-24T12:00:00Z"
    }
  ]
}
```

#### POST /rollback

Roll back a database to a previous version.

**Request:**
```json
{
  "database_name": "mydb",
  "target_version": 5
}
```

**Response (200):**
```json
{
  "request_id": "uuid",
  "rolled_back_to": 5,
  "new_message_id": "12347"
}
```

---

## 6. Sync Protocol

### 6.1 Trigger Conditions

The client initiates a sync push when any of the following occur:

| Trigger | Default | Configurable |
|---------|---------|--------------|
| Timer (periodic) | 30 seconds | Yes |
| Operation count threshold | 50 operations | Yes |
| Manual (`tgdb sync`) | — | N/A |
| Graceful shutdown | Always | No |

### 6.2 Sync States

```
                    ┌──────────────┐
                    │    IDLE      │
                    └──────┬───────┘
                           │ trigger fires
                           ▼
                    ┌──────────────┐
                    │  EXPORTING   │──┐
                    └──────┬───────┘  │ error
                           │          ▼
                           ▼    ┌──────────────┐
                    ┌──────────────┐    │  FAILED     │
                    │  UPLOADING   │    │ (retry next)│
                    └──────┬───────┘    └──────────────┘
                           │ success
                           ▼
                    ┌──────────────┐
                    │  CONFIRMING  │
                    └──────┬───────┘
                           │ registry updated
                           ▼
                    ┌──────────────┐
                    │  TRUNCATING  │
                    └──────┬───────┘
                           │ buffer cleared
                           ▼
                    ┌──────────────┐
                    │    IDLE      │
                    └──────────────┘
```

### 6.3 Batch Policy

To stay within Telegram's rate limits:

- **Max file size per upload:** 50 MB (Bot API) / 2 GB (Telethon local API)
- **Max uploads per minute per channel:** 15 (leaving headroom below Telegram's 20/min limit)
- **Max uploads per second per bot:** 15 (leaving headroom below Telegram's 20-30/sec limit)
- **Backpressure:** If upload queue exceeds 10 pending items, client surfaces `QUEUE_LAG` warning

### 6.4 File Format

```
Full snapshot:  {db_name}_v{version}.sqlcipher
  - Complete SQLCipher database file
  - Used for: initial sync, cold start, rollback

Changeset:      {db_name}_cs{changeset_id}.patch
  - Binary diff from SQLite Session Extension
  - Smaller than full snapshot
  - Applied on top of a known base version
```

---

## 7. Conflict Resolution

### 7.1 Strategy: Last-Write-Wins (v1)

The current version uses a simple last-write-wins (LWW) strategy. This is **not** conflict resolution — it is silent conflict erasure. The losing writer's changes are overwritten with no merge and no alert.

### 7.2 Detection

Conflicts are detected at upload time when the Web Gateway notices the client's base version does not match the Registry's latest version.

```
Client pushes version 7, but Registry shows latest is 8.
Gateway returns 409 Conflict with remote_version info.
Client must pull first, then re-apply local changes.
```

### 7.3 Recommended v2 Improvements

- Log both conflicting versions on detection (audit trail)
- Store conflict metadata in Registry DB for manual review
- Optionally implement operational transform or CRDT for structured data
- Surface conflict notifications to users via CLI or webhook

---

## 8. Error Handling

### 8.1 Client-Side

| Error | Behavior |
|-------|----------|
| Local SQLite write fails | Return error to caller immediately, do not queue for sync |
| Gateway unreachable | Retain changeset, retry on next trigger |
| Gateway returns 4xx | Log error, do not retry (client must fix) |
| Gateway returns 429 | Exponential backoff: 30s, 60s, 120s, 300s (cap) |
| Gateway returns 409 | Pull latest, merge (or LWW), retry push |
| Pull fails (cold start) | Block reads until pull succeeds, show progress |

### 8.2 Gateway-Side

| Error | Behavior |
|-------|----------|
| Telegram API error | Retry 3x with exponential backoff, then fail request |
| Lock acquisition timeout | Return 503 with retry-after, do not queue |
| Registry DB unavailable | Return 503, Gateway is degraded |
| Invalid file format | Return 400, log request |
| Bot token expired | Return 500, alert admin, all uploads fail |

### 8.3 Retry Policy

```
Attempt 1: immediate
Attempt 2: +5s
Attempt 3: +30s
Attempt 4: +2min
Attempt 5: +10min
Attempt 6: +1hour (max)
After max retries: surface to user via `tgdb logs`, mark request as failed
```

---

## 9. Deployment

### 9.1 Client

- Distributed as CLI binary and/or library package
- Local SQLite file stored at user-configured path (default: `~/.paradox/data.sqlcipher`)
- Config file: `~/.paradox/config.json`
- Logs: `~/.paradox/logs/`

### 9.2 Web Gateway

```
┌─────────────────────────────────────────────┐
│              Docker Compose                  │
│                                             │
│  ┌─────────────┐  ┌──────────────────────┐  │
│  │  Gateway API │  │  Registry DB         │  │
│  │  (FastAPI)   │  │  (PostgreSQL)        │  │
│  │  :8000       │  │  :5432               │  │
│  └─────────────┘  └──────────────────────┘  │
│                                             │
│  ┌─────────────┐  ┌──────────────────────┐  │
│  │  Redis       │  │  Nginx (TLS term.)   │  │
│  │  (locks)     │  │  :443 → :8000        │  │
│  │  :6379       │  │                      │  │
│  └─────────────┘  └──────────────────────┘  │
│                                             │
└─────────────────────────────────────────────┘
```

**Required environment variables:**

```bash
# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_API_ID=12345
TELEGRAM_API_HASH=abcdef1234567890

# Registry DB
DATABASE_URL=postgresql://user:pass@localhost:5432/paradox_registry

# Redis (distributed locks)
REDIS_URL=redis://localhost:6379/0

# Auth
JWT_SECRET=your-secret-key-here
API_KEY_SALT=your-salt-here

# Limits
MAX_UPLOAD_SIZE_MB=50
RATE_LIMIT_UPLOADS_PER_MINUTE=15
LOCK_TIMEOUT_SECONDS=30
```

### 9.3 Infrastructure Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Gateway API | 1 vCPU, 512 MB RAM | 2 vCPU, 1 GB RAM |
| PostgreSQL | 1 vCPU, 256 MB RAM | 2 vCPU, 1 GB RAM |
| Redis | 1 vCPU, 128 MB RAM | 1 vCPU, 256 MB RAM |
| Storage (Telegram) | Unlimited (free) | N/A |
| TLS certificate | Let's Encrypt (free) | Managed cert |

---

## 10. Configuration

### 10.1 Client Config (`~/.paradox/config.json`)

```json
{
  "database_path": "~/.paradox/data.sqlcipher",
  "encryption": {
    "cipher": "aes-256-cbc",
    "kdf_iterations": 256000,
    "page_size": 4096
  },
  "sync": {
    "gateway_url": "https://gateway.paradox-db.example.com/v1",
    "api_key": "your-api-key",
    "trigger_timer_seconds": 30,
    "trigger_ops_threshold": 50,
    "max_file_size_mb": 50,
    "auto_sync_on_shutdown": true
  },
  "conflict": {
    "strategy": "last-write-wins",
    "log_conflicts": true
  },
  "logging": {
    "level": "info",
    "path": "~/.paradox/logs/"
  }
}
```

### 10.2 Gateway Config

All configuration via environment variables (see §9.2). No config files in the gateway — twelve-factor app principles.

---

## 11. Monitoring & Observability

### 11.1 Metrics (Gateway)

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

### 11.2 Client Logging

`tgdb logs` shows:
- Every sync attempt (success/fail, timestamp, file size, duration)
- Queue depth and lag warnings
- Conflict detection events
- Telegram API errors with retry status

### 11.3 Health Checks

```
GET /health → 200 OK (Gateway is up)
GET /health/ready → 200 OK (Gateway + Registry + Redis all reachable)
GET /health/telegram → 200 OK (Bot token valid, Telegram API reachable)
```

---

## 12. Limitations & Trade-offs

| Aspect | Limitation | Mitigation |
|--------|-----------|------------|
| **Sync latency** | Seconds-scale, not millisecond | Document as "eventually synced" |
| **Durability window** | Between local write and confirmed upload, data is only local | Configurable batching interval, visible to user |
| **Rate limits** | ~20/min per channel, ~20-30/sec per bot | Backpressure, queue depth visibility |
| **Conflict resolution** | LWW only (v1) — silent data loss | Log both versions, surface to user |
| **File size** | 2GB practical limit (Telethon) | Chunking for larger databases (v2) |
| **Cold start** | Full pull required on new device | Document explicitly, show progress |
| **Centralization** | Web Gateway is a single point of coordination | Acceptable for target use cases |
| **Telegram dependency** | Telegram outage = no sync | Local data unaffected, sync queues |
| **No real-time sync** | Polling-based, not push-based | Acceptable for personal/low-concurrency use |

---

## 13. Future Considerations (v2+)

- **True conflict resolution:** Operational transform or CRDT-based merge for structured data
- **Multi-device push notifications:** Telegram webhook → Gateway → client notification
- **Database chunking:** Split large databases into chunks for rate-limit compliance
- **Incremental pull:** Only download changesets since last known version, not full file
- **Webhook sync:** Replace polling with Telegram Bot webhook for near-real-time sync
- **Multi-user sharing:** Shared channels with access control
- **Backup rotation:** Auto-prune old versions, configurable retention policy
- **Gateway federation:** Multiple gateway instances for redundancy
- **Client-side conflict UI:** Interactive conflict resolution via CLI prompts
