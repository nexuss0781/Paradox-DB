"""Hermetic tests for the SQLAlchemy dialect (no network)."""

import httpx
import pytest

from parad import dbapi
from parad.dbapi import ProgrammingError
from parad.dialect import ParadDialect


class FakeDBAPIConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(sql)


def test_import_dbapi():
    assert ParadDialect.import_dbapi() is dbapi


def test_dialect_metadata():
    assert ParadDialect.name == "parad"
    assert ParadDialect.driver == "remote"
    assert ParadDialect.default_paramstyle == "qmark"


def test_create_connect_args_renders_url():
    from sqlalchemy.engine import make_url

    d = ParadDialect(dbapi=dbapi)
    args, kwargs = d.create_connect_args(
        make_url("parad://token@local/proj/db?gateway=https://gw.example/v1&passphrase=sekret")
    )
    url = args[0]
    assert url.startswith("parad://")
    assert "/proj/db" in url
    assert "gateway=" in url
    assert "gw.example" in url
    assert kwargs == {}


def test_do_begin_sends_begin():
    d = ParadDialect(dbapi=dbapi)
    conn = FakeDBAPIConnection()
    d.do_begin(conn)
    assert conn.statements == ["BEGIN"]


def test_is_disconnect_network():
    d = ParadDialect(dbapi=dbapi)
    assert d.is_disconnect(httpx.ConnectError("boom"), None, None)
    assert d.is_disconnect(ConnectionError("boom"), None, None)
    assert d.is_disconnect(TimeoutError("boom"), None, None)


def test_is_disconnect_dbapi():
    d = ParadDialect(dbapi=dbapi)
    assert not d.is_disconnect(ProgrammingError("syntax error"), None, None)
    assert d.is_disconnect(ProgrammingError("session not found"), None, None)


def test_registry_and_create_engine():
    from sqlalchemy import create_engine

    engine = create_engine("parad://token@local/proj/db?gateway=https://gw.example/v1")
    assert engine.dialect.name == "parad"
    assert engine.dialect.driver == "remote"
    engine.dispose()


class FakeGatewayServer:
    """In-memory SQLite stand-in for the gateway SQL session endpoints."""

    def __init__(self):
        import sqlite3

        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.statements = []

    def ensure_project(self, name, description=""):
        return {"id": "p1", "name": name}

    def ensure_database(self, project_id, name, description=""):
        return {"id": "db1", "name": name}

    def sql_session_close(self, database_id, commit=True):
        return {"closed": True, "version": None}

    def sql(self, database_id, sql, params=(), passphrase="", executescript=False, flush=False):
        import sqlite3

        self.statements.append(sql)
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


def test_sqlalchemy_integration(monkeypatch):
    """Drive a full SQLAlchemy workflow through the remote DBAPI."""
    from sqlalchemy import create_engine, inspect, text

    server = FakeGatewayServer()
    monkeypatch.setattr(
        dbapi, "GatewayClient", lambda url, key="": server
    )

    engine = create_engine(
        "parad://token@local/proj/db?gateway=https://gw.example/v1"
    )

    # DDL + DML inside an explicit transaction
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.exec_driver_sql("INSERT INTO t (name) VALUES (?)", ("alice",))
        conn.exec_driver_sql("INSERT INTO t (name) VALUES (?)", ("bob",))

    # committed rows survive a fresh statement (BEGIN/COMMIT round-trip)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, name FROM t ORDER BY id")).fetchall()
    assert rows == [(1, "alice"), (2, "bob")]

    # reflection via PRAGMA / sqlite_master queries
    insp = inspect(engine)
    assert "t" in insp.get_table_names()
    cols = insp.get_columns("t")
    assert [c["name"] for c in cols] == ["id", "name"]

    # rollback discards the open transaction's changes
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO t (name) VALUES (?)", ("carol",))
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM t WHERE name = ?", ("bob",))
    with engine.connect() as conn:
        names = [r[0] for r in conn.execute(text("SELECT name FROM t ORDER BY id"))]
    assert names == ["alice", "carol"]

    # the dialect really sent BEGIN and COMMIT over the wire
    assert "BEGIN" in server.statements
    assert "COMMIT" in server.statements
    engine.dispose()
