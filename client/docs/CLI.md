# CLI Reference

The `parad` package ships a command-line client. After installing globally (or
via `npx`) it becomes the `parad` command:

```bash
npm install -g parad
parad --help
```

Machine-readable output is available on every command with `--json`.

## Commands

| Command | Description |
| --- | --- |
| `init <name>` | Create a new encrypted database. |
| `connect <url>` | Connect via a `parad://` connection string. |
| `exec <sql>` | Execute raw SQL. |
| `insert <table> <json>` | Insert a row. |
| `select <table> [where]` | Query rows. |
| `update <table> <set> <where>` | Update rows. |
| `delete <table> <where>` | Delete rows. |
| `push` | Push local changes to the gateway. |
| `pull [version]` | Pull latest (or a specific version). |
| `sync` | Push, then pull. |
| `status` | Show sync status for your databases. |
| `versions` | List remote versions of the configured database. |
| `rollback <version>` | Roll the database back to a version. |
| `config show \| set <key> <value>` | Show or update configuration. |
| `shell` | Interactive SQL REPL. |
| `--help`, `-h` | Show help. |
| `--version`, `-v` | Show version. |

> The CLI resolves its connection from `config.json` (see
> [CONFIGURATION.md](CONFIGURATION.md)). Run `parad connect <url>` once to set
> it up, or configure it via `parad config set` / environment variables.

## Examples

```bash
# Create a database
parad init myapp

# Connect using a connection string (also writes config.json)
parad connect 'parad://me@example.com:secret@local/acme/myapp?passphrase=hunter2'

# Schema and data
parad exec 'CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, task TEXT)'
parad insert todos '{"task": "write docs"}'
parad select todos
parad update todos '{"task": "ship"}' '{"id": 1}'
parad delete todos '{"id": 1}'

# Manual sync
parad push
parad pull
parad pull 3            # restore version 3 locally
parad sync

# Inspect
parad status --json
parad versions
parad config show

# Server-side rollback (to version 2) then hydrate locally
parad rollback 2

# Interactive shell
parad shell
parad> SELECT * FROM todos;
parad> help
parad> exit
```

## `--json`

Append `--json` to any command for parseable output:

```bash
$ parad status --json
{
  "user_id": "…",
  "databases": [
    {
      "name": "myapp",
      "latest_version": 4,
      "latest_message_id": "…",
      "pending_changesets": 0,
      "last_sync_at": "…",
      "local_version": 4,
      "dirty": false,
      "offline": false
    }
  ]
}
```

## Notes

- `push` resolves to `{ "pushed": true, "version": 4 }`; `pushed` is `false`
  (version `null`) when no gateway is configured.
- `pull` / `pullVersion` resolve to `{ "pulled": true|false }`.
- `rollback` rolls the server-side database back to the given version and then
  pulls it locally, resolving to `{ "rolled_back_to": <v>, "success": true }`.
- The CLI's engine closes cleanly after every command, so every write is
  re-encrypted to disk before the process exits.
