"""add compose source to site apps

Revision ID: 0026_site_app_compose
Revises: 0025_apps_independent
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026_site_app_compose"
down_revision: Union[str, None] = "0025_apps_independent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.add_column(sa.Column("compose_source", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("web_service", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.drop_column("web_service")
        batch_op.drop_column("compose_source")
