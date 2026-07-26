<p align="center">
  <img src="https://img.shields.io/badge/Paradox--DB-0.4.0-blue?style=for-the-badge&logo=sqlite&logoColor=white" alt="Paradox-DB">
</p>

<h1 align="center">Paradox-DB</h1>

<p align="center">
  <strong>Drop-in encrypted database with cloud sync.<br>One line of code. Your data, everywhere.</strong>
</p>

<p align="center">
  <a href="#-quickstart">Quickstart</a> •
  <a href="#-sdk">SDK</a> •
  <a href="#-cli">CLI</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-deployment">Deploy</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/SQLite-WAL-green?style=flat-square&logo=sqlite" alt="SQLite">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?style=flat-square&logo=telegram" alt="Telegram">
  <img src="https://img.shields.io/pypi/v/parad-blue?style=flat-square" alt="PyPI">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="MIT">
</p>

---

## What is Paradox-DB?

A local-first encrypted SQLite database that syncs to the cloud automatically. Install it, connect, and forget about infrastructure.

```python
from parad import connect

db = connect("users", passphrase="secret")
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
db.execute("INSERT INTO users VALUES (1, 'Alice')")
db.commit()  # auto-syncs to cloud
```

**No AWS. No Docker. No config files.** Just `pip install parad` and your data syncs through Telegram's infrastructure — free, encrypted, censorship-resistant.

---

## Quickstart

### Install

```bash
pip install parad
```

### Setup (one time)

```bash
parad auth register          # create account (prompts for email/password)
parad init users --project myapp  # creates everything: project, DB, local file, cloud backup
```

### Use

```bash
parad connect users          # connect + auto-sync daemon
parad insert users '{"name": "Alice", "email": "alice@test.com"}'
parad select users           # query data
parad status                 # check sync state
```

### In Python

```python
from parad import connect

# Connect to your database
db = connect("users", passphrase="secret")

# Full SQL support
db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, done INTEGER)")
db.execute("INSERT INTO tasks VALUES (1, 'Ship it', 0)")
db.commit()  # auto-syncs to cloud in background

# Query
rows = db.execute("SELECT * FROM tasks")
for row in rows:
    print(row["title"])

db.close()
```

### Connection String

```python
# Standard connection URL (like PostgreSQL)
db = connect(url="parad://local/users?passphrase=secret")

# From environment variable
import os
db = connect(url=os.environ["DATABASE_URL"])
```

```bash
# Generate connection URL
parad url
# → parad://local/users?passphrase=secret
```

```bash
# Environment variable support
export DATABASE_URL="parad://local/users?passphrase=secret"
export PARADOX_PASSPHRASE="secret"
```

---

## SDK

### `connect()` — The Core

```python
from parad import connect

# Basic
db = connect("mydb", passphrase="secret")

# With connection string
db = connect(url="parad://local/mydb?passphrase=secret")

# Auto-sync disabled
db = connect("mydb", passphrase="secret", auto_sync=False)
```

### Context Manager

```python
with connect("mydb", passphrase="secret") as db:
    db.execute("INSERT INTO logs VALUES (1, 'started')")
    db.commit()
# Automatically closes and syncs
```

### DB-API 2.0 Compatible

```python
db = connect("mydb", passphrase="secret")

# Execute SQL
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
db.execute("INSERT INTO users VALUES (?, ?)", ("Alice",))
db.commit()

# Query
rows = db.execute("SELECT * FROM users")
print(rows)  # [{"id": 1, "name": "Alice"}]

# Table inspection
db.tables()       # ["users"]
db.table_info("users")  # column info

# Cursor interface
cursor = db.cursor()
cursor.execute("SELECT * FROM users")
cursor.fetchall()  # [{"id": 1, "name": "Alice"}]
```

### Auto-Sync

When connected with `auto_sync=True` (default), a background daemon:

- **Pushes** local changes every 2 seconds (on file change)
- **Pulls** remote changes every 30 seconds
- **Handles conflicts** — pulls first on 409, then retries push
- **Never crashes** — all exceptions caught, keeps running

```python
db = connect("users", passphrase="secret", auto_sync=True)
db.execute("INSERT INTO data VALUES (1, 'auto-synced')")
db.commit()
# → Background thread pushes to cloud within seconds
```

### URL Helpers

```python
from parad import parse_url, generate_url

# Generate
url = generate_url("mydb", "secret123")
# → "parad://local/mydb?passphrase=secret123"

# Parse
parts = parse_url(url)
# → {"name": "mydb", "passphrase": "secret123", "gateway_url": ""}
```

---

## CLI

### Authentication

```bash
parad auth register          # register (prompts for email, username, password)
parad auth login             # login (prompts for email, password)
parad auth status            # show current user
```

Auto-auth: commands prompt for login if not authenticated.

### Database Setup

```bash
parad init <name>                    # one-step: auth + project + DB + local + push
parad init <name> --project <proj>   # specify project name
parad init <name> --watch            # start auto-sync daemon after init
parad connect <name>                 # connect to existing DB + start daemon
parad connect <name> --no-watch      # connect without daemon
parad url                            # show connection URL
```

### Data Operations

```bash
parad exec "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"
parad insert t '{"name": "Alice"}'
parad select t
parad select t '{"name": "Alice"}'   # with WHERE clause
parad update t '{"name": "Bob"}' '{"id": 1}'
parad delete t '{"id": 1}'
```

### Sync & Versions

```bash
parad push              # push local to cloud
parad pull              # pull latest from cloud
parad sync              # push + pull
parad status            # show sync state
parad versions          # list all versions
parad rollback          # rollback to previous version
```

### Project & Database Management

```bash
parad project create <name>
parad project list
parad project get <id>
parad project delete <id>

parad db create <project> <name>
parad db list <project>
parad db get <id>
parad db delete <id>
```

### Backups

```bash
parad backup create <db_id> <name> -n "before migration"
parad backup list <db_id>
parad backup restore <db_id> <backup_id>
```

### Advanced

```bash
parad shell              # interactive SQL REPL
parad config show        # show all config
parad config set key val # set config value
parad watch              # start sync daemon (foreground)
parad watch stop         # stop daemon
```

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                    Your App                       │
│                                                   │
│   from parad import connect                       │
│   db = connect("users", passphrase="secret")      │
│                                                   │
│   ┌────────────────────────────────────────────┐  │
│   │         ParadConnection (SDK)              │  │
│   │  ┌──────────────┐  ┌──────────────────┐   │  │
│   │  │ Engine        │  │ SyncDaemon       │   │  │
│   │  │ (encrypted    │  │ (background      │   │  │
│   │  │  SQLite)      │  │  push/pull)      │   │  │
│   │  └──────────────┘  └────────┬─────────┘   │  │
│   └─────────────────────────────┼──────────────┘  │
│                                 │                  │
└─────────────────────────────────┼──────────────────┘
                                  │ HTTPS
                   ┌──────────────▼──────────────┐
                   │       Gateway (FastAPI)      │
                   │  ┌──────────┐ ┌──────────┐  │
                   │  │PostgreSQL│ │  Redis   │  │
                   │  │ (users,  │ │ (locks)  │  │
                   │  │  projects│ │          │  │
                   │  │  dbs)    │ │          │  │
                   │  └──────────┘ └──────────┘  │
                   └──────────────┬──────────────┘
                                  │ Telegram Bot API
                   ┌──────────────▼──────────────┐
                   │     Telegram Channels        │
                   │  (encrypted file snapshots)  │
                   └─────────────────────────────┘
```

### How Sync Works

1. **Engine** decrypts the local `.db` file to a temp SQLite database
2. All queries run on the decrypted temp file (fast, familiar SQLite)
3. On `close()` or `commit()`, the temp file is re-encrypted and written back
4. **SyncDaemon** detects file changes via hash comparison
5. Changed file is uploaded to Telegram as a versioned snapshot
6. Remote changes are pulled periodically and written locally
7. Conflicts (409) trigger a pull-then-retry-push strategy

### Encryption

| Setting | Value |
|---------|-------|
| Cipher | AES-256-CBC |
| KDF | PBKDF2-HMAC-SHA512 |
| Iterations | 256,000 |
| Page Size | 4,096 bytes |

The encryption key **never leaves your machine**. The gateway stores raw SQLite data in Telegram channels — only you have the key.

---

## Gateway

### Deploy to Render

The gateway is live at **https://paradox-db.onrender.com**

```bash
# Local development
cd gateway
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_STORAGE_CHAT_ID` | Yes | Channel for file storage |
| `TELEGRAM_LOG_CHAT_ID` | No | Channel for log messages |
| `JWT_SECRET` | Yes | Random string for JWT signing |

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/auth/register` | Register user |
| `POST` | `/v1/auth/login` | Login |
| `GET` | `/v1/auth/me` | Current user |
| `GET/POST` | `/v1/projects` | List/create projects |
| `GET/POST` | `/v1/projects/{id}/databases` | List/create databases |
| `GET/PUT/DELETE` | `/v1/databases/{id}` | Database CRUD |
| `POST` | `/v1/upload` | Upload database |
| `GET` | `/v1/download` | Download database |
| `GET` | `/v1/status` | Sync status |
| `GET` | `/v1/versions` | Version history |
| `POST` | `/v1/rollback` | Rollback to version |
| `GET` | `/test` | Run E2E test suite |

---

## Configuration

### Environment Variables

```bash
PARADOX_PASSPHRASE="secret"     # default encryption passphrase
PARADOX_GATEWAY="https://paradox-db.onrender.com/v1"  # gateway URL
PARADOX_DATABASE="~/.paradox/mydb.db"  # override DB path
DATABASE_URL="parad://local/mydb?passphrase=secret"  # connection string
```

### Config File

Located at `~/.paradox/config.json`:

```json
{
  "database_path": "~/.paradox/users.db",
  "project_id": "...",
  "database_id": "...",
  "encryption": {
    "passphrase": "secret"
  },
  "sync": {
    "gateway_url": "https://paradox-db.onrender.com/v1",
    "api_key": "eyJ..."
  }
}
```

---

## Testing

```bash
# Gateway E2E test (live)
curl https://paradox-db.onrender.com/test

# Gateway unit tests
cd gateway && python -m pytest tests/ -v

# Client tests
python -c "from parad import connect; db = connect('test', passphrase='x'); db.execute('SELECT 1'); db.close()"
```

---

## Security

- **AES-256-CBC encryption** at rest with 256k PBKDF2 iterations
- **Encryption key never transmitted** — stays on your machine
- **JWT + API key auth** for gateway access
- **Redis distributed locks** prevent concurrent upload corruption
- **Parameterized queries** — SQL injection impossible
- **TLS 1.2+** for all transit

---

## Project Structure

```
Paradox-DB/
├── parad/                      # Python SDK + CLI (PyPI: parad)
│   └── parad/
│       ├── __init__.py         # exports connect(), ParadConnection
│       ├── cli.py              # 20+ CLI commands
│       ├── connection.py       # SDK: connect(), ParadConnection, SyncDaemon
│       ├── engine.py           # encrypted SQLite engine
│       ├── crypto.py           # AES-256-CBC encryption
│       ├── gateway.py          # HTTP client for gateway API
│       ├── watcher.py          # background sync daemon
│       ├── state.py            # sync state tracker
│       ├── config.py           # configuration management
│       ├── types.py            # Pydantic models
│       └── commands/           # CLI command implementations
│           ├── auth.py         # register, login, status
│           ├── init.py         # smart init (auto project+DB)
│           ├── connect.py      # connect + daemon
│           ├── sync.py         # push, pull, sync
│           ├── query.py        # exec, insert, select, update, delete
│           └── ...
├── gateway/                    # FastAPI gateway (Render-deployed)
│   ├── app/
│   │   ├── main.py             # FastAPI app, v2.0.0
│   │   ├── auth.py             # JWT + bcrypt
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── config.py           # Pydantic settings
│   │   └── routers/
│   │       ├── auth.py         # register, login, me
│   │       ├── projects.py     # project CRUD
│   │       ├── databases.py    # DB CRUD + versions + sync + rollback
│   │       ├── notifications.py # SSE real-time
│   │       └── test.py         # live E2E test suite
│   └── tests/
│       └── test_e2e.py         # unit tests (7/7)
└── README.md
```

---

## Roadmap

- [ ] Inotify-based file watching (replace polling)
- [ ] Multi-device merge (beyond last-write-wins)
- [ ] Node.js SDK (`npm install parad`)
- [ ] `parad://` OS protocol handler
- [ ] Web dashboard for sync monitoring
- [ ] WebSocket real-time sync notifications

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  Built with care. Your data belongs to you.
</p>
