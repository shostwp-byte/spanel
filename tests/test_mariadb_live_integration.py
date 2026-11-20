"""Live MariaDB integration tests (runs in CI mariadb-integration job with skip-name-resolve=1)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

LIVE_MARIADB = bool(os.environ.get("SPANEL_LIVE_MARIADB"))
pytestmark = pytest.mark.skipif(not LIVE_MARIADB, reason="SPANEL_LIVE_MARIADB=1 not set")


def _make_session():
    from app.core.database import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _run_as_tenant_tcp(db_user: str, db_password: str, db_name: str, sql: str, host: str = "127.0.0.1"):
    env = dict(os.environ)
    env["MYSQL_PWD"] = db_password
    return subprocess.run(
        ["mysql", "--protocol=TCP", "-h", host, "-u", db_user, "-N", "-B", db_name, "-e", sql],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_v1_live_mariadb_upgrade_write_locked_and_suspended_across_three_loopback_hosts(monkeypatch):
    """Round 18 V1:
    Upgrading a pre-1.0.10 account (only u@localhost exists) whose DB is
    write_locked=True and owner is suspended must succeed with zero ERROR 1141,
    create all 3 loopback hosts locked, enforce MAX_USER_CONNECTIONS + hourly
    limits on all 3 hosts, and reject TCP 127.0.0.1 logins.
    """
    from app.core import database as core_db
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount, User
    from app.services import mariadb

    monkeypatch.setattr(mariadb.settings, "command_dry_run", False)
    SessionLocal = _make_session()
    monkeypatch.setattr(core_db, "SessionLocal", SessionLocal)

    db_name = "ci_v1_sus_db"
    db_user = "ci_v1_sus_u"
    db_pass = "Str0ng!V1Pass99"

    # Pre-1.0.10 state: only 'localhost' exists (127.0.0.1 and ::1 do NOT exist yet)
    mariadb._run_sql(
        f"DROP DATABASE IF EXISTS `{db_name}`;\n"
        f"DROP USER IF EXISTS '{db_user}'@'localhost';\n"
        f"DROP USER IF EXISTS '{db_user}'@'127.0.0.1';\n"
        f"DROP USER IF EXISTS '{db_user}'@'::1';\n"
        f"CREATE DATABASE `{db_name}`;\n"
        f"CREATE USER '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}' ACCOUNT LOCK;\n"
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )

    try:
        with SessionLocal() as db:
            owner = User(
                username="ci_sus_owner",
                email="sus@example.com",
                hashed_password="x",
                role="end_user",
                is_active=False,
                mysql_max_queries_per_hour=750,
            )
            db.add(owner)
            db.commit()
            db.refresh(owner)
            acc = DatabaseAccount(
                owner_id=owner.id,
                db_name=db_name,
                db_user=db_user,
                db_password=encrypt(db_pass),
                write_locked=True,
            )
            db.add(acc)
            db.commit()

        assert mariadb.sync_local_loopback_accounts() == 1

        # Verify all 3 loopback hosts exist, are ACCOUNT LOCKed, and have limits applied
        res = mariadb._run_sql(
            "SELECT Host, JSON_VALUE(Priv, '$.account_locked'), "
            "CAST(IFNULL(JSON_VALUE(Priv, '$.max_user_connections'), 0) AS SIGNED), "
            "CAST(IFNULL(JSON_VALUE(Priv, '$.max_questions'), 0) AS SIGNED) "
            f"FROM mysql.global_priv WHERE User = '{db_user}' ORDER BY Host;\n"
        )
        rows = {}
        for line in (res.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) == 4 and parts[0] != "Host":
                rows[parts[0]] = (parts[1], int(parts[2]), int(parts[3]))
        assert set(rows.keys()) == {"localhost", "127.0.0.1", "::1"}, rows
        for host, (locked_flag, max_conns, max_qph) in rows.items():
            assert str(locked_flag).lower() in {"1", "true"}, f"{host} not locked: {rows}"
            assert 5 <= max_conns <= 15, f"{host} unexpected max_user_connections={max_conns}"
            assert max_qph == 750, f"{host} unexpected max_questions={max_qph}"

        # Real TCP login over 127.0.0.1 MUST be rejected because account is locked
        tcp_res = _run_as_tenant_tcp(db_user, db_pass, db_name, "SELECT 1;")
        assert tcp_res.returncode != 0
        assert "locked" in (tcp_res.stderr or "").lower(), tcp_res.stderr
    finally:
        mariadb.drop_database(db_name, db_user)


def test_v1_live_mariadb_write_locked_active_user_can_select_delete_but_not_insert_over_tcp(monkeypatch):
    """Round 18 V1:
    An active (non-suspended) user whose DB is write_locked=True and upgraded from
    localhost-only must be able to SELECT and DELETE over TCP 127.0.0.1, while
    INSERT and CREATE TABLE are denied.
    """
    from app.core import database as core_db
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount, User
    from app.services import mariadb

    monkeypatch.setattr(mariadb.settings, "command_dry_run", False)
    SessionLocal = _make_session()
    monkeypatch.setattr(core_db, "SessionLocal", SessionLocal)

    db_name = "ci_v1_ro_db"
    db_user = "ci_v1_ro_u"
    db_pass = "Str0ng!RoPass88"

    mariadb._run_sql(
        f"DROP DATABASE IF EXISTS `{db_name}`;\n"
        f"DROP USER IF EXISTS '{db_user}'@'localhost';\n"
        f"DROP USER IF EXISTS '{db_user}'@'127.0.0.1';\n"
        f"DROP USER IF EXISTS '{db_user}'@'::1';\n"
        f"CREATE DATABASE `{db_name}`;\n"
        f"CREATE TABLE `{db_name}`.items (id INT PRIMARY KEY, label VARCHAR(32));\n"
        f"INSERT INTO `{db_name}`.items VALUES (1, 'keep'), (2, 'cleanup');\n"
        f"CREATE USER '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';\n"
        f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )

    try:
        with SessionLocal() as db:
            owner = User(
                username="ci_ro_owner",
                email="ro@example.com",
                hashed_password="x",
                role="end_user",
                is_active=True,
            )
            db.add(owner)
            db.commit()
            db.refresh(owner)
            acc = DatabaseAccount(
                owner_id=owner.id,
                db_name=db_name,
                db_user=db_user,
                db_password=encrypt(db_pass),
                write_locked=True,
            )
            db.add(acc)
            db.commit()

        assert mariadb.sync_local_loopback_accounts() == 1

        sel = _run_as_tenant_tcp(db_user, db_pass, db_name, "SELECT COUNT(*) FROM items;")
        assert sel.returncode == 0, sel.stderr
        assert sel.stdout.strip() == "2"

        dele = _run_as_tenant_tcp(db_user, db_pass, db_name, "DELETE FROM items WHERE id = 2;")
        assert dele.returncode == 0, dele.stderr

        ins = _run_as_tenant_tcp(db_user, db_pass, db_name, "INSERT INTO items VALUES (3, 'denied');")
        assert ins.returncode != 0
        assert "denied" in (ins.stderr or "").lower(), ins.stderr

        ddl = _run_as_tenant_tcp(db_user, db_pass, db_name, "CREATE TABLE evil_tbl (x INT);")
        assert ddl.returncode != 0
        assert "denied" in (ddl.stderr or "").lower(), ddl.stderr
    finally:
        mariadb.drop_database(db_name, db_user)


def test_v5_live_mariadb_backup_restore_keeps_write_lock_and_account_lock(tmp_path, monkeypatch):
    """Round 18 V5 live check:
    Restoring a SQL dump onto a write-locked DB owned by a suspended user must
    import the SQL dump cleanly and leave all 3 loopback hosts locked and
    write-locked right afterward.
    """
    from app.models.entities import DatabaseAccount, User
    from app.services import backup, db_backends, mariadb

    monkeypatch.setattr(mariadb.settings, "command_dry_run", False)
    db_name = "ci_v5_bk_db"
    db_user = "ci_v5_bk_u"
    db_pass = "Str0ng!BkPass77"

    sql_dump = tmp_path / "restore.sql"
    sql_dump.write_text(
        "CREATE TABLE IF NOT EXISTS restored_tbl (id INT PRIMARY KEY, v VARCHAR(32));\n"
        "REPLACE INTO restored_tbl VALUES (1, 'from_backup');\n",
        encoding="utf-8",
    )

    mariadb.drop_database(db_name, db_user)
    mariadb.ensure_local_database_user_hosts(
        db_name,
        db_user,
        db_pass,
        write_locked=True,
        account_locked=True,
        hourly_limits=(400, 0, 0),
    )

    try:
        owner = User(
            username="ci_bk_owner",
            email="bk@example.com",
            hashed_password="x",
            role="end_user",
            is_active=False,
            mysql_max_queries_per_hour=400,
        )
        acc = DatabaseAccount(
            owner_id=1,
            db_name=db_name,
            db_user=db_user,
            db_password="enc",
            write_locked=True,
        )
        backup._restore_database_and_reapply_locks(
            db_backends.LocalBackend(),
            None,
            db_name,
            db_user,
            db_pass,
            sql_dump,
            existing_acc=acc,
            owner=owner,
        )
        # 1. Dump actually imported
        check_data = mariadb._run_sql(f"SELECT v FROM `{db_name}`.restored_tbl WHERE id = 1;\n")
        assert "from_backup" in (check_data.stdout or "")

        # 2. Suspended user still blocked over TCP 127.0.0.1
        tcp_locked = _run_as_tenant_tcp(db_user, db_pass, db_name, "SELECT 1;")
        assert tcp_locked.returncode != 0
        assert "locked" in (tcp_locked.stderr or "").lower(), tcp_locked.stderr

        # 3. After unsuspending owner, write_lock MUST still block INSERT while allowing SELECT
        mariadb.set_account_locked(db_user, False)
        sel_ok = _run_as_tenant_tcp(db_user, db_pass, db_name, "SELECT v FROM restored_tbl WHERE id = 1;")
        assert sel_ok.returncode == 0 and sel_ok.stdout.strip() == "from_backup"
        ins_denied = _run_as_tenant_tcp(db_user, db_pass, db_name, "INSERT INTO restored_tbl VALUES (2, 'no');")
        assert ins_denied.returncode != 0
        assert "denied" in (ins_denied.stderr or "").lower(), ins_denied.stderr
    finally:
        mariadb.drop_database(db_name, db_user)
