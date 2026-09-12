import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { eq, sql } from 'drizzle-orm';
import { integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';
import { drizzle } from '../src/drizzle.js';

const users = sqliteTable('users', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  name: text('name').notNull(),
  active: integer('active', { mode: 'boolean' }).notNull().default(true),
});

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe('Parad Drizzle adapter', () => {
  it('supports typed CRUD, returning, transactions, and encrypted close', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'parad-drizzle-'));
    tempDirs.push(dir);
    const db = await drizzle(
      { dbPath: join(dir, 'app.db'), passphrase: 'test-passphrase', autoSync: false },
      { schema: { users } },
    );

    await db.run(sql.raw(
      'CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)',
    ));

    await db.insert(users).values({ name: 'Alice' });
    const initial = await db.select().from(users);
    expect(initial).toEqual([{ id: 1, name: 'Alice', active: true }]);

    const inserted = await db.insert(users).values({ name: 'Bob', active: false }).returning();
    expect(inserted).toEqual([{ id: 2, name: 'Bob', active: false }]);

    await db.update(users).set({ active: true }).where(eq(users.name, 'Bob'));
    expect(await db.select().from(users).where(eq(users.name, 'Bob'))).toEqual([
      { id: 2, name: 'Bob', active: true },
    ]);

    await db.transaction(async (tx) => {
      await tx.insert(users).values({ name: 'Carol' });
      await tx.transaction(async (nested) => {
        await nested.insert(users).values({ name: 'Dave' });
      });
    });

    expect((await db.select().from(users)).map((row) => row.name)).toEqual(['Alice', 'Bob', 'Carol', 'Dave']);
    db.close();
  });

  it('reopens the encrypted database after Drizzle close', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'parad-drizzle-reopen-'));
    tempDirs.push(dir);
    const source = { dbPath: join(dir, 'app.db'), passphrase: 'test-passphrase', autoSync: false } as const;
    const first = await drizzle(source);
    await first.run(sql.raw('CREATE TABLE values_table (value TEXT NOT NULL)'));
    await first.run(sql`INSERT INTO values_table (value) VALUES (${'persisted'})`);
    first.close();

    const second = await drizzle(source);
    const rows = await second.all<{ value: string }>(sql`SELECT value FROM values_table`);
    expect(rows).toEqual([['persisted']]);
    second.close();
  });
});
