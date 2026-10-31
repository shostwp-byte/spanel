"""Unify package-first count quota limits (-1 unlimited, 0 disabled, >0 max)

Revision ID: unify_package_quota_limits
Revises: mail_upgrade
Create Date: 2026-10-31 10:00:00.000000

Normalizes the only two historical columns where ``0`` previously acted as
"unlimited":
  1. ``user_packages.subdomain_limit = 0`` -> ``-1`` (migration f7a7bbef3278
     added ``subdomain_limit`` with ``server_default='0'`` and ``websites.py``
     historically only enforced ``subdomain_limit > 0``).
  2. ``users.website_limit = 0`` (where ``package_id IS NULL`` and
     ``role != 'admin'``) -> ``-1`` (``websites.py`` historically treated
     ``website_limit == 0`` on un-packaged users as unlimited).

``database_limit``, ``sftp_accounts_limit``, and ``mail_accounts_limit`` are
intentionally NOT converted from ``0`` to ``-1`` because ``0`` already meant
"disabled" on those three columns. Finally, denormalized count columns on
``users`` linked to a ``user_packages`` row are refreshed from their package.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "unify_package_quota_limits"
down_revision: Union[str, None] = "mail_upgrade"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    pkg_cols = (
        {col["name"] for col in inspector.get_columns("user_packages")}
        if "user_packages" in tables
        else set()
    )
    user_cols = (
        {col["name"] for col in inspector.get_columns("users")}
        if "users" in tables
        else set()
    )

    if "subdomain_limit" in pkg_cols:
        bind.execute(
            sa.text(
                "UPDATE user_packages SET subdomain_limit = -1 WHERE subdomain_limit = 0"
            )
        )
        if bind.dialect.name != "sqlite":
            op.alter_column(
                "user_packages",
                "subdomain_limit",
                existing_type=sa.Integer(),
                existing_nullable=False,
                server_default="20",
            )

    if {"website_limit", "package_id", "role"} <= user_cols:
        bind.execute(
            sa.text(
                "UPDATE users SET website_limit = -1 "
                "WHERE package_id IS NULL AND role != 'admin' AND website_limit = 0"
            )
        )

    sync_fields = [
        col
        for col in (
            "website_limit",
            "database_limit",
            "sftp_accounts_limit",
            "mail_accounts_limit",
        )
        if col in pkg_cols and col in user_cols
    ]
    if sync_fields and "package_id" in user_cols and "id" in pkg_cols:
        select_cols = ", ".join(["id", *sync_fields])
        pkg_rows = bind.execute(
            sa.text(f"SELECT {select_cols} FROM user_packages")
        ).mappings().all()
        set_clause = ", ".join(f"{col} = :{col}" for col in sync_fields)
        update_stmt = sa.text(
            f"UPDATE users SET {set_clause} WHERE package_id = :pkg_id"
        )
        for row in pkg_rows:
            params = {"pkg_id": row["id"]}
            for col in sync_fields:
                raw_val = row[col]
                if raw_val is not None:
                    iv = int(raw_val)
                    params[col] = -1 if iv < 0 else iv
                else:
                    params[col] = -1
            bind.execute(update_stmt, params)


def downgrade() -> None:
    # Data normalization is intentionally one-way because turning -1 back into 0
    # would disable subdomain/website creation under pre-unification code paths
    # that check >= 0.
    pass
