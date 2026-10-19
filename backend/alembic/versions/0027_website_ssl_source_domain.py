"""website ssl_source_domain + cloudflare credentials

Revision ID: 0027_website_ssl_source_domain
Revises: 0026_site_app_compose
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_website_ssl_source_domain"
down_revision: Union[str, None] = "0026_site_app_compose"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "websites",
        sa.Column("ssl_source_domain", sa.String(length=253), nullable=True),
    )
    op.create_table(
        "cloudflare_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("zone", sa.String(length=253), nullable=False, unique=True, index=True),
        sa.Column("api_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("cloudflare_credentials")
    op.drop_column("websites", "ssl_source_domain")
