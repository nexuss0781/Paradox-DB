"""003 — Phase 4: users, projects, paradox_dbs, database_backups

Revision ID: 003_phase4
Revises: 002_version_history
Create Date: 2026-07-26 00:00:00.000000

Drops old tables (user_channels, version_history, old database_versions,
old sync_log, old conflict_log) and recreates with new schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003_phase4"
down_revision: Union[str, None] = "002_version_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Drop old tables ──
    op.execute("DROP TABLE IF EXISTS conflict_log CASCADE")
    op.execute("DROP TABLE IF EXISTS sync_log CASCADE")
    op.execute("DROP TABLE IF EXISTS version_history CASCADE")
    op.execute("DROP TABLE IF EXISTS database_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS user_channels CASCADE")

    # ── users ──
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── projects ──
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── paradox_dbs ──
    op.create_table(
        "paradox_dbs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_message_id", sa.String(64), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── database_versions (new schema) ──
    op.create_table(
        "database_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("db_id", UUID(as_uuid=True), sa.ForeignKey("paradox_dbs.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── database_backups ──
    op.create_table(
        "database_backups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("db_id", UUID(as_uuid=True), sa.ForeignKey("paradox_dbs.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # ── sync_log (new schema with UUID user_id) ──
    op.create_table(
        "sync_log",
        sa.Column("request_id", sa.String(36), primary_key=True, server_default=lambda: str(sa.text("gen_random_uuid()"))),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("telegram_message_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )

    # ── conflict_log (new schema) ──
    op.create_table(
        "conflict_log",
        sa.Column("conflict_id", sa.String(36), primary_key=True, server_default=lambda: str(sa.text("gen_random_uuid()"))),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("local_version", sa.Integer(), nullable=False),
        sa.Column("remote_version", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.String(32), nullable=False, server_default="lww"),
        sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conflict_log CASCADE")
    op.execute("DROP TABLE IF EXISTS sync_log CASCADE")
    op.execute("DROP TABLE IF EXISTS database_backups CASCADE")
    op.execute("DROP TABLE IF EXISTS database_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS paradox_dbs CASCADE")
    op.execute("DROP TABLE IF EXISTS projects CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
