"""002 — add version_history, conflict_log, api_key_hash

Revision ID: 002_version_history
Revises: 001_initial
Create Date: 2026-07-25 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_version_history"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_channels",
        sa.Column("api_key_hash", sa.String(64), nullable=True, unique=True),
    )

    op.create_table(
        "version_history",
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "version_type",
            sa.String(32),
            nullable=False,
            server_default="full",
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "database_name", "version"),
        sa.ForeignKeyConstraint(["user_id"], ["user_channels.user_id"]),
    )

    op.create_table(
        "conflict_log",
        sa.Column(
            "conflict_id",
            sa.String(36),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("local_version", sa.Integer(), nullable=False),
        sa.Column("remote_version", sa.Integer(), nullable=False),
        sa.Column("local_hash", sa.String(64), nullable=True),
        sa.Column("remote_hash", sa.String(64), nullable=True),
        sa.Column(
            "resolution",
            sa.String(32),
            nullable=False,
            server_default="lww",
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_channels.user_id"]),
    )


def downgrade() -> None:
    op.drop_table("conflict_log")
    op.drop_table("version_history")
    op.drop_column("user_channels", "api_key_hash")
