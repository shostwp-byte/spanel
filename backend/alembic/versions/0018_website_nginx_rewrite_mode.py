"""add managed nginx rewrite mode

Revision ID: 0018_website_nginx_rewrite_mode
Revises: 0017_backfill_database_owner_id
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_website_nginx_rewrite_mode"
down_revision: Union[str, None] = "0017_backfill_database_owner_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "websites",
        sa.Column("nginx_rewrite_mode", sa.String(length=32), nullable=True),
    )
    op.execute(
        "UPDATE websites "
        "SET nginx_rewrite_mode = CASE "
        "WHEN COALESCE(app_type, 'wordpress') IN ('wordpress', 'php') THEN 'front_controller' "
        "ELSE 'none' "
        "END"
    )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("websites") as batch_op:
            batch_op.alter_column(
                "nginx_rewrite_mode",
                existing_type=sa.String(length=32),
                nullable=False,
                server_default="none",
            )
    else:
        op.alter_column(
            "websites",
            "nginx_rewrite_mode",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="none",
        )


def downgrade() -> None:
    op.drop_column("websites", "nginx_rewrite_mode")
