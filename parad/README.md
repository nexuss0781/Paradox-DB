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
# Create a new encrypted database and print the canonical URL deliberately
parad auth login
parad init mydb --project myproject --print-database-url
# Retrieve the existing canonical URL; local values are checked first,
# then the owner-authenticated gateway recovery endpoint is used
parad url
parad url --print-database-url
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
| `parad config show/set` | Manage config; secret-bearing fields are redacted |
| `parad url [name]` | Retrieve the canonical database_url using local values, owner-authenticated server recovery, then legacy fallback |
| `parad url register <url>` | Explicitly store a known canonical URL on the gateway without snapshot mutation |
| `parad database-url [name]` | Alias for `parad url` |
## Canonical DATABASE_URL first
Use one canonical connection value for new applications and deployments. After `parad init` provisions the project/database, it persists `database_url` in `~/.paradox/config.json` and prints a redacted URL by default:
```bash
parad auth login
parad init mydb --project myproject
```
Print the complete secret-bearing value only when intentionally copying it into a secret manager:
```bash
parad init mydb --project myproject --print-database-url
```
For an existing database, retrieve the saved canonical value. If local values are unavailable, the CLI resolves the owned project/database and calls the read-only owner-authenticated gateway recovery endpoint:
```bash
parad url
parad url --print-database-url
```
Retrieval checks `DATABASE_URL`, then persisted `database_url`, then server recovery, then reconstructs and persists a canonical URL from legacy split fields when a passphrase is available. Project-scoped connections register the canonical URL with the gateway, which stores it encrypted at rest. The full value is returned only with `--print-database-url`; ordinary output is redacted. If the server has no stored URL and the passphrase is missing, it stops instead of inventing a replacement. New projects should use only `DATABASE_URL`; split fields remain supported for legacy applications.

For a database created before server URL storage existed, provide the already-known URL once with `parad url register '<url>'`. This updates only the encrypted server field; it does not run `init`, push, pull, or overwrite snapshots. A URL that was never previously stored cannot be recovered from server metadata alone.
Applications can use the same single value:
```python
import os
from parad import connect
db = connect(url=os.environ["DATABASE_URL"])
```

For a pre-feature database whose canonical URL is already known, register it explicitly without opening or syncing the database:
```python
from parad import register_canonical_database_url
register_canonical_database_url(os.environ["DATABASE_URL"])
```

An explicit `url` argument is strongest. Explicit `name` or `db_path` options remain available for legacy target selection.


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

The gateway stores the canonical `database_url` encrypted at rest in `paradox_dbs.database_url_encrypted`. Set a stable `DATABASE_URL_ENCRYPTION_KEY` on the gateway. The redacted endpoint is safe for metadata checks; full recovery requires the owner API key and an explicit reveal command.

- AES-256-CBC encryption at rest
- PBKDF2-HMAC-SHA512 key derivation (256k iterations)
- Your passphrase never leaves your machine
