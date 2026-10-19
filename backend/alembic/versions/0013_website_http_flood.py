"""add per-website HTTP flood toggle

Revision ID: 0013_website_http_flood
Revises: 0012_website_waf_rules
Create Date: 2026-06-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_website_http_flood"
down_revision: Union[str, None] = "0012_website_waf_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("websites", sa.Column("http_flood_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("websites", sa.Column("http_flood_config", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("websites", "http_flood_config")
    op.drop_column("websites", "http_flood_enabled")
