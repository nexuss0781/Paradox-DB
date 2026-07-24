# Paradox-DB Setup Guide

## Prerequisites

- **Client:** Bun >= 1.0 or Node.js >= 20 with better-sqlite3 native bindings
- **Gateway:** Python 3.11+, Docker & Docker Compose
- **Telegram:** Bot token from @BotFather, API credentials from my.telegram.org

## Quick Start (Client)

```bash
# Install
bun install

# Initialize a database
bun run src/cli.ts init mydb

# Open and use it
bun run src/cli.ts open mydb
bun run src/cli.ts exec "CREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT, body TEXT)"
bun run src/cli.ts insert notes '{"title":"Hello","body":"World"}'
bun run src/cli.ts select notes
```

## Gateway Setup

```bash
# Clone and configure
cp .env.example .env
# Edit .env with your Telegram credentials and secrets

# Start the stack
docker compose up --build -d

# Verify
curl http://localhost:8000/health

# Register a user
curl -X POST http://localhost:8000/v1/auth/register
```

## Connecting Client to Gateway

1. Register via the gateway to get an API key
2. Configure the client:

```bash
bun run src/cli.ts config set sync.gateway_url http://your-gateway:8000/v1
bun run src/cli.ts config set sync.api_key YOUR_API_KEY
```

3. Push to cloud:

```bash
bun run src/cli.ts sync
```

## Production Deployment

See [configuration.md](configuration.md) for all options.

```bash
cp .env.production .env
# Edit .env with production secrets

docker compose up --build -d

# With TLS (first time)
docker compose run certbot certonly --webroot \
  --webroot-path=/var/www/certbot \
  -d your-domain.com \
  --email you@your-domain.com \
  --agree-tos \
  --no-eff-email
```
