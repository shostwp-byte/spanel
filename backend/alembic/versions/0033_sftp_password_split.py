"""separate the SFTP password from the panel password

Revision ID: 0033_sftp_password_split
Revises: 0032_sftp_accounts
Create Date: 2026-09-21

Until now a panel user had one secret doing two jobs. Changing the panel
password rewrote the Linux account's password too (api/users.py,
api/panel_settings.py, api/provisioning.py all called
site_users.set_panel_user_password), and sshd offers password authentication to
that account on port 22 from anywhere. So the panel password was reachable by
brute force against SFTP, and an SFTP password leak was a panel compromise -
which, through the sudo helper, is root.

The schema change is one nullable timestamp, and its NULL is load-bearing:

  NULL       the Linux password has never been set independently. It is still
             whatever the panel password was last set to. These are every
             account that exists today.

  a time     the SFTP password was set on its own and has nothing to do with
             the panel password any more.

Existing accounts are deliberately NOT rotated by this migration. Changing
every customer's SFTP password during an update would break live deployments,
FTP clients and deploy scripts with no warning and no way to hand out the new
secret. They stay NULL and keep working, and the coupling is retired the first
time either password is set - see app/api/users.py, which rotates the Linux
password away when a legacy account changes its panel password, precisely so
that a rotation the user believes killed the old secret actually does.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0033_sftp_password_split"
down_revision: Union[str, None] = "0032_sftp_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("sftp_password_set_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "sftp_password_set_at")
