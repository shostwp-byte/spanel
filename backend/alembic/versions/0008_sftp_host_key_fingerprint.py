"""record SFTP backup target host key fingerprint for TOFU verification

Revision ID: 0008_sftp_host_key_fingerprint
Revises: 0007_panel_user_email_invalid_tld
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_sftp_host_key_fingerprint"
down_revision: Union[str, None] = "0007_panel_user_email_invalid_tld"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sftp_backup_targets") as batch_op:
        batch_op.add_column(sa.Column("host_key_type", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("host_key_fingerprint", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sftp_backup_targets") as batch_op:
        batch_op.drop_column("host_key_fingerprint")
        batch_op.drop_column("host_key_type")
