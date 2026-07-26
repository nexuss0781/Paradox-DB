"""Background sync daemon — polls local file and remote gateway, syncing automatically."""

import hashlib
import logging
import os
import signal
import threading
import time
from pathlib import Path

from parad.config import load_config, CONFIG_DIR, gateway_db_name
from parad.crypto import encrypt_file
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import (
    get_remote_version,
    set_remote_version,
    get_last_local_hash,
    set_last_local_hash,
)

logger = logging.getLogger("parad.watcher")

_BACKOFF_BASE: float = 2.0
_BACKOFF_MAX: float = 60.0


def _pid_path() -> Path:
    return CONFIG_DIR / "watch.pid"


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
    """Auto-sync watcher for a single database file."""

    def __init__(
        self,
        db_path: str,
        passphrase: str,
        gateway_url: str | None = None,
        api_key: str | None = None,
        push_interval: float = 2.0,
        pull_interval: float = 30.0,
    ):
        self.db_path = Path(db_path).expanduser()
        self.db_name = gateway_db_name(self.db_path)
        self.passphrase = passphrase
        self.push_interval = push_interval
        self.pull_interval = pull_interval

        config = load_config()
        self.gw = GatewayClient(
            gateway_url or config.sync.gateway_url,
            api_key or config.sync.api_key,
        )

        self._engine = Engine(str(self.db_path), passphrase)
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

        self._dirty = False
        self._last_local_hash: str = ""
        self._last_push: float = 0.0
        self._last_pull: float = 0.0
        self._consecutive_errors: int = 0

    # ── Core sync operations ─────────────────────────────────────

    def _push(self) -> bool:
        """Read local DB, upload to gateway, update state.  Returns True on success."""
        if not self.db_path.exists():
            return False

        try:
            raw = self._engine.get_raw_bytes()
        except Exception as exc:
            logger.error("Failed to read local DB: %s", exc)
            return False

        version = get_remote_version(self.db_name) or 0

        try:
            result = self.gw.upload(self.db_name, raw, version=version)
        except GatewayError as exc:
            if exc.status_code == 409:
                logger.warning("Conflict on push (409), pulling first")
                self._pull()
                return False
            logger.error("Push failed: %s", exc)
            return False
        except Exception as exc:
            logger.error("Push error: %s", exc)
            return False

        set_remote_version(self.db_name, result.version)
        h = _file_hash(self.db_path)
        set_last_local_hash(self.db_name, h)
        self._last_local_hash = h
        self._last_push = time.monotonic()
        self._dirty = False
        logger.info("Pushed v%s (msg=%s)", result.version, result.message_id)
        return True

    def _pull(self) -> bool:
        """Download from gateway, re-encrypt and write locally.  Returns True on success."""
        try:
            dl = self.gw.download(self.db_name)
        except GatewayError as exc:
            logger.error("Pull failed: %s", exc)
            return False
        except Exception as exc:
            logger.error("Pull error: %s", exc)
            return False

        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_bytes(encrypt_file(dl.bytes, self.passphrase))
        except Exception as exc:
            logger.error("Failed to write pulled DB: %s", exc)
            return False

        if dl.version is not None:
            set_remote_version(self.db_name, dl.version)
        h = _file_hash(self.db_path)
        set_last_local_hash(self.db_name, h)
        self._last_local_hash = h
        self._last_pull = time.monotonic()
        self._dirty = False
        logger.info("Pulled %s (%d bytes)", f"v{dl.version}" if dl.version else "latest", len(dl.bytes))
        return True

    def _check_local(self):
        """Compare current file hash against last known hash, mark dirty if changed."""
        if not self.db_path.exists():
            return
        h = _file_hash(self.db_path)
        if h and h != self._last_local_hash:
            self._dirty = True

    def _check_remote(self):
        """Query gateway status; pull if remote version is newer."""
        try:
            status = self.gw.status()
        except Exception as exc:
            logger.debug("Remote status check failed: %s", exc)
            return

        for db in status.databases:
            if db.name == self.db_name:
                local_ver = get_remote_version(self.db_name)
                if local_ver is None or db.latest_version > local_ver:
                    logger.info("Remote has v%s, local has v%s — pulling", db.latest_version, local_ver)
                    self._pull()
                break

    # ── Main loop ────────────────────────────────────────────────

    def _loop(self):
        """Main polling loop.  Runs until stop() is called."""
        h = _file_hash(self.db_path)
        self._last_local_hash = h

        known_hash = get_last_local_hash(self.db_name)
        if h and known_hash and h != known_hash:
            logger.info("Local file has unsynced changes, will push")
            self._dirty = True

        if get_remote_version(self.db_name) is None:
            logger.info("No local state, pulling initial copy from gateway")
            self._pull()

        logger.info("Watching %s (pid=%s)", self.db_name, os.getpid())

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
                success = False
                try:
                    success = self._push()
                except Exception as exc:
                    logger.error("Push error: %s", exc)

                if success:
                    self._consecutive_errors = 0
                else:
                    self._consecutive_errors += 1

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
    )

    def _handle_signal(sig, _frame):
        logger.info("Received signal %s, stopping watcher...", sig)
        watcher.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
