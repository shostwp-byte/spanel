"""DNS Manager addon: who owns which zone

Revision ID: 0039_dns_zones
Revises: 0038_notifications
Create Date: 2026-09-29

PowerDNS keeps the zones themselves; this only records ownership. Created only
when absent (a failed update can leave a table present and the revision
unstamped). A panel where nobody turns the addon on carries one empty table.
"""

import sqlalchemy as sa

from alembic import op

revision = "0039_dns_zones"
down_revision = "0038_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("dns_zones"):
        op.create_table(
            "dns_zones",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("name", sa.String(length=253), nullable=False, unique=True, index=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("dns_zones")
