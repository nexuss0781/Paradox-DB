# Drizzle integration

Parad exposes an adapter over Drizzle’s async SQLite proxy interface. This keeps Drizzle’s schema, query-builder, relational-query, prepared-statement, batch, and transaction APIs while Parad remains responsible for encrypted persistence, journaling, sync, and conflict handling.

## Install

```bash
npm install parad drizzle-orm
```

`drizzle-orm` is a peer dependency. Parad targets Drizzle’s SQLite dialect and does not pretend to be a MySQL database.

## Connect with the canonical URL

After successful CLI authentication and project/database creation, store the complete returned URL as `DATABASE_URL`:

```ts
import { drizzle } from 'parad/drizzle';
import * as schema from './schema.js';

const db = await drizzle(process.env.DATABASE_URL!, { schema });
```

The factory also accepts a Parad URL string, `ConnectOptions`, or an existing `ParadConnection`. Because Parad initializes sql.js asynchronously, the factory must be awaited.

## Schema and queries

```ts
import { eq } from 'drizzle-orm';
import { integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const users = sqliteTable('users', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  email: text('email').notNull().unique(),
  name: text('name'),
});

await db.insert(users).values({ email: 'alice@example.com', name: 'Alice' });
const alice = await db.select().from(users).where(eq(users.email, 'alice@example.com'));
await db.update(users).set({ name: 'Alice Smith' }).where(eq(users.email, 'alice@example.com'));
await db.delete(users).where(eq(users.email, 'alice@example.com'));
```

Drizzle generates SQLite SQL. Values are parameterized by Drizzle; as with the native Parad API, dynamic identifiers must come only from trusted application code.

## Transactions and batches

Transactions are asynchronous and map to journaled SQLite `BEGIN`, `COMMIT`, and `ROLLBACK` statements. Nested transactions use SQLite savepoints.

```ts
await db.transaction(async (tx) => {
  await tx.insert(users).values({ email: 'a@example.com' });
  await tx.transaction(async (nested) => {
    await nested.insert(users).values({ email: 'b@example.com' });
  });
});
```

Drizzle batch operations are routed through Parad’s same execution callback. The Parad journal therefore records the individual SQL statements while the encrypted database and sync lifecycle remain unchanged.

## Lifecycle

The returned database exposes Parad-specific methods:

```ts
await db.push();
await db.pull();
db.close();
```

Always call `db.close()` before process exit. It stops auto-sync, flushes the encrypted snapshot, and leaves the database recoverable on the next open. For serverless handlers, prefer `autoSync: false`, call `push()` and `pull()` at explicit lifecycle boundaries, and close the connection when the handler is finished.

## Supported result behavior

Schema-backed Drizzle queries return typed objects. Raw SQL without schema field metadata follows Drizzle’s SQLite proxy behavior and returns positional arrays. DML with `returning()` is supported through Parad’s journal-preserving raw-value execution path.
