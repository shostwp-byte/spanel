"""Reseller hierarchy

Revision ID: 0042_reseller_hierarchy
Revises: 0041_mail_domains
Create Date: 2026-09-30

"""

import sqlalchemy as sa
from alembic import op

revision = "0042_reseller_hierarchy"
down_revision = "0041_mail_domains"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. users: add reseller_id, reseller_package_id
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("reseller_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_users_reseller_id", ondelete="SET NULL"), nullable=True))
        batch_op.create_index("ix_users_reseller_id", ["reseller_id"])
    
    # Create reseller_packages table
    op.create_table(
        "reseller_packages",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True, index=True),
        sa.Column("slug", sa.String(length=100), nullable=True, unique=True, index=True),
        sa.Column("user_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("total_storage_mb", sa.Integer(), nullable=False, server_default="10240"),
        sa.Column("total_website_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("total_database_limit", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("overselling_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("reseller_package_id", sa.Integer(), sa.ForeignKey("reseller_packages.id", name="fk_users_reseller_package_id", ondelete="SET NULL"), nullable=True))
        batch_op.create_index("ix_users_reseller_package_id", ["reseller_package_id"])

    # 2. user_packages: add owner_id, is_reseller_package, etc.
    with op.batch_alter_table("user_packages") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_user_packages_owner_id", ondelete="CASCADE"), nullable=True))
        batch_op.create_index("ix_user_packages_owner_id", ["owner_id"])
        batch_op.add_column(sa.Column("is_reseller_package", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("reseller_user_limit", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("reseller_overselling_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade() -> None:
    with op.batch_alter_table("user_packages") as batch_op:
        batch_op.drop_column("reseller_overselling_enabled")
        batch_op.drop_column("reseller_user_limit")
        batch_op.drop_column("is_reseller_package")
        batch_op.drop_index("ix_user_packages_owner_id")
        batch_op.drop_column("owner_id")
    
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_reseller_package_id")
        batch_op.drop_column("reseller_package_id")
        batch_op.drop_index("ix_users_reseller_id")
        batch_op.drop_column("reseller_id")
        
    op.drop_table("reseller_packages")
