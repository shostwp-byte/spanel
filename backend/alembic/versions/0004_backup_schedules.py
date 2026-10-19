"""add backup schedules

Revision ID: 0004_backup_schedules
Revises: 0003_site_linux_users
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_backup_schedules"
down_revision: Union[str, None] = "0003_site_linux_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backup_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("sftp_backup_targets.id"), nullable=True),
        sa.Column("schedule", sa.String(length=100), nullable=False, server_default="0 2 * * *"),
        sa.Column("retention", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("last_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backup_schedules")
