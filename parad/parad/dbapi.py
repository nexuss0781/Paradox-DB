"""PEP 249 (DB-API 2.0) interface over Paradox-DB Gateway SQL sessions.

Lets SQLAlchemy -- and raw DB-API code -- run SQL against a versioned,
encrypted SQLite database that lives on the gateway.  Every connection
maps to a persistent server-side session, so ``BEGIN ... COMMIT``
semantics match a local SQLite connection, and each committed change is
stored as a brand-new version in the cloud.

Connections are made with a ``parad://`` URL::

    parad://token@local/<project>/<db>?gateway=https://...&passphrase=...
    parad://email:password@local/<project>/<db>?gateway=https://...

or with an explicit API key / passphrase::

    from parad import dbapi
    conn = dbapi.connect(url, api_key="...", passphrase="...")
"""

from __future__ import annotations

import base64
import os
import sqlite3
from typing import Any, Iterable, Sequence

from parad.config import load_config
from parad.connection import parse_url
from parad.gateway import GatewayClient, GatewayError

__all__ = [
    "apilevel",
    "threadsafety",
    "paramstyle",
    "sqlite_version_info",
    "connect",
    "Warning",
    "Error",
    "InterfaceError",
    "DatabaseError",
    "DataError",
    "OperationalError",
    "IntegrityError",
    "InternalError",
    "ProgrammingError",
    "NotSupportedError",
    "ParadRemoteConnection",
    "ParadRemoteCursor",
]

apilevel = "2.0"
threadsafety = 1
paramstyle = "qmark"
sqlite_version_info = sqlite3.sqlite_version_info

# -- exceptions (PEP 249 hierarchy) --------------------------------


class Warning(Exception):  # noqa: N818 - PEP 249 names these without Error
    pass


class Error(Exception):  # noqa: N818
    pass


class InterfaceError(Error):  # noqa: N818
    pass


class DatabaseError(Error):  # noqa: N818
    pass


class DataError(DatabaseError):  # noqa: N818
    pass


class OperationalError(DatabaseError):  # noqa: N818
    pass


class IntegrityError(DatabaseError):  # noqa: N818
    pass


class InternalError(DatabaseError):  # noqa: N818
    pass


class ProgrammingError(DatabaseError):  # noqa: N818
    pass


class NotSupportedError(DatabaseError):  # noqa: N818
    pass


# -- value codec ---------------------------------------------------

_BLOB_MARKER = "__parad_bytes__"


def _encode_param(value: Any) -> Any:
    """Encode a bind parameter for the JSON wire format."""
    if isinstance(value, bytes):
        return {_BLOB_MARKER: base64.b64encode(value).decode("ascii")}
    return value


def _decode_result(value: Any) -> Any:
    """Decode a single result cell from the JSON wire format."""
    if isinstance(value, dict) and _BLOB_MARKER in value:
        return base64.b64decode(value[_BLOB_MARKER])
    return value


def _decode_row(row: Sequence) -> list:
    return [_decode_result(v) for v in row]


_INTEGRITY_MARKERS = (
    "constraint failed",
)


def _map_gateway_error(exc: GatewayError) -> Error:
    status = exc.status_code
    detail = exc.detail
    lowered = str(detail).lower()
    if status in (401, 403):
        return InterfaceError(f"authentication failed: {detail}")
    if status == 404:
        return OperationalError(f"database not found: {detail}")
    if status == 429:
        return OperationalError(f"rate limited: {detail}")
    if status >= 500:
        return OperationalError(f"gateway error: {detail}")
    if any(marker in lowered for marker in _INTEGRITY_MARKERS):
        return IntegrityError(f"integrity error: {detail}")
    return ProgrammingError(str(detail))


# -- connection -----------------------------------------------------


class ParadRemoteConnection:
    """A DB-API connection backed by a persistent gateway SQL session.

    Do not construct directly -- use :func:`connect`.
    """

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        database_id: str,
        database_name: str = "",
        project: str = "",
        passphrase: str = "",
    ):
        self._gw = GatewayClient(gateway_url, api_key)
        self._database_id = database_id
        self._database_name = database_name
        self._project = project
        self._passphrase = passphrase
        self._closed = False
        self.isolation_level = ""
        self.in_transaction = False
        self.total_changes = 0

    @property
    def database_id(self) -> str:
        return self._database_id

    @property
    def gateway_url(self) -> str:
        return self._gw.gateway_url

    def _execute(
        self,
        sql: str,
        params: Iterable = (),
        executescript: bool = False,
        flush: bool = False,
    ) -> dict:
        if self._closed:
            raise InterfaceError("Connection is closed")
        try:
            data = self._gw.sql(
                self._database_id,
                sql=sql,
                params=list(params),
                passphrase=self._passphrase,
                executescript=executescript,
                flush=flush,
            )
        except GatewayError as exc:
            raise _map_gateway_error(exc) from exc
        self.in_transaction = bool(data.get("in_transaction", False))
        self.total_changes = int(data.get("changes", 0) or 0)
        return data

    def cursor(self) -> "ParadRemoteCursor":
        if self._closed:
            raise InterfaceError("Connection is closed")
        return ParadRemoteCursor(self)

    def execute(self, sql: str, params: Iterable = ()) -> dict:
        """Execute *sql* directly on the connection (no cursor)."""
        return self._execute(sql, params)

    def executescript(self, sql: str) -> dict:
        """Execute a multi-statement SQL script on the connection."""
        return self._execute(sql, executescript=True)

    def commit(self) -> None:
        """Commit the session, persisting any dirty work as a new version."""
        self._execute("COMMIT", flush=True)

    def rollback(self) -> None:
        """Roll back the open transaction (a no-op if none is active)."""
        self._execute("ROLLBACK")

    def set_isolation_level(self, level: str) -> None:
        self.isolation_level = level

    def get_isolation_level(self) -> str:
        return self.isolation_level

    def close(self) -> None:
        """Close the connection, persisting any committed but unsynced work."""
        if self._closed:
            return
        self._closed = True
        try:
            self._gw.sql_session_close(self._database_id, commit=True)
        except Exception:
            pass

    def __enter__(self) -> "ParadRemoteConnection":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class ParadRemoteCursor:
    """A DB-API cursor backed by the gateway SQL session."""

    def __init__(self, connection: ParadRemoteConnection):
        self.connection = connection
        self.description = None
        self.rowcount = -1
        self.arraysize = 1
        self.lastrowid = None
        self._rows: list = []

    def execute(self, sql: str, params: Iterable = None):
        data = self.connection._execute(sql, params or ())
        columns = data.get("columns")
        if columns:
            self.description = [(c, None, None, None, None, None, None) for c in columns]
            self.rowcount = -1
            self.lastrowid = None
        else:
            self.description = None
            self.rowcount = int(data.get("rowcount", -1) or -1)
            self.lastrowid = data.get("lastrowid") or None
        self._rows = [_decode_row(r) for r in (data.get("rows") or [])]
        return self

    def executemany(self, sql: str, seq_of_params: Sequence):
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def executescript(self, sql: str):
        self.connection._execute(sql, executescript=True)
        return self

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows.pop(0)

    def fetchmany(self, size: int | None = None):
        if size is None:
            size = self.arraysize
        out, self._rows = self._rows[:size], self._rows[size:]
        return out

    def fetchall(self):
        out, self._rows = self._rows, []
        return out

    def setinputsizes(self, *sizes) -> None:
        pass

    def setoutputsize(self, size, column=None) -> None:
        pass

    def close(self) -> None:
        self._rows = []
        self.description = None

    def __enter__(self) -> "ParadRemoteCursor":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __iter__(self):
        while self._rows:
            yield self._rows.pop(0)


# -- connect ---------------------------------------------------------


def connect(
    url: str,
    api_key: str = "",
    passphrase: str = "",
) -> ParadRemoteConnection:
    """Open a DB-API connection to a gateway-hosted database.

    The URL follows the ``parad://`` scheme used elsewhere in the SDK and
    must include a project scope plus a gateway URL or token::

        parad://token@local/<project>/<db>?gateway=https://paradox-db.onrender.com/v1
        parad://email:password@local/<project>/<db>?gateway=https://...

    ``api_key`` / ``passphrase`` may be supplied explicitly or come from
    the URL / ``~/.paradox/config.json`` / ``PARADOX_API_KEY`` /
    ``PARADOX_PASSPHRASE`` environment variables.
    """
    parsed = parse_url(url)
    name = parsed["name"]
    project = parsed.get("project") or ""
    if not project:
        raise ValueError(
            "server-side SQL requires a project in the URL "
            "(parad://…/local/<project>/<db>)"
        )

    cfg = load_config()
    resolved_gateway = parsed.get("gateway_url") or cfg.sync.gateway_url or ""
    if not resolved_gateway:
        raise ValueError(
            "server-side SQL requires a gateway URL "
            "(?gateway=… in the URL or config sync.gateway_url)"
        )

    token = api_key or parsed.get("token") or ""
    email = parsed.get("email") or ""
    password = parsed.get("password") or ""

    gw = GatewayClient(resolved_gateway)
    if not token and email and password:
        try:
            gw.login(email, password)
        except GatewayError as exc:
            raise OperationalError(f"login to gateway failed: {exc}") from exc
        token = gw.api_key or ""
    if not token:
        token = cfg.sync.api_key or os.environ.get("PARADOX_API_KEY", "")
    if not token:
        raise ValueError(
            "server-side SQL requires an API key "
            "(api_key=…, URL token, or config sync.api_key)"
        )
    gw.api_key = token

    try:
        proj = gw.ensure_project(project)
        db = gw.ensure_database(proj.get("id", ""), name)
    except GatewayError as exc:
        raise OperationalError(f"could not provision database on gateway: {exc}") from exc
    database_id = db.get("id", "")
    if not database_id:
        raise OperationalError("gateway did not return a database id")

    if not passphrase:
        passphrase = parsed.get("passphrase") or ""
    if not passphrase:
        passphrase = os.environ.get("PARADOX_PASSPHRASE", "")
    if not passphrase:
        passphrase = cfg.encryption.passphrase or ""

    return ParadRemoteConnection(
        gateway_url=resolved_gateway,
        api_key=token,
        database_id=database_id,
        database_name=name,
        project=project,
        passphrase=passphrase,
    )
