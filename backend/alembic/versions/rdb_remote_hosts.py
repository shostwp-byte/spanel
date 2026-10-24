"""Remote MySQL hosts in the panel database; databases remember their host

Revision ID: rdb_remote_hosts
Revises: isolation_resource_limits
Create Date: 2026-10-24 09:00:00.000000

Remote database hosts used to live in /var/lib/spanel/multi-server.json next
to the cluster peers. A database row has to point at the host it lives on, and
a foreign key cannot point into a JSON file, so the hosts move into a table.
Entries already in the JSON file are carried over once, here, and removed from
the file so a later edit in the panel is not undone by a stale copy.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "rdb_remote_hosts"
down_revision: Union[str, None] = "isolation_resource_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _legacy_state_files() -> list[Path]:
    paths = [Path(os.environ.get("SPANEL_MULTI_SERVER_FILE", "/var/lib/spanel/multi-server.json"))]
    paths.append(Path(".spanel_multi_server.json"))
    return [p for p in paths if p.is_file()]


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _import_legacy_hosts(bind) -> None:
    from app.core.secrets import encrypt, is_encrypted

    table = sa.table(
        "remote_db_hosts",
        sa.column("name", sa.String), sa.column("host", sa.String), sa.column("port", sa.Integer),
        sa.column("admin_user", sa.String), sa.column("admin_password", sa.String),
        sa.column("use_tls", sa.Boolean), sa.column("ca_cert", sa.Text),
        sa.column("allowed_client_hosts", sa.String), sa.column("enabled", sa.Boolean),
        sa.column("status", sa.String), sa.column("latency_ms", sa.Integer),
        sa.column("server_version", sa.String), sa.column("last_checked_at", sa.DateTime),
        sa.column("last_error", sa.Text), sa.column("created_at", sa.DateTime),
    )
    for path in _legacy_state_files():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict) or not isinstance(state.get("remote_db_hosts"), list):
            continue
        existing = {
            (str(row[0]).lower(), int(row[1]))
            for row in bind.execute(sa.text("SELECT host, port FROM remote_db_hosts"))
        }
        for entry in state["remote_db_hosts"]:
            if not isinstance(entry, dict):
                continue
            host = str(entry.get("host") or "").strip().lower()[:255]
            try:
                port = int(entry.get("port") or 3306)
            except (TypeError, ValueError):
                continue
            if not host or not (1 <= port <= 65535) or (host, port) in existing:
                continue
            password = str(entry.get("admin_password") or "").strip()
            if password and not is_encrypted(password):
                password = encrypt(password)
            bind.execute(table.insert().values(
                name=(str(entry.get("name") or host).strip() or host)[:80],
                host=host,
                port=port,
                admin_user=(str(entry.get("admin_user") or "root").strip() or "root")[:64],
                admin_password=password,
                use_tls=bool(entry.get("use_tls", False)),
                ca_cert="",
                allowed_client_hosts="",
                enabled=bool(entry.get("enabled", True)),
                status="unknown",
                latency_ms=None,
                server_version=str(entry.get("server_version") or "")[:128],
                last_checked_at=_parse_time(entry.get("last_checked_at")),
                last_error="",
                created_at=_parse_time(entry.get("created_at")) or datetime.utcnow(),
            ))
            existing.add((host, port))
        state.pop("remote_db_hosts", None)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(path)
        except OSError:
            # The rows are in; a file we cannot rewrite only means the old
            # copy lingers, and nothing reads that key any more.
            pass


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "remote_db_hosts" not in existing_tables:
        op.create_table(
            "remote_db_hosts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("host", sa.String(length=255), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False, server_default="3306"),
            sa.Column("admin_user", sa.String(length=64), nullable=False, server_default="root"),
            sa.Column("admin_password", sa.String(length=1024), nullable=False, server_default=""),
            sa.Column("use_tls", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("ca_cert", sa.Text(), nullable=False, server_default=""),
            sa.Column("allowed_client_hosts", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="unknown"),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("server_version", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("host", "port", name="uq_remote_db_hosts_host_port"),
        )
        op.create_index("ix_remote_db_hosts_id", "remote_db_hosts", ["id"], unique=False)

    if "database_accounts" in existing_tables:
        columns = {c["name"] for c in inspector.get_columns("database_accounts")}
        if "db_host_id" not in columns:
            with op.batch_alter_table("database_accounts", schema=None) as batch_op:
                batch_op.add_column(sa.Column("db_host_id", sa.Integer(), nullable=True))
                batch_op.create_index("ix_database_accounts_db_host_id", ["db_host_id"], unique=False)
                batch_op.create_foreign_key(
                    "fk_database_accounts_db_host_id", "remote_db_hosts", ["db_host_id"], ["id"]
                )

    try:
        _import_legacy_hosts(bind)
    except Exception:  # noqa: BLE001 - a bad legacy file must never block the upgrade
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "database_accounts" in set(inspector.get_table_names()):
        columns = {c["name"] for c in inspector.get_columns("database_accounts")}
        if "db_host_id" in columns:
            with op.batch_alter_table("database_accounts", schema=None) as batch_op:
                batch_op.drop_constraint("fk_database_accounts_db_host_id", type_="foreignkey")
                batch_op.drop_index("ix_database_accounts_db_host_id")
                batch_op.drop_column("db_host_id")
    if "remote_db_hosts" in set(inspector.get_table_names()):
        op.drop_index("ix_remote_db_hosts_id", table_name="remote_db_hosts")
        op.drop_table("remote_db_hosts")
