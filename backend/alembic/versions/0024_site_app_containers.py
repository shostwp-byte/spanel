"""add container fields and environment to site apps

Revision ID: 0024_site_app_containers
Revises: 0023_site_apps
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_site_app_containers"
down_revision: Union[str, None] = "0023_site_apps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.add_column(sa.Column("image", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("container_port", sa.Integer(), nullable=False, server_default="3000"))
        batch_op.add_column(sa.Column("cpu_limit", sa.String(length=8), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("env", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.drop_column("env")
        batch_op.drop_column("cpu_limit")
        batch_op.drop_column("container_port")
        batch_op.drop_column("image")
