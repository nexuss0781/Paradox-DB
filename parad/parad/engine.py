"""Local encrypted SQLite engine for parad."""

import os
import sqlite3
import tempfile
from pathlib import Path
from parad.crypto import encrypt_file, decrypt_file, DecryptionError


class Engine:
    """Encrypted SQLite database engine.
    
    Decrypts the file to a temp location, operates on it,
    then re-encrypts and writes back.
    """

    def __init__(self, db_path: str, passphrase: str):
        self.db_path = Path(db_path).expanduser()
        self.passphrase = passphrase
        self._conn: sqlite3.Connection | None = None
        self._tmp_path: str | None = None

    @property
    def is_open(self) -> bool:
        """Whether the engine currently has a live SQLite connection."""
        return self._conn is not None

    def _cleanup_tmp(self):
        """Unlink the temp file, if any, and reset _tmp_path."""
        if self._tmp_path:
            try:
                os.unlink(self._tmp_path)
            except FileNotFoundError:
                pass
            self._tmp_path = None

    def _decrypt_to_temp(self) -> str:
        """Decrypt DB to a temp file, return path.

        Raises :class:`DecryptionError` (a ValueError subclass) when the
        file cannot be decrypted — wrong passphrase or corrupt data.  No
        temp file is created in that case, so no half-open state remains.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        encrypted = self.db_path.read_bytes()
        try:
            decrypted = decrypt_file(encrypted, self.passphrase)
        except DecryptionError as exc:
            raise DecryptionError(
                f"Cannot open {self.db_path}: {exc}"
            ) from exc
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        try:
            tmp.write(decrypted)
            tmp.close()
        except OSError:
            tmp.close()
            os.unlink(tmp.name)
            raise
        self._tmp_path = tmp.name
        return self._tmp_path

    def _encrypt_from_temp(self):
        """Encrypt temp file back to db_path."""
        if not self._tmp_path:
            return
        decrypted = Path(self._tmp_path).read_bytes()
        encrypted = encrypt_file(decrypted, self.passphrase)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_bytes(encrypted)

    def open(self, create: bool = False) -> sqlite3.Connection:
        """Open the encrypted database.

        - If the file does not exist and ``create=True``, a fresh SQLite
          database is created.
        - If the file exists but is empty (0 bytes) and ``create=True``,
          it is treated as a fresh database and a new SQLite file is
          created — a partially-initialized connection string (e.g. a
          0-byte file left by ``touch``) therefore does not explode.
        - If the file exists and is non-empty, it is decrypted; a wrong
          passphrase or corrupt data raises ``DecryptionError`` and the
          engine is left fully closed (no temp file, no connection).
        - Calling ``open()`` while already open is a no-op returning the
          current connection.
        """
        if self.is_open:
            return self._conn
        self._cleanup_tmp()
        try:
            if create and (
                not self.db_path.exists() or self.db_path.stat().st_size == 0
            ):
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
                tmp.close()
                self._tmp_path = tmp.name
                self._conn = sqlite3.connect(self._tmp_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                return self._conn
            tmp_path = self._decrypt_to_temp()
            self._conn = sqlite3.connect(tmp_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            return self._conn
        except Exception:
            self._conn = None
            self._cleanup_tmp()
            raise

    def close(self):
        """Close the connection and re-encrypt the temp file to disk.

        Idempotent: safe to call any number of times, and a no-op when
        nothing is open.  Never raises when ``_conn`` is ``None``.
        """
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
        if not self._tmp_path:
            return
        self._encrypt_from_temp()
        self._cleanup_tmp()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute SQL and return rows as dicts."""
        if not self._conn:
            raise RuntimeError("Database not open")
        cursor = self._conn.execute(sql, params)
        if cursor.description:
            return [dict(row) for row in cursor.fetchall()]
        self._conn.commit()
        return []

    def insert(self, table: str, data: dict) -> int:
        """Insert a row, return rowid."""
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        cursor = self._conn.execute(sql, tuple(data.values()))
        self._conn.commit()
        return cursor.lastrowid

    def update(self, table: str, set_data: dict, where: dict) -> int:
        """Update rows, return changes count."""
        set_clause = ", ".join(f"{k} = ?" for k in set_data)
        where_clause = " AND ".join(f"{k} = ?" for k in where)
        sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
        params = tuple(set_data.values()) + tuple(where.values())
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.rowcount

    def delete(self, table: str, where: dict) -> int:
        """Delete rows, return changes count."""
        where_clause = " AND ".join(f"{k} = ?" for k in where)
        sql = f"DELETE FROM {table} WHERE {where_clause}"
        cursor = self._conn.execute(sql, tuple(where.values()))
        self._conn.commit()
        return cursor.rowcount

    def select(self, table: str, where: dict | None = None) -> list[dict]:
        """Query rows."""
        sql = f"SELECT * FROM {table}"
        params = ()
        if where:
            where_clause = " AND ".join(f"{k} = ?" for k in where)
            sql += f" WHERE {where_clause}"
            params = tuple(where.values())
        return self.execute(sql, params)

    def get_raw_bytes(self) -> bytes:
        """Return the current PLAINTEXT SQLite bytes (not encrypted).

        When the engine is open this is the live temp-file contents — the
        exact bytes that get uploaded to the gateway (which re-encrypts
        or hashes them).  When the engine is closed, the on-disk file is
        decrypted and its plaintext returned.
        """
        if self._tmp_path:
            return Path(self._tmp_path).read_bytes()
        if self.db_path.exists():
            encrypted = self.db_path.read_bytes()
            return decrypt_file(encrypted, self.passphrase)
        raise FileNotFoundError(f"Database not found: {self.db_path}")

    def create_tables(self):
        """Create default tables for a new database."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS _meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self._conn.commit()

    def list_tables(self) -> list[str]:
        """List all user tables."""
        rows = self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_%' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def table_info(self, table: str) -> list[dict]:
        """Get column info for a table."""
        return self.execute(f"PRAGMA table_info({table})")
