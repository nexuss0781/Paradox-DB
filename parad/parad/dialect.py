"""SQLAlchemy dialect for the Paradox-DB Gateway SQL sessions.

Install via the ``parad[alchemy]`` extra (``sqlalchemy>=2.0``).  Use any
of::

    engine = create_engine("parad://token@local/<project>/<db>?gateway=https://…")
    engine = create_engine("parad+remote://token@local/<project>/<db>?gateway=https://…")

The dialect is a thin layer over :mod:`parad.dbapi`: statements run on a
persistent gateway session, so transactions and committed changes behave
exactly like a local SQLite engine while every commit lands in the cloud
as a new version.
"""

from __future__ import annotations

import httpx

from sqlalchemy.dialects.sqlite.base import SQLiteDialect
from sqlalchemy.pool import SingletonThreadPool


class ParadDialect(SQLiteDialect):
    name = "parad"
    driver = "remote"
    default_paramstyle = "qmark"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        from parad import dbapi

        return dbapi

    def _get_server_version_info(self, connection):
        return self.dbapi.sqlite_version_info

    def create_connect_args(self, url):
        return ([url.render_as_string(hide_password=False)], {})

    @classmethod
    def get_pool_class(cls, url):
        return SingletonThreadPool

    def do_begin(self, dbapi_connection):
        dbapi_connection.execute("BEGIN")

    def is_disconnect(self, e, connection, cursor):
        if isinstance(
            e,
            (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.TransportError,
                ConnectionError,
                TimeoutError,
            ),
        ):
            return True
        from parad.dbapi import Error as DBAPIError

        if isinstance(e, DBAPIError):
            msg = str(e).lower()
            return "gateway" in msg or "session" in msg or "closed" in msg
        return False


dialect = ParadDialect
