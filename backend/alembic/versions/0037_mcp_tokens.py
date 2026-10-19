"""personal tokens for AI assistants reaching the panel over MCP

Revision ID: 0037_mcp_tokens
Revises: 0036_s3_backup_targets
Create Date: 2026-09-24

One table, created only when it is absent: a panel that has been through a
failed update can be left with the table present and the revision unstamped,
and CREATE TABLE on an existing name aborts the whole migration.

Nothing else in the schema changes. A panel where nobody turns the addon on
carries an empty table and behaves exactly as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_mcp_tokens"
down_revision = "0036_s3_backup_targets"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if _has_table("mcp_tokens"):
        return
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        # SHA-256 hex is 64 characters. The token itself is never stored.
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("can_write", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    if _has_table("mcp_tokens"):
        op.drop_table("mcp_tokens")
