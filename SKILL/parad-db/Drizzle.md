# Parad Drizzle AI Guide

This guide is the operational source of truth for AI agents working on Parad’s TypeScript Drizzle integration. Read this file before inspecting, designing, editing, testing, documenting, or reporting any Drizzle-related change.

The integration is a Drizzle SQLite adapter over Parad’s encrypted local SQLite engine. Drizzle owns schema declarations, SQL generation, typed query builders, relational queries, prepared queries, transactions, and batches. Parad owns authentication, project/database provisioning, canonical `DATABASE_URL` resolution, encryption, journaling, local persistence, auto-sync, manual sync, conflict handling, and shutdown.

## AI execution rule

When the user mentions Drizzle, `drizzle-orm`, `parad/drizzle`, a Drizzle schema, Drizzle queries, Drizzle migrations, or TypeScript ORM support, execute this sequence:

1. Read this `Drizzle.md` file.
2. Read `client/docs/DRIZZLE.md` for implementation examples.
3. Inspect `client/src/drizzle.ts`, `client/src/engine.ts`, `client/src/index.ts`, `client/package.json`, and the relevant tests before changing code.
4. Preserve the canonical `DATABASE_URL` workflow.
5. Preserve the existing Parad engine, encryption, journal, sync daemon, and CLI behavior.
6. Implement the smallest change that satisfies the request.
7. Add or update a focused test before changing unrelated code.
8. Run the complete client test suite, typecheck, build, and diff validation.
9. Report exact files, API behavior, tests, commit, and push status.

Do not create a second database adapter, a MySQL adapter, a direct SQLite file path workaround, or a separate Drizzle connection-string format. The supported route is `parad/drizzle` over the canonical Parad URL.

## Architecture map

| Layer | Responsibility | AI change rule |
|---|---|---|
| `client/src/connection.ts` | Opens Parad, resolves URL/config, provisions project/database, starts sync | Reuse it; do not bypass it. |
| `client/src/engine.ts` | Opens encrypted SQLite, journals writes, executes SQL, exports snapshots | Extend only when Drizzle needs a precise SQLite result shape. |
| `client/src/drizzle.ts` | Converts Parad into Drizzle’s async SQLite proxy database | Keep all Drizzle-specific behavior here. |
| `client/src/index.ts` | Root public exports | Export public adapter types and factory here. |
| `client/package.json` | Peer dependency, package exports, build metadata | Keep `drizzle-orm` as a peer dependency and preserve `parad/drizzle`. |
| `client/tests/drizzle.test.ts` | End-to-end Drizzle behavior | Add regression coverage here for every adapter change. |
| `client/docs/DRIZZLE.md` | User-facing implementation reference | Update when public behavior changes. |

## Canonical setup workflow

Always provision Parad before connecting Drizzle. The successful CLI flow is the source of the single deployment URL.

### Authenticate

Use the configured API key when one exists. If no key exists, authenticate through the CLI:

```bash
parad auth register
parad auth login
```

Gateway authentication uses `X-API-Key`. Do not send a Parad API key as `Authorization: Bearer`.

### Create or resolve the project and database

```bash
parad init <database-name> --project <project-name>
```

This authenticates, resolves the project, resolves the database, creates the encrypted local database, and pushes the initial state. Only a successful completion produces a usable deployment connection.

### Capture the connection URL

```bash
parad init <database-name> --project <project-name> --print-database-url
```

Store the complete output as one secret:

```text
DATABASE_URL=<complete Parad URL>
```

The URL contains the project, database, gateway, API-key token, and encryption passphrase required by the SDK. Never log the unredacted value. Use redacted output for normal logs.

## Installation and import

Install both packages:

```bash
npm install parad drizzle-orm
```

Use either public import:

```ts
import { drizzle } from 'parad';
```

```ts
import { drizzle } from 'parad/drizzle';
```

The implementation uses Drizzle’s async SQLite proxy API. Therefore the factory is awaited:

```ts
const db = await drizzle(process.env.DATABASE_URL!);
```

Never write `const db = drizzle(...)` and immediately execute queries without awaiting the factory.

## Factory API

```ts
export async function drizzle<TSchema extends Record<string, unknown> = Record<string, never>>(
  source?: ParadDrizzleSource,
  config?: DrizzleConfig<TSchema>,
): Promise<ParadDrizzleDatabase<TSchema>>
```

`ParadDrizzleSource` accepts the following values:

| Source | Use |
|---|---|
| `string` | Canonical `parad://...` URL or `DATABASE_URL` value. |
| `ConnectOptions` | Explicit Parad options such as `name`, `project`, `passphrase`, `apiKey`, `gatewayUrl`, and `autoSync`. |
| `ParadConnection` | An already opened Parad connection. |
| `undefined` | Implicit `DATABASE_URL`, persisted `database_url`, or configured defaults. |

Standard usage:

```ts
import * as schema from './schema.js';

const db = await drizzle(process.env.DATABASE_URL!, {
  schema,
});
```

Local test usage:

```ts
const db = await drizzle({
  dbPath: '/tmp/example.db',
  passphrase: 'test-passphrase',
  autoSync: false,
});
```

Use `autoSync: false` for deterministic tests, migrations, serverless handlers, and one-shot jobs. Use the default auto-sync behavior for normal CLI and desktop applications.

## Schema API

Define tables with Drizzle’s SQLite schema builders:

```ts
import { integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const users = sqliteTable('users', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  email: text('email').notNull().unique(),
  name: text('name'),
  active: integer('active', { mode: 'boolean' }).notNull().default(true),
});
```

Pass the schema to the factory:

```ts
import * as schema from './schema.js';

const db = await drizzle(process.env.DATABASE_URL!, { schema });
```

Use the schema as the source of truth for typed application queries. Do not duplicate table definitions in raw SQL and TypeScript unless the task explicitly requires a migration or bootstrap statement.

## Query API

Import operators from `drizzle-orm` and use the typed database object.

### Select

```ts
import { and, eq, gt } from 'drizzle-orm';

const rows = await db
  .select()
  .from(users)
  .where(and(eq(users.active, true), gt(users.id, 0)))
  .orderBy(users.name)
  .limit(25);
```

Available selection builders include `select`, `selectDistinct`, `from`, `where`, `orderBy`, `groupBy`, `having`, `limit`, `offset`, joins, aliases, common table expressions, set operations, and SQLite expressions.

### Insert

```ts
await db.insert(users).values({
  email: 'alice@example.com',
  name: 'Alice',
  active: true,
});
```

Multiple rows:

```ts
await db.insert(users).values([
  { email: 'alice@example.com', name: 'Alice', active: true },
  { email: 'bob@example.com', name: 'Bob', active: false },
]);
```

### Update

```ts
await db
  .update(users)
  .set({ active: false })
  .where(eq(users.email, 'alice@example.com'));
```

### Delete

```ts
await db.delete(users).where(eq(users.email, 'alice@example.com'));
```

### Upsert

```ts
await db
  .insert(users)
  .values({ email: 'alice@example.com', name: 'Alice', active: true })
  .onConflictDoUpdate({
    target: users.email,
    set: { name: 'Alice Updated' },
  });
```

### Returning rows

SQLite `RETURNING` is supported through the Parad engine bridge:

```ts
const inserted = await db
  .insert(users)
  .values({ email: 'carol@example.com', name: 'Carol' })
  .returning();

const updated = await db
  .update(users)
  .set({ active: true })
  .where(eq(users.email, 'carol@example.com'))
  .returning();
```

When a test fails around `returning()`, inspect both the SQL statement and the positional-row mapping in `ClientEngine.executeRawValues()` and `client/src/drizzle.ts`.

## Relational query API

When the schema contains Drizzle relations, use the relational API:

```ts
const result = await db.query.users.findMany({
  where: (user, operators) => operators.eq(user.active, true),
  with: {
    posts: true,
  },
});
```

The factory must receive the schema object for `db.query` to exist.

## Raw SQL API

Use Drizzle’s `sql` tag for parameterized raw SQL:

```ts
import { sql } from 'drizzle-orm';

await db.run(sql`
  CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    message TEXT NOT NULL
  )
`);
```

Read all rows:

```ts
const rows = await db.all(sql`SELECT id, message FROM audit_log`);
```

Read one row:

```ts
const row = await db.get(sql`SELECT id, message FROM audit_log LIMIT 1`);
```

Read positional values:

```ts
const values = await db.values(sql`SELECT message FROM audit_log`);
```

Raw queries without schema field metadata return positional arrays. Typed schema queries return mapped objects. Never interpolate untrusted values into identifiers or SQL text.

## Prepared queries

Use Drizzle placeholders and `.prepare()` for repeated queries:

```ts
import { eq, sql } from 'drizzle-orm';

const byId = db
  .select()
  .from(users)
  .where(eq(users.id, sql.placeholder('id')))
  .prepare();

const user = await byId.execute({ id: 1 });
```

The adapter routes Drizzle’s `run`, `all`, `get`, `values`, and `execute` methods through Parad’s SQL engine.

## Transaction API

Use async transaction callbacks:

```ts
await db.transaction(async (tx) => {
  await tx.insert(users).values({
    email: 'transaction@example.com',
    name: 'Transaction User',
  });
});
```

The adapter emits journaled SQLite transaction statements:

```text
BEGIN
application statements
COMMIT
```

An exception causes `ROLLBACK` and is rethrown to the caller.

Nested transactions use savepoints:

```ts
await db.transaction(async (tx) => {
  await tx.insert(users).values({ email: 'outer@example.com' });

  await tx.transaction(async (nested) => {
    await nested.insert(users).values({ email: 'nested@example.com' });
  });
});
```

When changing transaction behavior, add tests for commit, rollback, nested savepoint commit, nested savepoint rollback, and encrypted reopen.

## Batch API

Use the Drizzle SQLite proxy batch method for grouped statements:

```ts
const results = await db.batch([
  db.insert(users).values({ email: 'a@example.com' }),
  db.insert(users).values({ email: 'b@example.com' }),
]);
```

Batch execution uses the same Parad callback as individual execution. Keep result order stable and test every changed result shape.

## Parad lifecycle API

The returned database includes Parad-specific methods:

```ts
await db.push();
await db.pull();
db.close();
```

| Method | Result | AI action |
|---|---|---|
| `db.push()` | `Promise<number \| null>` | Upload the local snapshot or return `null` without a gateway. |
| `db.pull()` | `Promise<boolean>` | Replace local state when a newer remote snapshot exists. |
| `db.close()` | `void` | Stop sync and re-encrypt the local database before exit. |
| `db.$client` | `ParadConnection` | Access the underlying Parad connection when sync/config inspection is required. |

Call `db.close()` in tests and one-shot programs. In serverless code, use `autoSync: false`, perform explicit `push()` and `pull()` calls, and close the connection at the lifecycle boundary.

## Error diagnosis

Use this decision table when a Drizzle task fails:

| Symptom | Inspect | Correct action |
|---|---|---|
| `DATABASE_URL` missing | Environment and persisted config | Provision with CLI and set the complete URL. |
| `DecryptionError` | URL passphrase and local database | Use the original passphrase; do not generate a replacement for an existing database. |
| `GatewayError 401` | API key and gateway | Re-authenticate or rotate the key; keep `X-API-Key`. |
| SQL syntax error | Generated SQLite SQL | Reproduce with `db.run(sql.raw(...))` and fix SQLite syntax. |
| Typed result fields are undefined | Positional result mapping | Inspect `executeRawValues()` and field mapping; do not convert rows to objects in the proxy callback. |
| `returning()` fails | DML row execution | Verify the statement is journaled once and returned through positional rows. |
| Transaction does not roll back | Engine transaction statements | Test `BEGIN`, `ROLLBACK`, and savepoint paths directly. |
| Database disappears after test | Missing close | Call `db.close()` before deleting the temporary directory. |
| Sync changes unexpectedly | Auto-sync and daemon state | Use `autoSync: false` for deterministic tests and explicit sync. |

## Test workflow for AI changes

For every Drizzle code change:

```bash
cd /home/ubuntu/Paradox-DB/client
npm test
npm run typecheck
npm run build
npm run lint
```

At minimum, run the focused adapter test:

```bash
npm test -- --run tests/drizzle.test.ts
```

The adapter test must cover schema creation, typed CRUD, `returning()`, transactions, nested savepoints, close/reopen encryption, and any changed batch or result behavior.

Use `git diff --check` before committing. Keep generated build output out of the source commit unless the package contract explicitly requires it.

## Implementation rules for AI

When editing the adapter, follow these rules:

1. Keep the public factory async because Parad initialization is async.
2. Keep the adapter on Drizzle’s SQLite dialect; do not introduce a MySQL dialect.
3. Keep `drizzle-orm` as a peer dependency.
4. Route SQL through Parad’s engine so encryption, journaling, and local persistence remain authoritative.
5. Return positional rows to Drizzle’s proxy callback; Drizzle performs field-aware object mapping.
6. Execute every write exactly once.
7. Preserve `RETURNING` rows.
8. Preserve transaction and savepoint SQL.
9. Keep `DATABASE_URL` as the single deployment connection value.
10. Keep `db.close()`, `db.push()`, and `db.pull()` available on the returned object.
11. Add focused tests before broad refactors.
12. Update `client/docs/DRIZZLE.md` when public usage changes.
13. Update this guide only when the AI workflow or supported API contract changes.
14. Commit and push after verification, then report the commit and exact test results.

## Delivery report format

When the task is complete, report:

- The commit hash and branch.
- Files changed and the purpose of each file.
- New or changed public APIs.
- `DATABASE_URL` behavior.
- Tests, typecheck, build, and lint results.
- Any environment-dependent verification that could not run.
- Whether the working tree is clean.

## References

[1]: ../../client/docs/DRIZZLE.md "Parad TypeScript Drizzle implementation guide"
[2]: https://orm.drizzle.team/docs/overview "Drizzle ORM documentation"
[3]: https://orm.drizzle.team/docs/transactions "Drizzle transaction documentation"
