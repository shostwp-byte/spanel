"""notifications addon: preferences, delivery log, sign-in addresses

Revision ID: 0038_notifications
Revises: 0037_mcp_tokens
Create Date: 2026-09-27

Three tables, each created only when absent (a failed update can leave a
table present and the revision unstamped). A panel where nobody turns the
addon on carries three empty tables and behaves exactly as before.
"""

import sqlalchemy as sa

from alembic import op

revision = "0038_notifications"
down_revision = "0037_mcp_tokens"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("notification_prefs"):
        op.create_table(
            "notification_prefs",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("telegram_chat_id", sa.String(length=32), nullable=True),
            sa.Column("telegram_link_code", sa.String(length=32), nullable=True),
            sa.Column("telegram_link_expires_at", sa.DateTime(), nullable=True),
            sa.Column("muted_events", sa.Text(), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not _has_table("notification_log"):
        op.create_table(
            "notification_log",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("event", sa.String(length=48), nullable=False, index=True),
            sa.Column("channel", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("dedupe_key", sa.String(length=191), nullable=True, index=True),
        )
    if not _has_table("login_sources"):
        op.create_table(
            "login_sources",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("ip", sa.String(length=64), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "ip", name="uq_login_sources_user_ip"),
        )


def downgrade() -> None:
    for name in ("login_sources", "notification_log", "notification_prefs"):
        if _has_table(name):
            op.drop_table(name)
