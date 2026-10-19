"""Email addon, OPanel's structure: mail domains, forwarders, suspended mailboxes

Revision ID: 0041_mail_domains
Revises: 0040_mail_accounts
Create Date: 2026-09-29

Operator, 2026-09-29: "Chưa ổn. Mình thấy bạn nên login vào
opanel.media.io.vn để xem mail bên đó cấu trúc sao." Email is turned on per
domain (catch-all, DKIM key, webmail host, relay, the domain's own DNS
records), a domain has forwarders as well as mailboxes, and a mailbox can be
suspended. Every domain that already has mailboxes gets its row here, owned
like its mailboxes. Created only when absent (a failed update can leave a
table present and the revision unstamped).
"""

import sqlalchemy as sa

from alembic import op

revision = "0041_mail_domains"
down_revision = "0040_mail_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("mail_domains"):
        op.create_table(
            "mail_domains",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("domain", sa.String(length=253), nullable=False, unique=True, index=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("catch_all", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("dkim_public", sa.Text(), nullable=False, server_default=""),
            sa.Column("webmail_host", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("relay", sa.String(length=40), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.execute(
            "INSERT INTO mail_domains (domain, owner_id, catch_all, dkim_public, webmail_host, relay, created_at) "
            "SELECT domain, MIN(owner_id), '', '', 0, '', CURRENT_TIMESTAMP FROM mail_accounts GROUP BY domain"
        )
    if not inspector.has_table("mail_forwarders"):
        op.create_table(
            "mail_forwarders",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("domain", sa.String(length=253), nullable=False, index=True),
            sa.Column("local_part", sa.String(length=64), nullable=False),
            sa.Column("destinations", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("domain", "local_part", name="uq_mail_forwarder_address"),
        )
    columns = {column["name"] for column in inspector.get_columns("mail_accounts")}
    if "enabled" not in columns:
        op.add_column("mail_accounts", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("mail_accounts", "enabled")
    op.drop_table("mail_forwarders")
    op.drop_table("mail_domains")
