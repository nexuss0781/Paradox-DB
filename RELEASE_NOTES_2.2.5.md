# Paradox-DB 2.2.5

## Server-persisted canonical `database_url`

Version 2.2.5 completes the canonical connection URL feature across the gateway and both SDKs. For project-scoped connections, the canonical `database_url` is registered on the gateway and stored in the `paradox_dbs.database_url_encrypted` column using Fernet authenticated encryption. The gateway never includes the plaintext URL in ordinary database, project, or status responses.

The gateway exposes owner-authenticated endpoints for redacted metadata, explicit registration, and explicit reveal. Reveal is limited by the existing authenticated owner filter (`database_id` and `user_id`) and is a deliberate action rather than a side effect of listing or syncing.

The gateway deployment must set a stable `DATABASE_URL_ENCRYPTION_KEY` and preserve it across restarts and redeployments. If the key is changed, previously stored URLs cannot be decrypted. A non-placeholder `JWT_SECRET` can provide a compatibility fallback, but a dedicated Fernet key is recommended.

## SDK and CLI behavior

The TypeScript and Python SDKs now use the same recovery order: ambient `DATABASE_URL`, persisted local `database_url`, owner-authenticated server reveal, and only then legacy local reconstruction. Successful remote recovery is saved to the local configuration. Normal CLI output is redacted; the full secret-bearing value requires the explicit `--print-database-url` option.

Both CLIs provide an explicit migration command for databases created before server URL storage existed:

```bash
parad url register 'parad://...'
parad url --print-database-url
parad database-url --print-database-url
```

Registration validates the Parad URL and updates only the encrypted URL field. It does not call `init`, create a database, push a snapshot, pull a snapshot, or overwrite existing data. The corresponding public SDK helpers and gateway client methods are available in both languages.

## Important migration limitation

A database record created before this feature cannot reveal a historical canonical URL that was never stored. Server metadata and encrypted snapshots do not mathematically recover an unknown passphrase or provider URL. An owner must provide the original canonical URL once through the explicit registration command, after which future owner-authenticated recovery works. Do not run `init` merely to recover a URL from an existing database.

## Verification

The release includes gateway endpoint tests for encryption, redaction, invalid schemes, missing records, and owner isolation; TypeScript tests for remote reveal, old-gateway fallback, and registration; and Python tests for remote reveal and registration. The TypeScript SDK was built, typechecked, and tested; the Python SDK and gateway focused suites passed in the release workspace.

## Package versions

| Package | Version |
| --- | --- |
| TypeScript / npm | `2.2.5` |
| Python / PyPI | `2.2.5` |
| Gateway service | `2.2.5`; deployment includes the schema migration and endpoint implementation |

The gateway’s `DATABASE_URL_ENCRYPTION_KEY` must be configured before deploying the server feature. No secret values, API keys, provider credentials, or live database URLs are included in this release note.

## OmniRoute status

The existing OmniRoute database can use this feature only after the gateway endpoint is deployed and its original canonical URL is explicitly registered. The prior local-only implementation did not store that URL on the server, so the current server record cannot reveal it yet. This release does not claim or print the old OmniRoute secret without that one-time owner-supplied registration step.
