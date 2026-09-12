import { drizzle as drizzleProxy } from 'drizzle-orm/sqlite-proxy';
import type { SqliteRemoteDatabase, AsyncBatchRemoteCallback, AsyncRemoteCallback } from 'drizzle-orm/sqlite-proxy';
import type { DrizzleConfig } from 'drizzle-orm/utils';
import { connect, type ConnectOptions, ParadConnection } from './connection.js';

export type ParadDrizzleDatabase<TSchema extends Record<string, unknown> = Record<string, never>> =
  SqliteRemoteDatabase<TSchema> & {
    /** The underlying Parad connection. */
    $client: ParadConnection;
    /** Close the encrypted database and stop auto-sync. */
    close(): void;
    /** Push the current encrypted snapshot to the gateway. */
    push(): Promise<number | null>;
    /** Pull the latest encrypted snapshot from the gateway. */
    pull(): Promise<boolean>;
  };

export type ParadDrizzleSource = string | ConnectOptions | ParadConnection | undefined;

/**
 * Create a Drizzle SQLite database backed by Parad's encrypted engine.
 *
 * Parad opens sql.js asynchronously, so the factory is intentionally async:
 *
 *   const db = await drizzle(process.env.DATABASE_URL, { schema });
 *
 * Drizzle query builders, relational queries, prepared statements, and async
 * transactions remain available on the returned database. The attached
 * `close`, `push`, and `pull` methods retain Parad's encrypted persistence and
 * synchronization lifecycle.
 */
export async function drizzle<
  TSchema extends Record<string, unknown> = Record<string, never>,
>(
  source?: ParadDrizzleSource,
  config?: DrizzleConfig<TSchema>,
): Promise<ParadDrizzleDatabase<TSchema>> {
  const connection =
    source instanceof ParadConnection
      ? source
      : await connect(typeof source === 'string' || source === undefined ? source ?? {} : source);

  const callback: AsyncRemoteCallback = async (sql, params, method) => {
    if (method === 'run') {
      connection.engine.execute(sql, params);
      return { rows: [] };
    }
    return { rows: connection.engine.executeRawValues(sql, params) };
  };

  const batchCallback: AsyncBatchRemoteCallback = async (batch) => {
    return Promise.all(batch.map(({ sql, params, method }) => callback(sql, params, method)));
  };

  const db = drizzleProxy(callback, batchCallback, config) as ParadDrizzleDatabase<TSchema>;
  db.$client = connection;
  db.close = () => connection.close();
  db.push = () => connection.push();
  db.pull = () => connection.pull();
  return db;
}

export type { DrizzleConfig };
