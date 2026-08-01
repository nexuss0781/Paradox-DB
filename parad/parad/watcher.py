"""Background sync daemon — polls local file and remote gateway, syncing automatically."""

import hashlib
import logging
import os
import signal
import threading
import time
from pathlib import Path

from parad.config import load_config, config_dir, gateway_db_name
from parad.connection import db_state_key
from parad.crypto import encrypt_file
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError, is_connectivity_error
from parad.state import (
    get_remote_version,
    set_remote_version,
    get_last_local_hash,
    set_last_local_hash,
    mark_dirty,
    clear_dirty,
    is_dirty,
    set_offline,
    is_offline,
)

logger = logging.getLogger("parad.watcher")

_BACKOFF_BASE: float = 2.0
_BACKOFF_MAX: float = 60.0


def _pid_path() -> Path:
    return config_dir() / "watch.pid"


def _file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file, or empty string if missing."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Watcher:
    """Auto-sync watcher for a single database file.

    Conflict policy is **local-wins**: on a 409 the remote snapshot is
    pulled (base version advances) and the local bytes are re-pushed as a
    brand new version.  Local changes are never silently dropped.

    Offline handling mirrors :class:`parad.connection.SyncDaemon`: network
    / 5xx errors flip the offline flag (WARNING on transition, INFO on
    recovery) via ``_consecutive_errors``.
    """

    def __init__(
        self,
        db_path: str,
        passphrase: str,
        gateway_url: str | None = None,
        api_key: str | None = None,
        push_interval: float = 2.0,
        pull_interval: float = 30.0,
        project: str | None = None,
        database_id: str = "",
        project_id: str = "",
    ):
        self.db_path = Path(db_path).expanduser()
        self.db_name = gateway_db_name(self.db_path)
        self.db_key = db_state_key(self.db_name, project)
        self.passphrase = passphrase
        self.push_interval = push_interval
        self.pull_interval = pull_interval
        self._project = project
        self._database_id = database_id
        self._project_id = project_id

        config = load_config()
        self.gw = GatewayClient(
            gateway_url or config.sync.gateway_url,
            api_key or config.sync.api_key,
        )

        self._engine = Engine(str(self.db_path), passphrase)
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

        self._dirty = is_dirty(self.db_key)
        self._last_local_hash: str = ""
        self._last_push: float = 0.0
        self._last_pull: float = 0.0
        self._consecutive_errors: int = 0
        self._last_error: str | None = None
        self._offline = is_offline(self.db_key)

    # ── health / offline exposure ───────────────────────────────

    @property
    def offline(self) -> bool:
        return self._offline

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_errors

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ── failure bookkeeping ─────────────────────────────────────

    def _record_failure(self, exc: Exception):
        self._last_error = str(exc)
        if is_connectivity_error(exc):
            self._consecutive_errors += 1
            was_offline = self._offline
            self._offline = True
            set_offline(self.db_key, True)
            if not was_offline:
                logger.warning("Sync offline for %s: %s", self.db_key, exc)
            else:
                logger.debug(
                    "Still offline for %s (%d consecutive errors)",
                    self.db_key,
                    self._consecutive_errors,
                )
        else:
            logger.error("Sync failure for %s: %s", self.db_key, exc)

    def _record_success(self):
        was_offline = self._offline
        self._offline = False
        self._consecutive_errors = 0
        self._last_error = None
        set_offline(self.db_key, False)
        if was_offline:
            logger.info(
                "Sync back online for %s — pushing pending changes", self.db_key
            )

    # ── Core sync operations ─────────────────────────────────────

    def _write_local(self, raw: bytes):
        """Encrypt and write *raw* as the on-disk DB file."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_bytes(encrypt_file(raw, self.passphrase))

    def _finish_push(self, result):
        set_remote_version(self.db_key, result.version)
        h = _file_hash(self.db_path)
        set_last_local_hash(self.db_key, h)
        self._last_local_hash = h
        self._last_push = time.monotonic()
        self._dirty = False
        clear_dirty(self.db_key)
        self._record_success()
        logger.info("Pushed %s v%s (msg=%s)", self.db_key, result.version, result.message_id)

    def _push(self) -> bool:
        """Read local DB, upload to gateway, update state.  Returns True on success."""
        if not self.db_path.exists():
            return False

        try:
            raw = self._engine.get_raw_bytes()
        except Exception as exc:
            self._record_failure(exc)
            return False

        version = get_remote_version(self.db_key) or 0

        try:
            result = self.gw.upload(
                self.db_name,
                raw,
                version=version,
                database_id=self._database_id,
                project_id=self._project_id,
            )
        except GatewayError as exc:
            if exc.status_code == 409:
                # LOCAL-WINS: pull remote, then re-push OUR bytes as a new
                # version.  Local changes are never silently dropped.
                logger.warning(
                    "Conflict on push (409) for %s — pulling remote, re-pushing local (local-wins)",
                    self.db_key,
                )
                local_raw = raw
                try:
                    self._pull(force=True)
                    version = get_remote_version(self.db_key) or 0
                    result = self.gw.upload(
                        self.db_name,
                        local_raw,
                        version=version,
                        database_id=self._database_id,
                        project_id=self._project_id,
                    )
                except Exception as exc2:
                    self._record_failure(exc2)
                    mark_dirty(self.db_key)
                    return False
                # our bytes won — write them back so the on-disk file
                # matches what we pushed (defect B: no silent drop)
                self._write_local(local_raw)
                self._finish_push(result)
                return True
            self._record_failure(exc)
            return False
        except Exception as exc:
            self._record_failure(exc)
            return False

        self._finish_push(result)
        return True

    def _pull(self, force: bool = False) -> bool:
        """Download from gateway, re-encrypt and write locally.  Returns True on success.

        If the local DB has un-pushed changes and ``force`` is False, the
        local-wins policy applies: push the local bytes (handling any 409)
        instead of overwriting them with the remote snapshot.
        """
        had_dirty = self._dirty or is_dirty(self.db_key)
        if had_dirty and not force:
            logger.warning(
                "Local has unsynced changes (%s) — pushing local instead of overwriting (local-wins)",
                self.db_key,
            )
            return self._push()

        try:
            dl = self.gw.download(
                self.db_name,
                database_id=self._database_id,
                project_id=self._project_id,
            )
        except GatewayError as exc:
            self._record_failure(exc)
            return False
        except Exception as exc:
            self._record_failure(exc)
            return False

        try:
            self._write_local(dl.bytes)
        except Exception as exc:
            self._record_failure(exc)
            return False

        if dl.version is not None:
            set_remote_version(self.db_key, dl.version)
        h = _file_hash(self.db_path)
        set_last_local_hash(self.db_key, h)
        self._last_local_hash = h
        self._last_pull = time.monotonic()
        self._dirty = False
        clear_dirty(self.db_key)
        self._record_success()
        logger.info("Pulled %s (%d bytes)", self.db_key, len(dl.bytes))
        return True

    def _check_local(self):
        """Compare current file hash against last known hash, mark dirty if changed."""
        if not self.db_path.exists():
            return
        h = _file_hash(self.db_path)
        if h and h != self._last_local_hash:
            self._dirty = True
            mark_dirty(self.db_key)

    def _check_remote(self):
        """Query gateway status; sync if remote version is newer."""
        try:
            status = self.gw.status()
        except Exception as exc:
            self._record_failure(exc)
            return

        for db in status.databases:
            if db.name == self.db_name:
                local_ver = get_remote_version(self.db_key)
                if local_ver is None or db.latest_version > local_ver:
                    logger.info(
                        "Remote has v%s, local has v%s — syncing (local-wins)",
                        db.latest_version,
                        local_ver,
                    )
                    self._pull()
                break

    # ── Main loop ────────────────────────────────────────────────

    def _loop(self):
        """Main polling loop.  Runs until stop() is called."""
        h = _file_hash(self.db_path)
        self._last_local_hash = h

        known_hash = get_last_local_hash(self.db_key)
        if h and known_hash and h != known_hash:
            logger.info("Local file has unsynced changes, will push")
            self._dirty = True
            mark_dirty(self.db_key)

        if get_remote_version(self.db_key) is None:
            logger.info("No local state, pulling initial copy from gateway")
            self._pull()

        logger.info("Watching %s (pid=%s)", self.db_key, os.getpid())

        last_check_local = 0.0
        last_check_remote = 0.0

        while self._running.is_set():
            now = time.monotonic()

            # Local change detection (every push_interval)
            if now - last_check_local >= self.push_interval:
                try:
                    self._check_local()
                except Exception as exc:
                    logger.error("Local check error: %s", exc)
                last_check_local = now

            # Push if dirty and enough time since last push
            if self._dirty and (now - self._last_push) >= self.push_interval:
                try:
                    self._push()
                except Exception as exc:
                    self._record_failure(exc)

            # Remote change detection (every pull_interval)
            if now - last_check_remote >= self.pull_interval:
                try:
                    self._check_remote()
                except Exception as exc:
                    logger.error("Remote check error: %s", exc)
                last_check_remote = now

            # Sleep with backoff awareness
            self._running.wait(timeout=min(self.push_interval, 2.0))

    # ── Thread management ────────────────────────────────────────

    def start(self):
        """Create and start a daemon thread running the sync loop."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Watcher thread already running")
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="parad-watcher")
        self._thread.start()
        logger.info("Watcher thread started")

    def stop(self):
        """Signal the loop to stop and join the thread."""
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Watcher stopped")

    @property
    def is_alive(self) -> bool:
        """Whether the watcher thread is currently running."""
        return self._thread is not None and self._thread.is_alive()

    # ── Blocking convenience (for foreground use) ─────────────────

    def run(self):
        """Run the sync loop in the current thread (blocks)."""
        self._running.set()
        try:
            self._loop()
        finally:
            self._running.clear()


# ── CLI compatibility helpers ────────────────────────────────────


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def start_daemon(passphrase: str = "default"):
    """Start the watcher as a foreground process (for ``parad connect`` / ``parad watch``)."""
    _setup_logging()
    config = load_config()
    watcher = Watcher(
        db_path=config.database_path,
        passphrase=passphrase,
        project=getattr(config, "project_name", "") or None,
        database_id=config.database_id,
        project_id=config.project_id,
    )

    def _handle_signal(sig, _frame):
        logger.info("Received signal %s, stopping watcher...", sig)
        watcher.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config_dir().mkdir(parents=True, exist_ok=True)
    _pid_path().write_text(str(os.getpid()))

    try:
        watcher.run()
    finally:
        _pid_path().unlink(missing_ok=True)


def stop_daemon() -> bool:
    """Send SIGTERM to the running daemon.  Returns True if a signal was sent."""
    pid = get_daemon_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        _pid_path().unlink(missing_ok=True)
        return True
    except (ProcessLookupError, PermissionError):
        _pid_path().unlink(missing_ok=True)
        return False


def is_running() -> bool:
    """Check if a watcher daemon process is alive via PID file."""
    return get_daemon_pid() is not None


def get_daemon_pid() -> int | None:
    """Return the daemon PID if the process is alive, else ``None``."""
    pid_path = _pid_path()
    if not pid_path.exists():
        return None
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, ValueError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return None
