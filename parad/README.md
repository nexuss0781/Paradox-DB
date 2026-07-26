# parad

Encrypted local-first SQLite with Telegram cloud sync.

## Install

```bash
pip install parad
```

## Quick Start

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
