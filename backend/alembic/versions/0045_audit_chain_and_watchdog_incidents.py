"""Add audit_logs hash-chain columns and watchdog_incidents table

Revision ID: 0045_enterprise_audit_watchdog
Revises: 26efc588faeb
Create Date: 2026-10-23 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0045_enterprise_audit_watchdog"
down_revision: Union[str, None] = "26efc588faeb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "audit_logs" in existing_tables:
        audit_cols = {c["name"] for c in inspector.get_columns("audit_logs")}
        with op.batch_alter_table("audit_logs", schema=None) as batch_op:
            if "prev_hash" not in audit_cols:
                batch_op.add_column(sa.Column("prev_hash", sa.String(length=64), nullable=False, server_default=""))
            if "entry_hash" not in audit_cols:
                batch_op.add_column(sa.Column("entry_hash", sa.String(length=64), nullable=False, server_default=""))

    if "watchdog_incidents" not in existing_tables:
        op.create_table(
            "watchdog_incidents",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("service", sa.String(length=64), nullable=False),
            sa.Column("previous_state", sa.String(length=32), nullable=False, server_default="failed"),
            sa.Column("action", sa.String(length=64), nullable=False, server_default="auto_restart"),
            sa.Column("recovered", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("reason", sa.String(length=64), nullable=False, server_default="background"),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("watchdog_incidents", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_watchdog_incidents_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_watchdog_incidents_service"), ["service"], unique=False)
            batch_op.create_index(batch_op.f("ix_watchdog_incidents_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "watchdog_incidents" in existing_tables:
        op.drop_table("watchdog_incidents")
