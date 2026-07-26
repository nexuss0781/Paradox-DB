"""Auto-sync daemon — watches local DB and syncs with gateway automatically."""

import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from parad.config import load_config, CONFIG_DIR, gateway_db_name
from parad.crypto import encrypt_file
from parad.engine import Engine
from parad.gateway import GatewayClient, GatewayError
from parad.state import (
    load_state,
    save_state,
    get_remote_version,
    set_remote_version,
    get_last_local_hash,
    set_last_local_hash,
)

logger = logging.getLogger("parad.watcher")

STATE_DIR = CONFIG_DIR


def _state_path(db_name: str) -> Path:
    return STATE_DIR / f"{db_name}.watch.json"


def _pid_path() -> Path:
    return STATE_DIR / "watch.pid"


def _load_watch_state(db_name: str) -> dict:
    path = _state_path(db_name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_watch_state(db_name: str, state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(db_name).write_text(json.dumps(state, indent=2))


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Watcher:
    """Auto-sync watcher for a single database."""

    def __init__(
        self,
        passphrase: str = "default",
        push_interval: int = 5,
        pull_interval: int = 30,
        debounce_seconds: float = 3.0,
    ):
        self.config = load_config()
        self.passphrase = passphrase
        self.db_path = Path(self.config.database_path).expanduser()
        self.db_name = gateway_db_name(self.db_path)
        self.push_interval = push_interval
        self.pull_interval = pull_interval
        self.debounce_seconds = debounce_seconds
        self.gw = GatewayClient(
            self.config.sync.gateway_url,
            self.config.sync.api_key,
        )
        self._running = False
        self._last_push_time = 0.0
        self._last_pull_time = 0.0
        self._last_local_hash = ""
        self._pending_push = False

    def _push(self) -> bool:
        """Push local DB to gateway. Returns True on success."""
        if not self.db_path.exists():
            return False

        try:
            engine = Engine(str(self.db_path), self.passphrase)
            engine.open()
            raw = engine.get_raw_bytes()
            engine.close()
        except Exception as e:
            logger.error(f"Failed to read local DB: {e}")
            return False

        version = get_remote_version(self.db_name)

        try:
            result = self.gw.upload(self.db_name, raw, version=version if version else 0)
            set_remote_version(self.db_name, result.version)
            set_last_local_hash(self.db_name, _file_hash(self.db_path))
            self._last_local_hash = _file_hash(self.db_path)
            self._last_push_time = time.time()
            self._pending_push = False
            logger.info(f"Pushed v{result.version} (msg={result.message_id})")
            return True
        except GatewayError as e:
            if "409" in str(e):
                logger.warning(f"Conflict on push, pulling instead: {e}")
                self._pull()
            else:
                logger.error(f"Push failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Push error: {e}")
            return False

    def _pull(self) -> bool:
        """Pull remote DB from gateway. Returns True on success."""
        try:
            dl = self.gw.download(self.db_name)
        except GatewayError as e:
            logger.error(f"Pull failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Pull error: {e}")
            return False

        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path.write_bytes(encrypt_file(dl.bytes, self.passphrase))
            if dl.version is not None:
                set_remote_version(self.db_name, dl.version)
            set_last_local_hash(self.db_name, _file_hash(self.db_path))
            self._last_local_hash = _file_hash(self.db_path)
            self._last_pull_time = time.time()
            ver = f"v{dl.version}" if dl.version else "latest"
            logger.info(f"Pulled {ver} ({len(dl.bytes)} bytes)")
            return True
        except Exception as e:
            logger.error(f"Failed to write pulled DB: {e}")
            return False

    def _check_local_changes(self):
        """Detect local file changes and schedule push."""
        if not self.db_path.exists():
            return

        current_hash = _file_hash(self.db_path)
        if current_hash and current_hash != self._last_local_hash:
            self._pending_push = True

    def _check_remote_changes(self):
        """Detect remote version changes and pull."""
        try:
            status = self.gw.status()
        except Exception as e:
            logger.debug(f"Status check failed: {e}")
            return

        for db in status.databases:
            if db.name == self.db_name:
                remote_ver = db.latest_version
                local_ver = get_remote_version(self.db_name)
                if local_ver is None or remote_ver > local_ver:
                    logger.info(f"Remote has v{remote_ver}, local has v{local_ver}")
                    self._pull()

    def run(self):
        """Main watch loop."""
        self._running = True

        # Compare current file hash against last known hash from state
        current_hash = _file_hash(self.db_path)
        last_known_hash = get_last_local_hash(self.db_name)

        if current_hash and last_known_hash and current_hash != last_known_hash:
            logger.info("Local file has unsynced changes, will push.")
            self._pending_push = True

        self._last_local_hash = current_hash
        logger.info(f"Watching {self.db_name} (pid={os.getpid()})")

        # Initial sync: if no local state, pull from gateway
        if get_remote_version(self.db_name) is None:
            logger.info("No local state, pulling from gateway...")
            self._pull()
        elif self.db_path.exists():
            # Check if local hash matches remote
            local_hash = _file_hash(self.db_path)
            remote_hash = load_state(self.db_name).get("remote_hash", "")
            if remote_hash and local_hash != remote_hash:
                logger.info("Local hash differs from remote, pulling...")
                self._pull()

        while self._running:
            now = time.time()

            # Check local changes
            self._check_local_changes()

            # Push if pending and debounce elapsed
            if self._pending_push and (now - self._last_push_time) >= self.debounce_seconds:
                self._push()

            # Check remote changes periodically
            if (now - self._last_pull_time) >= self.pull_interval:
                self._check_remote_changes()

            time.sleep(2)  # Poll every 2 seconds

    def stop(self):
        self._running = False


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def start_daemon(passphrase: str = "default"):
    """Start the watcher as a foreground process."""
    _setup_logging()
    watcher = Watcher(passphrase=passphrase)

    def handle_signal(sig, frame):
        logger.info("Stopping watcher...")
        watcher.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Write PID
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _pid_path().write_text(str(os.getpid()))

    try:
        watcher.run()
    finally:
        if _pid_path().exists():
            _pid_path().unlink()


def is_running() -> bool:
    """Check if a watcher daemon is running."""
    pid_path = _pid_path()
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return True
    except (ProcessLookupError, ValueError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return False


def stop_daemon() -> bool:
    """Stop the running watcher daemon."""
    pid_path = _pid_path()
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        return True
    except (ProcessLookupError, ValueError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return False


def get_daemon_pid() -> int | None:
    """Get the PID of the running daemon, or None."""
    pid_path = _pid_path()
    if not pid_path.exists():
        return None
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, ValueError, PermissionError):
        return None
