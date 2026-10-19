"""add per-website WAF rule settings

Revision ID: 0012_website_waf_rules
Revises: 0011_website_waf_enabled
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_website_waf_rules"
down_revision: Union[str, None] = "0011_website_waf_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("websites", sa.Column("waf_default_rules", sa.Text(), nullable=False, server_default=""))
    op.add_column("websites", sa.Column("waf_custom_rules", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("websites", "waf_custom_rules")
    op.drop_column("websites", "waf_default_rules")
