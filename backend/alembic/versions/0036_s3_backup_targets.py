"""somewhere other than an SSH server to put a backup

Revision ID: 0036_s3_backup_targets
Revises: 0035_webauthn_credentials
Create Date: 2026-09-23

A backup that lives on the same machine as the thing it backs up is not a
backup. The panel could already push one to an SSH server; this adds object
storage, which is what most hosts actually have - Wasabi, Backblaze B2,
DigitalOcean Spaces, Cloudflare R2, a MinIO box, or S3 itself.

`sftp_backup_targets` keeps its name. Renaming it would move a foreign key
from backup_schedules and every reference to it for no gain, so `kind` carries
the distinction instead: "sftp" rows use host/port/username/..., "s3" rows use
endpoint/bucket/access_key/... and leave the others null. One table, one
foreign key, one list in the UI.

`secret_key` is encrypted the same way the SFTP password and private key
already are - see app/core/secrets.py. It is Text rather than String for the
same reason those are: ciphertext is longer than the secret.

backup_schedules gains `name_suffix`, which decides what the stored file is
called and therefore how many of them accumulate:

    none            user-alice.tar.gz            one file, overwritten
    day_of_week     user-alice-Mon.tar.gz        seven, rotating
    week_of_month   user-alice-W3.tar.gz         five, rotating
    full_date       user-alice-2026-09-23.tar.gz one a day, pruned by retention

The first three bound remote storage without deleting anything, which matters
on metered object storage. The default is full_date because that is what the
panel did before this column existed, and an update must not silently change
how many copies a customer keeps.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_s3_backup_targets"
down_revision = "0035_webauthn_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sftp_backup_targets") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=8), nullable=False, server_default="sftp"))
        batch.add_column(sa.Column("endpoint", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("region", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("bucket", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("access_key", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("secret_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("prefix", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("secure", sa.Boolean(), nullable=False, server_default=sa.true()))

    # Every row that exists right now is an SSH server, by definition.
    op.execute("UPDATE sftp_backup_targets SET kind = 'sftp' WHERE kind IS NULL OR kind = ''")

    with op.batch_alter_table("backup_schedules") as batch:
        batch.add_column(sa.Column("name_suffix", sa.String(length=16),
                                   nullable=False, server_default="full_date"))


def downgrade() -> None:
    with op.batch_alter_table("backup_schedules") as batch:
        batch.drop_column("name_suffix")
    with op.batch_alter_table("sftp_backup_targets") as batch:
        for column in ("secure", "prefix", "secret_key", "access_key",
                       "bucket", "region", "endpoint", "kind"):
            batch.drop_column(column)
