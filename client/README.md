# parad

**Paradox-DB TypeScript SDK.** A zero-config encrypted SQLite database with
cloud sync — the client half of the [Paradox-DB](https://github.com/nexuss0781/Paradox-DB)
platform.

- **Encrypted at rest** — every database file on disk is a single AES-256-CBC
  ciphertext blob. The SQLite database only ever exists decrypted in process
  memory (an in-memory `sql.js` database).
- **Byte-compatible with Python** — the `parad` SDK on PyPI uses the same KDF,
  salt, and padding, so databases move freely between languages.
- **Sync by default** — connect once and local writes are pushed to the gateway
  and remote changes pulled back automatically, with conflict resolution you
  don't have to think about.
- **Offline-first** — when the gateway is unreachable, changes are marked dirty
  and flushed automatically when connectivity returns.
- **Manual control when you want it** — `push()`, `pull()`, `pullVersion()`
  and a full CLI are available for explicit workflows.

```bash
npm install parad
```

```ts
import { connect } from 'parad';

// Create/open an encrypted database and start syncing (default gateway).
const db = await connect({ name: 'app' });

db.execute('CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, task TEXT)');
db.insert('todos', { task: 'ship parad' });

// The daemon pushes changes automatically. Local writes win on conflict.
await db.close();
```

Connect to a specific database on the cloud instead:

```ts
// Connection string: project-scoped, API-key authenticated, auto-provisioned.
const db = await connect('parad://pk_example@local/acme/todos?passphrase=hunter2');
```

## Documentation

| Guide | Contents |
| --- | --- |
| [Sync workflows](docs/SYNC.md) | Default auto-sync, offline handling, conflict rules, optional manual push/pull |
| [Connection strings](docs/CONNECTION_STRINGS.md) | URL format, API keys, Nexuss exchange, provisioning |
| [API reference](docs/API.md) | `connect`, `ParadConnection`, `SyncDaemon`, `ClientEngine`, `GatewayClient`, config & state |
| [Encryption](docs/ENCRYPTION.md) | AES-256-CBC, PBKDF2 parameters, file format |
| [CLI](docs/CLI.md) | `parad` command-line reference |
| [Configuration](docs/CONFIGURATION.md) | `config.json`, env vars, sync state files |
| [Errors](docs/ERRORS.md) | Every error class and when it is thrown |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common problems and fixes |

## Requirements

- Node.js **>= 18** (SQLite runs on **`sql.js`** — WASM, so **no native
  dependencies**; installs cleanly on any platform)

## Quickstart

```ts
import { connect } from 'parad';

const db = await connect({ name: 'notes' });

// Schema + data
db.execute(`CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  body TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
)`);
const id = db.insert('notes', { body: 'Hello, world' });

// Query
const rows = db.select('notes', { id }, { orderBy: 'created_at DESC' });
console.log(rows);

// Custom SQL
const result = db.execute('SELECT COUNT(*) AS n FROM notes');
console.log(result.rows[0].n);

// Manual sync (optional — the daemon does this automatically)
const version = await db.push();      // push local → gateway
const pulled = await db.pull();       // pull gateway → local
await db.close();
```

> **Note on `commit()` / `rollback()`:** each statement auto-commits, so these
> methods exist only for API parity with the Python SDK and are no-ops. Use
> SQLite transactions via `execute('BEGIN')` / `execute('COMMIT')` if you need
> atomicity.

## License

MIT
