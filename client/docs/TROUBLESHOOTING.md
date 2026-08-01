# Troubleshooting

Common problems and their fixes.

## `DecryptionError: Invalid passphrase or corrupt database file`

Opening failed.

- Confirm the **passphrase** matches the one used to create the database
  (`options.passphrase`, `?passphrase=`, `PARADOX_PASSPHRASE`, or the default
  `default`).
- Confirm `dbPath` points at a real `parad`-encrypted file — a raw SQLite file
  or a random file will not decrypt.
- If you changed `PARADOX_HOME`, the config and state moved too; the database
  path may now resolve elsewhere. Check `parad config show`.

## `SQLiteError` from `execute` / `insert` / `select`

The SQL statement itself failed.

- Wrap it to see the underlying message: `err.message` and `err.originalError`.
- Remember `commit()` / `rollback()` are no-ops; use
  `execute('BEGIN') ... execute('COMMIT')` for transactions.
- `select()` interpolates **column names** from object keys directly into SQL —
  never pass untrusted identifiers there.

## Sync never happens

- **No gateway configured.** `connect` only starts the daemon when a gateway
  resolves (`options.gatewayUrl`, URL `?gateway=`, or `config.sync.gateway_url`).
  Check `db.daemon` — if it's `null`, no daemon is running.
- **No changes detected.** The daemon pushes only when the local hash changed
  from the last push. If nothing differs, nothing uploads (by design).
- **Everything works but never calls `close()`.** Sync is real-time, but the
  encrypted file on disk is only rewritten on `close()`. Ensure you close, or
  rely on `getRawBytes()`/daemon for sync.
- If using `autoSync: false`, call `push()` / `pull()` manually.

## `push()` returns `null`

No gateway is configured for the connection. Pass `gatewayUrl`/`apiKey`, or
connect with a connection string that includes them.

## Changes appear dirty/offline and won't flush

The daemon marks a database `dirty`/`offline` on connectivity failures and
retries on every push tick. To recover:

- Confirm the gateway is reachable: `curl https://paradox-db.onrender.com/test`.
- Check `db.daemon?.lastError` for the specific failure.
- Fix the network/auth issue; the next successful push clears the flags
  automatically. Local writes are never lost.

## `GatewayError 401`

The bearer token is missing/expired.

- Reconnect with credentials: `connect('parad://user@example.com:pass@local/<db>')`
  (auto-login refreshes the token) or set `apiKey`.
- Run `parad connect <url>` to refresh `config.sync.api_key`.

## `GatewayError 409` during manual calls

A version conflict — someone else pushed first. `push()` resolves this
automatically with local-wins, but if you see it, your **remote and local base
versions diverged**. `await db.push()` handles it; alternatively
`await db.pull()` then re-push deliberately.

## `GatewayError 5xx` or status `0` (transport)

Treats as offline (by design). Retry later; cold-start timeouts are handled with
a 30s timeout in `GatewayClient`.

## Files are huge or sync slow

The gateway stores **full-file versions** (`version_type: 'full'`), so every
push sends the entire encrypted database. Keep files within
`sync.max_file_size_mb` (default 50 MB) and tune push cadence if needed.

## Tests need a gateway

The SDK's unit tests run against mocks and need no network. The gateway has a
self-check endpoint used to verify the whole pipeline:

```bash
curl https://paradox-db.onrender.com/test
```

## Where is my data / state stored?

| Thing | Location |
| --- | --- |
| Config | `$PARADOX_HOME` or `~/.paradox/config.json` |
| Sync state | `<configDir>/<dbKey>.sync.json` |
| Database | `dbPath` (default `~/.paradox/data.db`, or `<configDir>/<name>.db`) |
| Logs | `~/.paradox/logs/` |

Move everything consistently by setting `PARADOX_HOME` before any `connect`.
