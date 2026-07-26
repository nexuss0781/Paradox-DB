"""Core SDK — DB-API 2.0 inspired connection with automatic cloud sync."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from parad.config import CONFIG_DIR, gateway_db_name, load_config
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad import state as sync_state

logger = logging.getLogger("parad.connection")

__all__ = ["connect", "ParadConnection", "SyncDaemon", "parse_url", "generate_url"]

# ── URL helpers ─────────────────────────────────────────────────


def parse_url(url: str) -> dict:
    """Parse ``parad://local/dbname?passphrase=secret`` into parts.

    Returns a dict with keys ``name``, ``passphrase``, and
    ``gateway_url`` (empty string when absent).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("parad", "paradox"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    name = parsed.path.strip("/")
    if not name:
        raise ValueError("URL must contain a database name in the path")

    qs = parse_qs(parsed.query)
    passphrase = qs.get("passphrase", [""])[0]
    gateway_url = qs.get("gateway", [""])[0]

    return {"name": name, "passphrase": passphrase, "gateway_url": gateway_url}


def generate_url(name: str, passphrase: str, gateway_url: str = "") -> str:
    """Generate a ``parad://`` connection URL."""
    url = f"parad://local/{name}?passphrase={passphrase}"
    if gateway_url:
        url += f"&gateway={gateway_url}"
    return url


# ── Sync daemon ─────────────────────────────────────────────────


class SyncDaemon:
    """Background thread that keeps the local encrypted DB in sync with the
    gateway.

    * Polls the local file hash every *push_interval* seconds and pushes
      when it changes.
    * Pulls from the gateway every *pull_interval* seconds.
    * All exceptions are caught so the daemon never crashes the host thread.

    .. warning::

       The daemon is designed for CLI / single-process use.  In a web server
       (FastAPI, Flask, etc.) use ``auto_sync=False`` and call
       :meth:`ParadConnection.push` / :meth:`ParadConnection.pull` manually.
    """

    PUSH_INTERVAL = 2.0
    PULL_INTERVAL = 30.0

    def __init__(
        self,
        engine: Engine,
        db_name: str,
        gateway_url: str,
        api_key: str = "",
    ):
        self._engine = engine
        self._db_name = db_name
        self._gateway = GatewayClient(gateway_url, api_key)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_sync: float | None = None

    # ── public API ──────────────────────────────────────────────

    def start(self):
        """Start the background sync thread (daemon mode)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"parad-sync-{self._db_name}", daemon=True
        )
        self._thread.start()

    def stop(self):
        """Gracefully stop the background thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internal loop ───────────────────────────────────────────

    def _run(self):
        push_counter = 0.0
        pull_counter = 0.0
        while not self._stop_event.is_set():
            try:
                push_counter += 0.5
                pull_counter += 0.5

                if push_counter >= self.PUSH_INTERVAL:
                    push_counter = 0.0
                    self._maybe_push()

                if pull_counter >= self.PULL_INTERVAL:
                    pull_counter = 0.0
                    self._maybe_pull()

            except Exception:
                logger.debug("sync iteration error", exc_info=True)

            self._stop_event.wait(0.5)

    def _file_hash(self) -> str:
        try:
            raw = self._engine.get_raw_bytes()
            return hashlib.sha256(raw).hexdigest()
        except Exception:
            return ""

    def _maybe_push(self):
        current_hash = self._file_hash()
        if not current_hash:
            return
        last_hash = sync_state.get_last_local_hash(self._db_name)
        if current_hash == last_hash:
            return
        self._push(current_hash)

    def _push(self, current_hash: str):
        try:
            with self._lock:
                raw = self._engine.get_raw_bytes()
            remote_ver = sync_state.get_remote_version(self._db_name) or 0
            resp = self._gateway.upload(
                database_name=self._db_name,
                file_bytes=raw,
                version=remote_ver,
            )
            sync_state.set_remote_version(
                self._db_name, resp.version, current_hash
            )
            sync_state.set_last_local_hash(self._db_name, current_hash)
            self.last_sync = time.time()
        except GatewayError as exc:
            if exc.status_code == 409:
                self._pull()
                try:
                    with self._lock:
                        raw = self._engine.get_raw_bytes()
                    remote_ver = sync_state.get_remote_version(self._db_name) or 0
                    resp = self._gateway.upload(
                        database_name=self._db_name,
                        file_bytes=raw,
                        version=remote_ver,
                    )
                    sync_state.set_remote_version(
                        self._db_name, resp.version, current_hash
                    )
                    sync_state.set_last_local_hash(self._db_name, current_hash)
                    self.last_sync = time.time()
                except Exception:
                    logger.debug("retry push after conflict failed", exc_info=True)
            else:
                logger.debug("push failed: %s", exc)
        except Exception:
            logger.debug("push failed", exc_info=True)

    def _maybe_pull(self):
        self._pull()

    def _pull(self):
        """Pull from gateway and replace the local encrypted file.

        Uses a lock to avoid racing with concurrent engine access.  The
        engine is *not* closed/reopened — the on-disk encrypted file is
        replaced and the next ``engine.close()`` will re-encrypt from the
        current temp file.  On restart the fresh encrypted file is picked up.
        """
        try:
            result = self._gateway.download(self._db_name)
            if not result.bytes:
                return
            remote_ver = result.version
            remote_hash = hashlib.sha256(result.bytes).hexdigest()
            current_hash = self._file_hash()
            if remote_hash == current_hash:
                return

            # Encrypt and replace the on-disk file (don't close engine)
            from parad.crypto import encrypt_file

            encrypted = encrypt_file(result.bytes, self._engine.passphrase)
            with self._lock:
                self._engine.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._engine.db_path.write_bytes(encrypted)

            if remote_ver is not None:
                sync_state.set_remote_version(
                    self._db_name, remote_ver, remote_hash
                )
            sync_state.set_last_local_hash(self._db_name, remote_hash)

            self.last_sync = time.time()
        except Exception:
            logger.debug("pull failed", exc_info=True)


# ── Cursor wrapper ──────────────────────────────────────────────


class _Cursor:
    """Minimal DB-API 2.0 inspired cursor returned by ``ParadConnection.cursor()``."""

    def __init__(self, conn: "ParadConnection"):
        self._conn = conn
        self.description: list[tuple] | None = None
        self.rowcount: int = -1
        self._rows: list[dict] = []

    def execute(self, sql: str, params: tuple | list | None = None):
        self._rows = self._conn.execute(sql, params)
        self.rowcount = len(self._rows) if self._rows else 0
        return self

    def fetchall(self) -> list[dict]:
        return list(self._rows)

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


# ── Connection ──────────────────────────────────────────────────


class ParadConnection:
    """DB-API 2.0 inspired connection wrapper with transparent cloud sync.

    Wraps :class:`Engine` for encrypted SQLite operations and optionally
    runs a :class:`SyncDaemon` to keep the database synchronised with a
    remote gateway.

    For web servers (FastAPI, Flask, etc.) use ``auto_sync=False`` and call
    :meth:`push` / :meth:`pull` manually at startup and shutdown.
    """

    def __init__(
        self,
        db_path: str,
        passphrase: str,
        gateway_url: str = "",
        api_key: str = "",
        auto_sync: bool = True,
    ):
        self._db_path = str(Path(db_path).expanduser())
        self._passphrase = passphrase
        self._gateway_url = gateway_url
        self._api_key = api_key
        self._db_name = gateway_db_name(self._db_path) if self._gateway_url else ""

        self._engine = Engine(self._db_path, self._passphrase)
        self._engine.open(create=True)

        self._daemon: SyncDaemon | None = None
        if auto_sync and self._gateway_url:
            self._daemon = SyncDaemon(
                engine=self._engine,
                db_name=self._db_name,
                gateway_url=self._gateway_url,
                api_key=self._api_key,
            )
            self._daemon.start()

    # ── properties ──────────────────────────────────────────────

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def is_connected(self) -> bool:
        return self._engine._conn is not None

    # ── SQL interface ───────────────────────────────────────────

    def execute(self, sql: str, params: tuple | list | None = None) -> list[dict]:
        return self._engine.execute(sql, tuple(params) if params else ())

    def executescript(self, sql: str):
        if not self._engine._conn:
            raise RuntimeError("Database not open")
        self._engine._conn.executescript(sql)

    def commit(self):
        if self._engine._conn:
            self._engine._conn.commit()

    def rollback(self):
        if self._engine._conn:
            self._engine._conn.rollback()

    def close(self):
        if self._daemon is not None:
            self._daemon.stop()
            self._daemon = None
        self._engine.close()

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def table_info(self, table_name: str) -> list[dict]:
        return self._engine.table_info(table_name)

    def tables(self) -> list[str]:
        return self._engine.list_tables()

    # ── manual sync (for web servers) ───────────────────────────

    def push(self) -> int | None:
        """Push the local database to the gateway.

        Returns the remote version number, or ``None`` if no gateway is
        configured.  Safe to call from any context — no background threads.
        """
        if not self._gateway_url:
            return None
        gw = GatewayClient(self._gateway_url, self._api_key)
        raw = self._engine.get_raw_bytes()
        remote_ver = sync_state.get_remote_version(self._db_name) or 0
        resp = gw.upload(
            database_name=self._db_name,
            file_bytes=raw,
            version=remote_ver,
        )
        current_hash = hashlib.sha256(raw).hexdigest()
        sync_state.set_remote_version(self._db_name, resp.version, current_hash)
        sync_state.set_last_local_hash(self._db_name, current_hash)
        return resp.version

    def pull(self) -> bool:
        """Pull the latest version from the gateway, replacing the local file.

        Returns ``True`` if new data was downloaded, ``False`` if already
        up-to-date or no gateway is configured.  Safe to call from any
        context — no background threads.

        After pulling, the on-disk encrypted file is replaced.  The caller
        should re-open the connection or call :meth:`close` and re-connect
        to pick up the new data in the temp file.
        """
        if not self._gateway_url:
            return False
        gw = GatewayClient(self._gateway_url, self._api_key)
        result = gw.download(self._db_name)
        if not result.bytes:
            return False

        remote_hash = hashlib.sha256(result.bytes).hexdigest()
        current_hash = ""
        try:
            current_hash = hashlib.sha256(self._engine.get_raw_bytes()).hexdigest()
        except Exception:
            pass
        if remote_hash == current_hash:
            return False

        from parad.crypto import encrypt_file

        encrypted = encrypt_file(result.bytes, self._passphrase)
        self._engine.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine.db_path.write_bytes(encrypted)

        if result.version is not None:
            sync_state.set_remote_version(self._db_name, result.version, remote_hash)
        sync_state.set_last_local_hash(self._db_name, remote_hash)

        # Reopen engine so temp file reflects the new data
        self._engine.close()
        self._engine.open()

        return True

    # ── context manager ─────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ── Convenience factory ─────────────────────────────────────────


def connect(
    name: str | None = None,
    passphrase: str | None = None,
    url: str | None = None,
    db_path: str | None = None,
    gateway_url: str | None = None,
    auto_sync: bool = True,
    pull_on_startup: bool = False,
) -> ParadConnection:
    """Connect to a Parad database.

    Usage::

        # Local only
        db = connect("mydb", passphrase="secret", auto_sync=False)

        # With cloud sync (CLI / desktop)
        db = connect("mydb", passphrase="secret")

        # Web server (Render, etc.)
        db = connect("chatdb", passphrase=os.environ["PARADOX_PASSPHRASE"],
                      auto_sync=False, pull_on_startup=True)

        # From connection string
        db = connect(url="parad://local/mydb?passphrase=secret")
        db = connect(url=os.environ["DATABASE_URL"])

    Resolution order:

    1. If *url* is provided, parse it for name / passphrase / gateway_url.
    2. If *name* is provided, derive *db_path* from ``~/.paradox/{name}.db``.
    3. If *db_path* is provided, use it directly.
    4. If no positional hints, fall back to config defaults.
    5. Passphrase: explicit > parsed from URL > ``PARADOX_PASSPHRASE`` env > config > ``"default"``.
    6. Gateway: explicit > parsed from URL > config > empty (no sync).

    Parameters:

    - **pull_on_startup**: If ``True`` and a gateway is configured, download
      the latest version from the gateway before returning.  Useful for
      web servers on ephemeral filesystems (Render, Heroku, etc.) where the
      local file may not exist or may be stale.
    """
    cfg = load_config()
    parsed_url: dict = {}

    if url:
        parsed_url = parse_url(url)

    # ── resolve db_path ─────────────────────────────────────────
    resolved_path = db_path
    if resolved_path is None and name:
        resolved_path = str(CONFIG_DIR / f"{name}.db")
    if resolved_path is None:
        resolved_path = cfg.database_path

    # ── resolve passphrase ──────────────────────────────────────
    resolved_passphrase = passphrase
    if not resolved_passphrase:
        resolved_passphrase = parsed_url.get("passphrase", "")
    if not resolved_passphrase:
        resolved_passphrase = os.environ.get("PARADOX_PASSPHRASE", "")
    if not resolved_passphrase:
        resolved_passphrase = "default"

    # ── resolve gateway_url ─────────────────────────────────────
    resolved_gateway = gateway_url
    if not resolved_gateway:
        resolved_gateway = parsed_url.get("gateway_url", "")
    if not resolved_gateway:
        resolved_gateway = cfg.sync.gateway_url or ""

    # ── api_key from config if gateway is set ───────────────────
    api_key = cfg.sync.api_key if resolved_gateway else ""

    conn = ParadConnection(
        db_path=resolved_path,
        passphrase=resolved_passphrase,
        gateway_url=resolved_gateway,
        api_key=api_key,
        auto_sync=auto_sync and bool(resolved_gateway),
    )

    # ── pull on startup ─────────────────────────────────────────
    if pull_on_startup and resolved_gateway:
        try:
            conn.pull()
        except Exception:
            logger.debug("pull_on_startup failed", exc_info=True)

    return conn
