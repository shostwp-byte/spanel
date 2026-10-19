"""add revoked JWT token store

Revision ID: 0010_revoked_tokens
Revises: 0009_two_panel_roles
Create Date: 2026-05-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_revoked_tokens"
down_revision: Union[str, None] = "0009_two_panel_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("jti", sa.String(length=128), nullable=False, unique=True, index=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("revoked_tokens")
