# Paradox-DB 2.2.4

## Canonical database_url-first workflow

Version 2.2.4 makes the canonical `database_url` the primary deployment and CLI workflow for both the TypeScript and Python SDKs. New applications should set one `DATABASE_URL` secret and connect with that value rather than assembling gateway, project, database, passphrase, and API-key settings separately.

## New CLI commands

Both SDK CLIs now provide matching commands:

```bash
parad url
parad url --print-database-url
parad database-url --print-database-url
```

Normal output is redacted. Full output requires the explicit `--print-database-url` opt-in and should only be copied into a secret manager. Retrieval checks the ambient `DATABASE_URL`, then persisted `config.database_url`, then reconstructs and persists the canonical URL from legacy split configuration when a passphrase is available.

## Safety and compatibility

The `init` workflow still provisions new databases and persists the canonical URL. The `url` command is non-mutating with respect to the remote database and does not create or overwrite a remote snapshot. Existing `parad://` and `paradox://` URLs, explicit connection options, split configuration fields, and legacy `get_connection_url` behavior remain supported.

`config show` and sensitive `config set` output now redact connection URLs, API keys, passphrases, passwords, and tokens.

## Package versions

| SDK | Version |
| --- | --- |
| TypeScript / npm | `2.2.4` |
| Python / PyPI | `2.2.4` |

The release must be tested and built separately for both SDKs before publication.
