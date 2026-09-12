# SQLAlchemy integration

Parad provides a SQLAlchemy dialect backed by its encrypted SQLite engine. SQLAlchemy supplies the ORM and query construction; Parad retains responsibility for encryption, local persistence, cloud synchronization, and conflict handling.

## Install

```bash
pip install "parad[sqlalchemy]"
```

The SQLAlchemy dependency is optional. Existing users of `parad.connect()` do not need to install it.

## Connect with `DATABASE_URL`

Create or provision the database through the Parad CLI first, then store the complete successful connection URL as one deployment secret:

```bash
export DATABASE_URL='parad://...'
```

Create a SQLAlchemy engine using the same URL:

```python
from sqlalchemy import create_engine

engine = create_engine(
    "parad://...",
    future=True,
)
```

The URL carries the project, database, gateway, API-key token, and encryption passphrase. Do not log the unredacted value.

## Declarative ORM usage

```python
from sqlalchemy import String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

Base.metadata.create_all(engine)

with Session(engine) as session:
    session.add(User(name="Alice"))
    session.commit()
    users = session.scalars(select(User)).all()
```

The DB-API layer is also available for applications that do not use the ORM:

```python
from parad.dbapi import connect

connection = connect(os.environ["DATABASE_URL"])
try:
    cursor = connection.execute("SELECT 1")
    print(cursor.fetchone())
finally:
    connection.close()
```

## Transactions and lifecycle

SQLAlchemy `Session` and `Connection` transaction boundaries map to the underlying SQLite connection. Use normal SQLAlchemy transaction contexts:

```python
with engine.begin() as connection:
    connection.exec_driver_sql(
        "INSERT INTO users (name) VALUES (?)",
        ("Bob",),
    )
```

Dispose engines when a process or worker is finished:

```python
engine.dispose()
```

Disposal closes the Parad connection and re-encrypts the temporary SQLite file. For web servers and serverless functions, use an explicit pool/lifecycle policy, normally `auto_sync=False`, and call `ParadConnection.push()` or `pull()` at controlled application boundaries when using the lower-level API.

## Compatibility scope

The first supported release targets SQLAlchemy 2.x Core and ORM workflows: declarative models, metadata creation, parameterized SQL, CRUD, result fetching, commits, rollbacks, and engine disposal. Parad-specific synchronization and encryption remain outside SQLAlchemy’s dialect contract and are intentionally exposed through the underlying `ParadConnection` available from `parad.dbapi.Connection.parad`.
