"""Unit tests for the parad connection-string helpers.

Mirrors client/tests/connection.test.ts: the six documented ``parad://``
forms for ``parse_url`` / ``generate_url`` round-trips, ``db_state_key``
project scoping, and the auth-resolution precedence inside ``connect()``.
"""

import pytest

import parad.config as _cfg
import parad.connection as pc
from parad.connection import connect, db_state_key, generate_url, parse_url
from parad.config import get_canonical_database_url, load_config


class FakeGatewayClient:
    instances = []

    def __init__(self, gateway_url, api_key=""):
        self.gateway_url = gateway_url
        self.api_key = api_key
        self.login_calls = 0
        FakeGatewayClient.instances.append(self)

    def login(self, email, password):
        self.login_calls += 1
        self.login_email = email
        self.login_password = password
        self.api_key = "pk_from_login"
        return {"user_id": "u1", "email": email, "username": email, "api_key": self.api_key}

    def ensure_project(self, name, description=""):
        return {"id": "p-" + name, "name": name}

    def ensure_database(self, project_id, name, description=""):
        return {"id": "d-" + name, "name": name}


@pytest.fixture
def fake_gateway(monkeypatch):
    FakeGatewayClient.instances = []
    captured = {}

    class FakeParadConnection:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(pc, "GatewayClient", FakeGatewayClient)
    monkeypatch.setattr(pc, "ParadConnection", FakeParadConnection)
    return captured


def test_parse_url_local_only():
    parsed = parse_url("parad://local/mydb?passphrase=secret")
    assert parsed == {
        "name": "mydb",
        "project": None,
        "passphrase": "secret",
        "gateway_url": "",
        "token": "",
        "email": "",
        "password": "",
    }


def test_parse_url_project_scoped_with_gateway():
    parsed = parse_url(
        "parad://local/myproj/mydb"
        "?passphrase=secret&gateway=https://paradox-db.onrender.com/v1"
    )
    assert parsed["name"] == "mydb"
    assert parsed["project"] == "myproj"
    assert parsed["gateway_url"] == "https://paradox-db.onrender.com/v1"


def test_parse_url_explicit_token_query():
    parsed = parse_url(
        "parad://local/myproj/mydb"
        "?passphrase=secret&gateway=https://g/v1&token=tok-abc"
    )
    assert parsed["token"] == "tok-abc"


def test_parse_url_email_password_userinfo():
    parsed = parse_url(
        "parad://alice@example.com:secretpw@local/myproj/mydb"
        "?passphrase=secret"
    )
    assert parsed["email"] == "alice@example.com"
    assert parsed["password"] == "secretpw"
    assert parsed["token"] == ""


def test_parse_url_token_userinfo():
    parsed = parse_url(
        "parad://tok-abc@local/myproj/mydb?passphrase=secret"
    )
    assert parsed["token"] == "tok-abc"
    assert parsed["email"] == ""
    assert parsed["password"] == ""


def test_parse_url_nested_project_path():
    parsed = parse_url("parad://local/myproj/sub/mydb?gateway=https://g/v1")
    assert parsed["project"] == "myproj/sub"
    assert parsed["name"] == "mydb"


def test_parse_url_rejects_missing_db_name():
    with pytest.raises(ValueError, match="must contain a database name"):
        parse_url("parad://local")
    with pytest.raises(ValueError, match="must contain a database name"):
        parse_url("parad://")


def test_parse_url_rejects_unsupported_scheme():
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        parse_url("postgres://local/mydb")


def test_parse_url_accepts_paradox_scheme():
    parsed = parse_url("paradox://local/mydb?passphrase=secret")
    assert parsed["name"] == "mydb"
    assert parsed["project"] is None
    assert parsed["passphrase"] == "secret"


def test_generate_url_round_trips_token_form():
    url = generate_url("mydb", "secret", "https://g/v1", "proj", "t1")
    parsed = parse_url(url)
    assert parsed["name"] == "mydb"
    assert parsed["project"] == "proj"
    assert parsed["token"] == "t1"
    assert parsed["passphrase"] == "secret"
    assert parsed["gateway_url"] == "https://g/v1"
    assert parsed["email"] == ""
    assert parsed["password"] == ""


def test_generate_url_round_trips_email_password_form():
    url = generate_url(
        "mydb", "secret", "https://g/v1", "proj", "", "alice@example.com", "pw"
    )
    parsed = parse_url(url)
    assert parsed["email"] == "alice@example.com"
    assert parsed["password"] == "pw"
    assert parsed["token"] == ""


def test_generate_url_local_only():
    assert generate_url("mydb", "secret") == "parad://local/mydb?passphrase=secret"


def test_generate_url_omits_empty_query_params():
    assert generate_url("mydb") == "parad://local/mydb"


def test_canonical_database_url_prefers_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PARADOX_HOME", str(tmp_path))
    url = "parad://local/proj/newdb?passphrase=secret&gateway=https://g/v1"
    monkeypatch.setenv("DATABASE_URL", url)
    assert get_canonical_database_url() == url


def test_canonical_database_url_prefers_persisted_config(monkeypatch, tmp_path):
    monkeypatch.setenv("PARADOX_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"database_url": "parad://local/proj/db?passphrase=secret"}')
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_canonical_database_url() == "parad://local/proj/db?passphrase=secret"


def test_canonical_database_url_reconstructs_and_persists_legacy_config(monkeypatch, tmp_path):
    monkeypatch.setenv("PARADOX_HOME", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "config.json").write_text(
        '{"database_path": "~/legacy.db", "project_name": "proj", '
        '"encryption": {"passphrase": "secret"}, '
        '"sync": {"gateway_url": "https://g/v1", "api_key": "token"}}'
    )
    url = get_canonical_database_url()
    parsed = parse_url(url)
    assert parsed["name"] == "legacy"
    assert parsed["project"] == "proj"
    assert parsed["passphrase"] == "secret"
    assert parsed["token"] == "token"
    assert load_config().database_url == url


def test_db_state_key_project_scoped():
    assert db_state_key("mydb", "myproj") == "myproj/mydb"
    assert db_state_key("mydb", "myproj/sub") == "myproj/sub/mydb"
    assert db_state_key("mydb") == "mydb"
    assert db_state_key("mydb", None) == "mydb"


def test_connect_explicit_api_key_beats_url_token(fake_gateway):
    captured = fake_gateway
    connect(
        url="parad://local/myproj/mydb"
        "?passphrase=secret&gateway=https://g/v1&token=url-tok",
        api_key="explicit-tok",
        auto_sync=False,
    )
    assert captured["api_key"] == "explicit-tok"
    assert sum(g.login_calls for g in FakeGatewayClient.instances) == 0


def test_connect_url_token_beats_userinfo_token(fake_gateway):
    captured = fake_gateway
    connect(
        url="parad://ui-tok@local/myproj/mydb"
        "?passphrase=secret&gateway=https://g/v1&token=query-tok",
        auto_sync=False,
    )
    assert captured["api_key"] == "query-tok"
    assert sum(g.login_calls for g in FakeGatewayClient.instances) == 0


def test_connect_userinfo_token_used(fake_gateway):
    captured = fake_gateway
    connect(
        url="parad://tok-abc@local/myproj/mydb"
        "?passphrase=secret&gateway=https://g/v1",
        auto_sync=False,
    )
    assert captured["api_key"] == "tok-abc"
    assert sum(g.login_calls for g in FakeGatewayClient.instances) == 0


def test_connect_email_password_triggers_login(fake_gateway):
    captured = fake_gateway
    connect(
        url="parad://alice@example.com:secretpw@local/myproj/mydb"
        "?passphrase=secret&gateway=https://g/v1",
        auto_sync=False,
    )
    assert sum(g.login_calls for g in FakeGatewayClient.instances) == 1
    login_gw = FakeGatewayClient.instances[0]
    assert login_gw.login_email == "alice@example.com"
    assert login_gw.login_password == "secretpw"
    assert captured["api_key"] == "pk_from_login"


def test_connect_email_password_requires_gateway(monkeypatch):
    cfg = _cfg.load_config()
    cfg.sync.gateway_url = ""
    monkeypatch.setattr(pc, "load_config", lambda: cfg)
    with pytest.raises(ValueError, match="require a gateway"):
        connect(
            url="parad://alice@example.com:secretpw@local/myproj/mydb"
            "?passphrase=secret",
            auto_sync=False,
        )


def test_connect_config_api_key_fallback(fake_gateway):
    captured = fake_gateway
    _cfg.set_config_value("sync.api_key", "cfg-tok")
    connect(
        url="parad://local/myproj/mydb"
        "?passphrase=secret&gateway=https://g/v1",
        auto_sync=False,
    )
    assert captured["api_key"] == "cfg-tok"
    assert sum(g.login_calls for g in FakeGatewayClient.instances) == 0
