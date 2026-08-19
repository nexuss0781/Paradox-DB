"""PEP 249 DB-API adapter for Parad's encrypted SQLite engine."""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable, Sequence

from .connection import ParadConnection, connect as parad_connect

apilevel = "2.0"
threadsafety = 1
paramstyle = "qmark"

Warning = sqlite3.Warning
Error = sqlite3.Error
InterfaceError = sqlite3.InterfaceError
DatabaseError = sqlite3.DatabaseError
DataError = sqlite3.DataError
OperationalError = sqlite3.OperationalError
IntegrityError = sqlite3.IntegrityError
InternalError = sqlite3.InternalError
ProgrammingError = sqlite3.ProgrammingError
NotSupportedError = sqlite3.NotSupportedError

Binary = sqlite3.Binary
Date = sqlite3.Date
Time = sqlite3.Time
Timestamp = sqlite3.Timestamp
DateFromTicks = sqlite3.DateFromTicks
TimeFromTicks = sqlite3.TimeFromTicks
TimestampFromTicks = sqlite3.TimestampFromTicks
sqlite_version = sqlite3.sqlite_version
sqlite_version_info = sqlite3.sqlite_version_info


class Cursor:
    """PEP 249 cursor backed by Parad's live SQLite connection."""

    arraysize = 1

    def __init__(self, connection: "Connection"):
        self.connection = connection
        self._cursor: sqlite3.Cursor | None = None
        self._closed = False

    def _require_open(self) -> sqlite3.Cursor:
        if self._closed or self.connection.closed:
            raise InterfaceError("cursor is closed")
        if self._cursor is None:
            self._cursor = self.connection._raw.cursor()
        return self._cursor

    @property
    def description(self):
        return self._cursor.description if self._cursor is not None else None

    @property
    def rowcount(self) -> int:
        if self._cursor is None:
            return -1
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        if self._cursor is None:
            return None
        return self._cursor.lastrowid

    def execute(self, operation: str, parameters: Sequence[Any] | None = None):
        cursor = self._require_open()
        self.connection._lock.acquire()
        try:
            cursor.execute(operation, tuple(parameters or ()))
        except Exception:
            self.connection._lock.release()
            raise
        self.connection._lock.release()
        return self

    def executemany(self, operation: str, seq_of_parameters: Iterable[Sequence[Any]]):
        cursor = self._require_open()
        self.connection._lock.acquire()
        try:
            cursor.executemany(operation, [tuple(params) for params in seq_of_parameters])
        except Exception:
            self.connection._lock.release()
            raise
        self.connection._lock.release()
        return self

    def executescript(self, script: str):
        cursor = self._require_open()
        self.connection._lock.acquire()
        try:
            cursor.executescript(script)
        except Exception:
            self.connection._lock.release()
            raise
        self.connection._lock.release()
        return self

    def fetchone(self):
        return self._require_open().fetchone()

    def fetchmany(self, size: int | None = None):
        return self._require_open().fetchmany(self.arraysize if size is None else size)

    def fetchall(self):
        return self._require_open().fetchall()

    def setinputsizes(self, sizes):
        return None

    def setoutputsize(self, size, column=None):
        return None

    def close(self):
        if not self._closed and self._cursor is not None:
            self._cursor.close()
        self._closed = True

    def __iter__(self):
        return iter(self._require_open())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class Connection:
    """PEP 249 connection backed by a ParadConnection."""

    def __init__(self, database_url: str, **kwargs: Any):
        self._parad = parad_connect(url=database_url, auto_sync=kwargs.pop("auto_sync", False))
        self._raw = self._parad.engine._conn
        if self._raw is None:
            raise InterfaceError("Parad database engine is not open")
        self._lock = threading.RLock()
        self.closed = False

    @property
    def parad(self) -> ParadConnection:
        return self._parad

    def cursor(self) -> Cursor:
        if self.closed:
            raise InterfaceError("connection is closed")
        return Cursor(self)

    def commit(self):
        if self.closed:
            raise InterfaceError("connection is closed")
        with self._lock:
            self._raw.commit()

    def rollback(self):
        if self.closed:
            raise InterfaceError("connection is closed")
        with self._lock:
            self._raw.rollback()

    def close(self):
        if not self.closed:
            with self._lock:
                self._parad.close()
            self.closed = True

    def execute(self, operation: str, parameters: Sequence[Any] | None = None):
        cursor = self.cursor()
        cursor.execute(operation, parameters)
        return cursor

    def executemany(self, operation: str, seq_of_parameters: Iterable[Sequence[Any]]):
        cursor = self.cursor()
        cursor.executemany(operation, seq_of_parameters)
        return cursor

    def executescript(self, script: str):
        cursor = self.cursor()
        cursor.executescript(script)
        return cursor

    def create_function(self, *args, **kwargs):
        if self.closed:
            raise InterfaceError("connection is closed")
        return self._raw.create_function(*args, **kwargs)

    def set_authorizer(self, *args, **kwargs):
        if self.closed:
            raise InterfaceError("connection is closed")
        return self._raw.set_authorizer(*args, **kwargs)

    def interrupt(self):
        if not self.closed:
            return self._raw.interrupt()

    def __enter__(self):
        if self.closed:
            raise InterfaceError("connection is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def connect(database: str | None = None, **kwargs: Any) -> Connection:
    """Return a DB-API connection for a canonical Parad URL."""
    if not database:
        raise ProgrammingError("Parad DB-API requires a DATABASE_URL or database URL")
    return Connection(database, **kwargs)
