"""Add reseller_id ownership tracking to provisioning_accounts

Revision ID: reseller_provisioning_scope
Revises: unify_package_quota_limits
Create Date: 2026-11-01 15:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "reseller_provisioning_scope"
down_revision: Union[str, None] = "unify_package_quota_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "provisioning_accounts" not in tables:
        return

    cols = {c["name"] for c in inspector.get_columns("provisioning_accounts")}
    if "reseller_id" not in cols:
        op.add_column(
            "provisioning_accounts",
            sa.Column("reseller_id", sa.Integer(), nullable=True),
        )
    indexes = {idx["name"] for idx in inspector.get_indexes("provisioning_accounts")}
    if "ix_provisioning_accounts_reseller_id" not in indexes:
        op.create_index(
            "ix_provisioning_accounts_reseller_id",
            "provisioning_accounts",
            ["reseller_id"],
            unique=False,
        )
    if "users" in tables:
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if {"id", "reseller_id"} <= user_cols:
            bind.execute(
                sa.text(
                    "UPDATE provisioning_accounts "
                    "SET reseller_id = (SELECT u.reseller_id FROM users u WHERE u.id = provisioning_accounts.user_id) "
                    "WHERE reseller_id IS NULL AND user_id IS NOT NULL"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "provisioning_accounts" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("provisioning_accounts")}
    if "ix_provisioning_accounts_reseller_id" in indexes:
        op.drop_index("ix_provisioning_accounts_reseller_id", table_name="provisioning_accounts")
    cols = {c["name"] for c in inspector.get_columns("provisioning_accounts")}
    if "reseller_id" in cols and bind.dialect.name != "sqlite":
        op.drop_column("provisioning_accounts", "reseller_id")
