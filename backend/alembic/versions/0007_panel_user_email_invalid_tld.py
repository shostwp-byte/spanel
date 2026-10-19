"""move generated panel user emails to the RFC 2606 .invalid TLD

Revision ID: 0007_panel_user_email_invalid_tld
Revises: 0006_backup_schedule_user_sets
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0007_panel_user_email_invalid_tld"
down_revision: Union[str, None] = "0006_backup_schedule_user_sets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # @users.spanel.vn / @users.spanel.test were synthetic addresses for
    # auto-created panel-only users; spanel.vn is a real domain and spanel.test
    # is reserved but ambiguous. Move every synthetic address to the reserved
    # .invalid TLD (RFC 2606) so it can never accidentally route mail.
    op.execute(
        "UPDATE users SET email = REPLACE(email, '@users.spanel.vn', '@users.spanel.invalid') "
        "WHERE email LIKE '%@users.spanel.vn'"
    )
    op.execute(
        "UPDATE users SET email = REPLACE(email, '@users.spanel.test', '@users.spanel.invalid') "
        "WHERE email LIKE '%@users.spanel.test'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET email = REPLACE(email, '@users.spanel.invalid', '@users.spanel.vn') "
        "WHERE email LIKE '%@users.spanel.invalid'"
    )
