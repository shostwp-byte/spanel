"""passkeys, stored per hostname

Revision ID: 0035_webauthn_credentials
Revises: 0034_sftp_accounts_default_limit
Create Date: 2026-09-22

A passkey is bound to one Relying Party ID, which is a hostname. That is
WebAuthn's design, not a choice: a credential created for panel.example.com is
invisible to a browser talking to any other name, including this panel's own IP.

app/serve.py deliberately answers on every hostname on the machine that has a
certificate, so a customer may reach the panel by several names. rp_id is
therefore a column rather than a setting: one account can hold a passkey for
each name it actually signs in through, and the login page offers whichever one
matches the host in the address bar.

Google Authenticator stays available alongside it, on purpose. A passkey that
does not match the current hostname simply is not offered, and without a second
factor to fall back on that would be a lockout - see app/api/auth.py, which
falls through to TOTP whenever a passkey cannot be used.

No foreign key on user_id, for the reason 0014 and 0032 give: PRAGMA
foreign_keys is off on the shipped SQLite backend, so the constraint would be
decorative. app/services/teardown.py sweeps these explicitly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0035_webauthn_credentials"
down_revision: Union[str, None] = "0034_sftp_accounts_default_limit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        # base64url of the raw credential id. Unique across the panel: a
        # browser will not offer the same credential to two accounts, and a
        # duplicate here would make the login lookup ambiguous.
        sa.Column("credential_id", sa.String(length=512), nullable=False, unique=True, index=True),
        sa.Column("public_key", sa.Text(), nullable=False),
        # Replay defence. An authenticator that counts must never go backwards;
        # one that does not count reports 0 forever, which is allowed.
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        # The hostname this credential belongs to. Indexed with user_id because
        # every login asks "does this account have a passkey for this host".
        sa.Column("rp_id", sa.String(length=253), nullable=False, index=True),
        sa.Column("transports", sa.String(length=128), nullable=False, server_default=""),
        # What the customer called it, so a lost device can be identified.
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_webauthn_credentials_user_rp",
        "webauthn_credentials",
        ["user_id", "rp_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_webauthn_credentials_user_rp", table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
