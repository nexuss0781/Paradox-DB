# Paradox-DB 2.2.3

This synchronized patch release publishes the TypeScript npm SDK and Python PyPI SDK with the canonical single-URL workflow and ORM integrations.

## TypeScript SDK

The npm package includes the async Drizzle SQLite adapter at both `parad` and `parad/drizzle`. It supports typed SQLite schemas, CRUD, `returning()`, prepared queries, transactions, nested savepoints, batches, raw SQL, and Parad lifecycle methods such as `close()`, `push()`, and `pull()`.

## Python SDK

The PyPI package includes the optional `parad[sqlalchemy]` integration. It provides a PEP 249 DB-API connection and the registered `parad://` SQLAlchemy dialect for Core and ORM workflows, transactions, encrypted disposal, and the shared Parad connection URL.

## Shared connection workflow

Both SDKs consume the complete URL returned after successful Parad CLI authentication and project/database creation:

```text
DATABASE_URL=<complete Parad connection URL>
```

The URL remains the single deployment connection value and carries the project, database, gateway, authentication, and encryption configuration required by Parad.

## Verification

The release was prepared from the verified `main` branch. Package artifacts must pass the TypeScript test/typecheck/build workflow, Python SDK tests and compilation, npm package inspection, and PyPI wheel/sdist checks before publication.
