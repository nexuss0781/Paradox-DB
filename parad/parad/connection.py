"""Core SDK — DB-API 2.0 inspired connection with automatic cloud sync."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import parad.config as _config
from parad.config import gateway_db_name, load_config, save_config, set_config_value
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError, is_connectivity_error
from parad import state as sync_state

logger = logging.getLogger("parad.connection")

__all__ = ["connect", "ParadConnection", "SyncDaemon", "parse_url", "generate_url", "db_state_key"]

# ── URL helpers ─────────────────────────────────────────────────


def parse_url(url: str) -> dict:
    """Parse a postgres-like ``parad://`` connection string.

    Supported forms::

        parad://local/{name}?passphrase=...                                    # local only
        parad://local/{project}/{name}?passphrase=...&gateway=...              # project scoped
        parad://local/{project}/{name}?passphrase=...&gateway=...&token=<jwt>  # explicit token
        parad://{email}:{password}@local/{project}/{name}?passphrase=...       # auto-login
        parad://{token}@local/{project}/{name}?passphrase=...                  # userinfo token

    Returns a dict with keys ``name``, ``project``, ``passphrase``,
    ``gateway_url``, ``token``, ``email``, ``password``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("parad", "paradox"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    qs = parse_qs(parsed.query)
    passphrase = qs.get("passphrase", [""])[0]
    gateway_url = qs.get("gateway", [""])[0]
    token = qs.get("token", [""])[0]

    email = ""
    password = ""
    if parsed.username is not None:
        username = unquote(parsed.username)
        if parsed.password is not None:
            password = unquote(parsed.password)
        if password or "@" in username:
            email = username
        elif not token:
            token = username

    parts = parsed.path.strip("/").split("/")
    name = parts[-1] if parts else ""
    if not name:
        raise ValueError("URL must contain a database name in the path")
    project = "/".join(parts[:-1]) or None

    return {
        "name": name,
        "project": project,
        "passphrase": passphrase,
        "gateway_url": gateway_url,
        "token": token,
        "email": email,
        "password": password,
    }


def generate_url(
    name: str,
    passphrase: str = "",
    gateway_url: str = "",
    project: str | None = None,
    token: str = "",
    email: str = "",
    password: str = "",
) -> str:
    """Generate a ``parad://`` connection URL (postgres-like)."""
    userinfo = ""
    if email and password:
        userinfo = f"{quote(email, safe='@')}:{quote(password, safe='')}@"
    elif token:
        userinfo = f"{quote(token, safe='')}@"
    path = f"local/{project}/{name}" if project else f"local/{name}"
    url = f"parad://{userinfo}{path}"
    qs = []
    if passphrase:
        qs.append(f"passphrase={quote(passphrase, safe='')}")
    if gateway_url:
        qs.append(f"gateway={quote(gateway_url, safe=':/')}")
    if token and email and password:
        qs.append(f"token={quote(token, safe='')}")
    if qs:
        url += "?" + "&".join(qs)
    return url


def db_state_key(name: str, project: str | None = None) -> str:
    """State-file key for a database.

    Project-scoped databases key state as ``"{project}/{name}"``
    (``sanitize_state_key`` flattens this to ``{project}__{name}`` →
    ``~/.paradox/{project}__{name}.sync.json``); legacy project-less
    databases key by the bare ``name``.
    """
    if project:
        return f"{project}/{name}"
    return name


# ── Sync daemon ─────────────────────────────────────────────────


class SyncDaemon:
    """Background thread that keeps the local encrypted DB in sync with the
    gateway.

    * Polls the local file hash every *push_interval* seconds and pushes
      when it changes.
    * Pulls from the gateway every *pull_interval* seconds.
    * All exceptions are caught so the daemon never crashes the host thread.

    Conflict policy is **local-wins**: on a 409 the remote snapshot is
    pulled (so the base version advances) and the local bytes are then
    re-pushed as a brand new version.  Local writes are never dropped.

    Offline handling: network / 5xx failures flip the offline flag (a
    single WARNING on the transition, INFO on recovery).  ``last_error``
    and ``consecutive_failures`` stay readable so callers can surface
    sync health.  409 conflicts never count as offline.

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
        project: str | None = None,
        database_id: str = "",
        project_id: str = "",
        storage_channel: str = "",
        log_channel: str = "",
    ):
        self._engine = engine
        self._db_name = db_name
        self._db_key = db_state_key(db_name, project)
        self._database_id = database_id
        self._project_id = project_id
        self._storage_channel = storage_channel
        self._log_channel = log_channel
        self._gateway = GatewayClient(gateway_url, api_key)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_sync: float | None = None

        # offline / failure tracking (defect C)
        self._offline = bool(sync_state.is_offline(self._db_key))
        self._consecutive_failures = 0
        self._last_error: str | None = None

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

    # ── health / offline exposure ───────────────────────────────

    @property
    def offline(self) -> bool:
        """True while the daemon believes it cannot reach the gateway."""
        return self._offline

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ── failure bookkeeping ─────────────────────────────────────

    def _on_success(self):
        was_offline = self._offline
        self._offline = False
        self._consecutive_failures = 0
        self._last_error = None
        sync_state.set_offline(self._db_key, False)
        if was_offline:
            logger.info(
                "Sync back online for %s — pushing pending changes", self._db_key
            )

    def _on_failure(self, exc: Exception, conflict: bool = False):
        if conflict:
            # conflicts are handled by the local-wins retry, not a
            # connectivity failure
            return
        self._last_error = str(exc)
        if is_connectivity_error(exc):
            self._consecutive_failures += 1
            was_offline = self._offline
            self._offline = True
            sync_state.set_offline(self._db_key, True)
            sync_state.mark_dirty(self._db_key)
            if not was_offline:
                logger.warning("Sync offline for %s: %s", self._db_key, exc)
            else:
                logger.debug(
                    "Still offline for %s (%d consecutive failures)",
                    self._db_key,
                    self._consecutive_failures,
                )
        else:
            logger.debug("sync failure for %s: %s", self._db_key, exc)

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

            except Exception as exc:
                self._on_failure(exc)

            self._stop_event.wait(0.5)

    def _file_hash(self) -> str:
        try:
            with self._lock:
                raw = self._engine.get_raw_bytes()
            return hashlib.sha256(raw).hexdigest()
        except Exception:
            return ""

    def _replace_local_locked(self, raw: bytes):
        """Replace the on-disk encrypted DB with *raw* and refresh the engine.

        Order matters: the engine is closed *first* — its stale temp would
        otherwise be re-encrypted over the new file on ``close()`` (defect A).
        Then the new encrypted file is written, then the engine is reopened
        so its temp reflects *raw*.  Callers take ``self._lock``.
        """
        from parad.crypto import encrypt_file

        encrypted = encrypt_file(raw, self._engine.passphrase)
        with self._lock:
            self._engine.close()
            self._engine.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._engine.db_path.write_bytes(encrypted)
            self._engine.open()

    def _maybe_push(self):
        current_hash = self._file_hash()
        if not current_hash:
            return
        last_hash = sync_state.get_last_local_hash(self._db_key)
        if current_hash == last_hash:
            return
        self._push(current_hash)

    def _push(self, current_hash: str):
        try:
            with self._lock:
                raw = self._engine.get_raw_bytes()
            remote_ver = sync_state.get_remote_version(self._db_key) or 0
            resp = self._gateway.upload(
                database_name=self._db_name,
                database_id=self._database_id,
                project_id=self._project_id,
                file_bytes=raw,
                version=remote_ver,
                storage_chat_id=self._storage_channel,
                log_chat_id=self._log_channel,
            )
            self._on_success()
            sync_state.set_remote_version(self._db_key, resp.version, current_hash)
            sync_state.set_last_local_hash(self._db_key, current_hash)
            sync_state.clear_dirty(self._db_key)
            self.last_sync = time.time()
            logger.info("Pushed %s v%s (msg=%s)", self._db_key, resp.version, resp.message_id)
        except GatewayError as exc:
            if exc.status_code == 409:
                # LOCAL-WINS: capture our bytes, pull the remote snapshot
                # (advances the base version), then re-push OUR bytes as a
                # brand new version.  Local writes are never silently lost.
                logger.info(
                    "Conflict (409) on %s — pulling remote, re-pushing local (local-wins)",
                    self._db_key,
                )
                local_raw = raw
                try:
                    self._pull()
                    remote_ver = sync_state.get_remote_version(self._db_key) or 0
                    resp = self._gateway.upload(
                        database_name=self._db_name,
                        database_id=self._database_id,
                        project_id=self._project_id,
                        file_bytes=local_raw,
                        version=remote_ver,
                        storage_chat_id=self._storage_channel,
                        log_chat_id=self._log_channel,
                    )
                    # our bytes won — persist them locally so engine/disk
                    # reflect what was pushed (avoids a spurious re-push)
                    self._replace_local_locked(local_raw)
                    self._on_success()
                    sync_state.set_remote_version(self._db_key, resp.version, current_hash)
                    sync_state.set_last_local_hash(self._db_key, current_hash)
                    sync_state.clear_dirty(self._db_key)
                    self.last_sync = time.time()
                    logger.info(
                        "Local-wins re-push for %s: v%s (msg=%s)",
                        self._db_key,
                        resp.version,
                        resp.message_id,
                    )
                except Exception as exc2:
                    self._on_failure(exc2, conflict=True)
                    sync_state.mark_dirty(self._db_key)
            else:
                self._on_failure(exc)
        except Exception as exc:
            self._on_failure(exc)

    def _maybe_pull(self):
        self._pull()

    def _pull(self) -> bool:
        """Pull the latest remote snapshot and replace the local file.

        Returns ``True`` when the local file was updated (or is already
        current); ``False`` on failure (the caller handles offline state).
        """
        try:
            result = self._gateway.download(
                database_name=self._db_name,
                database_id=self._database_id,
                project_id=self._project_id,
                storage_chat_id=self._storage_channel,
            )
        except Exception as exc:
            self._on_failure(exc)
            return False
        if not result.bytes:
            return True
        remote_ver = result.version
        remote_hash = hashlib.sha256(result.bytes).hexdigest()
        current_hash = self._file_hash()
        if remote_hash == current_hash:
            return True

        self._replace_local_locked(result.bytes)
        if remote_ver is not None:
            sync_state.set_remote_version(self._db_key, remote_ver, remote_hash)
        sync_state.set_last_local_hash(self._db_key, remote_hash)
        sync_state.clear_dirty(self._db_key)
        self._on_success()
        self.last_sync = time.time()
        logger.info("Pulled %s v%s (%d bytes)", self._db_key, remote_ver, len(result.bytes))
        return True


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
        project: str | None = None,
        database_id: str = "",
        project_id: str = "",
        pull_on_startup: bool = False,
        storage_channel: str = "",
        log_channel: str = "",
    ):
        self._db_path = str(Path(db_path).expanduser())
        self._passphrase = passphrase
        self._gateway_url = gateway_url
        self._api_key = api_key
        self._project = project
        self._database_id = database_id
        self._project_id = project_id
        self._storage_channel = storage_channel
        self._log_channel = log_channel
        self._db_name = gateway_db_name(self._db_path) if self._gateway_url else ""
        self._db_key = db_state_key(self._db_name, project)

        self._engine = Engine(self._db_path, self._passphrase)
        self._engine.open(create=True)

        self._daemon: SyncDaemon | None = None
        if auto_sync and self._gateway_url:
            self._daemon = SyncDaemon(
                engine=self._engine,
                db_name=self._db_name,
                gateway_url=self._gateway_url,
                api_key=self._api_key,
                project=project,
                database_id=self._database_id,
                project_id=self._project_id,
                storage_channel=storage_channel,
                log_channel=log_channel,
            )
            # Pull BEFORE the daemon thread starts so the engine
            # close/reopen cannot race the background loop (defect D).
            if pull_on_startup:
                try:
                    self.pull()
                except Exception:
                    logger.debug("pull_on_startup failed", exc_info=True)
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
        """Push the local database to the gateway (local-wins on 409).

        Returns the remote version number, or ``None`` if no gateway is
        configured.  Safe to call from any context — no background threads.
        """
        if not self._gateway_url:
            return None
        gw = GatewayClient(self._gateway_url, self._api_key)
        raw = self._engine.get_raw_bytes()
        remote_ver = sync_state.get_remote_version(self._db_key) or 0
        try:
            resp = gw.upload(
                database_name=self._db_name,
                database_id=self._database_id,
                project_id=self._project_id,
                file_bytes=raw,
                version=remote_ver,
                storage_chat_id=self._storage_channel,
                log_chat_id=self._log_channel,
            )
        except GatewayError as exc:
            if exc.status_code != 409:
                raise
            local_raw = raw
            logger.info("Conflict (409) on %s — pulling remote, re-pushing local (local-wins)", self._db_key)
            self.pull()
            remote_ver = sync_state.get_remote_version(self._db_key) or 0
            resp = gw.upload(
                database_name=self._db_name,
                database_id=self._database_id,
                project_id=self._project_id,
                file_bytes=local_raw,
                version=remote_ver,
                storage_chat_id=self._storage_channel,
                log_chat_id=self._log_channel,
            )
            self._apply_local(local_raw)
            raw = local_raw
        current_hash = hashlib.sha256(raw).hexdigest()
        sync_state.set_remote_version(self._db_key, resp.version, current_hash)
        sync_state.set_last_local_hash(self._db_key, current_hash)
        sync_state.clear_dirty(self._db_key)
        return resp.version

    def _apply_local(self, raw: bytes):
        """Persist *raw* as the local DB (encrypt to disk + reopen engine)."""
        from parad.crypto import encrypt_file

        encrypted = encrypt_file(raw, self._passphrase)
        self._engine.close()
        self._engine.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine.db_path.write_bytes(encrypted)
        self._engine.open()

    def pull(self) -> bool:
        """Pull the latest version from the gateway, replacing the local file.

        Returns ``True`` if new data was downloaded, ``False`` if already
        up-to-date or no gateway is configured.  Safe to call from any
        context — no background threads.

        After pulling, the on-disk encrypted file is replaced and the
        engine is reopened so the live temp file reflects the new data.
        """
        if not self._gateway_url:
            return False
        gw = GatewayClient(self._gateway_url, self._api_key)
        result = gw.download(
            database_name=self._db_name,
            database_id=self._database_id,
            project_id=self._project_id,
            storage_chat_id=self._storage_channel,
        )
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

        # close FIRST so the stale temp cannot re-encrypt over the new file
        self._engine.close()
        encrypted = encrypt_file(result.bytes, self._passphrase)
        self._engine.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine.db_path.write_bytes(encrypted)
        self._engine.open()

        if result.version is not None:
            sync_state.set_remote_version(self._db_key, result.version, remote_hash)
        sync_state.set_last_local_hash(self._db_key, remote_hash)
        sync_state.clear_dirty(self._db_key)
        return True

    # ── context manager ─────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ── Convenience factory ─────────────────────────────────────────


def generate_passphrase() -> str:
    """Generate a cryptographically random 256-bit database passphrase."""
    return secrets.token_urlsafe(32)


def _announce_passphrase(passphrase: str, db_path: str) -> None:
    """Print a newly generated passphrase once and persist it in ~/.paradox/.env."""
    msg = (
        "[parad] Generated a new encryption passphrase for "
        f"'{db_path}': {passphrase}\n"
        "[parad] It was saved to ~/.paradox/config.json and ~/.paradox/.env. "
        "Keep it safe — it is NOT recoverable if lost, and it must match on "
        "every machine sharing this database.\n"
    )
    try:
        sys.stderr.write(msg)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        env_file = _config.config_dir() / ".env"
        line = f'export PARADOX_PASSPHRASE="{passphrase}"\n'
        if env_file.exists():
            content = env_file.read_text()
            if "PARADOX_PASSPHRASE=" not in content:
                env_file.write_text(content.rstrip() + "\n" + line)
        else:
            env_file.write_text("# parad auto-generated encryption passphrase\n" + line)
    except Exception:
        pass


def connect(
    name: str | None = None,
    passphrase: str | None = None,
    url: str | None = None,
    db_path: str | None = None,
    gateway_url: str | None = None,
    api_key: str | None = None,
    auto_sync: bool = True,
    pull_on_startup: bool = False,
    storage_channel: str | None = None,
    log_channel: str | None = None,
    allow_legacy_default: bool = False,
) -> ParadConnection:
    """Connect to a Parad database.

    Usage::

        # Local only
        db = connect("mydb", passphrase="secret", auto_sync=False)

        # With cloud sync (CLI / desktop)
        db = connect("mydb", passphrase="secret")

        # From connection string (postgres-like)
        db = connect(url="parad://local/proj/mydb?passphrase=secret&gateway=https://g/v1")
        db = connect(url=os.environ["DATABASE_URL"])

    Resolution order:

    1. If *url* is provided, parse it for name / project / passphrase /
       gateway_url / token / email / password.
    2. If *name* is provided, derive *db_path* from ``~/.paradox/{name}.db``.
    3. If *db_path* is provided, use it directly.
    4. If no positional hints, fall back to config defaults.
    5. Passphrase: explicit > parsed from URL > ``PARADOX_PASSPHRASE`` env >
       config > **auto-generated on first connect** (persisted to config +
       ``~/.paradox/.env``, announced at the CLI). Existing DB files with no
       configured passphrase keep the legacy ``"default"`` so they stay
       readable.
    6. Gateway: explicit > parsed from URL > config.
    7. Auth token: explicit ``api_key`` arg > URL ``token`` param > userinfo
       token > ``email:password`` (auto-login) > config ``sync.api_key``.

    Project / database are auto-provisioned on the gateway (created if
    missing) whenever a project name is present in the URL, and the
    resolved ids are persisted to config.

    Parameters:

    - **pull_on_startup**: If ``True`` and a gateway is configured, download
      the latest version from the gateway before the sync daemon starts.
      Useful for web servers on ephemeral filesystems (Render, Heroku, etc.).
    """
    cfg = load_config()
    parsed_url: dict = {}

    if url:
        parsed_url = parse_url(url)

    url_name = parsed_url.get("name") or ""
    url_project = parsed_url.get("project") or None

    # ── resolve db_path ─────────────────────────────────────────
    resolved_path = db_path
    if resolved_path is None and name:
        resolved_path = str(_config.config_dir() / f"{name}.db")
    if resolved_path is None and url_name:
        resolved_path = str(_config.config_dir() / f"{url_name}.db")
    if resolved_path is None:
        resolved_path = cfg.database_path

    # ── resolve passphrase ──────────────────────────────────────
    resolved_passphrase = passphrase
    if not resolved_passphrase:
        resolved_passphrase = parsed_url.get("passphrase", "")
    if not resolved_passphrase:
        resolved_passphrase = os.environ.get("PARADOX_PASSPHRASE", "")
    if not resolved_passphrase:
        resolved_passphrase = cfg.encryption.passphrase or ""
    if not resolved_passphrase:
        # First-time connect: generate a strong passphrase, persist it, and
        # surface it to the user (CLI notice + ~/.paradox/.env) so it can be reused
        # on other machines. Never auto-generate for an existing DB file —
        # that keeps legacy 'default'-encrypted databases readable.
        if not os.path.exists(resolved_path):
            resolved_passphrase = generate_passphrase()
            try:
                set_config_value("encryption.passphrase", resolved_passphrase)
            except Exception:
                pass
            _announce_passphrase(resolved_passphrase, resolved_path)
            cfg = load_config()
        elif allow_legacy_default:
            resolved_passphrase = "default"
        else:
            raise ValueError(
                f"No passphrase configured for existing database '{resolved_path}'. "
                "Set PARADOX_PASSPHRASE or passphrase explicitly. "
                "Use allow_legacy_default=True only for legacy databases encrypted with 'default'."
            )

    # ── resolve gateway_url ─────────────────────────────────────
    resolved_gateway = gateway_url
    if not resolved_gateway:
        resolved_gateway = parsed_url.get("gateway_url", "")
    if not resolved_gateway:
        resolved_gateway = cfg.sync.gateway_url or ""

    # ── resolve api_key / auth ──────────────────────────────────
    token = api_key
    if not token:
        token = parsed_url.get("token", "") or ""
    email = parsed_url.get("email", "") or ""
    password = parsed_url.get("password", "") or ""

    resolved_api_key = ""
    if token:
        resolved_api_key = token
    elif email and password:
        if not resolved_gateway:
            raise ValueError("email/password in URL require a gateway")
        gw = GatewayClient(resolved_gateway)
        try:
            result = gw.login(email, password)
        except GatewayError as exc:
            raise ConnectionError(f"Login to gateway failed: {exc}") from exc
        resolved_api_key = gw.api_key
        if not resolved_api_key:
            raise ConnectionError("Login succeeded but no token was returned")
        try:
            set_config_value("sync.api_key", resolved_api_key)
            cfg = load_config()
        except Exception:
            pass
    else:
        resolved_api_key = cfg.sync.api_key if resolved_gateway else ""

    # ── project / database provisioning ─────────────────────────
    project_id = ""
    database_id = ""
    if resolved_gateway and url_project:
        gw = GatewayClient(resolved_gateway, resolved_api_key)
        try:
            proj = gw.ensure_project(url_project)
            project_id = proj.get("id", "")
            dbs = gw.ensure_database(project_id, url_name)
            database_id = dbs.get("id", "")
            try:
                cfg.database_path = resolved_path
                cfg.project_id = project_id
                cfg.database_id = database_id
                cfg.project_name = url_project
                cfg.sync.gateway_url = resolved_gateway
                cfg.sync.api_key = resolved_api_key
                save_config(cfg)
            except Exception:
                pass
        except GatewayError as exc:
            raise ConnectionError(
                f"Could not provision project/database on gateway: {exc}"
            ) from exc
    else:
        try:
            cfg.database_path = resolved_path
            if resolved_gateway and resolved_api_key:
                cfg.sync.gateway_url = resolved_gateway
                cfg.sync.api_key = resolved_api_key
            save_config(cfg)
        except Exception:
            pass

    conn = ParadConnection(
        db_path=resolved_path,
        passphrase=resolved_passphrase,
        gateway_url=resolved_gateway,
        api_key=resolved_api_key,
        auto_sync=auto_sync and bool(resolved_gateway),
        project=url_project,
        database_id=database_id,
        project_id=project_id,
        pull_on_startup=pull_on_startup and bool(resolved_gateway),
        storage_channel=storage_channel or os.environ.get("PARADOX_STORAGE_CHANNEL", ""),
        log_channel=log_channel or os.environ.get("PARADOX_LOG_CHANNEL", ""),
    )

    return conn
