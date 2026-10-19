"""add per-website WAF flag

Revision ID: 0011_website_waf_enabled
Revises: 0010_revoked_tokens
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_website_waf_enabled"
down_revision: Union[str, None] = "0010_revoked_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "websites",
        sa.Column("waf_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("websites", "waf_enabled")
