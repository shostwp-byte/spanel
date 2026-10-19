"""add per-site linux user field

Revision ID: 0003_site_linux_users
Revises: 0002_2fa_sftp
Create Date: 2026-05-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_site_linux_users"
down_revision: Union[str, None] = "0002_2fa_sftp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("websites") as batch_op:
        batch_op.add_column(sa.Column("linux_user", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("websites") as batch_op:
        batch_op.drop_column("linux_user")
