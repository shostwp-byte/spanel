"""Add message templates

Revision ID: 46e0c1ba7bc1
Revises: 0044
Create Date: 2026-10-05 23:01:49.031057

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46e0c1ba7bc1'
down_revision: Union[str, None] = '0044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'message_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reseller_id', sa.Integer(), nullable=False),
        sa.Column('template_type', sa.String(length=50), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['reseller_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('reseller_id', 'template_type', name='uix_reseller_template_type')
    )
    op.create_index(op.f('ix_message_templates_id'), 'message_templates', ['id'], unique=False)
    op.create_index(op.f('ix_message_templates_reseller_id'), 'message_templates', ['reseller_id'], unique=False)
    op.create_index(op.f('ix_message_templates_template_type'), 'message_templates', ['template_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_message_templates_template_type'), table_name='message_templates')
    op.drop_index(op.f('ix_message_templates_reseller_id'), table_name='message_templates')
    op.drop_index(op.f('ix_message_templates_id'), table_name='message_templates')
    op.drop_table('message_templates')
