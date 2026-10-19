"""allow backup schedules to target multiple users

Revision ID: 0006_backup_schedule_user_sets
Revises: 0005_panel_user_email_domain
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_backup_schedule_user_sets"
down_revision: Union[str, None] = "0005_panel_user_email_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("backup_schedules") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("user_ids", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("all_users", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("backup_schedules") as batch_op:
        batch_op.drop_column("all_users")
        batch_op.drop_column("user_ids")
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
