"""whitelabel and webhooks

Revision ID: 0043_whitelabel_and_webhooks
Revises: 0042_reseller_hierarchy
Create Date: 2026-10-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0043_whitelabel_and_webhooks"
down_revision: Union[str, None] = "0042_reseller_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whitelabel_configs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("reseller_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("company_name", sa.String(length=100), nullable=False, server_default="Control Panel"),
        sa.Column("panel_domain", sa.String(length=253), nullable=True, unique=True, index=True),
        sa.Column("logo_url", sa.String(length=255), nullable=True),
        sa.Column("primary_color", sa.String(length=32), nullable=False, server_default="#4f46e5"),
        sa.Column("support_url", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=True),
        sa.Column("events", sa.Text(), nullable=False, server_default="*"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("webhook_endpoints")
    op.drop_table("whitelabel_configs")
