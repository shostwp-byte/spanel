"""Email addon: mailboxes, and how many each account may have

Revision ID: 0040_mail_accounts
Revises: 0039_dns_zones
Create Date: 2026-09-29

The mail itself lives in the owner's home; this table is the list the panel
hands to the mail server. The limit defaults to 10 rather than 0: a package
that refuses every mailbox the moment an administrator turns the addon on is
the mistake 0032/0034 made with SFTP accounts. Created only when absent (a
failed update can leave a table present and the revision unstamped).
"""

import sqlalchemy as sa

from alembic import op

revision = "0040_mail_accounts"
down_revision = "0039_dns_zones"
branch_labels = None
depends_on = None

DEFAULT_LIMIT = "10"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("mail_accounts"):
        op.create_table(
            "mail_accounts",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("domain", sa.String(length=253), nullable=False, index=True),
            sa.Column("local_part", sa.String(length=64), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("quota_mb", sa.Integer(), nullable=False, server_default="1024"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("domain", "local_part", name="uq_mail_account_address"),
        )
    for table in ("user_packages", "users"):
        if "mail_accounts_limit" not in _columns(table):
            op.add_column(
                table,
                sa.Column("mail_accounts_limit", sa.Integer(), nullable=False, server_default=DEFAULT_LIMIT),
            )


def downgrade() -> None:
    op.drop_column("users", "mail_accounts_limit")
    op.drop_column("user_packages", "mail_accounts_limit")
    op.drop_table("mail_accounts")
