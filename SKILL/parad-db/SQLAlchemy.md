# Parad SQLAlchemy AI Guide

This guide is the operational source of truth for AI agents working on Parad’s Python DB-API and SQLAlchemy integration. Read this file before inspecting, designing, editing, testing, documenting, or reporting any Python ORM or SQLAlchemy-related change.

Parad’s Python integration has two layers. `parad.dbapi` provides a PEP 249 connection and cursor over Parad’s encrypted SQLite engine. `parad.sqlalchemy` provides a SQLAlchemy dialect that uses the same DB-API connection. SQLAlchemy owns ORM models, SQL construction, sessions, result objects, and transaction orchestration. Parad owns authentication, project/database provisioning, canonical `DATABASE_URL`, encryption, local persistence, synchronization, conflict handling, and shutdown.

## AI execution rule

When the user mentions SQLAlchemy, `create_engine`, `Session`, ORM models, Python database compatibility, DB-API, `parad.dbapi`, or a `parad://` SQLAlchemy URL, execute this sequence:

1. Read this `SQLAlchemy.md` file.
2. Read `parad/docs/SQLALCHEMY.md` for implementation examples.
3. Inspect `parad/parad/dbapi.py`, `parad/parad/sqlalchemy.py`, `parad/parad/connection.py`, `parad/parad/engine.py`, `parad/pyproject.toml`, and relevant tests before changing code.
4. Preserve the canonical `DATABASE_URL` produced by the successful Parad CLI provisioning flow.
5. Preserve `parad.connect()`, the encrypted engine, local persistence, sync daemon, CLI behavior, and existing URL parsing.
6. Use the DB-API layer as the SQLAlchemy boundary; do not bypass it from the dialect.
7. Add a focused test before changing unrelated code.
8. Run the Python SDK tests, compile checks, SQLAlchemy entrypoint smoke test, and package build.
9. Report exact files, public APIs, tests, package status, commit, and push status.

Do not create a second Python URL format, a separate ORM-only database, a plain unencrypted SQLite path, or a direct gateway-to-SQLAlchemy shortcut. The supported route is:

```text
DATABASE_URL → parad:// SQLAlchemy dialect → Parad DB-API → ParadConnection → encrypted SQLite engine
```

## Architecture map

| Layer | Responsibility | AI change rule |
|---|---|---|
| `parad/parad/connection.py` | Resolves URL/config, opens Parad, provisions databases, manages sync | Reuse it; do not duplicate URL or provisioning logic. |
| `parad/parad/engine.py` | Opens temporary SQLite, encrypts on close, executes SQLite | Preserve its encrypted lifecycle and transaction semantics. |
| `parad/parad/dbapi.py` | PEP 249 connection and cursor adapter | Keep DB-API behavior standard and delegate to Parad. |
| `parad/parad/sqlalchemy.py` | SQLAlchemy dialect and URL translation | Keep the dialect thin; route all connections through `parad.dbapi`. |
| `parad/pyproject.toml` | Optional dependency and SQLAlchemy dialect entrypoint | Preserve `parad[sqlalchemy]` and the `parad` dialect registration. |
| `parad/tests/test_sqlalchemy.py` | DB-API, engine, ORM, transaction, and reopen tests | Add regression tests for every adapter change. |
| `parad/docs/SQLALCHEMY.md` | Python user-facing implementation reference | Update when public usage changes. |

## Authentication and database provisioning

Complete authentication and database creation before creating an SQLAlchemy engine.

### Authenticate

Use an existing API key when one is configured. If no key exists, use the CLI:

```bash
parad auth register
parad auth login
```

Parad gateway authentication uses `X-API-Key`. Never send a Parad API key as `Authorization: Bearer`.

### Create or resolve the project and database

```bash
parad init <database-name> --project <project-name>
```

This authenticates, creates or resolves the project, creates or resolves the database, creates the encrypted local SQLite file, and pushes the initial state.

### Produce the canonical URL

```bash
parad init <database-name> --project <project-name> --print-database-url
```

Store the complete successful output as one secret:

```bash
DATABASE_URL=<complete Parad URL>
```

The URL carries the project, database, gateway, API-key token, and encryption passphrase. Never log the unredacted value. Use redacted output for normal terminal logs.

## Installation

Install Parad with its optional SQLAlchemy integration:

```bash
pip install "parad[sqlalchemy]"
```

The base package continues to support applications that use only `parad.connect()` or `parad.dbapi`.

## SQLAlchemy engine API

Create an engine directly from the canonical Parad URL:

```python
import os
from sqlalchemy import create_engine

engine = create_engine(
    os.environ["DATABASE_URL"],
    future=True,
)
```

The registered dialect is:

```text
parad://...
```

The dialect class is:

```python
from parad.sqlalchemy import ParadDialect
```

Its public behavior is:

| API | Behavior |
|---|---|
| `ParadDialect.name` | `parad` |
| `ParadDialect.driver` | SQLite-compatible driver name |
| `ParadDialect.import_dbapi()` | Returns `parad.dbapi` |
| `ParadDialect.create_connect_args(url)` | Passes the complete canonical URL to the DB-API connection |
| `create_engine("parad://...")` | Creates a SQLAlchemy Engine backed by Parad |
| `engine.connect()` | Opens a SQLAlchemy Connection over Parad DB-API |
| `engine.begin()` | Opens a connection transaction context |
| `engine.dispose()` | Closes pooled DB-API connections and re-encrypts Parad databases |

Do not parse the URL again inside the dialect. `ParadConnection` is the authority for URL parsing and configuration resolution.

## DB-API module API

Import the lower-level adapter when the application does not need ORM models:

```python
from parad import dbapi
from parad.dbapi import connect
```

### Module constants and exceptions

`parad.dbapi` exposes the PEP 249 module surface:

```python
apilevel
threadsafety
paramstyle
Warning
Error
InterfaceError
DatabaseError
DataError
OperationalError
IntegrityError
InternalError
ProgrammingError
NotSupportedError
Binary
Date
Time
Timestamp
DateFromTicks
TimeFromTicks
TimestampFromTicks
sqlite_version
sqlite_version_info
```

The parameter style is:

```text
qmark
```

Use `?` placeholders for DB-API statements.

### DB-API connection factory

```python
connection = connect(os.environ["DATABASE_URL"])
```

The signature is:

```python
connect(database: str | None = None, **kwargs) -> parad.dbapi.Connection
```

`database` is the complete Parad URL. The connection factory accepts `auto_sync=False` for deterministic jobs and test processes:

```python
connection = connect(
    os.environ["DATABASE_URL"],
    auto_sync=False,
)
```

### DB-API Connection APIs

The returned `Connection` provides:

| Method or property | Purpose |
|---|---|
| `connection.cursor()` | Create a DB-API cursor. |
| `connection.execute(sql, params=())` | Execute one SQL statement and return a cursor. |
| `connection.executemany(sql, parameter_sets)` | Execute a statement for multiple parameter sets. |
| `connection.executescript(script)` | Execute a SQLite script. |
| `connection.commit()` | Commit the current SQLite transaction. |
| `connection.rollback()` | Roll back the current SQLite transaction. |
| `connection.close()` | Close the Parad connection and re-encrypt the database. |
| `connection.create_function(...)` | Register a SQLite function. |
| `connection.set_authorizer(...)` | Register a SQLite authorizer. |
| `connection.interrupt()` | Interrupt an active SQLite operation. |
| `connection.parad` | Access the underlying `ParadConnection`. |
| `connection.closed` | Check whether the DB-API connection is closed. |

Example:

```python
connection = connect(os.environ["DATABASE_URL"])
try:
    cursor = connection.execute(
        "SELECT id, name FROM users WHERE active = ?",
        (1,),
    )
    rows = cursor.fetchall()
finally:
    connection.close()
```

The connection is also a context manager:

```python
with connect(os.environ["DATABASE_URL"]) as connection:
    connection.execute(
        "INSERT INTO users (name) VALUES (?)",
        ("Alice",),
    )
    connection.commit()
```

### DB-API Cursor APIs

The returned `Cursor` provides:

| Method or property | Purpose |
|---|---|
| `cursor.execute(sql, params=())` | Execute one statement. |
| `cursor.executemany(sql, parameter_sets)` | Execute repeated statements. |
| `cursor.executescript(script)` | Execute a SQLite script. |
| `cursor.fetchone()` | Fetch one row. |
| `cursor.fetchmany(size=None)` | Fetch a group of rows. |
| `cursor.fetchall()` | Fetch all rows. |
| `cursor.description` | SQLite/PEP 249 result-column metadata. |
| `cursor.rowcount` | Number of affected rows when reported by SQLite. |
| `cursor.lastrowid` | Last inserted row ID. |
| `cursor.arraysize` | Default `fetchmany()` size. |
| `cursor.close()` | Close the cursor. |

Cursor rows are SQLite row objects. SQLAlchemy consumes them through the dialect and maps them into SQLAlchemy result objects.

## Declarative ORM API

Define models with SQLAlchemy 2.x typed declarative syntax:

```python
from sqlalchemy import Boolean, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

Create tables through metadata:

```python
Base.metadata.create_all(engine)
```

Create a session:

```python
with Session(engine) as session:
    session.add(User(name="Alice", active=True))
    session.commit()
```

Select ORM objects:

```python
with Session(engine) as session:
    users = session.scalars(
        select(User).where(User.active.is_(True)).order_by(User.id)
    ).all()
```

Update objects:

```python
with Session(engine) as session:
    user = session.scalar(select(User).where(User.name == "Alice"))
    user.active = False
    session.commit()
```

Delete objects:

```python
with Session(engine) as session:
    user = session.scalar(select(User).where(User.name == "Alice"))
    session.delete(user)
    session.commit()
```

## SQLAlchemy Core API

Use Core statements when the application does not need ORM identity tracking:

```python
from sqlalchemy import insert, select, update

with engine.begin() as connection:
    connection.execute(
        insert(User).values(name="Alice", active=True)
    )

with engine.connect() as connection:
    result = connection.execute(
        select(User).where(User.active.is_(True))
    )
    rows = result.mappings().all()
```

Use driver SQL for direct SQLite statements:

```python
with engine.begin() as connection:
    connection.exec_driver_sql(
        "INSERT INTO users (name, active) VALUES (?, ?)",
        ("Bob", True),
    )
```

Use SQLAlchemy bind parameters for portable generated statements:

```python
from sqlalchemy import text

with engine.connect() as connection:
    row = connection.execute(
        text("SELECT id, name FROM users WHERE name = :name"),
        {"name": "Alice"},
    ).first()
```

## Transaction API

Use normal SQLAlchemy transaction contexts:

```python
with engine.begin() as connection:
    connection.execute(insert(User).values(name="Committed"))
```

The context commits on successful completion and rolls back when an exception escapes.

Use explicit connection transactions:

```python
with engine.connect() as connection:
    transaction = connection.begin()
    try:
        connection.execute(insert(User).values(name="Explicit"))
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
```

Use ORM transactions:

```python
with Session(engine) as session:
    with session.begin():
        session.add(User(name="Session Transaction"))
```

Use nested transactions for SQLite savepoints:

```python
with Session(engine) as session:
    with session.begin():
        session.add(User(name="Outer"))
        with session.begin_nested():
            session.add(User(name="Savepoint"))
```

When modifying transaction behavior, test commit, rollback, nested savepoint commit, nested savepoint rollback, and reopen after disposal.

## Parad lifecycle API from SQLAlchemy

SQLAlchemy exposes standard engine disposal. The underlying Parad connection is available through the DB-API connection:

```python
with engine.connect() as connection:
    raw_dbapi_connection = connection.connection
    parad_connection = raw_dbapi_connection.driver_connection.parad
```

Use the Parad connection for explicit synchronization:

```python
parad_connection.push()
parad_connection.pull()
parad_connection.close()
```

In ordinary applications, call:

```python
engine.dispose()
```

`engine.dispose()` closes DB-API connections. Parad then re-encrypts the temporary SQLite database into its local encrypted file.

For server processes and serverless functions, create the engine with a controlled lifecycle and use `auto_sync=False` where the application performs explicit sync at known boundaries. Do not allow multiple independent sync daemons to manage the same database process unless the task explicitly designs that lifecycle.

## DATABASE_URL and environment rules

Use one connection value everywhere:

```python
import os

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)
```

The URL resolution authority remains `parad.connection.parse_url()` and `parad.connect()`. Do not manually split the URL in application code or in the SQLAlchemy dialect.

Keep secrets out of logs:

```python
from parad import redact_url

print(redact_url(DATABASE_URL))
```

Use the full value only as a secret-manager or deployment environment value.

## Error diagnosis

Use this decision table when a SQLAlchemy task fails:

| Symptom | Inspect | Correct action |
|---|---|---|
| `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:parad` | Package installation and entrypoint metadata | Install `parad[sqlalchemy]`; rebuild/reinstall the package. |
| Missing `SQLAlchemy` module | Python environment | Install `pip install "parad[sqlalchemy]"`. |
| `DATABASE_URL` missing | Environment/config and CLI provisioning | Run successful `parad init`; set the complete URL. |
| `DecryptionError` | URL passphrase and local file | Use the original passphrase for the existing database. |
| Gateway 401 | API key and URL | Re-authenticate or rotate the API key; keep `X-API-Key`. |
| `ProgrammingError` | SQL placeholders | DB-API uses `?`; SQLAlchemy `text()` uses named bind parameters. |
| `OperationalError: database not open` | Engine/connection lifecycle | Keep the engine alive and do not use a disposed connection. |
| Data disappears after process exit | Missing engine disposal | Call `engine.dispose()` or close the DB-API/Parad connection. |
| Transaction remains open | Session autobegin or missing commit/rollback | Inspect SQLAlchemy session state and use explicit transaction contexts. |
| Sync changes during tests | Auto-sync daemon | Use a local URL or `auto_sync=False` and explicit `push()`/`pull()`. |
| ORM model fields do not map | Table metadata and column names | Ensure model declarations match the SQLite schema and call `Base.metadata.create_all()`. |

## Test workflow for AI changes

Run the Python SDK suite:

```bash
cd /home/ubuntu/Paradox-DB
PYTHONPATH="$PWD/parad" pytest -q parad/tests
```

Run compilation:

```bash
python3 -m compileall -q parad
```

Run the installed dialect smoke test:

```python
from sqlalchemy import create_engine

engine = create_engine("parad://local/smoke?passphrase=test-passphrase")
with engine.begin() as connection:
    connection.exec_driver_sql("CREATE TABLE IF NOT EXISTS smoke (value TEXT)")
engine.dispose()
```

Build the package:

```bash
rm -rf /tmp/parad-wheel
mkdir -p /tmp/parad-wheel
python3 -m pip wheel ./parad --no-deps --no-build-isolation -w /tmp/parad-wheel
```

Validate formatting and repository status:

```bash
git diff --check
git status --short
```

Add or update tests in `parad/tests/test_sqlalchemy.py` for every adapter change. The test set must cover DB-API cursor behavior, ORM CRUD, transactions, encrypted reopen, and the installed `parad://` entrypoint.

## Implementation rules for AI

1. Keep `parad.dbapi` independent of SQLAlchemy so DB-API users do not need the optional ORM dependency.
2. Keep SQLAlchemy as an optional package extra: `parad[sqlalchemy]`.
3. Keep the registered dialect name exactly `parad`.
4. Keep `ParadDialect.create_connect_args()` passing the complete URL to `parad.dbapi`.
5. Do not duplicate URL parsing in `parad.sqlalchemy`.
6. Do not bypass `ParadConnection` or `Engine` from the dialect.
7. Preserve PEP 249 cursor attributes and exception classes.
8. Preserve SQLite `?` parameter binding in the DB-API layer.
9. Preserve SQLAlchemy transaction commit and rollback behavior.
10. Preserve `engine.dispose()` as the reliable encrypted close boundary.
11. Keep `DATABASE_URL` as the one deployment connection value.
12. Keep API keys, passphrases, and passwords out of logs and source code.
13. Add focused tests before broad refactors.
14. Update `parad/docs/SQLALCHEMY.md` when public behavior changes.
15. Update this guide only when the AI workflow or supported API contract changes.
16. Commit and push after verification, then report the commit and exact test results.

## Delivery report format

When the task is complete, report:

- Commit hash and branch.
- Files changed and the purpose of each file.
- New or changed public APIs.
- `DATABASE_URL` behavior.
- DB-API and SQLAlchemy verification results.
- Package build and entrypoint verification.
- Environment-dependent tests that could not run.
- Whether the working tree is clean.

## References

[1]: ../../parad/docs/SQLALCHEMY.md "Parad Python SQLAlchemy implementation guide"
[2]: https://docs.sqlalchemy.org/20/ "SQLAlchemy 2.0 documentation"
[3]: https://docs.sqlalchemy.org/20/core/engines.html "SQLAlchemy engine documentation"
[4]: https://docs.sqlalchemy.org/20/orm/session_transaction.html "SQLAlchemy transaction documentation"
