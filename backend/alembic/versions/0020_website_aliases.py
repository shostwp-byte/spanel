"""add website domain aliases

Revision ID: 0020_website_aliases
Revises: 0019_website_ssl_metadata
Create Date: 2026-07-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_website_aliases"
down_revision: Union[str, None] = "0019_website_ssl_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "website_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("website_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="alias"),
        sa.Column("ssl_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["website_id"], ["websites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_website_aliases_id"), "website_aliases", ["id"], unique=False)
    op.create_index(op.f("ix_website_aliases_website_id"), "website_aliases", ["website_id"], unique=False)
    op.create_index(op.f("ix_website_aliases_domain"), "website_aliases", ["domain"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_website_aliases_domain"), table_name="website_aliases")
    op.drop_index(op.f("ix_website_aliases_website_id"), table_name="website_aliases")
    op.drop_index(op.f("ix_website_aliases_id"), table_name="website_aliases")
    op.drop_table("website_aliases")
