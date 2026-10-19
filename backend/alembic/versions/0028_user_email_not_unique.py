"""panel user emails need not be unique

Revision ID: 0028_user_email_not_unique
Revises: 0027_website_ssl_source_domain
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0028_user_email_not_unique"
down_revision: Union[str, None] = "0027_website_ssl_source_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The column was created with unique=True + index=True, i.e. a single
    # UNIQUE index named ix_users_email. Swap it for a plain index so two
    # accounts may share an email; the username stays unique.
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_email")
        batch_op.create_index("ix_users_email", ["email"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_email")
        batch_op.create_index("ix_users_email", ["email"], unique=True)
