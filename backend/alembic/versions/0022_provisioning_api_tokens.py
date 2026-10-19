"""add provisioning api tokens

Revision ID: 0022_provisioning_api_tokens
Revises: 0021_user_packages
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_provisioning_api_tokens"
down_revision: Union[str, None] = "0021_user_packages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default="provisioning:read,provisioning:write"),
        sa.Column("allowed_ips", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_tokens_id"), "api_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_api_tokens_name"), "api_tokens", ["name"], unique=False)
    op.create_index(op.f("ix_api_tokens_token_hash"), "api_tokens", ["token_hash"], unique=True)

    op.create_table(
        "provisioning_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("primary_website_id", sa.Integer(), nullable=True),
        sa.Column("package_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("last_action", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["primary_website_id"], ["websites.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["package_id"], ["user_packages.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_provisioning_accounts_id"), "provisioning_accounts", ["id"], unique=False)
    op.create_index(op.f("ix_provisioning_accounts_external_id"), "provisioning_accounts", ["external_id"], unique=True)
    op.create_index(op.f("ix_provisioning_accounts_user_id"), "provisioning_accounts", ["user_id"], unique=False)

    # Phase 2: enhance UserPackage fields
    with op.batch_alter_table("user_packages") as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("database_limit", sa.Integer(), nullable=False, server_default="5"))
        batch_op.add_column(sa.Column("alias_limit", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("backup_retention_days", sa.Integer(), nullable=False, server_default="7"))
        batch_op.add_column(sa.Column("terminal_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("waf_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")))
        batch_op.add_column(sa.Column("wordpress_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    op.create_index(op.f("ix_user_packages_slug"), "user_packages", ["slug"], unique=True)

def downgrade() -> None:
    op.drop_index(op.f("ix_user_packages_slug"), table_name="user_packages")
    with op.batch_alter_table("user_packages") as batch_op:
        batch_op.drop_column("wordpress_enabled")
        batch_op.drop_column("waf_enabled")
        batch_op.drop_column("terminal_enabled")
        batch_op.drop_column("backup_retention_days")
        batch_op.drop_column("alias_limit")
        batch_op.drop_column("database_limit")
        batch_op.drop_column("slug")

    op.drop_index(op.f("ix_provisioning_accounts_user_id"), table_name="provisioning_accounts")
    op.drop_index(op.f("ix_provisioning_accounts_external_id"), table_name="provisioning_accounts")
    op.drop_index(op.f("ix_provisioning_accounts_id"), table_name="provisioning_accounts")
    op.drop_table("provisioning_accounts")

    op.drop_index(op.f("ix_api_tokens_token_hash"), table_name="api_tokens")
    op.drop_index(op.f("ix_api_tokens_name"), table_name="api_tokens")
    op.drop_index(op.f("ix_api_tokens_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
