<p align="center">
  <br>
  <img src="https://img.shields.io/badge/Paradox--DB-Local--First%20Cloud%20Sync-blue?style=for-the-badge&logo=sqlite&logoColor=white" alt="Paradox-DB">
  <br><br>
</p>

<h1 align="center">Paradox-DB</h1>

<p align="center">
  <strong>Local-first SQLite database with Telegram-backed async cloud sync</strong>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-features">Features</a> •
  <a href="#-deployment">Deployment</a> •
  <a href="#-api-reference">API</a> •
  <a href="#-contributing">Contributing</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/TypeScript-5.5-blue?style=flat-square&logo=typescript" alt="TypeScript">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/SQLite-WAL-green?style=flat-square&logo=sqlite" alt="SQLite">
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?style=flat-square&logo=telegram" alt="Telegram">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="MIT License">
</p>

---

## What is Paradox-DB?

Paradox-DB solves a fundamental problem: **you shouldn't need the internet to use your database, but you should always have a backup.**

It's a local-first database engine that gives you **sub-millisecond reads and writes** on your machine, while asynchronously syncing to the cloud through Telegram's infrastructure — one of the most reliable, censorship-resistant messaging platforms on Earth.

No AWS bills. No complex distributed systems. Just your data, fast on your device, safe in the cloud.

---

## Why Telegram?

| Traditional Cloud DB | Paradox-DB |
|---------------------|------------|
| Monthly costs that scale with storage | **Free** — Telegram bots get unlimited storage |
| Vendor lock-in (AWS, GCP, Azure) | **No lock-in** — Telegram is a protocol, not a platform |
| Complex auth and IAM setup | **Simple** — bot token + channel = done |
| Single point of failure | **Censorship-resistant** — Telegram operates across jurisdictions |
| Requires always-on connection | **Async** — sync when you want, work offline always |

---

## Features

### Local Engine
- **SQLCipher encryption** — AES-256-CBC with 256k PBKDF2 iterations
- **WAL mode** — concurrent reads during writes, zero-lock reads
- **Sub-millisecond latency** — all queries hit local disk
- **Full CRUD** — INSERT, SELECT, UPDATE, DELETE with parameterized queries

### Cloud Sync
- **Async push/pull** — sync on timer, on operation count, or manually
- **Version tracking** — every sync creates a versioned snapshot
- **Conflict detection** — last-write-wins with full conflict logging
- **Rate limiting** — respects Telegram's 20 files/min/channel limit
- **Automatic retry** — 6-level exponential backoff on transient failures

### Gateway API
- **FastAPI + PostgreSQL** — async, typed, auto-documented
- **JWT + API key auth** — dual authentication strategies
- **Redis distributed locks** — prevents concurrent upload corruption
- **Prometheus metrics** — request counts, latency histograms, error rates
- **Docker Compose** — one command to production

### CLI
```bash
tgdb init mydb                    # Create encrypted database
tgdb insert notes '{"title":"Hi"}' # Insert data
tgdb select notes                  # Query data
tgdb sync                          # Push to cloud
tgdb pull                          # Pull from cloud
tgdb status                        # Check sync state
tgdb shell                         # Interactive SQL REPL
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Telegram Channel                     │
│     (versioned snapshots, encrypted files)        │
└──────────────────────┬──────────────────────────┘
                       │ async upload/download
┌──────────────────────▼──────────────────────────┐
│              Web Gateway (FastAPI)                │
│  ┌─────────────┐  ┌──────────┐  ┌────────────┐  │
│  │ PostgreSQL   │  │  Redis   │  │  Nginx     │  │
│  │ (registry)   │  │ (locks)  │  │  (TLS)     │  │
│  └─────────────┘  └──────────┘  └────────────┘  │
└──────────────────────▲──────────────────────────┘
                       │ HTTP REST (auth + rate-limit)
┌──────────────────────┴──────────────────────────┐
│              Client Engine (Bun/Node.js)          │
│  ┌─────────────────────────────────────────────┐ │
│  │  local.sqlcipher (SQLCipher, WAL mode)      │ │
│  │  ChangeTracker → SyncManager → Gateway      │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## Quick Start

### Prerequisites

- [Bun](https://bun.sh) >= 1.0 (or Node.js >= 20)
- [Docker](https://docker.com) + Docker Compose
- A [Telegram Bot Token](https://t.me/BotFather)

### 1. Clone & Install

```bash
git clone https://github.com/nexuss0781/Paradox-DB.git
cd Paradox-DB
make install
```

### 2. Start the Gateway

```bash
cp .env.example .env
# Edit .env with your Telegram credentials

docker compose up --build -d
curl http://localhost:8000/health
# → {"status": "healthy"}
```

### 3. Use the Client

```bash
cd client

# Create a database
bun run src/cli.ts init myapp

# Insert some data
bun run src/cli.ts exec "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
bun run src/cli.ts insert users '{"name":"Alice","email":"alice@example.com"}'

# Query it
bun run src/cli.ts select users

# Sync to the cloud
bun run src/cli.ts config set sync.api_key YOUR_API_KEY
bun run src/cli.ts sync
```

### 4. Deploy to Render (Production)

See [docs/setup.md](docs/setup.md) for full deployment guide.

```bash
# One-click deploy button (coming soon)
# Or follow the manual steps in docs/setup.md
```

---

## Features Deep Dive

### Encryption

Every database file is encrypted with SQLCipher before it touches disk:

| Setting | Value |
|---------|-------|
| Cipher | AES-256-CBC |
| KDF | PBKDF2-HMAC-SHA512 |
| Iterations | 256,000 |
| Page Size | 4,096 bytes |

The encryption key **never leaves your machine** and is **never transmitted** to the gateway.

### Change Tracking

Every write operation is automatically tracked:

```typescript
engine.insert('notes', { title: 'Hello' });
// → Changeset buffer records: { type: 'insert', table: 'notes', data: {...} }

engine.update('notes', { title: 'Updated' }, { id: 1 });
// → Changeset buffer records: { type: 'update', table: 'notes', where: {id:1}, set: {title:'Updated'} }
```

Changesets are exported as JSON patches, uploaded to Telegram, and can be imported on other devices.

### Conflict Resolution

When two devices modify the same data:

1. **Detection** — gateway compares client version vs. remote version
2. **Response** — returns 409 with remote version info
3. **Resolution** — client pulls remote, merges locally (LWW in v1)
4. **Logging** — conflict details stored in `conflict_log` table

### Retry with Exponential Backoff

Network failures trigger automatic retry with 6 escalating levels:

```
Level 1: 1s delay    (immediate retry)
Level 2: 2s delay    (brief wait)
Level 3: 5s delay    (moderate wait)
Level 4: 15s delay   (extended wait)
Level 5: 60s delay   (patience)
Level 6: 300s delay  (last resort)
```

---

## Project Structure

```
Paradox-DB/
├── client/                    # TypeScript client engine
│   ├── src/
│   │   ├── engine.ts          # Core SQLite CRUD operations
│   │   ├── change-tracker.ts  # Changeset recording & export
│   │   ├── sync-manager.ts    # Push/pull orchestration
│   │   ├── conflict-handler.ts # LWW conflict resolution
│   │   ├── retry.ts           # Exponential backoff retry
│   │   ├── config.ts          # Configuration management
│   │   ├── cli.ts             # Full CLI interface
│   │   ├── types.ts           # TypeScript type definitions
│   │   ├── errors.ts          # Custom error classes
│   │   └── index.ts           # Public API exports
│   └── tests/                 # 8 test suites, 90+ tests
├── gateway/                   # Python FastAPI gateway
│   ├── app/
│   │   ├── main.py            # FastAPI application
│   │   ├── auth.py            # JWT + API key authentication
│   │   ├── database.py        # SQLAlchemy async engine
│   │   ├── models.py          # Database models (User, Version, Conflict)
│   │   ├── metrics.py         # Prometheus metrics
│   │   ├── retry.py           # Telegram retry logic
│   │   ├── config.py          # Pydantic settings
│   │   ├── routers/           # API endpoints
│   │   │   ├── upload.py      # Push sync endpoint
│   │   │   ├── download.py    # Pull sync endpoint
│   │   │   ├── versions.py    # Version listing
│   │   │   ├── rollback.py    # Version rollback
│   │   │   ├── status.py      # Sync status
│   │   │   ├── health.py      # Health checks
│   │   │   └── auth.py        # User registration
│   │   └── services/
│   │       └── telegram.py    # Telegram Bot API client
│   └── tests/                 # 10 test suites, 100+ tests
├── nginx/nginx.conf           # TLS termination + rate limiting
├── docs/                      # Documentation
│   ├── setup.md               # Installation guide
│   ├── configuration.md       # All config options
│   ├── api.md                 # Gateway API reference
│   ├── cli.md                 # CLI command reference
│   └── troubleshooting.md     # Common errors and fixes
├── tests/                     # Load test + security audit
│   ├── load_test.py           # 100-user concurrent load test
│   └── security_audit.py      # Security checklist validator
├── docker-compose.yml         # Production stack (5 services)
├── install.sh                 # One-line installer
├── Makefile                   # Dev commands
└── SPEC/                      # Engineering specifications
```

---

## API Reference

### Authentication

```bash
# Register a new user
curl -X POST http://localhost:8000/v1/auth/register
# → { "user_id": "...", "api_key": "pk_...", "channel_id": "" }
```

### Sync Push

```bash
# Upload database to Telegram
curl -X POST http://localhost:8000/v1/upload \
  -H "X-API-Key: pk_..." \
  -H "Content-Type: application/json" \
  -d '{"database_name": "mydb", "file_data": "<base64>"}'
# → { "version": 2, "message_id": "123" }
```

### Sync Pull

```bash
# Download latest version
curl -X GET "http://localhost:8000/v1/download?database_name=mydb" \
  -H "X-API-Key: pk_..."
# → Binary SQLite file
```

### Version History

```bash
# List all versions
curl -X GET "http://localhost:8000/v1/versions?database_name=mydb" \
  -H "X-API-Key: pk_..."
# → { "versions": [{ "version": 1, "uploaded_at": "..." }] }
```

Full API docs: [docs/api.md](docs/api.md)

---

## Deployment

### Render (Recommended)

1. Fork this repo
2. Create a **PostgreSQL** instance on Render
3. Create a **Redis** instance on Render
4. Create a **Web Service** from this repo
5. Set environment variables (see [docs/configuration.md](docs/configuration.md))
6. Deploy

### Docker Compose (Self-Hosted)

```bash
cp .env.production .env
# Fill in all secrets

docker compose up --build -d
```

This starts 5 services: Gateway, PostgreSQL, Redis, Nginx (TLS), Certbot (auto-renew).

### Manual

```bash
# Gateway
cd gateway
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Client
cd client
bun install && bun run build
```

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_API_ID` | Yes | API credentials from my.telegram.org |
| `TELEGRAM_API_HASH` | Yes | API credentials from my.telegram.org |
| `POSTGRES_PASSWORD` | Yes | Strong database password |
| `JWT_SECRET` | Yes | Random string for JWT signing |
| `API_KEY_SALT` | Yes | Random string for API key hashing |

Full config reference: [docs/configuration.md](docs/configuration.md)

---

## Testing

```bash
# Client tests (90+ tests)
cd client && bun test

# Gateway tests (100+ tests, requires Docker)
cd gateway && docker compose up -d postgres redis
PYTHONPATH=. pytest tests/ -v

# Load test (100 concurrent users)
python3 tests/load_test.py --url http://localhost:8000

# Security audit
python3 tests/security_audit.py --url http://localhost:8000
```

---

## Security

- **SQLCipher encryption** — database encrypted at rest with AES-256
- **Key never transmitted** — encryption key stays on your machine
- **TLS 1.2+** — all transit encrypted via Nginx
- **Rate limiting** — per-user and per-channel limits prevent abuse
- **Parameterized queries** — SQL injection impossible
- **No secrets in logs** — API keys and tokens are hashed/masked

Security audit script: `python3 tests/security_audit.py`

---

## Roadmap

- [ ] Web UI dashboard for sync monitoring
- [ ] Multi-device merge (beyond LWW)
- [ ] End-to-end encryption for gateway transit
- [ ] Webhook notifications on sync events
- [ ] Mobile client (React Native)
- [ ] Desktop app (Electron/Tauri)

---

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing`)
5. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  Built with care. Your data belongs to you.
</p>
