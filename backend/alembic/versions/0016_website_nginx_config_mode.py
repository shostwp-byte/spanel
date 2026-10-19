"""track website nginx ownership and document roots

Revision ID: 0016_website_nginx_config_mode
Revises: 0015_fix_website_id_nullable
Create Date: 2026-06-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_website_nginx_config_mode"
down_revision: Union[str, None] = "0015_fix_website_id_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "websites",
        sa.Column("document_root", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "websites",
        sa.Column("nginx_config_mode", sa.String(length=16), nullable=True),
    )
    # Normalize all existing websites once. The updater regenerates their
    # vhosts immediately after migrations using these managed settings.
    op.execute(
        "UPDATE websites SET document_root = 'public_html', nginx_config_mode = 'managed'"
    )
    with op.batch_alter_table("websites") as batch_op:
        batch_op.alter_column(
            "document_root",
            existing_type=sa.String(length=255),
            nullable=False,
            server_default="public_html",
        )
        batch_op.alter_column(
            "nginx_config_mode",
            existing_type=sa.String(length=16),
            nullable=False,
            server_default="managed",
        )


def downgrade() -> None:
    op.drop_column("websites", "nginx_config_mode")
    op.drop_column("websites", "document_root")
