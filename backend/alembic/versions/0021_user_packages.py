"""add user packages

Revision ID: 0021_user_packages
Revises: 0020_website_aliases
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_user_packages"
down_revision: Union[str, None] = "0020_website_aliases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_packages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("website_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("storage_limit_mb", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_packages_id"), "user_packages", ["id"], unique=False)
    op.create_index(op.f("ix_user_packages_name"), "user_packages", ["name"], unique=True)
    op.execute(
        "INSERT INTO user_packages (name, website_limit, storage_limit_mb, created_at) "
        "VALUES ('Default', 5, 1024, CURRENT_TIMESTAMP)"
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("package_id", sa.Integer(), nullable=True))
        batch_op.create_index(op.f("ix_users_package_id"), ["package_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_users_package_id_user_packages",
            "user_packages",
            ["package_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_package_id_user_packages", type_="foreignkey")
        batch_op.drop_index(op.f("ix_users_package_id"))
        batch_op.drop_column("package_id")
    op.drop_index(op.f("ix_user_packages_name"), table_name="user_packages")
    op.drop_index(op.f("ix_user_packages_id"), table_name="user_packages")
    op.drop_table("user_packages")
