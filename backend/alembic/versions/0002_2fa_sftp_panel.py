"""add two-factor auth and sftp backup targets

Revision ID: 0002_2fa_sftp
Revises: 0001_initial
Create Date: 2026-05-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_2fa_sftp"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("totp_secret", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "sftp_backup_targets",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(length=100), unique=True, nullable=False, index=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password", sa.Text(), nullable=True),
        sa.Column("private_key", sa.Text(), nullable=True),
        sa.Column("remote_path", sa.String(length=500), nullable=False, server_default="/backups/spanel"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sftp_backup_targets")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret")
