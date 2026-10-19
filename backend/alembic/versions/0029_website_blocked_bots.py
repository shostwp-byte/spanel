"""per-website bot blocking list

Revision ID: 0029_website_blocked_bots
Revises: 0028_user_email_not_unique
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029_website_blocked_bots"
down_revision: Union[str, None] = "0028_user_email_not_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Newline-separated User-Agent substrings; empty means block nothing, which
    # is what every existing site gets. server_default rather than only a Python
    # default so rows written outside the ORM cannot leave it NULL - the vhost
    # renderer treats the value as text.
    op.add_column(
        "websites",
        sa.Column("blocked_bots", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("websites", "blocked_bots")
