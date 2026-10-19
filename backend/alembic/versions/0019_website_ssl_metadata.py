"""add website SSL metadata

Revision ID: 0019_website_ssl_metadata
Revises: 0018_website_nginx_rewrite_mode
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_website_ssl_metadata"
down_revision: Union[str, None] = "0018_website_nginx_rewrite_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("websites", sa.Column("ssl_mode", sa.String(length=16), nullable=True))
    op.add_column("websites", sa.Column("ssl_cert_path", sa.String(length=500), nullable=True))
    op.add_column("websites", sa.Column("ssl_key_path", sa.String(length=500), nullable=True))
    op.add_column("websites", sa.Column("ssl_ca_path", sa.String(length=500), nullable=True))
    op.add_column("websites", sa.Column("ssl_updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE websites SET ssl_mode = CASE WHEN ssl_enabled THEN 'letsencrypt' ELSE 'none' END")
    with op.batch_alter_table("websites") as batch_op:
        batch_op.alter_column(
            "ssl_mode",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default="none",
        )


def downgrade() -> None:
    op.drop_column("websites", "ssl_updated_at")
    op.drop_column("websites", "ssl_ca_path")
    op.drop_column("websites", "ssl_key_path")
    op.drop_column("websites", "ssl_cert_path")
    op.drop_column("websites", "ssl_mode")
