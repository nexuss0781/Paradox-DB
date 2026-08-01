"""End-to-end workflow tests for the hardened sync layer (hermetic mock gateway).

Covers the core confirmed product workflow against a local in-process fake of
the live gateway contract (``/v1/auth/login``, ``/v1/projects``,
``/v1/projects/{id}/databases``, ``/v1/upload``, ``/v1/download``):

  T1  connect(url) with email:password -> auto-login (JWT persisted as
      api_key), project/database auto-provisioning, project-scoped state key.
  T2  offline -> recovery batch push via the daemon (one push = one new
      version); offline flag + persisted state; WARNING/INFO transitions.
  T3  conflict 409 -> pull + re-push local-wins (local content wins, remote
      version increments, nothing silently dropped, no spurious re-push).
  T4  is_connectivity_error classification (ConnectError=offline, 5xx=offline,
      409=NOT offline).
"""

import base64
import hashlib
import http.server
import json
import logging
import os
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest

import parad.config as _cfg
import parad.connection as pc
from parad import state as st
from parad.connection import connect, SyncDaemon, db_state_key
from parad.engine import Engine
from parad.gateway import GatewayError, is_connectivity_error

PASSPHRASE = "secret"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class FastSyncDaemon(SyncDaemon):
    PUSH_INTERVAL = 0.5
    PULL_INTERVAL = 2.0


class Store:
    def __init__(self):
        self.projects = []
        self.databases = []
        self.login_calls = 0
        self.upload_calls = 0
        self._p = 0
        self._d = 0

    def project_by_name(self, name):
        return next((p for p in self.projects if p["name"] == name), None)

    def create_project(self, name):
        self._p += 1
        p = {"id": f"p-{self._p}", "name": name}
        self.projects.append(p)
        return p

    def databases_in(self, pid):
        return [d for d in self.databases if d["project_id"] == pid]

    def db_by_name_in(self, pid, name):
        return next(
            (d for d in self.databases_in(pid) if d["name"] == name), None
        )

    def create_database(self, pid, name):
        self._d += 1
        d = {
            "id": f"d-{self._d}",
            "name": name,
            "project_id": pid,
            "versions": {},
            "latest": 0,
        }
        self.databases.append(d)
        return d

    def db_by_id(self, did):
        return next((d for d in self.databases if d["id"] == did), None)

    def db_by_name(self, name):
        return next((d for d in self.databases if d["name"] == name), None)


class Handler(http.server.BaseHTTPRequestHandler):
    store: Store = None
    server_down = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, headers=None):
        if isinstance(body, bytes):
            data = body
            ctype = "application/octet-stream"
        else:
            data = json.dumps(body).encode()
            ctype = "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _req_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _db(self, qs, body=None):
        b = body or {}
        did = b.get("database_id") or qs.get("database_id", [""])[0]
        dname = b.get("database_name") or qs.get("database_name", [""])[0]
        return (
            self.store.db_by_id(did)
            if did
            else (self.store.db_by_name(dname) if dname else None)
        )

    def do_GET(self):
        if self.server_down:
            return self._send(503, {"detail": "offline"})
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        s = self.store
        if path == "/v1/auth/me":
            self._send(
                200,
                {"id": "u1", "username": "alice", "email": "alice@example.com"},
            )
        elif path == "/v1/projects":
            self._send(200, s.projects)
        elif path.startswith("/v1/projects/") and path.endswith("/databases"):
            self._send(200, s.databases_in(path.split("/")[3]))
        elif path == "/v1/status":
            dbs = [
                {
                    "name": d["name"],
                    "latest_version": d["latest"],
                    "latest_message_id": f"m{d['latest']}",
                }
                for d in s.databases
            ]
            self._send(200, {"user_id": "u1", "databases": dbs})
        elif path == "/v1/download":
            d = self._db(qs)
            if not d:
                return self._send(404, {"detail": "not found"})
            ver = int(qs.get("version", [str(d["latest"])])[0])
            payload = d["versions"].get(ver)
            if payload is None:
                return self._send(404, {"detail": "version not found"})
            self._send(
                200,
                payload,
                {"x-version": str(ver), "x-message-id": f"m{ver}"},
            )
        else:
            self._send(404, {"detail": "no route"})

    def do_POST(self):
        if self.server_down:
            return self._send(503, {"detail": "offline"})
        path = urllib.parse.urlparse(self.path).path
        s = self.store
        body = self._req_body()
        if path == "/v1/auth/login":
            s.login_calls += 1
            self._send(
                200,
                {
                    "access_token": "tok-abc",
                    "token_type": "bearer",
                    "username": body.get("email", ""),
                    "user_id": "u1",
                },
            )
        elif path == "/v1/auth/register":
            self._send(
                200,
                {
                    "access_token": "tok-abc",
                    "user_id": "u1",
                    "username": body.get("username", ""),
                    "email": body.get("email", ""),
                },
            )
        elif path == "/v1/projects":
            self._send(201, s.create_project(body["name"]))
        elif path.startswith("/v1/projects/") and path.endswith("/databases"):
            self._send(201, s.create_database(path.split("/")[3], body["name"]))
        elif path == "/v1/upload":
            s.upload_calls += 1
            d = self._db({}, body)
            if not d:
                return self._send(404, {"detail": "database not found"})
            client_ver = int(body.get("version", 0) or 0)
            if client_ver < d["latest"]:
                return self._send(
                    409,
                    {"error": "conflict_detected", "remote_version": d["latest"]},
                )
            payload = base64.b64decode(body["file_data"])
            new_ver = d["latest"] + 1
            d["versions"][new_ver] = payload
            d["latest"] = new_ver
            self._send(
                200,
                {
                    "request_id": f"r{new_ver}",
                    "database_id": d["id"],
                    "message_id": f"m{new_ver}",
                    "version": new_ver,
                    "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        else:
            self._send(404, {"detail": "no route"})


class Gateway:
    def __init__(self, store):
        self.store = store
        self.httpd = None
        self.port = 0
        self.thread = None

    def start(self, port=None):
        Handler.store = self.store
        Handler.server_down = False
        self.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port or 0), Handler
        )
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def wait_for(pred, timeout=25.0, step=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def sqlite_blob(path, passphrase, rows):
    eng = Engine(str(path), passphrase)
    eng.open(create=True)
    eng.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    for r in rows:
        eng.execute("INSERT INTO t VALUES (?)", (r,))
    blob = eng.get_raw_bytes()
    eng.close()
    return blob


@pytest.fixture
def log_capture():
    records = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = ListHandler()
    logger = logging.getLogger("parad.connection")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    yield records
    logger.setLevel(old_level)
    logger.removeHandler(handler)


def test_connect_email_password_provisioning(paradox_home):
    store = Store()
    gw = Gateway(store)
    base = gw.start()
    url = (
        f"parad://alice@example.com:secretpw@local/myproj/mydb"
        f"?passphrase={PASSPHRASE}&gateway={base}"
    )
    try:
        conn = connect(url=url, auto_sync=False)
        cfg = _cfg.load_config()
        assert store.login_calls >= 1, "login should hit the gateway"
        assert cfg.sync.api_key == "tok-abc", f"got {cfg.sync.api_key!r}"
        assert store.project_by_name("myproj") is not None
        assert cfg.project_name == "myproj"
        assert cfg.project_id.startswith("p-")
        assert cfg.database_id.startswith("d-")
        assert store.db_by_name_in(cfg.project_id, "mydb") is not None

        state_file = (
            _cfg.config_dir()
            / f"{st.sanitize_state_key(db_state_key('mydb', 'myproj'))}.sync.json"
        )
        st.mark_dirty(conn._db_key)
        assert state_file.exists(), str(state_file.name)
        assert not (_cfg.config_dir() / "mydb.sync.json").exists()
        assert conn._db_key == "myproj/mydb"
        conn.close()
    finally:
        gw.stop()


def test_offline_batch_push_recovery(paradox_home, log_capture, monkeypatch):
    store = Store()
    gw = Gateway(store)
    base = gw.start()
    monkeypatch.setattr(pc, "SyncDaemon", FastSyncDaemon)
    url = (
        f"parad://tok-abc@local/offproj/offdb"
        f"?passphrase={PASSPHRASE}&gateway={base}"
    )
    conn = None
    try:
        conn = connect(url=url, auto_sync=True)
        d = conn._daemon
        key = conn._db_key

        assert wait_for(
            lambda: store.db_by_name("offdb")
            and store.db_by_name("offdb")["latest"] >= 1
        ), "initial push should reach the gateway"

        gw.stop()
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('offline-change')")
        conn.commit()

        assert wait_for(lambda: d.offline is True, timeout=20), (
            f"daemon should go offline (offline={d.offline} "
            f"failures={d.consecutive_failures})"
        )
        assert st.is_offline(key) is True
        assert st.is_dirty(key) is True
        assert bool(d.last_error), str(d.last_error)[:60]
        assert any("Sync offline" in r for r in log_capture)

        uploads_before = store.upload_calls
        gw.start(port=gw.port)

        def recovered():
            db = store.db_by_name("offdb")
            return (
                (not d.offline)
                and db is not None
                and db["latest"] == 2
                and any("Sync back online" in r for r in log_capture)
            )

        assert wait_for(recovered), (
            f"recovery push should complete (latest={store.db_by_name('offdb')['latest']})"
        )

        remote = store.db_by_name("offdb")["versions"][2].decode(
            "utf-8", "replace"
        )
        local = conn.engine.get_raw_bytes().decode("utf-8", "replace")
        assert store.upload_calls - uploads_before == 1, (
            f"one push = one new version "
            f"(uploads delta={store.upload_calls - uploads_before})"
        )
        assert "offline-change" in remote
        assert st.is_offline(key) is False
        assert any("Sync back online" in r for r in log_capture)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        gw.stop()


def test_conflict_local_wins(paradox_home):
    store = Store()
    gw = Gateway(store)
    base = gw.start()
    local_path = Path(paradox_home) / "conflict_local.db"
    key = None
    try:
        proj = store.create_project("confproj")
        db = store.create_database(proj["id"], "conflict")
        remote_tmp = Path(paradox_home) / "conflict_remote_tmp.db"
        remote_bytes = sqlite_blob(remote_tmp, PASSPHRASE, ["remote-data"])
        remote_tmp.unlink(missing_ok=True)
        db["versions"][1] = remote_bytes
        db["latest"] = 1

        local_bytes = sqlite_blob(local_path, PASSPHRASE, ["local-data"])
        key = db_state_key("conflict", "confproj")
        engine = Engine(str(local_path), PASSPHRASE)
        engine.open()

        daemon = FastSyncDaemon(
            engine=engine,
            db_name="conflict",
            gateway_url=base,
            api_key="tok-abc",
            project="confproj",
            database_id=db["id"],
            project_id=proj["id"],
        )
        daemon.start()
        try:
            assert wait_for(
                lambda: store.db_by_name("conflict")["latest"] == 2, timeout=25
            ), f"conflict should resolve to v2 (latest={store.db_by_name('conflict')['latest']})"

            assert wait_for(
                lambda: st.get_last_local_hash(key) == sha(local_bytes),
                timeout=10,
            ), f"resolution should persist (stored={st.get_last_local_hash(key)})"
            assert store.db_by_name("conflict")["versions"][2] == local_bytes
            assert store.db_by_name("conflict")["versions"][1] == remote_bytes
            assert engine.get_raw_bytes() == local_bytes
            assert daemon.offline is False and st.is_offline(key) is False
            time.sleep(2.5)
            assert store.upload_calls == 2, f"uploads={store.upload_calls}"
        finally:
            daemon.stop()
            engine.close()
    finally:
        gw.stop()


def test_connectivity_classification():
    with pytest.raises(Exception) as exc:
        httpx.get("http://127.0.0.1:1/nope", timeout=2.0)
    assert is_connectivity_error(exc.value) is True
    assert is_connectivity_error(GatewayError(503, "x")) is True
    assert is_connectivity_error(GatewayError(409, "conflict")) is False


def test_manual_push_pull_creates_versions_and_reverts_local(paradox_home):
    store = Store()
    gw = Gateway(store)
    base = gw.start()
    local_path = Path(paradox_home) / "manual.db"
    url = (
        f"parad://tok-abc@local/manualproj/manual"
        f"?passphrase={PASSPHRASE}&gateway={base}"
    )
    conn = None
    try:
        conn = connect(url=url, db_path=str(local_path), auto_sync=False)
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", ("one",))
        assert conn.push() == 1, "first push should create v1"
        conn.execute("INSERT INTO t VALUES (?)", ("two",))
        assert conn.push() == 2, "second push should create v2"
        assert store.db_by_name("manual")["latest"] == 2

        from parad.gateway import GatewayClient

        result = GatewayClient(base, "tok-abc").download(
            database_name="manual", version=1
        )
        assert result.version == 1
        conn._apply_local(result.bytes)
        rows = conn.execute("SELECT v FROM t")
        assert rows == [{"v": "one"}], f"local rows after revert: {rows}"
    finally:
        if conn is not None:
            conn.close()
        gw.stop()


def test_manual_rollback_pull_interop(paradox_home):
    store = Store()
    gw = Gateway(store)
    base = gw.start()
    local_path = Path(paradox_home) / "rollback.db"
    url = (
        f"parad://tok-abc@local/rollproj/rollback"
        f"?passphrase={PASSPHRASE}&gateway={base}"
    )
    conn = None
    try:
        conn = connect(url=url, db_path=str(local_path), auto_sync=False)
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", ("one",))
        assert conn.push() == 1
        conn.execute("INSERT INTO t VALUES (?)", ("two",))
        assert conn.push() == 2
        db = store.db_by_name("rollback")
        assert db["latest"] == 2

        db["versions"][db["latest"] + 1] = db["versions"][1]
        db["latest"] += 1

        assert conn.pull() is True
        assert db["latest"] == 3
        rows = conn.execute("SELECT v FROM t")
        assert rows == [{"v": "one"}], f"local rows after rollback: {rows}"
    finally:
        if conn is not None:
            conn.close()
        gw.stop()
