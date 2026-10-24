"""Email: autoresponder schedules, DKIM rotation, DMARC, MTA-STS and mail hosts

Revision ID: mail_upgrade
Revises: api_hardening
Create Date: 2026-10-24 09:00:00.000000

An autoresponder gets an on/off switch, a date range and how often one sender
hears it. A mail domain gets what two-selector DKIM rotation keeps (the key
being published, the key being retired, where the rotation stands), its DMARC
policy, its MTA-STS mode and TLS-RPT addresses, and which of its mail hosts
(mail., mta-sts., autoconfig., autodiscover.) are served with a certificate.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "mail_upgrade"
down_revision: Union[str, None] = "api_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUTORESPONDER_COLUMNS = (
    ("enabled", lambda: sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1")),
    ("start_date", lambda: sa.Column("start_date", sa.String(length=10), nullable=False, server_default="")),
    ("end_date", lambda: sa.Column("end_date", sa.String(length=10), nullable=False, server_default="")),
    ("interval_days", lambda: sa.Column("interval_days", sa.Integer(), nullable=False, server_default="1")),
)

DOMAIN_COLUMNS = (
    ("dkim_selector", lambda: sa.Column("dkim_selector", sa.String(length=63), nullable=False, server_default="spanel")),
    ("dkim_next_selector", lambda: sa.Column("dkim_next_selector", sa.String(length=63), nullable=False, server_default="")),
    ("dkim_next_public", lambda: sa.Column("dkim_next_public", sa.Text(), nullable=False, server_default="")),
    ("dkim_old_selector", lambda: sa.Column("dkim_old_selector", sa.String(length=63), nullable=False, server_default="")),
    ("dkim_old_public", lambda: sa.Column("dkim_old_public", sa.Text(), nullable=False, server_default="")),
    ("dkim_state", lambda: sa.Column("dkim_state", sa.String(length=16), nullable=False, server_default="")),
    ("dkim_state_at", lambda: sa.Column("dkim_state_at", sa.DateTime(), nullable=True)),
    ("dkim_rotated_at", lambda: sa.Column("dkim_rotated_at", sa.DateTime(), nullable=True)),
    ("dmarc_policy", lambda: sa.Column("dmarc_policy", sa.Text(), nullable=False, server_default="")),
    ("mta_sts_mode", lambda: sa.Column("mta_sts_mode", sa.String(length=10), nullable=False, server_default="")),
    ("mta_sts_id", lambda: sa.Column("mta_sts_id", sa.String(length=32), nullable=False, server_default="")),
    ("tls_rpt", lambda: sa.Column("tls_rpt", sa.Text(), nullable=False, server_default="")),
    ("mail_hosts", lambda: sa.Column("mail_hosts", sa.String(length=255), nullable=False, server_default="")),
)


def _add(table: str, columns) -> None:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return
    have = {column["name"] for column in inspector.get_columns(table)}
    missing = [make() for name, make in columns if name not in have]
    if not missing:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for column in missing:
            batch_op.add_column(column)


def _drop(table: str, columns) -> None:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return
    have = {column["name"] for column in inspector.get_columns(table)}
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name, _ in columns:
            if name in have:
                batch_op.drop_column(name)


def upgrade() -> None:
    _add("mail_autoresponders", AUTORESPONDER_COLUMNS)
    _add("mail_domains", DOMAIN_COLUMNS)


def downgrade() -> None:
    _drop("mail_domains", DOMAIN_COLUMNS)
    _drop("mail_autoresponders", AUTORESPONDER_COLUMNS)
