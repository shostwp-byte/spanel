"""Per-account resource limit columns, and MariaDB hourly limits

The users table has carried the per-account copy of the package limits
(CPU, memory, tasks, IO, inodes, MySQL quota) since they were added, but only
through core.database._ensure_resource_limit_columns at startup and never in
the User model, so whatever a package or an administrator set was lost the
moment the request ended. The model maps them now; this migration makes an
alembic-only database carry them too, idempotently.

New: MAX_QUERIES_PER_HOUR, MAX_UPDATES_PER_HOUR and MAX_CONNECTIONS_PER_HOUR
for the database accounts a user owns, on packages and users. 0 means no
limit, which is what every existing package and user gets.

Revision ID: isolation_resource_limits
Revises: 0045_enterprise_audit_watchdog
Create Date: 2026-10-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "isolation_resource_limits"
down_revision: Union[str, None] = "0045_enterprise_audit_watchdog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_HOURLY = (
    ("mysql_max_queries_per_hour", "0"),
    ("mysql_max_updates_per_hour", "0"),
    ("mysql_max_connections_per_hour", "0"),
)
# Same defaults as core.database._ensure_resource_limit_columns.
PACKAGE_LIMITS = (
    ("cpu_quota_percent", "100"),
    ("io_read_mbps", "0"),
    ("io_write_mbps", "0"),
    ("iops_read_max", "0"),
    ("iops_write_max", "0"),
    ("memory_high_mb", "1024"),
    ("memory_max_mb", "2048"),
    ("tasks_max", "512"),
    ("mysql_storage_limit_mb", "0"),
    ("mysql_max_db_size_mb", "0"),
)
USER_LIMITS = (
    ("bandwidth_limit_mb", "0"),
    ("inode_limit", "0"),
    ("database_limit", "5"),
) + PACKAGE_LIMITS

TABLE_COLUMNS = {
    "user_packages": PACKAGE_LIMITS + MYSQL_HOURLY,
    "users": USER_LIMITS + MYSQL_HOURLY,
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    for table, columns in TABLE_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        missing = [(name, default) for name, default in columns if name not in present]
        if not missing:
            continue
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name, default in missing:
                batch_op.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default=default))


def downgrade() -> None:
    # Only the hourly limits are this revision's own; the other columns
    # predate it on every server that ran _ensure_resource_limit_columns.
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    for table in TABLE_COLUMNS:
        if table not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        with op.batch_alter_table(table, schema=None) as batch_op:
            for name, _ in MYSQL_HOURLY:
                if name in present:
                    batch_op.drop_column(name)
