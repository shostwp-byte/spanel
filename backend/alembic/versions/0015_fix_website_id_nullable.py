"""fix website_id nullable on sqlite

Revision ID: 0015_fix_website_id_nullable
Revises: 0014_database_owner_id
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_fix_website_id_nullable"
down_revision: Union[str, None] = "0014_database_owner_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite needs batch mode to change column nullability
        with op.batch_alter_table("database_accounts") as batch_op:
            batch_op.alter_column("website_id", existing_type=sa.Integer(), nullable=True)
    # Non-SQLite was already handled in 0014


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("database_accounts") as batch_op:
            batch_op.alter_column("website_id", existing_type=sa.Integer(), nullable=False)
