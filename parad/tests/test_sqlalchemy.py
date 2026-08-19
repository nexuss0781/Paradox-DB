from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, create_engine, select
from sqlalchemy.dialects import registry
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from parad.dbapi import connect as dbapi_connect

registry.register("parad", "parad.sqlalchemy", "ParadDialect")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


def test_dbapi_cursor_and_transaction():
    conn = dbapi_connect("parad://local/dbapi_test?passphrase=test-passphrase")
    try:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        cursor.execute("INSERT INTO users (name) VALUES (?)", ("Alice",))
        conn.commit()
        cursor.execute("SELECT name FROM users")
        assert cursor.fetchone()[0] == "Alice"
        assert cursor.description[0][0] == "name"
    finally:
        conn.close()


def test_sqlalchemy_orm_crud_and_transaction():
    engine = create_engine("parad://local/sqlalchemy_test?passphrase=test-passphrase")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(User(name="Alice", active=True))
            session.commit()
            assert session.scalar(select(User.name)) == "Alice"
            session.commit()

            with session.begin():
                session.add(User(name="Bob", active=False))

            names = session.scalars(select(User.name).order_by(User.id)).all()
            assert names == ["Alice", "Bob"]
    finally:
        engine.dispose()


def test_sqlalchemy_reopens_encrypted_database():
    url = "parad://local/sqlalchemy_reopen?passphrase=test-passphrase"
    first = create_engine(url)
    Base.metadata.create_all(first)
    with first.begin() as conn:
        conn.execute(User.__table__.insert().values(name="Persisted", active=True))
    first.dispose()

    second = create_engine(url)
    try:
        with Session(second) as session:
            assert session.scalar(select(User.name)) == "Persisted"
    finally:
        second.dispose()
