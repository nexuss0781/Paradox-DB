# parad

Encrypted local-first SQLite with Telegram cloud sync — a git-like, zero-cost
workflow database. Telegram is the disk: every change you commit becomes a
version-stored snapshot you can revert to anytime.

## Install

```bash
pip install parad
```

## Quick Start

### Python SDK (developer workflow — auto-sync on by default)

Connect with a postgres-like connection string, write SQL offline, and the
sync daemon version-stores your changes automatically. No manual push needed.

```python
from parad import connect

# Auto-login (email:password), auto-provision project + database on the
# gateway, open/create the local encrypted SQLite file.
db = connect(url="parad://alice@example.com:secretpw@local/myproj/mydb?passphrase=secret")

# Build SQL offline like any SQLite database ...
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)")
db.execute("INSERT INTO users (name) VALUES (?)", ("alice",))
db.commit()

# ... and it's already pushed to the cloud as a new snapshot version (~2s).
# Revert anytime (from the CLI):
# !parad rollback 1

db.close()
```

Offline? Keep writing — changes are flagged `dirty` and batch-pushed as new
versions the moment you reconnect. On conflicts your local data always wins
(never silently dropped).

Local-only is just as easy:

```python
db = connect("mydb", passphrase="secret", auto_sync=False)
```

### CLI

```bash
# Create an encrypted database
parad init mydb

# Push to cloud
parad push

# Pull latest
parad pull

# Check status
parad status

# Interactive SQL
parad shell
```

## Commands

| Command | Description |
|---|---|
| `parad init <name>` | Create encrypted DB + register with gateway; emit canonical DATABASE_URL |
| `parad push` | Push database to Telegram cloud |
| `parad pull [version]` | Pull latest or specific version |
| `parad sync` | Push then pull |
| `parad status` | Local vs remote version |
| `parad versions` | List all remote versions |
| `parad rollback <ver>` | Rollback to previous version |
| `parad exec <sql>` | Run raw SQL |
| `parad insert <table> <json>` | Insert a row |
| `parad select <table> [where]` | Query rows |
| `parad update <table> <set> <where>` | Update rows |
| `parad delete <table> <where>` | Delete rows |
| `parad shell` | Interactive SQL REPL |
| `parad config show/set` | Manage config |

## Canonical DATABASE_URL

After `parad init` successfully provisions the project and database, Parad persists the complete connection URL in `config.json` as `database_url` and prints a redacted form:

```bash
parad init mydb --project myproject
```

Use `--print-database-url` only when intentionally copying the complete secret-bearing URL into a secret manager:

```bash
parad init mydb --project myproject --print-database-url
```

Applications can use the same single value:

```python
import os
from parad import connect

db = connect(url=os.environ["DATABASE_URL"])
```

An explicit `url`, `name`, or `db_path` argument takes precedence over an ambient `DATABASE_URL`. Existing connection strings and config-based workflows remain supported.

## SQLAlchemy

Install the optional integration with `pip install "parad[sqlalchemy]"`, then use the same canonical URL with `create_engine("parad://...")`. The ORM, Core, DB-API, and encrypted lifecycle examples are documented in [`docs/SQLALCHEMY.md`](docs/SQLALCHEMY.md).

## Configuration

Config lives at `~/.paradox/config.json`:

```json
{
  "database_url": "parad://<api-key>@local/project/mydb?gateway=https%3A%2F%2F...&passphrase=...",
  "database_path": "~/.paradox/data.db",
  "sync": {
    "gateway_url": "https://paradox-db.onrender.com/v1",
    "api_key": "pk_..."
  }
}
```

## Security

- AES-256-CBC encryption at rest
- PBKDF2-HMAC-SHA512 key derivation (256k iterations)
- Your passphrase never leaves your machine
