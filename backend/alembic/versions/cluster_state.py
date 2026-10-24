"""Cluster: zone origins, DNS sync outbox, account transfer jobs

Revision ID: cluster_state
Revises: 0045_enterprise_audit_watchdog
Create Date: 2026-10-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cluster_state"
down_revision: Union[str, None] = "0045_enterprise_audit_watchdog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())

    if "cluster_zone_origins" not in existing_tables:
        op.create_table(
            "cluster_zone_origins",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("zone", sa.String(length=253), nullable=False),
            sa.Column("origin_uid", sa.String(length=40), nullable=True),
            sa.Column("origin_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("origin_owner", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("source_serial", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_cluster_zone_origins_id", "cluster_zone_origins", ["id"])
        op.create_index("ix_cluster_zone_origins_zone", "cluster_zone_origins", ["zone"], unique=True)
        op.create_index("ix_cluster_zone_origins_origin_uid", "cluster_zone_origins", ["origin_uid"])

    if "cluster_dns_outbox" not in existing_tables:
        op.create_table(
            "cluster_dns_outbox",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("node_id", sa.String(length=32), nullable=False),
            sa.Column("zone", sa.String(length=253), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False, server_default="upsert"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("node_id", "zone", name="uq_cluster_dns_outbox_node_zone"),
        )
        op.create_index("ix_cluster_dns_outbox_id", "cluster_dns_outbox", ["id"])
        op.create_index("ix_cluster_dns_outbox_node_id", "cluster_dns_outbox", ["node_id"])
        op.create_index("ix_cluster_dns_outbox_zone", "cluster_dns_outbox", ["zone"])
        op.create_index("ix_cluster_dns_outbox_status", "cluster_dns_outbox", ["status"])
        op.create_index("ix_cluster_dns_outbox_next_attempt_at", "cluster_dns_outbox", ["next_attempt_at"])

    if "cluster_transfer_jobs" not in existing_tables:
        op.create_table(
            "cluster_transfer_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_uid", sa.String(length=32), nullable=False),
            sa.Column("direction", sa.String(length=8), nullable=False, server_default="out"),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("node_id", sa.String(length=40), nullable=False, server_default=""),
            sa.Column("node_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("step", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column("options", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("archive_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("archive_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("archive_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("remote_job_uid", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_cluster_transfer_jobs_id", "cluster_transfer_jobs", ["id"])
        op.create_index("ix_cluster_transfer_jobs_job_uid", "cluster_transfer_jobs", ["job_uid"], unique=True)
        op.create_index("ix_cluster_transfer_jobs_username", "cluster_transfer_jobs", ["username"])
        op.create_index("ix_cluster_transfer_jobs_status", "cluster_transfer_jobs", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    for table in ("cluster_transfer_jobs", "cluster_dns_outbox", "cluster_zone_origins"):
        if table in existing_tables:
            op.drop_table(table)
