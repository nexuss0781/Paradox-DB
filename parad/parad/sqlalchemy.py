"""SQLAlchemy dialect for Parad's encrypted SQLite database."""

from __future__ import annotations

from sqlalchemy.dialects.sqlite.base import SQLiteDialect

from . import dbapi


class ParadDialect(SQLiteDialect):
    """SQLite dialect whose DB-API connections are backed by Parad."""

    name = "parad"
    driver = "sqlite"
    supports_statement_cache = False

    @classmethod
    def import_dbapi(cls):
        return dbapi

    def create_connect_args(self, url):
        # Preserve the complete canonical URL, including its API token,
        # gateway, project, database, and passphrase query parameters.
        return [url.render_as_string(hide_password=False)], {"auto_sync": False}

    def get_driver_name(self):
        return "sqlite"


dialect = ParadDialect
