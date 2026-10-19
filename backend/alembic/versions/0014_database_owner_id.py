"""database belongs to user, not website

Revision ID: 0014_database_owner_id
Revises: 0013_website_http_flood
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_database_owner_id"
down_revision: Union[str, None] = "0013_website_http_flood"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists (works for SQLite and other backends)."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        result = bind.execute(sa.text(f"PRAGMA table_info('{table}')"))
        columns = [row[1] for row in result]
        return column in columns
    from sqlalchemy import inspect
    insp = inspect(bind)
    col_names = [c["name"] for c in insp.get_columns(table)]
    return column in col_names


def _get_column_notnull(table: str, column: str) -> bool:
    """Check if a column is NOT NULL in SQLite."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return False
    result = bind.execute(sa.text(f"PRAGMA table_info('{table}')"))
    for row in result:
        if row[1] == column:
            return bool(row[3])  # notnull flag
    return False


def _backfill_database_owner_id() -> None:
    op.execute(
        "UPDATE database_accounts SET owner_id = ("
        "  SELECT websites.owner_id FROM websites WHERE websites.id = database_accounts.website_id"
        ") WHERE owner_id IS NULL AND website_id IS NOT NULL"
    )
    op.execute(
        "UPDATE database_accounts SET owner_id = ("
        "  SELECT users.id FROM users"
        "  ORDER BY CASE WHEN users.role = 'admin' THEN 0 ELSE 1 END, users.id"
        "  LIMIT 1"
        ") WHERE owner_id IS NULL AND EXISTS (SELECT 1 FROM users)"
    )


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER COLUMN nullable. Use batch mode to recreate table.
        needs_rebuild = False

        # First ensure owner_id column exists
        if not _column_exists("database_accounts", "owner_id"):
            needs_rebuild = True
        elif _get_column_notnull("database_accounts", "website_id"):
            # website_id is still NOT NULL, need to rebuild
            needs_rebuild = True

        if needs_rebuild:
            with op.batch_alter_table("database_accounts") as batch_op:
                if not _column_exists("database_accounts", "owner_id"):
                    batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
                batch_op.alter_column("website_id", existing_type=sa.Integer(), nullable=True)

        _backfill_database_owner_id()
    else:
        if not _column_exists("database_accounts", "owner_id"):
            op.add_column("database_accounts", sa.Column("owner_id", sa.Integer(), nullable=True))
        _backfill_database_owner_id()
        op.alter_column("database_accounts", "owner_id", existing_type=sa.Integer(), nullable=False)
        op.alter_column("database_accounts", "website_id", existing_type=sa.Integer(), nullable=True)
        op.create_foreign_key("fk_database_accounts_owner_id", "database_accounts", "users", ["owner_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("database_accounts") as batch_op:
            batch_op.drop_column("owner_id")
            batch_op.alter_column("website_id", existing_type=sa.Integer(), nullable=False)
    else:
        op.drop_constraint("fk_database_accounts_owner_id", "database_accounts", type_="foreignkey")
        op.alter_column("database_accounts", "website_id", existing_type=sa.Integer(), nullable=False)
        op.drop_column("database_accounts", "owner_id")
