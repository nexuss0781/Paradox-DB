"""004 — persist Telegram file_id for durable downloads

Revision ID: 004_file_id
Revises: 003_phase4
Create Date: 2026-09-12 00:00:00.000000

Downloads previously relied on forwardMessage(message_id), which fails once a
message is no longer visible to the bot (recreated channel, removed bot, per-user
channels). Storing the Telegram file_id lets the gateway fetch bytes via getFile,
which does not depend on channel membership or message age.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_file_id"
down_revision: Union[str, None] = "004_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE paradox_dbs "
        "ADD COLUMN IF NOT EXISTS latest_file_id VARCHAR(256)"
    )
    op.execute(
        "ALTER TABLE database_versions "
        "ADD COLUMN IF NOT EXISTS file_id VARCHAR(256)"
    )
    op.execute(
        "ALTER TABLE database_backups "
        "ADD COLUMN IF NOT EXISTS file_id VARCHAR(256)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE paradox_dbs DROP COLUMN IF EXISTS latest_file_id")
    op.execute("ALTER TABLE database_versions DROP COLUMN IF EXISTS file_id")
    op.execute("ALTER TABLE database_backups DROP COLUMN IF EXISTS file_id")