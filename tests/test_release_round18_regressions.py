"""Regression tests for Round 18 findings (V1..V6, M1..M8, and P1-3 MAX_USER_CONNECTIONS)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))


def _make_session():
    from app.core.database import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# --------------------------------------------------------------------------- V1 & P1-3 MAX_USER_CONNECTIONS


def test_v1_create_credentials_locks_atomically_and_avoids_revoke_error_1141_and_splits_max_connections(monkeypatch):
    from app.services import mariadb

    sent: list[str] = []
    monkeypatch.setattr(mariadb.settings, "command_dry_run", False)
    monkeypatch.setattr(
        mariadb,
        "_run_sql",
        lambda sql, check=True: sent.append(sql) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    mariadb.create_database_credentials(
        "shop_db",
        "shop_u",
        "StrongPass!2026",
        allow_existing_user=True,
        allow_existing_database=True,
        write_locked=True,
        account_locked=True,
    )

    assert len(sent) >= 2
    main_sql = sent[0]
    limits_sql = sent[1]
    for host in mariadb.LOCAL_CLIENT_HOSTS:
        target = f"'shop_u'@'{host}'"
        create_stmt = f"CREATE USER IF NOT EXISTS {target} IDENTIFIED BY 'StrongPass!2026' ACCOUNT LOCK;"
        lock_stmt = f"ALTER USER {target} ACCOUNT LOCK;"
        seed_grant = f"GRANT {mariadb.WRITE_LOCKED_PRIVILEGES} ON `shop_db`.* TO {target};"
        revoke_stmt = f"REVOKE ALL PRIVILEGES ON `shop_db`.* FROM {target};"

        assert create_stmt in main_sql
        assert lock_stmt in main_sql
        assert seed_grant in main_sql
        assert revoke_stmt in main_sql

        # ACCOUNT LOCK must run BEFORE any REVOKE/GRANT, and a GRANT must precede REVOKE
        # so brand-new loopback hosts never abort with MariaDB ERROR 1141.
        idx_create = main_sql.index(create_stmt)
        idx_lock = main_sql.index(lock_stmt)
        idx_first_grant = main_sql.index(seed_grant)
        idx_revoke = main_sql.index(revoke_stmt)
        idx_second_grant = main_sql.index(seed_grant, idx_revoke)
        assert idx_create < idx_lock < idx_first_grant < idx_revoke < idx_second_grant

        # MAX_USER_CONNECTIONS budget (25 default) is split across 3 hosts (10/host -> 30 total, not 75)
        assert f"ALTER USER IF EXISTS {target} WITH MAX_USER_CONNECTIONS 10 MAX_STATEMENT_TIME 15;" in limits_sql

    # change_database_password also creates locked hosts atomically and enforces resource limits
    sent.clear()
    monkeypatch.setattr(mariadb, "_panel_user_is_suspended", lambda u: True)
    mariadb.change_database_password("shop_u", "RotatedPass!99")
    assert len(sent) == 2
    for host in mariadb.LOCAL_CLIENT_HOSTS:
        target = f"'shop_u'@'{host}'"
        assert f"CREATE USER IF NOT EXISTS {target} IDENTIFIED BY 'RotatedPass!99' ACCOUNT LOCK;" in sent[0]
        assert f"MAX_USER_CONNECTIONS 10" in sent[1]


# --------------------------------------------------------------------------- V5


def test_v5_backup_restore_reapplies_write_lock_suspension_and_hourly_limits(tmp_path, monkeypatch):
    from app.models.entities import DatabaseAccount, User
    from app.services import backup, db_backends, mariadb

    sql_dump = tmp_path / "dump.sql"
    sql_dump.write_text("SELECT 1;\n", encoding="utf-8")

    events: list[tuple] = []
    backend = db_backends.LocalBackend()
    monkeypatch.setattr(
        backend,
        "create_database_credentials",
        lambda db_name, db_user, db_pw, **kw: events.append(("create_temp", db_name, db_user)),
    )
    monkeypatch.setattr(
        backend,
        "set_account_locked",
        lambda db_user, locked: events.append(("unlock_for_import", db_user, locked)),
    )
    monkeypatch.setattr(
        backend,
        "import_database_as_tenant",
        lambda db_name, path, **kw: events.append(("import", db_name)),
    )
    monkeypatch.setattr(
        mariadb,
        "ensure_local_database_user_hosts",
        lambda db_name, db_user, db_pw, **kw: events.append(
            ("reapply", db_name, kw["write_locked"], kw["account_locked"], kw["hourly_limits"])
        ),
    )
    monkeypatch.setattr(
        mariadb,
        "set_account_locked",
        lambda db_user, locked: events.append(("kill_and_lock", db_user, locked)),
    )

    owner = User(
        username="sus_bk",
        email="bk@example.com",
        hashed_password="x",
        role="end_user",
        is_active=False,
        mysql_max_queries_per_hour=500,
    )
    acc = DatabaseAccount(
        owner_id=1,
        db_name="bk_db",
        db_user="bk_u",
        db_password="enc",
        write_locked=True,
    )

    backup._restore_database_and_reapply_locks(
        backend,
        None,
        "bk_db",
        "bk_u",
        "RestorePass!9",
        sql_dump,
        existing_acc=acc,
        owner=owner,
    )
    assert events == [
        ("create_temp", "bk_db", "bk_u"),
        ("unlock_for_import", "bk_u", False),
        ("import", "bk_db"),
        ("reapply", "bk_db", True, True, (500, 0, 0)),
        ("kill_and_lock", "bk_u", True),
    ]

    # Even if SQL import raises mid-way, finally MUST re-apply locks
    events.clear()
    monkeypatch.setattr(
        backend,
        "import_database_as_tenant",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("corrupt sql")),
    )
    with pytest.raises(RuntimeError, match="corrupt sql"):
        backup._restore_database_and_reapply_locks(
            backend,
            None,
            "bk_db",
            "bk_u",
            "RestorePass!9",
            sql_dump,
            existing_acc=acc,
            owner=owner,
        )
    assert ("reapply", "bk_db", True, True, (500, 0, 0)) in events
    assert ("kill_and_lock", "bk_u", True) in events
