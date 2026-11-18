"""Raise the old default TasksMax (80, 512) to 2048, once

a263c4a raised the package/user TasksMax default to 2048 (Next.js / Tokio
thread pools hit EAGAIN under 512) and moved existing rows with an UPDATE in
_ensure_resource_limit_columns, which runs at every start: an admin could no
longer keep 80 or 512 on a package. The raise of rows still on an old
default happens here, once.

Revision ID: tasks_max_2048_once
Revises: website_permissions_policy
Create Date: 2026-11-14 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "tasks_max_2048_once"
down_revision: Union[str, None] = "website_permissions_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("user_packages", "users")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        if not inspector.has_table(table):
            continue
        if "tasks_max" not in {c["name"] for c in inspector.get_columns(table)}:
            continue
        op.execute(sa.text(f"UPDATE {table} SET tasks_max = 2048 WHERE tasks_max IN (80, 512)"))


def downgrade() -> None:
    # Which rows were raised is not recorded; leave the values as they are.
    pass
