"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-19

This revision creates the full spanel schema as it stood after the
'apply_simple_migrations' helper had finished. Existing deployments are
stamped to this revision by the upgrade tooling so no DDL is replayed on
servers that already have these tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("username", sa.String(length=64), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(length=255), unique=True, nullable=False, index=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("website_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("storage_limit_mb", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "websites",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("domain", sa.String(length=255), unique=True, nullable=False, index=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("root_path", sa.String(length=500), nullable=False),
        sa.Column("php_version", sa.String(length=16), nullable=False, server_default="8.3"),
        sa.Column("app_type", sa.String(length=32), nullable=False, server_default="wordpress"),
        sa.Column("ssl_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("nginx_custom", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "database_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("website_id", sa.Integer(), sa.ForeignKey("websites.id"), nullable=False),
        sa.Column("db_name", sa.String(length=64), unique=True, nullable=False),
        sa.Column("db_user", sa.String(length=64), unique=True, nullable=False),
        sa.Column("db_password", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("database_accounts")
    op.drop_table("websites")
    op.drop_table("users")
