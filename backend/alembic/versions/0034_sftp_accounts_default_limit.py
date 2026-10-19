"""give SFTP sub-accounts a usable default limit

Revision ID: 0034_sftp_accounts_default_limit
Revises: 0033_sftp_password_split
Create Date: 2026-09-22

0032 shipped sftp_accounts_limit defaulting to 0 on every package and every
user, on the reasoning that a second credential into a customer's files should
be opt-in. That reasoning does not survive contact with what the feature is.

A sub-account reaches one website, as a uid that already owns it. It grants no
access the customer does not already have through the file manager and through
their own SFTP login - it only lets them delegate part of it, which is the
whole point. The thing being gated is not new power; it is the ability to hand
a narrower credential to a designer instead of the account password.

So the default was not caution, it was a feature that refused everybody:

    Your hosting package does not include SFTP accounts

on a panel where nothing could raise the limit either (see the commit that
wired it through the API). Reported twice from real use.

Three is a default, not a policy. An administrator who wants none sets 0 and
this migration will not undo that: it only moves rows that are still at the
shipped 0, and it runs once.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0034_sftp_accounts_default_limit"
down_revision: Union[str, None] = "0033_sftp_password_split"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_LIMIT = 3


def upgrade() -> None:
    # Only rows still carrying the shipped 0. Nobody can have chosen a value
    # yet - there was no way to set one - so in practice this is every row, but
    # the condition is what makes a re-run harmless.
    op.execute(
        f"UPDATE user_packages SET sftp_accounts_limit = {DEFAULT_LIMIT} "
        "WHERE sftp_accounts_limit = 0"
    )
    op.execute(
        f"UPDATE users SET sftp_accounts_limit = {DEFAULT_LIMIT} "
        "WHERE sftp_accounts_limit = 0"
    )


def downgrade() -> None:
    op.execute("UPDATE users SET sftp_accounts_limit = 0")
    op.execute("UPDATE user_packages SET sftp_accounts_limit = 0")
