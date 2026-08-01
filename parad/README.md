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
| `parad init <name>` | Create encrypted DB + register with gateway |
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

## Configuration

Config lives at `~/.paradox/config.json`:

```json
{
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
