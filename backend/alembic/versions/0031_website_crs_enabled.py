"""per-website OWASP CRS opt-in

Revision ID: 0031_website_crs_enabled
Revises: 0030_user_terminal_enabled
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0031_website_crs_enabled"
down_revision: Union[str, None] = "0030_user_terminal_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Off for every existing site, and no backfill on purpose.
    #
    # CRS is not free the way the other WAF toggles are. Each site that loads it
    # builds its own rule set inside nginx: measured on a live server, one site
    # costs about 325 MB of RSS, and switching it on for all nineteen WAF-
    # enabled sites at once took nginx from 146 MB to 6.3 GB on a 7.9 GB box.
    # Turning that on for existing customers by default would be an outage.
    #
    # So this is a per-site decision an admin makes deliberately, against the
    # memory the server actually has.
    op.add_column(
        "websites",
        sa.Column("crs_enabled", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("websites", "crs_enabled")
