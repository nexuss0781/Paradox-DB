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

### WASM binary fails to load (sql.js)

`sql.js` ships the SQLite engine as WebAssembly in
`node_modules/sql.js/dist/sql-wasm.wasm`. If your bundler strips `.wasm` assets,
copy it beside the bundle or call `initSqlJs({ locateFile: (f) => … })` and point
it at the `.wasm` file. The `parad` package ships no native bindings, so there is
never a `node-gyp`/better-sqlite3 build step.
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
