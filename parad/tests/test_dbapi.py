"""Hermetic tests for the parad DB-API 2.0 interface (no network)."""

import base64

import pytest

from parad import dbapi
from parad.gateway import GatewayError


class FakeGateway:
    """Recorded GatewayClient stand-in used to avoid the network."""

    def __init__(self, gateway_url, api_key=""):
        self.gateway_url = gateway_url
        self.api_key = api_key
        self.calls = []

    def ensure_project(self, name, description=""):
        self.calls.append(("ensure_project", name))
        return {"id": "proj-1", "name": name}

    def ensure_database(self, project_id, name, description=""):
        self.calls.append(("ensure_database", project_id, name))
        return {"id": "db-1", "name": name}

    def login(self, email, password):
        self.calls.append(("login", email))
        self.api_key = "login-token"
        return {"api_key": "login-token"}

    def sql(self, database_id, sql, params=(), passphrase="", executescript=False, flush=False):
        self.calls.append(
            ("sql", database_id, sql, list(params), passphrase, executescript, flush)
        )
        return {
            "columns": ["id", "blob"],
            "rows": [[1, {"__parad_bytes__": base64.b64encode(b"hello").decode()}]],
            "rowcount": -1,
            "lastrowid": None,
            "changes": 0,
            "in_transaction": False,
            "persisted_version": None,
        }

    def sql_session_close(self, database_id, commit=True):
        self.calls.append(("close", database_id, commit))
        return {"closed": True, "version": None}


@pytest.fixture
def fake_gateway(monkeypatch):
    created = []

    def factory(url, key=""):
        gw = FakeGateway(url, key)
        created.append(gw)
        return gw

    monkeypatch.setattr(dbapi, "GatewayClient", factory)
    return created


# -- value codec ----------------------------------------------------


def test_encode_param_bytes():
    assert dbapi._encode_param(b"abc") == {"__parad_bytes__": "YWJj"}
    assert dbapi._encode_param(42) == 42
    assert dbapi._encode_param("x") == "x"


def test_decode_result_bytes_and_row():
    assert dbapi._decode_result({"__parad_bytes__": "YWJj"}) == b"abc"
    assert dbapi._decode_result(7) == 7
    assert dbapi._decode_row([1, {"__parad_bytes__": "eQ=="}]) == [1, b"y"]


# -- module metadata ------------------------------------------------


def test_module_attributes():
    assert dbapi.apilevel == "2.0"
    assert dbapi.threadsafety == 1
    assert dbapi.paramstyle == "qmark"
    assert isinstance(dbapi.sqlite_version_info, tuple)
    assert issubclass(dbapi.IntegrityError, dbapi.DatabaseError)
    assert issubclass(dbapi.DatabaseError, dbapi.Error)
    assert issubclass(dbapi.Error, Exception)


# -- connect / provisioning -----------------------------------------


def test_connect_provisions_and_returns_connection(fake_gateway):
    conn = dbapi.connect(
        "parad://token@local/myproj/mydb?gateway=https://gw.example/v1",
        api_key="tok",
    )
    assert isinstance(conn, dbapi.ParadRemoteConnection)
    assert conn.database_id == "db-1"
    assert conn.gateway_url == "https://gw.example/v1"
    calls = [c[0] for c in fake_gateway[0].calls]
    assert "ensure_project" in calls
    assert "ensure_database" in calls


def test_connect_requires_project():
    with pytest.raises(ValueError, match="project"):
        dbapi.connect("parad://local/mydb?gateway=https://gw.example/v1", api_key="tok")


def test_connect_requires_gateway(monkeypatch):
    from parad.config import load_config

    cfg = load_config()
    cfg.sync.gateway_url = ""
    monkeypatch.setattr(dbapi, "load_config", lambda: cfg)
    monkeypatch.setattr(dbapi, "GatewayClient", FakeGateway)
    with pytest.raises(ValueError, match="gateway"):
        dbapi.connect("parad://local/proj/mydb", api_key="tok")


# -- connection behaviour -------------------------------------------


def test_connection_execute_sends_payload(fake_gateway):
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    data = conn._execute("SELECT ?", [5], flush=True)
    call = fake_gateway[-1].calls[-1]
    assert call[1] == "db-1"
    assert call[2] == "SELECT ?"
    assert call[3] == [5]
    assert call[5] is False
    assert call[6] is True
    # _execute returns the raw gateway payload; BLOB markers are decoded
    # at the cursor layer (see test_cursor_fetch_semantics)
    assert data["rows"][0][1] == {"__parad_bytes__": "aGVsbG8="}


def test_connection_tracks_transaction_state(fake_gateway):
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    conn._execute("BEGIN")
    assert conn.in_transaction is False  # fake returns False


def test_connection_commit_flushes(fake_gateway):
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    conn.commit()
    call = fake_gateway[-1].calls[-1]
    assert call[2] == "COMMIT"
    assert call[6] is True


def test_connection_rollback(fake_gateway):
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    conn.rollback()
    assert fake_gateway[-1].calls[-1][2] == "ROLLBACK"


def test_connection_close_closes_session(fake_gateway):
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    conn.close()
    assert fake_gateway[-1].calls[-1][0] == "close"
    assert fake_gateway[-1].calls[-1][2] is True
    with pytest.raises(dbapi.InterfaceError):
        conn.execute("SELECT 1")


def test_connection_error_mapping(monkeypatch):
    class ErringGateway(FakeGateway):
        def __init__(self, url, key=""):
            super().__init__(url, key)

        def sql(self, *args, **kwargs):
            raise GatewayError(400, "near 'x': syntax error")

    monkeypatch.setattr(dbapi, "GatewayClient", lambda url, key="": ErringGateway(url, key))
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    with pytest.raises(dbapi.ProgrammingError, match="syntax error"):
        conn.execute("SELECT x from")


def test_connection_error_mapping_auth(monkeypatch):
    class AuthGateway(FakeGateway):
        def sql(self, *args, **kwargs):
            raise GatewayError(401, "invalid api key")

    monkeypatch.setattr(dbapi, "GatewayClient", lambda url, key="": AuthGateway(url, key))
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    with pytest.raises(dbapi.InterfaceError, match="authentication"):
        conn.cursor().execute("SELECT 1")


# -- cursor behaviour -----------------------------------------------


def test_cursor_fetch_semantics(fake_gateway):
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    cur = conn.cursor()
    cur.execute("SELECT id, blob FROM t")
    assert cur.description == [("id", None, None, None, None, None, None), ("blob", None, None, None, None, None, None)]
    assert cur.rowcount == -1
    row = cur.fetchone()
    assert row == [1, b"hello"]
    assert cur.fetchone() is None
    assert cur.fetchall() == []


def test_cursor_fetchmany_and_iter():
    class ManyGateway(FakeGateway):
        def sql(self, *args, **kwargs):
            return {
                "columns": ["n"],
                "rows": [[1], [2], [3]],
                "rowcount": -1,
                "lastrowid": None,
                "changes": 0,
                "in_transaction": False,
                "persisted_version": None,
            }

    from unittest.mock import patch

    with patch.object(dbapi, "GatewayClient", lambda url, key="": ManyGateway(url, key)):
        conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
        cur = conn.cursor()
        cur.execute("SELECT n FROM t")
        assert cur.fetchmany(2) == [[1], [2]]
        assert list(cur) == [[3]]


def test_cursor_rowcount_lastrowid(monkeypatch):
    class InsertGateway(FakeGateway):
        def sql(self, *args, **kwargs):
            return {
                "columns": None,
                "rows": [],
                "rowcount": 1,
                "lastrowid": 42,
                "changes": 1,
                "in_transaction": True,
                "persisted_version": None,
            }

    monkeypatch.setattr(dbapi, "GatewayClient", lambda url, key="": InsertGateway(url, key))
    conn = dbapi.connect("parad://local/p/db?gateway=https://gw.example/v1", api_key="tok")
    cur = conn.cursor()
    cur.execute("INSERT INTO t VALUES (?)", [1])
    assert cur.rowcount == 1
    assert cur.lastrowid == 42
    assert cur.description is None
