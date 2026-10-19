"""per-user terminal permission, enforced at the API

Revision ID: 0030_user_terminal_enabled
Revises: 0029_website_blocked_bots
Create Date: 2026-09-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030_user_terminal_enabled"
down_revision: Union[str, None] = "0029_website_blocked_bots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Added with server_default="0" so the column is never NULL, then existing
    # rows are backfilled to 1.
    #
    # The backfill is the point. UserPackage.terminal_enabled has existed since
    # packages were added but was never read: the terminal only ever checked
    # website ownership, so every end user has had terminal access all along.
    # Turning the flag on for the first time must not quietly take that away
    # from accounts that are using it today - on the server this was found on,
    # all thirteen end users would have lost it mid-session. Admins can revoke
    # per user, or assign a package that does.
    #
    # New accounts get the model default instead, which is off.
    op.add_column(
        "users",
        sa.Column("terminal_enabled", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE users SET terminal_enabled = 1")


def downgrade() -> None:
    op.drop_column("users", "terminal_enabled")
