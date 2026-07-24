# Paradox-DB Troubleshooting

## Client

### "EncryptionError: Database key is incorrect"

The passphrase doesn't match the database. Either:
- Set `PARADOX_PASSPHRASE` to the correct value
- The database file was created with a different passphrase

### "DatabaseNotOpenError"

Call `engine.open(passphrase)` before executing queries.

### "SQLiteError: not a database"

The file exists but isn't a valid SQLite database. Check `database_path` in config.

### better-sqlite3 fails to install

Ensure native build tools are available:
```bash
# Ubuntu/Debian
sudo apt install build-essential python3

# macOS
xcode-select --install

# Then rebuild
cd client && npm rebuild better-sqlite3
```

## Gateway

### Gateway won't start

1. Check all required environment variables are set:
   ```bash
   docker compose config
   ```
2. Check PostgreSQL is healthy:
   ```bash
   docker compose ps postgres
   ```
3. Check logs:
   ```bash
   docker compose logs gateway
   ```

### "rate_limited" on upload

You're hitting Telegram's upload limit (~20 files/min/channel). Wait 60 seconds and retry. The client handles this automatically with exponential backoff.

### "conflict_detected" (409)

Your local version is behind the remote. Pull first, then push:
```bash
tgdb pull
tgdb sync
```

### "lock_timeout"

Another upload for the same database is in progress. Wait for it to complete (default: 30s).

## Docker

### Ports already in use

```bash
# Check what's using the port
lsof -i :5432
lsof -i :8000

# Stop all Paradox services
docker compose down
```

### Certbot fails to issue certificate

1. Ensure DNS points to your server
2. Port 80 must be accessible from the internet
3. Check certbot logs:
   ```bash
   docker compose logs certbot
   ```

### "relation user_channels does not exist"

Run migrations:
```bash
docker compose exec gateway alembic upgrade head
```
