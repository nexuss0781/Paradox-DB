"""001 initial — create user_channels, database_versions, sync_log

Revision ID: 001_initial
Revises:
Create Date: 2026-07-24 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_channels",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("channel_id", sa.String(64), nullable=False),
        sa.Column("bot_token_id", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "database_versions",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("database_name", sa.String(255), primary_key=True),
        sa.Column("latest_message_id", sa.String(64), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_channels.user_id"]),
    )

    op.create_table(
        "sync_log",
        sa.Column(
            "request_id",
            sa.String(36),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("telegram_message_id", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user_channels.user_id"]),
    )


def downgrade() -> None:
    op.drop_table("sync_log")
    op.drop_table("database_versions")
    op.drop_table("user_channels")
