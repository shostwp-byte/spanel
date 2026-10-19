"""collapse panel roles to admin and end_user

Revision ID: 0009_two_panel_roles
Revises: 0008_sftp_host_key_fingerprint
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0009_two_panel_roles"
down_revision: Union[str, None] = "0008_sftp_host_key_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'admin' WHERE role IN ('super_admin', 'admin')")
    op.execute("UPDATE users SET role = 'end_user' WHERE role IN ('user', 'readonly') OR role IS NULL OR role = ''")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'user' WHERE role = 'end_user'")
