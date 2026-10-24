"""Provisioning API hardening: token expiry, rate limit, creator, idempotency keys

Revision ID: api_hardening
Revises: 0045_enterprise_audit_watchdog
Create Date: 2026-10-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "api_hardening"
down_revision: Union[str, None] = "0045_enterprise_audit_watchdog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "api_tokens" in existing_tables:
        token_cols = {c["name"] for c in inspector.get_columns("api_tokens")}
        with op.batch_alter_table("api_tokens", schema=None) as batch_op:
            # NULL keeps every existing token valid forever, as it was.
            if "expires_at" not in token_cols:
                batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
            # 0 means the panel default (see services/provisioning.py).
            if "rate_limit_per_minute" not in token_cols:
                batch_op.add_column(sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="0"))
            if "created_by_id" not in token_cols:
                batch_op.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))

    if "provisioning_idempotency_keys" not in existing_tables:
        op.create_table(
            "provisioning_idempotency_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("token_id", sa.Integer(), nullable=False),
            sa.Column("idem_key", sa.String(length=255), nullable=False),
            sa.Column("method", sa.String(length=10), nullable=False, server_default=""),
            sa.Column("path", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("request_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("response_body", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_id", "idem_key", name="uq_provisioning_idempotency_token_key"),
        )
        with op.batch_alter_table("provisioning_idempotency_keys", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_provisioning_idempotency_keys_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_provisioning_idempotency_keys_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "provisioning_idempotency_keys" in existing_tables:
        op.drop_table("provisioning_idempotency_keys")
    if "api_tokens" in existing_tables:
        token_cols = {c["name"] for c in inspector.get_columns("api_tokens")}
        with op.batch_alter_table("api_tokens", schema=None) as batch_op:
            for col in ("created_by_id", "rate_limit_per_minute", "expires_at"):
                if col in token_cols:
                    batch_op.drop_column(col)
