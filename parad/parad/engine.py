"""Local encrypted SQLite engine for parad."""

import os
import sqlite3
import tempfile
from pathlib import Path
from parad.crypto import encrypt_file, decrypt_file


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

    def _decrypt_to_temp(self) -> str:
        """Decrypt DB to a temp file, return path."""
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        encrypted = self.db_path.read_bytes()
        decrypted = decrypt_file(encrypted, self.passphrase)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.write(decrypted)
        tmp.close()
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
        """Open the encrypted database. If create=True and file doesn't exist, create new."""
        if create and not self.db_path.exists():
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

    def close(self):
        """Close and re-encrypt."""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._encrypt_from_temp()
        if self._tmp_path:
            os.unlink(self._tmp_path)
            self._tmp_path = None

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
        """Get the encrypted database as raw bytes (for push)."""
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
