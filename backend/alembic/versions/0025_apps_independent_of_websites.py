"""make applications independent of websites

An app used to hang off a website; now it belongs to a panel user, lives in its
own directory and gets its own port, and a website in "application" mode points
at one. The "proxy" website mode and the proxy-only app kind are dropped: an app
is always something the panel runs.

Revision ID: 0025_apps_independent
Revises: 0024_site_app_containers
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025_apps_independent"
down_revision: Union[str, None] = "0024_site_app_containers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()

    # Websites gain the pointer first, so the app rows can be matched to it
    # before the old website_id column disappears.
    with op.batch_alter_table("websites") as batch_op:
        batch_op.add_column(sa.Column("app_id", sa.Integer(), nullable=True))
        batch_op.create_index(op.f("ix_websites_app_id"), ["app_id"], unique=False)

    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))

    # A proxy-only app was just a routing record with no process behind it.
    connection.execute(sa.text("DELETE FROM site_apps WHERE kind = 'proxy'"))

    connection.execute(
        sa.text(
            "UPDATE site_apps SET owner_id = ("
            "  SELECT websites.owner_id FROM websites WHERE websites.id = site_apps.website_id"
            ")"
        )
    )
    # Point each website at the app it used to own, lowest id wins where a
    # website somehow had more than one.
    connection.execute(
        sa.text(
            "UPDATE websites SET app_id = ("
            "  SELECT MIN(site_apps.id) FROM site_apps WHERE site_apps.website_id = websites.id"
            ")"
        )
    )
    # Apps whose website is gone cannot be attributed to an owner.
    connection.execute(sa.text("DELETE FROM site_apps WHERE owner_id IS NULL"))

    connection.execute(sa.text("UPDATE websites SET app_type = 'application' WHERE app_type = 'nodejs'"))
    # Proxy sites lose their mode along with the feature; PHP is the safe landing
    # spot and the vhost is rewritten on the next save.
    connection.execute(sa.text("UPDATE websites SET app_type = 'php' WHERE app_type = 'proxy'"))
    connection.execute(sa.text("UPDATE websites SET app_id = NULL WHERE app_type <> 'application'"))

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        fk_name = bind.execute(sa.text(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site_apps' AND COLUMN_NAME = 'website_id' "
            "AND REFERENCED_TABLE_NAME IS NOT NULL"
        )).scalar()
        if fk_name:
            op.drop_constraint(fk_name, "site_apps", type_="foreignkey")
        op.drop_constraint("uq_site_apps_website_name", "site_apps", type_="unique")

    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.drop_index(op.f("ix_site_apps_website_id"))
        batch_op.drop_column("website_id")
        batch_op.drop_column("app_root")
        batch_op.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(op.f("ix_site_apps_owner_id"), ["owner_id"], unique=False)
        batch_op.create_unique_constraint("uq_site_apps_owner_name", ["owner_id", "name"])
        batch_op.create_foreign_key(
            "fk_site_apps_owner_id_users", "users", ["owner_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("websites") as batch_op:
        batch_op.create_foreign_key(
            "fk_websites_app_id_site_apps", "site_apps", ["app_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("websites") as batch_op:
        batch_op.drop_constraint("fk_websites_app_id_site_apps", type_="foreignkey")
        batch_op.drop_index(op.f("ix_websites_app_id"))
        batch_op.drop_column("app_id")

    with op.batch_alter_table("site_apps") as batch_op:
        batch_op.drop_constraint("fk_site_apps_owner_id_users", type_="foreignkey")
        batch_op.drop_constraint("uq_site_apps_owner_name", type_="unique")
        batch_op.drop_index(op.f("ix_site_apps_owner_id"))
        batch_op.add_column(sa.Column("app_root", sa.String(length=255), nullable=False, server_default="app"))
        batch_op.add_column(sa.Column("website_id", sa.Integer(), nullable=True))
        batch_op.create_index(op.f("ix_site_apps_website_id"), ["website_id"], unique=False)
        batch_op.drop_column("owner_id")
