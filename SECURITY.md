# Paradox-DB Security Guide

## Authentication

The gateway provides `POST /v1/auth/register` and `POST /v1/auth/login`. Passwords must be at least 12 characters and no more than 72 UTF-8 bytes because bcrypt is used for password hashing. The gateway stores password hashes, never plaintext passwords.

Authenticated API requests use the `X-API-Key` header. `Authorization: Bearer` is intentionally not accepted. Registration, login, and explicit key creation return the plaintext key once; the gateway stores only its SHA-256 hash.

## API-key lifecycle

Each user may create multiple named API keys. Keys can have an optional expiration time and can be listed or revoked. Logging in or creating a new key does not invalidate existing keys; revoke keys explicitly when a device or integration is no longer trusted.

```bash
curl -X POST "$PARADOX_GATEWAY/v1/auth/api-keys" \
  -H "X-API-Key: $PARADOX_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"laptop","expires_at":"2027-01-01T00:00:00Z"}'
```

Never commit an API key, password, or passphrase. Store the key in `PARADOX_API_KEY`, a process secret, or an OS keychain. Treat a key returned by registration or creation as a secret because it cannot be recovered from the gateway after it is lost.

## Passphrases

The local SDK exports `generatePassphrase()` for explicit setup. If `connect()` creates a new database and no passphrase is configured, it generates a random 256-bit passphrase and saves it to the local configuration. The passphrase is not sent to the gateway and is not recoverable by Paradox-DB.

For an existing database, the SDK now requires an explicit passphrase. Use `allowLegacyDefault: true` only when opening a legacy database that is known to have been encrypted with the literal passphrase `default`.

Prefer environment variables or a secret manager:

```bash
export PARADOX_PASSPHRASE='use-a-secret-manager-in-production'
export PARADOX_API_KEY='pk_...'
```

Connection URLs containing `email:password`, `token`, or `passphrase` are convenient for local experiments but unsafe for production. URLs may be recorded in shell history, process listings, logs, crash reports, and telemetry.

## Deployment checklist

- Run Alembic migrations, including `004_api_keys`.
- Use HTTPS for every gateway URL.
- Configure a shared rate-limit store when running multiple gateway instances; the built-in limiter is process-local.
- Configure PostgreSQL backups and test restoration.
- Add email verification, password reset, MFA, and account-deletion workflows before exposing registration to the public internet.
- Audit every project, database, version, backup, download, restore, and rollback route for `user_id` scoping.
