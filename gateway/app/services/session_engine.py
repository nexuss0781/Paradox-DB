"""Persistent server-side SQL engine.

A session is a live in-memory SQLite connection hydrated from the latest
stored snapshot.  Clients (the JS/Python SDKs and SQLAlchemy engines) send
SQL statements over HTTP; each statement runs inside the *same* persistent
connection so ``BEGIN ... COMMIT`` spans multiple requests.  When a
transaction commits (or an explicit ``flush`` is sent) the connection is
serialized, re-encrypted to match the stored format, and pushed as a brand
new version — the same versioned snapshot history the SDKs produce locally.

Sessions are keyed by ``"{user_id}:{database_id}"``, expire after an idle
TTL, and are evicted by a background sweeper.  Eviction of a dirty session
persists it first (best effort), so committed work is never silently lost.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("paradox.sql")

SQLITE_MAGIC = b"SQLite format 3\x00"

# JSON-safe wrapper used when BLOB values travel over the wire.
_BYTES_KEY = "__parad_bytes__"

_COMMIT_RE = re.compile(r"^\s*(?:COMMIT|END)\b", re.IGNORECASE)
_BEGIN_RE = re.compile(r"^\s*(?:BEGIN|START)\b", re.IGNORECASE)
_ROLLBACK_RE = re.compile(r"^\s*(?:ROLLBACK)\b", re.IGNORECASE)
_READ_RE = re.compile(r"^\s*(?:SELECT|EXPLAIN)\b", re.IGNORECASE)


def is_sqlite_bytes(data: bytes) -> bool:
    """Return True when *data* starts with the SQLite file magic."""
    return data[:16] == SQLITE_MAGIC


def encode_value(value: Any) -> Any:
    """Encode a value returned by sqlite3 into a JSON-safe form."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {_BYTES_KEY: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {k: encode_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_value(v) for v in value]
    return value


def decode_value(value: Any) -> Any:
    """Decode a JSON-serialized value back into a sqlite3 parameter."""
    if isinstance(value, dict):
        if _BYTES_KEY in value and len(value) == 1:
            try:
                return base64.b64decode(value[_BYTES_KEY])
            except Exception:
                return value
        return {k: decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    return value


def _export_bytes(conn: sqlite3.Connection) -> bytes:
    """Serialize the live connection to plaintext SQLite bytes.

    Prefers ``serialize()`` (Python 3.11+) and falls back to a ``backup()``
    round-trip for older interpreters.
    """
    if hasattr(conn, "serialize"):
        return conn.serialize()
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    try:
        tmp.close()
        dest = sqlite3.connect(tmp.name)
        try:
            conn.backup(dest)
        finally:
            dest.close()
        return Path(tmp.name).read_bytes()
    finally:
        try:
            Path(tmp.name).unlink()
        except FileNotFoundError:
            pass


def _open_conn(data: bytes) -> tuple[sqlite3.Connection, str]:
    """Open a persistent SQLite connection hydrated with *data* (empty = fresh).

    Returns ``(conn, tmp_path)`` — the connection lives on a temp file so the
    gateway process can unlink it cleanly when the session closes.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".parad.sql", delete=False)
    if data:
        tmp.write(data)
    tmp.close()
    conn = sqlite3.connect(tmp.name, check_same_thread=False)
    conn.isolation_level = None
    conn.row_factory = None
    return conn, tmp.name


def dispose_conn(conn: sqlite3.Connection, tmp_path: str) -> None:
    try:
        conn.close()
    except Exception:
        pass
    try:
        Path(tmp_path).unlink()
    except FileNotFoundError:
        pass


@dataclass
class SqlSession:
    """A live server-side SQLite session for one database."""

    key: str
    conn: sqlite3.Connection
    tmp_path: str
    passphrase: str
    mode: str  # "plain" | "encrypted" — the on-disk snapshot format
    base_version: int
    baseline_changes: int = 0
    baseline_bytes_hash: str = ""
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dirty: bool = False

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used


@dataclass
class SqlResult:
    columns: Optional[list[str]] = None
    rows: list[list[Any]] = field(default_factory=list)
    rowcount: int = -1
    lastrowid: int = 0
    changes: int = 0
    in_transaction: bool = False

    def to_dict(self, session: SqlSession) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "rowcount": self.rowcount,
            "lastrowid": self.lastrowid,
            "changes": self.changes,
            "in_transaction": self.in_transaction,
            "session": {
                "id": session.key,
                "mode": session.mode,
                "version": session.base_version,
                "dirty": session.dirty,
                "idle_seconds": round(session.idle_seconds, 1),
            },
        }


class SqlSessionStore:
    """In-memory registry of :class:`SqlSession` instances.

    Threads: sqlite connections are created with ``check_same_thread=False``;
    each session's ``lock`` serialises access so statements never run
    concurrently on the same connection.
    """

    def __init__(
        self,
        ttl_seconds: int = 600,
        max_sessions: int = 256,
        sweep_interval: int = 30,
    ):
        self._sessions: dict[str, SqlSession] = {}
        self._persisters: dict[
            str, Callable[[SqlSession, bytes], Awaitable[int]]
        ] = {}
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._sweep_interval = sweep_interval
        self._create_lock = asyncio.Lock()
        self._sweeper_task: Optional[asyncio.Task] = None

    @property
    def count(self) -> int:
        return len(self._sessions)

    def get(self, key: str) -> Optional[SqlSession]:
        return self._sessions.get(key)

    def bind_persister(
        self, key: str, persister: Callable[[SqlSession, bytes], Awaitable[int]]
    ) -> None:
        self._persisters[key] = persister

    def get_persister(self, key: str) -> Optional[Callable[[SqlSession, bytes], Awaitable[int]]]:
        return self._persisters.get(key)

    def start(self) -> None:
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweeper_task is not None:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            self._sweeper_task = None
        for key in list(self._sessions.keys()):
            await self.evict(key)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval)
            try:
                await self.sweep()
            except Exception:
                logger.exception("SQL session sweep failed")

    async def sweep(self) -> None:
        now = time.time()
        for session in list(self._sessions.values()):
            if now - session.last_used > self._ttl:
                await self.evict(session.key)

    async def get_or_create(
        self,
        key: str,
        passphrase: str,
        snapshot: bytes,
        version: int,
    ) -> tuple[SqlSession, bool]:
        """Return the live session for *key*, creating it if needed.

        An idle session past the TTL is evicted (persisting dirty work) and
        replaced.  Returns ``(session, created)``.
        """
        async with self._create_lock:
            session = self._sessions.get(key)
            if session is not None and session.idle_seconds <= self._ttl:
                return session, False
            if session is not None:
                await self._evict_locked(key)
            if len(self._sessions) >= self._max:
                await self._evict_locked(next(iter(self._sessions)))
            session = self._build(key, passphrase, snapshot, version)
            self._sessions[key] = session
            return session, True

    def _build(self, key: str, passphrase: str, snapshot: bytes, version: int) -> SqlSession:
        if snapshot:
            if is_sqlite_bytes(snapshot):
                mode, data = "plain", snapshot
            else:
                if not passphrase:
                    raise ValueError(
                        "database snapshot is encrypted — a passphrase is required"
                    )
                from ..crypto import decrypt_data

                mode = "encrypted"
                try:
                    data = decrypt_data(snapshot, passphrase)
                except Exception as exc:
                    raise ValueError(
                        f"invalid passphrase or corrupt snapshot: {exc}"
                    ) from exc
        else:
            mode = "encrypted" if passphrase else "plain"
            data = b""
        conn, tmp_path = _open_conn(data)
        session = SqlSession(
            key=key,
            conn=conn,
            tmp_path=tmp_path,
            passphrase=passphrase,
            mode=mode,
            base_version=version,
        )
        try:
            session.baseline_bytes_hash = hashlib.sha256(
                _export_bytes(conn)
            ).hexdigest()
        except Exception:
            session.baseline_bytes_hash = ""
        logger.info("SQL session opened %s (mode=%s v%s)", key, mode, version)
        return session

    async def execute(
        self,
        session: SqlSession,
        sql: str,
        params: list[Any] | None = None,
        executescript: bool = False,
    ) -> SqlResult:
        session.last_used = time.time()
        async with session.lock:
            return await asyncio.to_thread(
                self._run, session, sql, params or [], executescript
            )

    @staticmethod
    def _compute_dirty(session: SqlSession, sql: str) -> bool:
        """Net-change detection.

        ``total_changes`` counts rolled-back statements too, so after a
        ROLLBACK the exported bytes are hashed against the baseline to
        decide whether anything actually changed.
        """
        if _ROLLBACK_RE.match(sql.strip()):
            try:
                return (
                    hashlib.sha256(_export_bytes(session.conn)).hexdigest()
                    != session.baseline_bytes_hash
                )
            except Exception:
                pass
        return session.conn.total_changes != session.baseline_changes

    def _run(
        self,
        session: SqlSession,
        sql: str,
        params: list[Any],
        executescript: bool,
    ) -> SqlResult:
        conn = session.conn
        trimmed = sql.strip()
        read_only = not executescript and _READ_RE.match(trimmed) is not None
        if executescript:
            conn.executescript(sql)
            session.dirty = self._compute_dirty(session, trimmed)
            return SqlResult(
                changes=conn.total_changes - session.baseline_changes,
                in_transaction=bool(conn.in_transaction),
            )
        # COMMIT/ROLLBACK with no open transaction are no-ops (DBAPI semantics).
        if (
            (_COMMIT_RE.match(trimmed) or _ROLLBACK_RE.match(trimmed))
            and not conn.in_transaction
        ):
            session.dirty = self._compute_dirty(session, trimmed)
            return SqlResult(
                in_transaction=False,
                changes=conn.total_changes - session.baseline_changes,
            )
        try:
            decoded = tuple(decode_value(v) for v in params)
            cursor = conn.execute(sql, decoded)
        except sqlite3.Error as exc:
            raise SqlExecutionError(str(exc)) from exc
        if cursor.description:
            columns = [col[0] for col in cursor.description]
            rows = [[encode_value(v) for v in row] for row in cursor.fetchall()]
            if not read_only:
                session.dirty = self._compute_dirty(session, trimmed)
            return SqlResult(
                columns=columns,
                rows=rows,
                rowcount=-1,
                lastrowid=0,
                changes=conn.total_changes - session.baseline_changes,
                in_transaction=bool(conn.in_transaction),
            )
        if not conn.in_transaction:
            conn.commit()
        session.dirty = self._compute_dirty(session, trimmed)
        return SqlResult(
            rowcount=cursor.rowcount,
            lastrowid=cursor.lastrowid or 0,
            changes=conn.total_changes - session.baseline_changes,
            in_transaction=bool(conn.in_transaction),
        )

    async def persist(
        self,
        session: SqlSession,
        persister: Callable[[SqlSession, bytes], Awaitable[int]],
    ) -> int:
        """Export the session bytes and push them as a new version.

        Runs under the session lock so the snapshot cannot be torn by a
        concurrent statement.  A no-op (no new version) when the exported
        bytes are byte-identical to the baseline — e.g. a transaction that
        was rolled back or netted to the same state.  Resets the persistence
        baseline on success.
        """
        async with session.lock:
            raw = await asyncio.to_thread(_export_bytes, session.conn)
            new_hash = hashlib.sha256(raw).hexdigest()
            if new_hash == session.baseline_bytes_hash:
                session.baseline_changes = session.conn.total_changes
                session.dirty = False
                return session.base_version
            version = await persister(session, raw)
            session.baseline_changes = session.conn.total_changes
            session.baseline_bytes_hash = new_hash
            session.base_version = version
            session.dirty = False
        return version

    async def evict(self, key: str) -> Optional[SqlSession]:
        async with self._create_lock:
            return await self._evict_locked(key)

    async def _evict_locked(self, key: str) -> Optional[SqlSession]:
        session = self._sessions.pop(key, None)
        if session is None:
            return None
        persister = self._persisters.pop(key, None)
        try:
            if session.dirty and persister is not None:
                await self.persist(session, persister)
        except Exception:
            logger.exception("Failed to persist dirty SQL session %s on eviction", key)
        finally:
            dispose_conn(session.conn, session.tmp_path)
            logger.info("SQL session closed %s", key)
        return session


class SqlExecutionError(Exception):
    """Raised when a statement fails on the server-side SQLite connection."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def is_commit(sql: str) -> bool:
    return _COMMIT_RE.match(sql.strip()) is not None


def is_begin(sql: str) -> bool:
    return _BEGIN_RE.match(sql.strip()) is not None
