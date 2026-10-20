"""Add missing foreign keys and cascades

Revision ID: f7a7bbef3278
Revises: 46e0c1ba7bc1
Create Date: 2026-10-06 10:04:10.647828

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a7bbef3278'
down_revision: Union[str, None] = '46e0c1ba7bc1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if '_alembic_tmp_api_tokens' in existing_tables:
        op.drop_table('_alembic_tmp_api_tokens')

    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    with op.batch_alter_table('backup_schedules', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)
        batch_op.create_foreign_key('fk_backup_schedules_user_id', 'users', ['user_id'], ['id'], ondelete='CASCADE')
        batch_op.create_foreign_key('fk_backup_schedules_target_id', 'sftp_backup_targets', ['target_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('cloudflare_credentials', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)
        batch_op.alter_column('updated_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    with op.batch_alter_table('database_accounts', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)
        batch_op.create_foreign_key('fk_database_accounts_owner_id', 'users', ['owner_id'], ['id'], ondelete='CASCADE')
        batch_op.create_foreign_key('fk_database_accounts_website_id', 'websites', ['website_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('provisioning_accounts', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)
        batch_op.alter_column('updated_at',
               existing_type=sa.DATETIME(),
               nullable=False)
        batch_op.create_foreign_key('fk_provisioning_accounts_user_id', 'users', ['user_id'], ['id'])
        batch_op.create_foreign_key('fk_provisioning_accounts_package_id', 'user_packages', ['package_id'], ['id'])
        batch_op.create_foreign_key('fk_provisioning_accounts_primary_website_id', 'websites', ['primary_website_id'], ['id'])

    with op.batch_alter_table('revoked_tokens', schema=None) as batch_op:
        batch_op.alter_column('revoked_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    sftp_indexes = {idx['name'] for idx in inspector.get_indexes('sftp_accounts')}
    with op.batch_alter_table('sftp_accounts', schema=None) as batch_op:
        if 'ix_sftp_accounts_owner_label' in sftp_indexes:
            batch_op.drop_index('ix_sftp_accounts_owner_label')
        batch_op.create_unique_constraint('uq_sftp_accounts_owner_label', ['owner_id', 'website_id', 'label'])
        batch_op.create_foreign_key('fk_sftp_accounts_website_id', 'websites', ['website_id'], ['id'], ondelete='CASCADE')
        batch_op.create_foreign_key('fk_sftp_accounts_owner_id', 'users', ['owner_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('sftp_backup_targets', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    with op.batch_alter_table('site_apps', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    pkg_cols = {c['name'] for c in inspector.get_columns('user_packages')}
    new_pkg_cols = [
        ('bandwidth_limit_mb', sa.Column('bandwidth_limit_mb', sa.Integer(), nullable=False, server_default='0')),
        ('inode_limit', sa.Column('inode_limit', sa.Integer(), nullable=False, server_default='0')),
        ('subdomain_limit', sa.Column('subdomain_limit', sa.Integer(), nullable=False, server_default='0')),
        ('email_forwarders_limit', sa.Column('email_forwarders_limit', sa.Integer(), nullable=False, server_default='0')),
        ('mailing_lists_limit', sa.Column('mailing_lists_limit', sa.Integer(), nullable=False, server_default='0')),
        ('autoresponders_limit', sa.Column('autoresponders_limit', sa.Integer(), nullable=False, server_default='0')),
        ('domain_pointers_limit', sa.Column('domain_pointers_limit', sa.Integer(), nullable=False, server_default='0')),
        ('ftp_accounts_limit', sa.Column('ftp_accounts_limit', sa.Integer(), nullable=False, server_default='0')),
        ('ips_limit', sa.Column('ips_limit', sa.Integer(), nullable=False, server_default='0')),
        ('anonymous_ftp', sa.Column('anonymous_ftp', sa.Boolean(), nullable=False, server_default='0')),
        ('cgi_access', sa.Column('cgi_access', sa.Boolean(), nullable=False, server_default='0')),
        ('git_access', sa.Column('git_access', sa.Boolean(), nullable=False, server_default='0')),
        ('php_access', sa.Column('php_access', sa.Boolean(), nullable=False, server_default='0')),
        ('spamassassin', sa.Column('spamassassin', sa.Boolean(), nullable=False, server_default='0')),
        ('catch_all_email', sa.Column('catch_all_email', sa.Boolean(), nullable=False, server_default='0')),
        ('ssl_access', sa.Column('ssl_access', sa.Boolean(), nullable=False, server_default='0')),
        ('ssh_access', sa.Column('ssh_access', sa.Boolean(), nullable=False, server_default='0')),
        ('ssh_access_users', sa.Column('ssh_access_users', sa.Boolean(), nullable=False, server_default='0')),
        ('cron_jobs', sa.Column('cron_jobs', sa.Boolean(), nullable=False, server_default='0')),
        ('redis_access', sa.Column('redis_access', sa.Boolean(), nullable=False, server_default='0')),
        ('system_info', sa.Column('system_info', sa.Boolean(), nullable=False, server_default='0')),
        ('login_keys', sa.Column('login_keys', sa.Boolean(), nullable=False, server_default='0')),
        ('dns_control', sa.Column('dns_control', sa.Boolean(), nullable=False, server_default='0')),
        ('personal_dns_mode', sa.Column('personal_dns_mode', sa.String(length=32), nullable=False, server_default='none')),
        ('share_server_ip', sa.Column('share_server_ip', sa.Boolean(), nullable=False, server_default='0')),
    ]
    with op.batch_alter_table('user_packages', schema=None) as batch_op:
        for col_name, col_obj in new_pkg_cols:
            if col_name not in pkg_cols:
                batch_op.add_column(col_obj)
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    webauthn_indexes = {idx['name'] for idx in inspector.get_indexes('webauthn_credentials')}
    if 'ix_webauthn_credentials_user_rp' in webauthn_indexes:
        with op.batch_alter_table('webauthn_credentials', schema=None) as batch_op:
            batch_op.drop_index('ix_webauthn_credentials_user_rp')

    with op.batch_alter_table('website_aliases', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)

    with op.batch_alter_table('websites', schema=None) as batch_op:
        batch_op.alter_column('created_at',
               existing_type=sa.DATETIME(),
               nullable=False)
        batch_op.create_foreign_key('fk_websites_owner_id', 'users', ['owner_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    pass
