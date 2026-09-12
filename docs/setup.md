# Paradox-DB Setup Guide

## Prerequisites

- **Client:** Bun >= 1.0 or Node.js >= 18 (SQLite via `sql.js` WASM — no native bindings, no build step)
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

# Configure Nexuss Auth routing (Google stays enabled in the Nexuss project)
export NEXUSS_AUTH_URL="https://your-nexuss-auth.example"
export NEXUSS_AUTH_PROJECT_ID="your-paradox-project-id"
```

## Connecting Client to Gateway

1. Sign in with Google through the configured Paradox/Nexuss web flow, create a Nexuss project token, and exchange it for a Paradox key:
2. Configure the client:

```bash
parad auth login --api-key nxa_...
bun run src/cli.ts config set sync.gateway_url http://your-gateway:8000/v1
bun run src/cli.ts config set sync.api_key pk_...
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
