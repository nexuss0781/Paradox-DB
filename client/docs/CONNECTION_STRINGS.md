# Connection Strings

## Canonical `DATABASE_URL`

After a successful project/database provisioning flow, Parad persists the resolved connection string as `database_url` in `~/.paradox/config.json`. The same value can be supplied through the `DATABASE_URL` environment variable and consumed by `connect()` with no additional connection arguments:

```ts
const db = await connect(process.env.DATABASE_URL!);
```

The CLI emits a redacted `DATABASE_URL` after successful `init` or `connect`. Use `--print-database-url` only when intentionally copying the complete secret-bearing URL into a secret manager:

```text
parad init mydb --project myproject --print-database-url
```

An explicit `url`, `name`, or `dbPath` argument takes precedence over an ambient `DATABASE_URL`. Existing `parad://`, `paradox://`, local-only, email/password, token, and config-based workflows remain supported.

`parad` uses postgres-style connection strings to express everything needed to
open a database in one line: *where it lives locally, who you are, and where to
sync it*.

```
parad://[userinfo@]local[/<project>/]<database>[?query]
```

| Part | Meaning |
| --- | --- |
| `parad://` | Scheme. `parad:` and `paradox:` are both accepted. |
| `userinfo` | Optional credentials (see below). |
| `local/` | Fixed literal — marks the path as a database reference. |
| `<project>` | Optional project (folder) on the gateway. |
| `<database>` | **Required.** Database name. |
| `query` | Optional `passphrase`, `gateway`, and `token` parameters. |

---

## Examples

```text
# Simplest — local encrypted DB, default gateway + default passphrase
parad://local/todos

# Project-scoped (auto-provisioned on the gateway)
parad://local/acme/inventory

# Encrypted with a passphrase
parad://local/todos?passphrase=hunter2

# Authenticate with a bearer token
parad://TOKEN@local/todos

# Authenticate with email + password (auto-login, token persisted to config)
parad://me@example.com:secret@local/acme/todos

# Everything explicit
parad://me@example.com:secret@local/acme/todos?passphrase=hunter2&gateway=https%3A%2F%2Fparadox-db.onrender.com%2Fv1
```

---

## Authentication

Credentials can come from three places, in priority order (all read in
`connect`):

1. `options.apiKey` (explicit argument)
2. the URL:
   - **token** — `token@` userinfo, or a `?token=` query parameter;
   - **email:password** — `email:password@` userinfo (auto-login flow);
3. `config.sync.api_key` (persisted from a previous login).

### API key

```text
parad://TOKEN@local/todos
parad://local/todos?token=TOKEN
```

The key is sent as `X-API-Key: pk_...` on every gateway request.

### Email + password (auto-login)

```text
parad://me@example.com:secret@local/todos
```

When userinfo contains an `@` (i.e. it looks like an email) or a password,
`connect` calls `POST /auth/login`, uses the returned `api_key`, and
persists it into `config.json` so the next `connect` works without the
credentials. A gateway is required for this flow.

### Token persisted from login

After the first auto-login, even this works:

```ts
await connect('parad://local/todos'); // uses saved api_key + gateway
```

---

## Encryption passphrase

The passphrase comes from the URL's `?passphrase=` parameter, an explicit
`options.passphrase`, the `PARADOX_PASSPHRASE` environment variable, or — when
none are provided — the string `default`.

```text
parad://local/todos?passphrase=hunter2
```

> The passphrase only ever lives client-side. The gateway never sees it; it
> receives the fully encrypted bytes.

---

## Provisioning

If the URL includes a **project**, `connect` will:

1. `ensureProject(<project>)` — find or create the project;
2. `ensureDatabase(<projectId>, <name>)` — find or create the database;

then save the resolved `project_id`, `database_id`, gateway, and credentials to
`config.json`. This is idempotent — connecting again is a no-op.

---

## Programmatic helpers

### `parseUrl(url)`

```ts
import { parseUrl } from 'parad';

parseUrl('parad://me@example.com:secret@local/acme/todos?passphrase=hunter2');
// {
//   name: 'todos',
//   project: 'acme',
//   passphrase: 'hunter2',
//   gateway_url: '',
//   token: '',
//   email: 'me@example.com',
//   password: 'secret',
// }
```

Throws if the scheme is unsupported or the database name is missing.

### `generateUrl(...)`

```ts
import { generateUrl } from 'parad';

const url = generateUrl(
  'todos',            // name
  'hunter2',          // passphrase
  'https://paradox-db.onrender.com/v1', // gateway
  'acme',             // project
  '',                 // token
  'me@example.com',   // email
  'secret',           // password
);
// parad://me@example.com:secret@local/acme/todos?passphrase=hunter2&gateway=…
```

Signature:

```ts
generateUrl(
  name: string,
  passphrase?: string,
  gatewayUrl?: string,
  project?: string | null,
  token?: string,
  email?: string,
  password?: string,
): string
```

When both `email` and `password` are supplied they are encoded as userinfo; a
bare `token` is encoded as userinfo as well.

### `dbStateKey(name, project?)`

```ts
import { dbStateKey } from 'parad';

dbStateKey('todos');          // 'todos'
dbStateKey('todos', 'acme');  // 'acme/todos'
```

This is the stable key used for per-database sync state.
