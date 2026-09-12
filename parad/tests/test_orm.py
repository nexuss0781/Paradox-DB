"""Hermetic ORM integration tests for the parad dialect (no network).

Drives declarative models, relationships, backrefs, lazy loading and
unique constraints through the remote gateway session — the shape of the
Digital-edu schema — using an in-memory SQLite stand-in for the gateway.
"""

from __future__ import annotations

import pytest
from sqlalchemy import (
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from parad import dbapi
from parad.gateway import GatewayError


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)

    progress: Mapped[list["Progress"]] = relationship(back_populates="user")
    user_badges: Mapped[list["UserBadge"]] = relationship(back_populates="user")


class Progress(Base):
    __tablename__ = "progress"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    content_id: Mapped[str] = mapped_column(String(200), nullable=False)
    completed: Mapped[bool] = mapped_column(default=False)

    user: Mapped[User] = relationship(back_populates="progress")


class Badge(Base):
    __tablename__ = "badges"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    awards: Mapped[list["UserBadge"]] = relationship(
        back_populates="badge",
        cascade="all, delete-orphan",
        single_parent=True,
    )


class UserBadge(Base):
    __tablename__ = "user_badges"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    badge_id: Mapped[int] = mapped_column(ForeignKey("badges.id"), nullable=False)

    user: Mapped[User] = relationship(back_populates="user_badges")
    badge: Mapped[Badge] = relationship(back_populates="awards")

    __mapper_args__ = {"confirm_deleted_rows": False}


class Restriction(Base):
    __tablename__ = "restrictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    content_id: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_user_content"),
    )


class FakeGatewayServer:
    """In-memory SQLite stand-in for the gateway SQL session endpoints."""

    def __init__(self):
        import sqlite3

        self.conn = sqlite3.connect(":memory:", isolation_level=None)

    def ensure_project(self, name, description=""):
        return {"id": "p1", "name": name}

    def ensure_database(self, project_id, name, description=""):
        return {"id": "db1", "name": name}

    def sql_session_close(self, database_id, commit=True):
        return {"closed": True, "version": None}

    def sql(self, database_id, sql, params=(), passphrase="", executescript=False, flush=False):
        import sqlite3

        sql = sql.strip()
        try:
            if (
                sql.upper() in ("COMMIT", "ROLLBACK")
                and not self.conn.in_transaction
            ):
                return {
                    "columns": None,
                    "rows": [],
                    "rowcount": -1,
                    "lastrowid": None,
                    "changes": 0,
                    "in_transaction": False,
                    "persisted_version": None,
                }
            cur = self.conn.execute(sql, tuple(params))
            if cur.description:
                return {
                    "columns": [d[0] for d in cur.description],
                    "rows": [list(r) for r in cur.fetchall()],
                    "rowcount": -1,
                    "lastrowid": None,
                    "changes": 0,
                    "in_transaction": bool(self.conn.in_transaction),
                    "persisted_version": None,
                }
            return {
                "columns": None,
                "rows": [],
                "rowcount": cur.rowcount,
                "lastrowid": cur.lastrowid,
                "changes": 0,
                "in_transaction": bool(self.conn.in_transaction),
                "persisted_version": None,
            }
        except sqlite3.Error as e:
            raise GatewayError(400, str(e))


@pytest.fixture
def engine(monkeypatch):
    server = FakeGatewayServer()
    monkeypatch.setattr(dbapi, "GatewayClient", lambda url, key="": server)
    eng = create_engine("parad://token@local/proj/db?gateway=https://gw.example/v1")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_create_all_creates_tables(engine):
    from sqlalchemy import inspect

    insp = inspect(engine)
    names = set(insp.get_table_names())
    assert {"users", "progress", "badges", "user_badges", "restrictions"} <= names


def test_insert_and_query(session_factory):
    with session_factory() as s:
        s.add(User(email="a@x.com", username="alice"))
        s.commit()
    with session_factory() as s:
        users = s.execute(select(User)).scalars().all()
        assert len(users) == 1
        assert users[0].email == "a@x.com"


def test_relationship_lazy_load_across_commit(session_factory):
    with session_factory() as s:
        u = User(email="a@x.com", username="alice")
        u.progress = [
            Progress(content_id="w1", completed=True),
            Progress(content_id="w2"),
        ]
        s.add(u)
        s.commit()

    with session_factory() as s:
        user = s.execute(select(User).where(User.username == "alice")).scalar_one()
        rows = user.progress  # lazy SELECT over the remote session
        assert len(rows) == 2
        assert {p.content_id for p in rows} == {"w1", "w2"}


def test_backref_and_many_to_many(session_factory):
    with session_factory() as s:
        u = User(email="a@x.com", username="alice")
        b = Badge(name="streak")
        u.user_badges.append(UserBadge(badge=b))
        s.add(u)
        s.commit()

    with session_factory() as s:
        user = s.execute(select(User)).scalars().one()
        assert len(user.user_badges) == 1
        assert user.user_badges[0].badge.name == "streak"


def test_unique_constraint_raises_integrity_error(session_factory):
    with session_factory() as s:
        s.add(User(email="dup@x.com", username="alice"))
        s.commit()
    with pytest.raises(IntegrityError):
        with session_factory() as s:
            s.add(User(email="dup@x.com", username="bob"))
            s.commit()


def test_composite_unique_constraint(session_factory):
    with session_factory() as s:
        s.add(Restriction(user_id=1, content_id="c1"))
        s.commit()
    with pytest.raises(IntegrityError):
        with session_factory() as s:
            s.add(Restriction(user_id=1, content_id="c1"))
            s.commit()


def test_rollback_discards_uncommitted(session_factory):
    with session_factory() as s:
        s.add(User(email="tmp@x.com", username="tmp"))
        s.rollback()
    with session_factory() as s:
        assert s.execute(select(User)).scalars().all() == []


def test_cascade_delete_orphan(session_factory):
    with session_factory() as s:
        b = Badge(name="combo")
        b.awards = [UserBadge(user_id=1), UserBadge(user_id=2)]
        s.add(b)
        s.commit()
        s.delete(b)
        s.commit()
    with session_factory() as s:
        remaining = s.execute(select(func.count()).select_from(UserBadge)).scalar()
        assert remaining == 0


def test_update_and_delete(session_factory):
    with session_factory() as s:
        u = User(email="a@x.com", username="alice")
        s.add(u)
        s.commit()
        u.username = "alice2"
        s.commit()
    with session_factory() as s:
        user = s.execute(select(User).where(User.username == "alice2")).scalar_one()
        s.delete(user)
        s.commit()
    with session_factory() as s:
        assert s.execute(select(User)).scalars().all() == []
