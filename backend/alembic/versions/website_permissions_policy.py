"""Add permissions_policy column to websites

Revision ID: website_permissions_policy
Revises: nodejs_instances_table
Create Date: 2026-11-13 14:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "website_permissions_policy"
down_revision: Union[str, None] = "nodejs_instances_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("websites"):
        existing = {c["name"] for c in inspector.get_columns("websites")}
        if "permissions_policy" not in existing:
            op.add_column(
                "websites",
                sa.Column("permissions_policy", sa.Text(), nullable=False, server_default=""),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("websites"):
        existing = {c["name"] for c in inspector.get_columns("websites")}
        if "permissions_policy" in existing:
            op.drop_column("websites", "permissions_policy")
