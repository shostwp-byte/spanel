"""Create nodejs_instances table and migrate legacy JSON state

Revision ID: nodejs_instances_table
Revises: reseller_provisioning_scope
Create Date: 2026-11-11 11:55:00.000000
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "nodejs_instances_table"
down_revision: Union[str, None] = "reseller_provisioning_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load_legacy_instances(bind: sa.engine.Connection, tables: set[str]) -> Dict[str, Any]:
    if "nodejs_toolkit_state" in tables:
        for col_name in ("payload", "state_json"):
            try:
                row = bind.execute(
                    sa.text(f"SELECT {col_name} FROM nodejs_toolkit_state WHERE id = 1")
                ).fetchone()
                if row and row[0]:
                    data = json.loads(row[0])
                    if isinstance(data, dict) and isinstance(data.get("instances"), dict):
                        return data["instances"]
            except Exception:
                continue

    data_dir = Path(os.environ.get("SPANEL_DATA_DIR") or "/var/lib/spanel")
    json_path = data_dir / "nodejs_toolkit_state.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict) and isinstance(data.get("instances"), dict):
                return data["instances"]
        except Exception:
            pass
    return {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "nodejs_instances" not in tables:
        op.create_table(
            "nodejs_instances",
            sa.Column(
                "website_id",
                sa.Integer(),
                sa.ForeignKey("websites.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "owner_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("subpath", sa.String(length=120), nullable=False, server_default=""),
            sa.Column(
                "app_name",
                sa.String(length=120),
                nullable=False,
                server_default="Node.js Application",
            ),
            sa.Column(
                "template",
                sa.String(length=40),
                nullable=False,
                server_default="express-api",
            ),
            sa.Column(
                "framework",
                sa.String(length=64),
                nullable=False,
                server_default="Node.js",
            ),
            sa.Column("node_major", sa.String(length=8), nullable=False, server_default="22"),
            sa.Column("start_mode", sa.String(length=16), nullable=False, server_default="node"),
            sa.Column(
                "start_arg",
                sa.String(length=120),
                nullable=False,
                server_default="server.js",
            ),
            sa.Column("port", sa.Integer(), nullable=True),
            sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="512"),
            sa.Column(
                "node_env",
                sa.String(length=24),
                nullable=False,
                server_default="production",
            ),
            sa.Column("process_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("systemd_sandbox", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "db_account_id",
                sa.Integer(),
                sa.ForeignKey("database_accounts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("git_repo", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("git_branch", sa.String(length=80), nullable=False, server_default="main"),
            sa.Column("git_build_script", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("last_deploy_at", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("last_deploy_status", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("webhook_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("webhook_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("last_npm_logs", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("installed_at", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("updated_at", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("detached", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index(
            "ix_nodejs_instances_owner_id",
            "nodejs_instances",
            ["owner_id"],
            unique=False,
        )
        op.create_index(
            "ix_nodejs_instances_port",
            "nodejs_instances",
            ["port"],
            unique=True,
        )
        op.create_index(
            "ix_nodejs_instances_db_account_id",
            "nodejs_instances",
            ["db_account_id"],
            unique=False,
        )

    # Migrate legacy JSON state into nodejs_instances while keeping legacy JSON intact for rollback
    legacy_map = _load_legacy_instances(bind, tables)
    if not legacy_map or "websites" not in tables:
        return

    existing_ids = {
        row[0]
        for row in bind.execute(sa.text("SELECT website_id FROM nodejs_instances")).fetchall()
    }
    used_ports = {
        row[0]
        for row in bind.execute(
            sa.text("SELECT port FROM nodejs_instances WHERE port IS NOT NULL")
        ).fetchall()
    }

    now_iso = datetime.now(timezone.utc).isoformat()
    for site_id_str, inst in legacy_map.items():
        if not isinstance(inst, dict):
            continue
        try:
            site_id = int(site_id_str)
        except (TypeError, ValueError):
            continue
        if site_id in existing_ids:
            continue

        site_row = bind.execute(
            sa.text("SELECT id, owner_id, domain FROM websites WHERE id = :sid"),
            {"sid": site_id},
        ).fetchone()
        if not site_row:
            continue
        owner_id = int(site_row[1])
        site_domain = str(site_row[2] or "")

        detached = bool(inst.get("detached", False))
        raw_port = inst.get("port")
        port_val = None
        if not detached and raw_port is not None:
            try:
                cand_port = int(raw_port)
                if 21000 <= cand_port <= 21999 and cand_port not in used_ports:
                    port_val = cand_port
                    used_ports.add(cand_port)
            except (TypeError, ValueError):
                port_val = None

        db_acc_id = None
        if "database_accounts" in tables:
            raw_acc_id = inst.get("db_account_id")
            if raw_acc_id is not None and not isinstance(raw_acc_id, bool):
                try:
                    row_acc = bind.execute(
                        sa.text(
                            "SELECT id FROM database_accounts "
                            "WHERE id = :aid AND owner_id = :oid AND website_id = :wid"
                        ),
                        {"aid": int(raw_acc_id), "oid": owner_id, "wid": site_id},
                    ).fetchone()
                    if row_acc:
                        db_acc_id = int(row_acc[0])
                except (TypeError, ValueError):
                    db_acc_id = None
            elif inst.get("db_name"):
                row_acc = bind.execute(
                    sa.text(
                        "SELECT id FROM database_accounts "
                        "WHERE db_name = :dname AND owner_id = :oid AND website_id = :wid"
                    ),
                    {
                        "dname": str(inst.get("db_name")).strip(),
                        "oid": owner_id,
                        "wid": site_id,
                    },
                ).fetchone()
                if row_acc:
                    db_acc_id = int(row_acc[0])

        try:
            npm_logs_json = json.dumps(inst.get("last_npm_logs") or [], ensure_ascii=False)
        except Exception:
            npm_logs_json = "[]"

        bind.execute(
            sa.text(
                "INSERT INTO nodejs_instances ("
                "website_id, owner_id, subpath, app_name, template, framework, "
                "node_major, start_mode, start_arg, port, memory_mb, node_env, "
                "process_enabled, systemd_sandbox, db_account_id, git_repo, git_branch, "
                "git_build_script, last_deploy_at, last_deploy_status, webhook_enabled, "
                "webhook_token, last_npm_logs, installed_at, updated_at, detached"
                ") VALUES ("
                ":website_id, :owner_id, :subpath, :app_name, :template, :framework, "
                ":node_major, :start_mode, :start_arg, :port, :memory_mb, :node_env, "
                ":process_enabled, :systemd_sandbox, :db_account_id, :git_repo, :git_branch, "
                ":git_build_script, :last_deploy_at, :last_deploy_status, :webhook_enabled, "
                ":webhook_token, :last_npm_logs, :installed_at, :updated_at, :detached"
                ")"
            ),
            {
                "website_id": site_id,
                "owner_id": owner_id,
                "subpath": str(inst.get("subpath") or "")[:120],
                "app_name": str(inst.get("app_name") or site_domain or "Node.js Application")[:120],
                "template": str(inst.get("template") or "express-api")[:40],
                "framework": str(inst.get("framework") or "Node.js")[:64],
                "node_major": str(inst.get("node_major") or "22")[:8],
                "start_mode": str(inst.get("start_mode") or "node")[:16],
                "start_arg": str(inst.get("start_arg") or "server.js")[:120],
                "port": port_val,
                "memory_mb": int(inst.get("memory_mb") or 512),
                "node_env": str(inst.get("node_env") or "production")[:24],
                "process_enabled": bool(inst.get("process_enabled", True)),
                "systemd_sandbox": bool(inst.get("systemd_sandbox", True)),
                "db_account_id": db_acc_id,
                "git_repo": str(inst.get("git_repo") or "")[:255],
                "git_branch": str(inst.get("git_branch") or "main")[:80],
                "git_build_script": str(inst.get("git_build_script") or "")[:64],
                "last_deploy_at": str(inst.get("last_deploy_at") or "")[:64],
                "last_deploy_status": str(inst.get("last_deploy_status") or "")[:32],
                "webhook_enabled": False,
                "webhook_token": "",
                "last_npm_logs": npm_logs_json,
                "installed_at": str(inst.get("installed_at") or now_iso)[:64],
                "updated_at": str(inst.get("updated_at") or now_iso)[:64],
                "detached": detached,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "nodejs_instances" in tables:
        op.drop_table("nodejs_instances")
