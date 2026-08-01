# Configuration

`parad` is zero-config by default but fully configurable. Configuration lives
in two places: a single `config.json` and per-database sync state files.

## Config directory

```
PARADOX_HOME (env)         if set, used directly
~/.paradox                  default (i.e. $HOME/.paradox)
```

```ts
import { configDir, getDefaultConfigPath } from 'parad';

configDir();              // /home/you/.paradox  (or $PARADOX_HOME)
getDefaultConfigPath();   // /home/you/.paradox/config.json
```

## `config.json`

Read with `loadConfig()` and written with `saveConfig()`. `loadConfig`
**deep-merges** the file over the built-in defaults, and `~` in string values is
expanded to the home directory.

### `ClientConfig`

```ts
interface ClientConfig {
  database_path: string;         // default: ~/.paradox/data.db
  project_id: string;            // resolved via provisioning
  project_name: string;
  database_id: string;
  encryption: {
    cipher: string;              // 'aes-256-cbc'
    kdf_iterations: number;      // 256000
    page_size: number;           // 4096
  };
  sync: {
    gateway_url: string;             // https://paradox-db.onrender.com/v1
    api_key: string;                 // bearer token
    trigger_timer_seconds: number;   // 30
    trigger_ops_threshold: number;   // 50
    max_file_size_mb: number;        // 50
    auto_sync_on_shutdown: boolean;  // true
  };
  conflict: {
    strategy: 'last-write-wins' | 'first-write-wins' | 'manual';  // 'last-write-wins'
    log_conflicts: boolean;      // true
  };
  logging: {
    level: 'debug' | 'info' | 'warn' | 'error';  // 'info'
    path: string;                // ~/.paradox/logs
  };
}
```

> **Note:** the SDK's runtime daemon cadence is configured per-connection via
> `pushIntervalMs` / `pullIntervalMs`, and conflict resolution is hard-wired to
> **local-wins** for both automatic and manual pushes. The `conflict` /
> `trigger_*` fields above are stored for gateway/operational use and
> cross-SDK parity.

### CLI access

```bash
parad config show
parad config set sync.gateway_url https://paradox-db.onrender.com/v1
parad config set sync.api_key <token>
```

`config set` supports dotted paths into the object (e.g. `logging.level debug`).

## Environment variables

| Variable | Effect |
| --- | --- |
| `PARADOX_HOME` | Overrides the config/state directory. |
| `PARADOX_PASSPHRASE` | Fallback encryption passphrase when none is provided. |

`connect` also writes resolved values back to `config.json` (database path,
project/database ids, gateway URL, api key) — non-fatally — so a one-time
`connect` makes later connects and CLI commands work automatically.

## Sync state files

For every database `parad` tracks sync state in
`<configDir>/<dbKey>.sync.json` where `<dbKey>` is the connection key with
unsafe filename characters sanitized (`myproject/mydb` -> `myproject__mydb`).

```json
{
  "database_name": "todos",
  "remote_version": 4,
  "remote_hash": "...sha256 of the remote file...",
  "last_sync": "2026-08-01T12:00:00.000Z",
  "last_local_hash": "...sha256 of the last pushed local file...",
  "dirty": false,
  "offline": false
}
```

| Field | Meaning |
| --- | --- |
| `remote_version` | The gateway version your local file is based on. |
| `remote_hash` | SHA-256 of the last downloaded remote snapshot. |
| `last_sync` | ISO timestamp of the last successful sync. |
| `last_local_hash` | SHA-256 of the local file the last time it was pushed. |
| `dirty` | True while there are unsynced local changes. |
| `offline` | True while the gateway is unreachable. |

Programmatic access via the `state` namespace:

```ts
import { state } from 'parad';

state.getSyncStatus('todos');
state.isDirty('todos');      state.markDirty('todos');     state.clearDirty('todos');
state.isOffline('todos');    state.setOffline('todos', true);
state.getRemoteVersion('todos');
```

## Layout summary

```
$PARADOX_HOME or ~/.paradox/
├── config.json              # client config (merged over defaults)
├── <dbKey>.sync.json        # per-database sync state
└── logs/                    # logging output (default)
```
