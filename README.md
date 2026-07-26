<p align="center">
  <img src="https://img.shields.io/badge/Paradox--DB-1.0.3-blue?style=for-the-badge&logo=sqlite&logoColor=white" alt="Paradox-DB">
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
  <a href="#-gateway">Gateway</a>
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

## Platform Support

| Platform | Status | Install |
|----------|--------|---------|
| **Linux** (x86_64) | Full | `pip install parad` |
| **macOS** (Intel + Apple Silicon) | Full | `pip install parad` |
| **Windows** (x86_64) | Full | `pip install parad` |
| **Python 3.10+** | Required | — |

CLI, SDK, and gateway all run on any platform Python supports. Gateway deploys to Render (Linux containers).

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

All 33 commands and subcommands:

```
parad [--version]
│
├── connect <NAME>                Connect to a database + start sync daemon
│   --passphrase                  Encryption passphrase (env: PARADOX_PASSPHRASE)
│   --no-watch                    Don't start auto-sync daemon
│   --watch                       Keep running in foreground
│
├── init <NAME>                   One-step bootstrap: auth + project + DB + local + push
│   --passphrase                  Encryption passphrase
│   --gateway                     Override gateway URL
│   --project                     Project name (creates if not found)
│   --watch                       Start daemon after init
│
├── push                          Push local database to gateway
│   --passphrase                  Encryption passphrase
│
├── pull [VERSION]                Pull database from gateway (optional: specific version)
│   --passphrase                  Encryption passphrase
│
├── sync                          Push + pull (bidirectional)
│   --passphrase                  Encryption passphrase
│
├── status                        Show local vs remote sync state
│
├── versions                      List remote versions of current database
│   --name/-n                     Database name
│
├── rollback <VERSION>            Roll back to a previous version + pull locally
│   --passphrase                  Encryption passphrase
│
├── exec <SQL>                    Execute raw SQL
│   --passphrase                  Encryption passphrase
│
├── insert <TABLE> <DATA_JSON>    Insert a row from JSON
│   --passphrase                  Encryption passphrase
│
├── select <TABLE> [WHERE_JSON]   Query rows (optional JSON WHERE)
│   --passphrase                  Encryption passphrase
│
├── update <TABLE> <SET> <WHERE>  Update rows with JSON SET and WHERE
│   --passphrase                  Encryption passphrase
│
├── delete <TABLE> <WHERE_JSON>   Delete rows matching JSON WHERE
│   --passphrase                  Encryption passphrase
│
├── shell                         Interactive SQL REPL (exit: quit, \q)
│   --passphrase                  Encryption passphrase
│
├── watch                         Manage auto-sync daemon
│   --passphrase                  Encryption passphrase
│   --stop                        Stop the running daemon
│   --status                      Show daemon PID / status
│
├── auth                          Authentication group
│   register                      Create account (prompts for email, username, password)
│       --email                   Email (prompted)
│       --username                Username (prompted)
│       --password                Password (prompted, hidden)
│   login                         Log in (prompts for email, password)
│       --email                   Email (prompted)
│       --password                Password (prompted, hidden)
│   status                        Show current logged-in user
│
├── config                        Configuration group
│   show                          Print current config as JSON
│   set <KEY> <VALUE>             Set a config value (dotted path, e.g. sync.api_key)
│
├── project                       Project management group
│   list                          List all projects
│   create <NAME>                 Create a project
│       --description/-d          Description text
│   get <NAME_OR_ID>              Get project details
│   delete <NAME_OR_ID>          Delete project + all databases
│       --yes                     Skip confirmation
│
├── db                            Database management group
│   list <PROJECT>                List databases in a project
│   create <PROJECT> <NAME>       Create a database in a project
│       --description/-d          Description text
│   get <DATABASE_ID>             Get database details
│   delete <DATABASE_ID>          Delete database + all versions
│       --yes                     Skip confirmation
│
├── version                       Version management group
│   list <DATABASE_ID>            List all versions of a database
│   diff <DATABASE_ID> <A> <B>   Compare two versions (hash, size, identical)
│
└── backup                        Backup management group
    create <DATABASE_ID> <NAME>   Create a named backup at current version
        --notes/-n                Optional notes
    list <DATABASE_ID>            List all backups
    restore <DATABASE_ID> <ID>    Restore from backup (creates new version)
        --yes                     Skip confirmation
```

### Examples

```bash
# Full workflow: init → use → manage
parad auth register
parad init users --project myapp
parad exec "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
parad insert users '{"name": "Alice", "email": "alice@example.com"}'
parad insert users '{"name": "Bob", "email": "bob@example.com"}'
parad select users
parad select users '{"name": "Alice"}'
parad update users '{"email": "alice@new.com"}' '{"id": 1}'
parad delete users '{"id": 2}'
parad push
parad status

# Project management
parad project list
parad project create myapp -d "My application"
parad db list myapp
parad db create myapp analytics

# Version management
parad versions
parad rollback 3
parad version list <database_id>
parad version diff <database_id> 1 3

# Backups
parad backup create <db_id> before-migration -n "Pre-migration snapshot"
parad backup list <db_id>
parad backup restore <db_id> <backup_id>

# Interactive SQL
parad shell
parad> SELECT * FROM users;
parad> .quit

# Auto-sync daemon
parad connect users --watch    # foreground
parad watch                    # background
parad watch --status           # check PID
parad watch --stop             # kill daemon
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

### Deploy

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

**Auth**
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/auth/register` | Register user |
| `POST` | `/v1/auth/login` | Login |
| `GET` | `/v1/auth/me` | Current user |

**Projects**
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/projects` | List projects |
| `POST` | `/v1/projects` | Create project |
| `GET` | `/v1/projects/{id}` | Get project |
| `PUT` | `/v1/projects/{id}` | Update project |
| `DELETE` | `/v1/projects/{id}` | Delete project |

**Databases**
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/projects/{id}/databases` | List databases |
| `POST` | `/v1/projects/{id}/databases` | Create database |
| `GET` | `/v1/databases/{id}` | Get database |
| `PUT` | `/v1/databases/{id}` | Update database |
| `DELETE` | `/v1/databases/{id}` | Delete database |

**Versions & Sync**
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/databases/{id}/versions` | List versions |
| `GET` | `/v1/databases/{id}/versions/{n}` | Get version |
| `GET` | `/v1/databases/{id}/diff` | Compare versions |
| `POST` | `/v1/upload` | Upload database |
| `GET` | `/v1/download` | Download database |
| `GET` | `/v1/status` | Sync status |
| `GET` | `/v1/versions` | Version history |
| `POST` | `/v1/rollback` | Rollback to version |

**Backups**
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/databases/{id}/backups` | Create backup |
| `GET` | `/v1/databases/{id}/backups` | List backups |
| `POST` | `/v1/databases/{id}/backups/{bid}/restore` | Restore backup |

**System**
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/test` | Run E2E test suite |
| `GET` | `/health` | Health check |

---

## Configuration

### Environment Variables

All parad behavior is configurable via environment variables. These override any values in `~/.paradox/config.json`.

```bash
# Authentication (for CI/cloud — no interactive prompts)
PARADOX_API_KEY="eyJ..."          # API token from parad auth login

# Encryption
PARADOX_PASSPHRASE="secret"       # default encryption passphrase

# Gateway
PARADOX_GATEWAY="https://paradox-db.onrender.com/v1"  # gateway URL

# Database
PARADOX_DATABASE="~/.paradox/mydb.db"  # override DB path

# Connection string (parsed by SDK)
DATABASE_URL="parad://local/mydb?passphrase=secret"
```

### .env File Support

parad automatically loads `.env` files from the current directory (if `python-dotenv` is installed — included by default):

```bash
# .env
PARADOX_API_KEY=eyJ...
PARADOX_PASSPHRASE=mysecret
PARADOX_GATEWAY=https://paradox-db.onrender.com/v1
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

### Cloud / CI Deployment

For non-interactive environments (Render, GitHub Actions, Docker, etc.), set `PARADOX_API_KEY` as an environment variable. All commands will use it automatically without prompting.

**Render:**
```bash
# Set in Render dashboard → Environment
PARADOX_API_KEY=eyJ...
PARADOX_PASSPHRASE=mysecret
```

**GitHub Actions:**
```yaml
env:
  PARADOX_API_KEY: ${{ secrets.PARADOX_API_KEY }}
  PARADOX_PASSPHRASE: ${{ secrets.PARADOX_PASSPHRASE }}
steps:
  - run: parad push
```

**Docker:**
```bash
docker run -e PARADOX_API_KEY=eyJ... -e PARADOX_PASSPHRASE=secret myapp
```

**Shell / script:**
```bash
export PARADOX_API_KEY="eyJ..."
export PARADOX_PASSPHRASE="secret"
parad push
parad pull
parad status
```

If no token is set and stdin is not a TTY, parad exits with a clear error:
```
Error: Not authenticated. Set PARADOX_API_KEY environment variable or run 'parad auth login' first.
```

---

## Testing

```bash
# Gateway E2E test (live) — all 10 steps
curl https://paradox-db.onrender.com/test

# Gateway unit tests
cd gateway && python -m pytest tests/ -v

# Quick SDK smoke test
python3 -c "
from parad import connect
db = connect('_test', passphrase='x')
db.execute('CREATE TABLE t (id INTEGER PRIMARY KEY)')
db.execute('INSERT INTO t VALUES (1)')
db.commit()
print(db.execute('SELECT * FROM t'))
db.close()
"
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
├── parad/                          # Python SDK + CLI (PyPI: parad)
│   └── parad/
│       ├── __init__.py             # exports connect(), ParadConnection
│       ├── cli.py                  # 33 CLI commands (Click)
│       ├── connection.py           # SDK: connect(), ParadConnection, SyncDaemon
│       ├── engine.py               # encrypted SQLite engine
│       ├── crypto.py               # AES-256-CBC encryption
│       ├── gateway.py              # HTTP client for gateway API
│       ├── watcher.py              # background sync daemon
│       ├── state.py                # sync state tracker
│       ├── config.py               # configuration management
│       ├── types.py                # Pydantic models
│       └── commands/
│           ├── __init__.py         # group registration
│           ├── auth.py             # register, login, status
│           ├── init.py             # smart init (auto project+DB)
│           ├── connect.py          # connect + daemon
│           ├── sync.py             # push, pull, sync
│           ├── query.py            # exec, insert, select, update, delete
│           ├── status.py           # status, versions, rollback
│           ├── shell.py            # interactive SQL REPL
│           ├── watch.py            # daemon management
│           ├── projects.py         # project CRUD
│           ├── databases.py        # database CRUD
│           ├── versions.py         # version list, diff
│           ├── backups.py          # backup create, list, restore
│           └── config_cmd.py       # config show, set
├── gateway/                        # FastAPI gateway (Render-deployed)
│   ├── app/
│   │   ├── main.py                 # FastAPI app, v2.0.0
│   │   ├── auth.py                 # JWT + bcrypt
│   │   ├── models.py               # SQLAlchemy models
│   │   ├── config.py               # Pydantic settings
│   │   └── routers/
│   │       ├── auth.py             # register, login, me
│   │       ├── projects.py         # project CRUD
│   │       ├── databases.py        # DB CRUD + versions + sync + rollback + legacy
│   │       ├── notifications.py    # SSE real-time
│   │       └── test.py             # live E2E test suite (10/10)
│   └── tests/
│       └── test_e2e.py             # unit tests (7/7)
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
