# Design: SQLite-Local / Telegram-Synced Database

**Status:** Draft
**Pattern:** Local-first storage with asynchronous cloud sync
**One-line description:** A local SQLite engine gives the app real (sub-millisecond) query speed; Telegram is used purely as an off-site, versioned, durable *object store* for the underlying `.sqlite` file or its changesets — not as a query engine.

---

## 1. Why this pattern, and what it is not

Telegram's Bot API is a general-purpose messaging API, not a database wire protocol. It was never built for synchronous, low-latency request/response cycles at OLTP scale. So this design does **not** attempt `Query → Telegram → Result`. Instead:

- All reads and writes hit a **local SQLite file**. This is where the actual "millisecond performance" comes from — and it comes from SQLite, not from Telegram.
- Telegram is used **asynchronously**, in the background, as cheap unlimited cloud storage for backup, versioning, and multi-device sync.

Treat it as a **cloud-synced SQLite provider**, not a distributed database. Good fit: personal projects, config storage, low-concurrency apps, sidestepping cloud storage bills. Bad fit: anything needing real-time multi-writer consistency, sub-second cross-client sync, or contractual durability guarantees.

---

## 2. Architecture Overview

```
┌─────────────┐        instant read/write        ┌──────────────┐
│   Client     │ ───────────────────────────────► │  local.sqlite │
│ (CLI / Lib)  │ ◄─────────────────────────────── │  (WAL mode)   │
└──────┬───────┘                                   └──────────────┘
       │  async, batched
       │  (diff / changeset upload)
       ▼
┌─────────────────┐      sendDocument / getFile     ┌───────────────────┐
│  Web Gateway API │ ───────────────────────────────► │ Telegram Channel   │
│ (FastAPI/Elysia) │ ◄─────────────────────────────── │ (private, bot=admin)│
│  + Registry DB   │                                   └───────────────────┘
└──────────────────┘
```

**Components:**

1. **Client (CLI/Lib)** — owns the local SQLite file, handles change tracking (WAL / session extension), and pushes diffs on a timer or operation-count trigger.
2. **Web Gateway (API)** — auth, rate-limit buffering, talks to the Telegram Bot API, owns a small Registry DB (Postgres/Redis) mapping `UserID → ChannelID` and `DatabaseName → latest Telegram message_id`.
3. **Telegram Channel** — the "hard drive." Private channel, bot is admin, each version/changeset is a message with an attached file.

---

## 3. Component Detail

### A. Client Engine
- Every `INSERT`/`UPDATE`/`DELETE` executes on `local.sqlite` immediately (< 1ms).
- Change tracking via **SQLite Session Extension** (preferred) so only binary diffs are shipped, not the whole file.
- Before starting a session, pulls the latest version from Telegram to reduce (not eliminate) conflict risk.

### B. Web Gateway
- Registry DB (Postgres or Redis): `UserID → ChannelID`, `DatabaseName → Latest_Telegram_Message_ID`.
- Wraps `sendDocument` (upload) and `getFile` (download).
- Logs every request (type, timestamp, user, success/failure) so failed syncs are visible and retryable.
- Should own a **distributed lock** (e.g. Redis lock) per database to serialize concurrent uploads — this is itself a new single point of coordination, see §5.

### C. Telegram Channel
- Private channel, bot as admin.
- Each version identified by Telegram's file/message ID — this *is* your version history for free, which is a genuine advantage of this approach.

---

## 4. Data Flow

**Write (fast path, local only):**
1. `db.insert({name: "Alice"})`
2. Executes on local `data.db` — **< 1ms**.
3. *(async, out of band)* Client sends diff to `/upload`.
4. Gateway calls `bot.send_document(chat_id, file)`.
5. Telegram returns `message_id`; Registry updated.

**Read (fast path, local only):**
1. `db.select("name")` — hits local file — **< 1ms**.

Note what's *not* in this flow: nothing about the write is confirmed durable in Telegram until step 5 completes, and the client already returned control to the caller before that happens.

---

## 5. Reality Check — Where "Millisecond Performance" Actually Breaks Down

This is the part worth being explicit about, because it's easy to read "< 1ms" in the diagram and assume the whole system is millisecond-grade. It isn't. Only the *local* leg is.

| Claim | What's actually true |
|---|---|
| "Reads/writes are < 1ms" | True, but that's SQLite on local disk. Telegram contributes **zero** to this number — it's off the critical path entirely. |
| "Syncs happen fast in the background" | Telegram Bot API round-trips typically run **200–800ms**, more for larger files, before you even account for encryption/upload time. This is not millisecond-grade by any definition. |
| "Multi-device / multi-user sync" | A change is only visible to another client after: upload completes → Registry updates → the other client polls or is notified → it downloads and applies. Realistic end-to-end propagation is **hundreds of ms to several seconds**, not "sync." |
| "Bot API rate limits" | ~20–30 messages/sec per bot, and tighter limits (~20/min) for messages into the same chat. This caps sustained write throughput hard — high-frequency writers will queue, and that queue depth is unbounded unless you build backpressure. |
| "Durability" | Between a local write and a *successful, confirmed* Telegram upload, data lives only on local disk. A crash, disk failure, or lost device in that window is **data loss**, not a sync delay. This window is not "milliseconds" — it's however long your batching interval (`X` seconds / `Y` ops) is. |
| "Conflict resolution" | Documented as last-write-wins. That's not conflict *resolution*, it's silent conflict *erasure* — the losing writer's changes are gone with no merge, no alert, unless you build one. |
| "Locking" | The Web Gateway lock solves cross-client races for uploads, but it's a new centralized dependency you now have to keep available and correct — the exact kind of infrastructure this design was trying to avoid needing. |
| "2GB file limit is fine" | True for storage, false for latency: pulling a large file to check/merge state before a write defeats the "instant" read path the moment the local cache is stale or missing (new device, cache eviction, cold start). |

**Bottom line:** the local SQLite layer is genuinely millisecond-fast. The Telegram-backed layer is a **best-effort, seconds-scale, eventually-consistent replication/backup mechanism** riding on a messaging API's rate limits and latency profile. That's a fine trade for the stated use cases (config storage, personal projects, low-concurrency side projects), but the design should be described and sold as "instant local, eventually-synced cloud" — not as a millisecond database with Telegram as its backend.

---

## 6. Recommended Mitigations

- **Backpressure**: if the upload queue backs up past the rate limit, surface it to the client (`tgdb logs` already logs failures — extend this to queue depth/lag).
- **Durability window disclosure**: make the batching interval (`X` seconds / `Y` ops) configurable and visible, so users can trade risk vs. request volume knowingly.
- **Conflict visibility**: at minimum, log both versions on a detected conflict instead of silently overwriting, even if true merge is out of scope for v1.
- **Encryption**: SQLCipher before upload, key held client-side only, as already specified — this is correct and shouldn't be weakened for convenience.
- **Cold-start cost**: document explicitly that a new device's first read is *not* millisecond — it requires a full pull first.

---

## 7. Technical Stack (as proposed)

- **Backend:** FastAPI (Python) or Elysia (Bun/JS)
- **Telegram client:** Telethon (Python) or GramJS (JS) — better large-file/stream handling than the plain Bot API
- **Change tracking:** SQLite Session Extension (changesets, not full-file diffs)
- **Encryption:** SQLCipher, client-held key only
- **Registry store:** Postgres or Redis

---

## 8. Verdict

A legitimate and useful **cloud-synced SQLite provider** for low-concurrency, cost-sensitive use cases — config stores, personal tools, hobby projects. Not a real-time or high-concurrency database, and the "millisecond" framing should be scoped explicitly to the local read/write path, not the system as a whole.
