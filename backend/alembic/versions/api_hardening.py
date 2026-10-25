"""Provisioning API hardening and webhook outbox: token expiry, rate limit, creator, idempotency keys, webhook_outbox

Revision ID: api_hardening
Revises: cluster_state
Create Date: 2026-10-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "api_hardening"
down_revision: Union[str, None] = "cluster_state"
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

    if "users" in existing_tables:
        # Normally added at start-up by _ensure_resource_limit_columns; made
        # part of the schema here now that the model maps it.
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if "bandwidth_limit_mb" not in user_cols:
            with op.batch_alter_table("users", schema=None) as batch_op:
                batch_op.add_column(sa.Column("bandwidth_limit_mb", sa.Integer(), nullable=False, server_default="0"))

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

    if "webhook_endpoints" in existing_tables:
        # Room for the encrypted secret ("fernet:" + token).
        with op.batch_alter_table("webhook_endpoints", schema=None) as batch_op:
            batch_op.alter_column("secret", existing_type=sa.String(length=255), type_=sa.String(length=512), existing_nullable=True)

    if "webhook_outbox" not in existing_tables:
        op.create_table(
            "webhook_outbox",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("delivery_id", sa.String(length=40), nullable=False),
            sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("url", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("event", sa.String(length=100), nullable=False, server_default=""),
            sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("last_status_code", sa.Integer(), nullable=True),
            sa.Column("response_preview", sa.Text(), nullable=False, server_default=""),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("has_signature", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("replayed_from", sa.String(length=40), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("webhook_outbox", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_webhook_outbox_id"), ["id"], unique=False)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_delivery_id"), ["delivery_id"], unique=True)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_endpoint_id"), ["endpoint_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_user_id"), ["user_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_event"), ["event"], unique=False)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_status"), ["status"], unique=False)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_next_attempt_at"), ["next_attempt_at"], unique=False)
            batch_op.create_index(batch_op.f("ix_webhook_outbox_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if "webhook_outbox" in existing_tables:
        op.drop_table("webhook_outbox")
    if "provisioning_idempotency_keys" in existing_tables:
        op.drop_table("provisioning_idempotency_keys")
    if "api_tokens" in existing_tables:
        token_cols = {c["name"] for c in inspector.get_columns("api_tokens")}
        with op.batch_alter_table("api_tokens", schema=None) as batch_op:
            for col in ("created_by_id", "rate_limit_per_minute", "expires_at"):
                if col in token_cols:
                    batch_op.drop_column(col)
