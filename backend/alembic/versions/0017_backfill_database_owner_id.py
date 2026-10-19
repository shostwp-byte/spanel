"""backfill missing database owner ids

Revision ID: 0017_backfill_database_owner_id
Revises: 0016_website_nginx_config_mode
Create Date: 2026-07-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_backfill_database_owner_id"
down_revision: Union[str, None] = "0016_website_nginx_config_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKFILL_FROM_WEBSITE_SQL = (
    "UPDATE database_accounts "
    "SET owner_id = ("
    "  SELECT websites.owner_id FROM websites "
    "  WHERE websites.id = database_accounts.website_id"
    ") "
    "WHERE owner_id IS NULL "
    "  AND website_id IS NOT NULL "
    "  AND EXISTS ("
    "    SELECT 1 FROM websites "
    "    WHERE websites.id = database_accounts.website_id"
    "  )"
)

BACKFILL_FROM_ADMIN_SQL = (
    "UPDATE database_accounts "
    "SET owner_id = ("
    "  SELECT users.id FROM users "
    "  ORDER BY CASE WHEN users.role = 'admin' THEN 0 ELSE 1 END, users.id "
    "  LIMIT 1"
    ") "
    "WHERE owner_id IS NULL "
    "  AND EXISTS (SELECT 1 FROM users)"
)


def upgrade() -> None:
    # Some servers upgraded from 1.0.4 after database_accounts.website_id had
    # become nullable, leaving standalone or partially migrated databases with
    # owner_id = NULL. That breaks FastAPI response validation for /databases.
    op.execute(BACKFILL_FROM_WEBSITE_SQL)
    op.execute(BACKFILL_FROM_ADMIN_SQL)
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("database_accounts") as batch_op:
            batch_op.alter_column(
                "owner_id",
                existing_type=sa.Integer(),
                nullable=False,
            )
    else:
        op.alter_column(
            "database_accounts",
            "owner_id",
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("database_accounts") as batch_op:
            batch_op.alter_column(
                "owner_id",
                existing_type=sa.Integer(),
                nullable=True,
            )
    else:
        op.alter_column(
            "database_accounts",
            "owner_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
