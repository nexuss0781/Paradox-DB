# Paradox-DB Configuration Reference

## Client Configuration

Config file location: `~/.paradox/config.json`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `database_path` | string | `~/.paradox/data.sqlcipher` | Path to the SQLite database file |
| `encryption.cipher` | string | `aes-256-cbc` | SQLCipher cipher algorithm |
| `encryption.kdf_iterations` | number | `256000` | PBKDF2 key derivation iterations |
| `encryption.page_size` | number | `4096` | SQLite page size in bytes |
| `sync.gateway_url` | string | `http://localhost:8000/v1` | Gateway API base URL |
| `sync.api_key` | string | `""` | API key for gateway authentication |
| `sync.trigger_timer_seconds` | number | `30` | Auto-sync interval (0 = disabled) |
| `sync.trigger_ops_threshold` | number | `50` | Sync after N local operations |
| `sync.max_file_size_mb` | number | `50` | Max upload size (Telegram limit: 50) |
| `sync.auto_sync_on_shutdown` | boolean | `true` | Sync when client closes |
| `conflict.strategy` | string | `last-write-wins` | Conflict resolution strategy |
| `conflict.log_conflicts` | boolean | `true` | Log conflicts to database |
| `logging.level` | string | `info` | Log level: debug, info, warn, error |
| `logging.path` | string | `~/.paradox/logs` | Log file directory |

### Environment Variables (Client)

| Variable | Description |
|----------|-------------|
| `PARADOX_PASSPHRASE` | Database encryption passphrase |

## Gateway Configuration

All configuration via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection string |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `TELEGRAM_API_ID` | Yes | — | Telegram API ID |
| `TELEGRAM_API_HASH` | Yes | — | Telegram API hash |
| `JWT_SECRET` | Yes | — | Secret for JWT signing |
| `API_KEY_SALT` | Yes | — | Salt for API key hashing |
| `MAX_UPLOAD_SIZE_MB` | No | `50` | Max upload size |
| `RATE_LIMIT_UPLOADS_PER_MINUTE` | No | `15` | Telegram upload rate limit |
| `LOCK_TIMEOUT_SECONDS` | No | `30` | Distributed lock timeout |

### Docker Compose

```bash
# Development
docker compose up --build

# Production
cp .env.production .env  # Edit with real secrets
docker compose up --build -d
```

Services: `postgres`, `redis`, `gateway`, `nginx`, `certbot`
