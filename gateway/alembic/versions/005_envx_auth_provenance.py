"""Add nullable ENVX/Nexuss identity provenance."""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_envx_auth_provenance"
down_revision: str | tuple[str, str] | None = ("004_api_keys", "004_file_id")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USER_COLUMNS = {
    "auth_project_id": sa.String(128),
    "auth_provider": sa.String(64),
    "auth_issuer": sa.String(512),
    "auth_subject": sa.String(255),
    "auth_permissions": sa.Text(),
    "avatar_url": sa.String(2048),
}
_KEY_COLUMNS = {
    "auth_project_id": sa.String(128),
    "auth_provider": sa.String(64),
    "auth_issuer": sa.String(512),
    "auth_subject": sa.String(255),
    "auth_permissions": sa.Text(),
}


def upgrade() -> None:
    for name, column_type in _USER_COLUMNS.items():
        op.add_column("users", sa.Column(name, column_type, nullable=True))
    for name, column_type in _KEY_COLUMNS.items():
        op.add_column("api_keys", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name in reversed(list(_KEY_COLUMNS)):
        op.drop_column("api_keys", name)
    for name in reversed(list(_USER_COLUMNS)):
        op.drop_column("users", name)
