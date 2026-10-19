"""add site apps and node hosting package limits

Revision ID: 0023_site_apps
Revises: 0022_provisioning_api_tokens
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_site_apps"
down_revision: Union[str, None] = "0022_provisioning_api_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_apps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("website_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, server_default="app"),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="proxy"),
        sa.Column("app_root", sa.String(length=255), nullable=False, server_default="app"),
        sa.Column("start_kind", sa.String(length=16), nullable=True),
        sa.Column("start_arg", sa.String(length=255), nullable=True),
        sa.Column("node_major", sa.String(length=8), nullable=True),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("memory_limit_mb", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("autostart", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="stopped"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["website_id"], ["websites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("website_id", "name", name="uq_site_apps_website_name"),
    )
    op.create_index(op.f("ix_site_apps_id"), "site_apps", ["id"], unique=False)
    op.create_index(op.f("ix_site_apps_website_id"), "site_apps", ["website_id"], unique=False)
    op.create_index(op.f("ix_site_apps_port"), "site_apps", ["port"], unique=True)

    # Off by default: existing packages keep behaving exactly as before until an
    # admin raises the limit.
    with op.batch_alter_table("user_packages") as batch_op:
        batch_op.add_column(sa.Column("node_apps_limit", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("node_app_memory_mb", sa.Integer(), nullable=False, server_default="512"))


def downgrade() -> None:
    with op.batch_alter_table("user_packages") as batch_op:
        batch_op.drop_column("node_app_memory_mb")
        batch_op.drop_column("node_apps_limit")
    op.drop_index(op.f("ix_site_apps_port"), table_name="site_apps")
    op.drop_index(op.f("ix_site_apps_website_id"), table_name="site_apps")
    op.drop_index(op.f("ix_site_apps_id"), table_name="site_apps")
    op.drop_table("site_apps")
