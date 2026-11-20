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


# --------------------------------------------------------------------------- M1, M5, M6, M7


def test_m1_m5_m6_nodejs_site_lock_blocks_all_mutating_endpoints_and_prevents_resurrection(tmp_path, monkeypatch):
    import json
    import os
    import time
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app.api import nodejs_manager as nm
    from app.api.deps import get_current_user
    from app.core.config import settings
    from app.core.database import get_db
    from app.models.entities import NodejsInstance, User, UserPackage, Website
    from app.services import addons

    monkeypatch.setattr(addons, "is_installed", lambda slug: True)
    monkeypatch.setattr(settings, "nodejs_manager_enabled", True)
    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)

    SessionLocal = _make_session()
    app_dir = tmp_path / "site_app"
    app_dir.mkdir()
    (app_dir / "package.json").write_text('{"name":"demo","version":"1.0.0"}\n', encoding="utf-8")
    (app_dir / ".env").write_text("PORT=21030\nNODE_ENV=production\n", encoding="utf-8")

    with SessionLocal() as db:
        pkg = UserPackage(name="NodePkgM1", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.flush()
        user = User(username="m1user", email="m1@example.com", hashed_password="x", role="end_user", package_id=pkg.id)
        db.add(user)
        db.flush()
        site = Website(
            domain="m1.example.com",
            owner_id=user.id,
            root_path=str(app_dir),
            document_root="public_html",
            linux_user="m1user",
            app_type="application",
        )
        db.add(site)
        db.flush()
        db.add(
            NodejsInstance(
                website_id=site.id,
                owner_id=user.id,
                app_name="m1.example.com",
                port=21030,
                node_major="22",
                start_mode="node",
                start_arg="server.js",
                memory_mb=512,
                node_env="production",
                process_enabled=True,
                detached=False,
            )
        )
        db.commit()
        site_id = site.id
        user_id = user.id

    monkeypatch.setattr(nm, "_resolve_nodejs_paths", lambda s, db, sub="": ("m1user", app_dir, app_dir))

    app = FastAPI()
    app.include_router(nm.router, prefix="/api")

    def _override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def _override_user():
        with SessionLocal() as s:
            return s.query(User).filter(User.id == user_id).first()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    client = TestClient(app)

    # Hold an active live-PID lock on site_id simulating a running background job
    held = nm._acquire_deploy_lock(site_id, operation="process:start")
    try:
        # M1: EVERY mutating endpoint must return 409 Conflict while lock is held
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/remove", json={"delete_files": False}).status_code == 409
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/detach").status_code == 409
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/process", json={"action": "stop"}).status_code == 409
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/npm", json={"action": "install"}).status_code == 409
        assert client.put(f"/api/nodejs-manager/instances/{site_id}/env", json={"mode": "raw", "raw_content": "PORT=21099\n"}).status_code == 409
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/security", json={"apply_all": True, "enabled": True}).status_code == 409
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/permissions-policy", json={"permissions_policy": "microphone=(self)"}).status_code == 409
        assert client.post(f"/api/nodejs-manager/instances/{site_id}/webhook", json={"enabled": True}).status_code == 409
        assert client.delete(f"/api/nodejs-manager/instances/{site_id}/logs").status_code == 409

        # M5: Failed process request with new port MUST NOT have mutated .env before acquiring lock
        res_port = client.post(
            f"/api/nodejs-manager/instances/{site_id}/process",
            json={"action": "restart", "port": 21088, "node_env": "staging"},
        )
        assert res_port.status_code == 409
        env_text = (app_dir / ".env").read_text(encoding="utf-8")
        assert "PORT=21030" in env_text and "PORT=21088" not in env_text
    finally:
        nm._release_deploy_lock(held)

    # M6: If _inspect_nodejs_instance raises before BackgroundTasks handoff, lock MUST be released!
    monkeypatch.setattr(
        nm,
        "_inspect_nodejs_instance",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom inspect")),
    )
    with pytest.raises(RuntimeError, match="boom inspect"):
        client.post(f"/api/nodejs-manager/instances/{site_id}/process", json={"action": "restart"})
    assert not nm._deploy_lock_path(site_id).exists()

    # M1: _update_instance_state must NOT resurrect a deleted NodejsInstance by default
    with SessionLocal() as db:
        db.query(NodejsInstance).filter(NodejsInstance.website_id == site_id).delete()
        db.commit()
        with pytest.raises(HTTPException) as exc_nf:
            nm._update_instance_state(site_id, lambda inst: inst.update({"node_env": "production"}), db=db)
        assert exc_nf.value.status_code == 404
        assert db.query(NodejsInstance).filter(NodejsInstance.website_id == site_id).first() is None

    # M6: Atomic stale-lock reclamation when a dead PID left a lock behind
    lock_path = nm._deploy_lock_path(site_id)
    lock_path.write_text(json.dumps({"pid": 99999999, "started_at": time.time(), "operation": "dead"}) + "\n", encoding="utf-8")
    reclaimed = nm._acquire_deploy_lock(site_id, operation="fresh-op")
    try:
        meta = nm._read_deploy_lock_meta(reclaimed)
        assert meta["pid"] == os.getpid()
        assert meta["operation"] == "fresh-op"
        assert not reclaimed.with_name(f"{reclaimed.name}.reclaim").exists()
    finally:
        nm._release_deploy_lock(reclaimed)


# --------------------------------------------------------------------------- M2 & M3


def test_m2_m3_reseller_create_user_checks_ceilings_and_partial_edits_allow_grandfathered_users(monkeypatch):
    from fastapi import HTTPException
    from app.api import resources as resources_api
    from app.api import users as users_api
    from app.api.resources import UserResourceLimitsUpdate
    from app.models.entities import ResellerPackage, User, UserPackage
    from app.schemas.schemas import UserCreate, UserUpdate
    from app.services import resource_monitor, site_users

    monkeypatch.setattr(resource_monitor, "apply_user_cgroup_limits_async", lambda uid: None)
    monkeypatch.setattr(resource_monitor, "collect_and_enforce_mysql_quotas", lambda db: {})
    os_touched: list[str] = []
    monkeypatch.setattr(site_users, "ensure_panel_user", lambda u, p: os_touched.append(u) or u)

    SessionLocal = _make_session()
    fake_req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), url=SimpleNamespace(scheme="https"))

    with SessionLocal() as db:
        rpkg = ResellerPackage(
            name="ResPoolM2",
            slug="res-pool-m2",
            user_limit=10,
            total_storage_mb=20480,
            total_website_limit=20,
            total_database_limit=20,
            overselling_enabled=False,
        )
        # Global admin-authored shared package with unlimited RAM (0) and 400% CPU
        shared_over_pkg = UserPackage(
            name="AdminGlobalUnlimited",
            owner_id=None,
            storage_limit_mb=2048,
            website_limit=2,
            database_limit=2,
            memory_max_mb=0,
            cpu_quota_percent=400,
        )
        db.add_all([rpkg, shared_over_pkg])
        db.flush()

        reseller = User(
            username="res_m2",
            email="res_m2@example.com",
            hashed_password="x",
            role="reseller",
            reseller_package_id=rpkg.id,
            memory_max_mb=2048,
            memory_high_mb=1024,
            cpu_quota_percent=100,
            tasks_max=2048,
        )
        db.add(reseller)
        db.flush()

        # Grandfathered customer previously given 4096 MB RAM by Admin (> reseller's 2048 MB ceiling)
        legacy_cust = User(
            username="legacy_m3",
            email="old@example.com",
            hashed_password="x",
            role="end_user",
            reseller_id=reseller.id,
            website_limit=2,
            storage_limit_mb=2048,
            database_limit=2,
            memory_max_mb=4096,
            cpu_quota_percent=100,
            tasks_max=2048,
        )
        db.add(legacy_cust)
        db.commit()
        db.refresh(reseller)
        db.refresh(legacy_cust)

        # M2: Reseller creating a new customer with AdminGlobalUnlimited MUST be rejected 403
        # BEFORE ensure_panel_user touches the OS.
        with pytest.raises(HTTPException) as exc_m2:
            users_api.create_user(
                UserCreate(
                    username="newcust_m2",
                    email="new@example.com",
                    password="StrongPassword!123",
                    role="end_user",
                    package_id=shared_over_pkg.id,
                ),
                fake_req,
                db,
                reseller,
            )
        assert exc_m2.value.status_code == 403
        assert os_touched == []
        db.rollback()

        # M3: Reseller editing ONLY email on grandfathered customer MUST succeed (not blocked by untouched memory_max_mb=4096)
        out = users_api.update_user(
            legacy_cust.id,
            UserUpdate(email="updated@example.com"),
            fake_req,
            db,
            reseller,
        )
        assert out["email"] == "updated@example.com"

        # M3: Reseller editing ONLY database_limit via /resources/users/{id}/limits MUST succeed
        res_ok = resources_api.update_user_resource_limits(
            legacy_cust.id,
            UserResourceLimitsUpdate(database_limit=3),
            fake_req,
            db,
            reseller,
        )
        assert res_ok["ok"] is True

        # M3: Reseller attempting to explicitly raise/set memory_max_mb above ceiling STILL gets 403
        with pytest.raises(HTTPException) as exc_m3:
            resources_api.update_user_resource_limits(
                legacy_cust.id,
                UserResourceLimitsUpdate(memory_max_mb=8192),
                fake_req,
                db,
                reseller,
            )
        assert exc_m3.value.status_code == 403
        db.rollback()


# --------------------------------------------------------------------------- V2 & V6


def test_v2_site_app_dependency_installers_run_inside_tenant_home_jail():
    helper_text = (REPO_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    assert "run_in_tenant_home_jail()" in helper_text
    jail_fn = helper_text[helper_text.index("run_in_tenant_home_jail()"):helper_text.index("site_open_basedir()")]
    assert "unshare --mount --propagation private" in jail_fn
    assert "timeout --signal=TERM --kill-after=10" in jail_fn
    assert "user-${jail_uid}.slice" in jail_fn

    for verb in ("site-app-install-deps)", "site-app-python-deps)", "site-app-git-pull)"):
        start = helper_text.index(verb)
        block = helper_text[start:helper_text.index(";;", start)]
        assert "run_in_tenant_home_jail" in block, f"{verb} must use run_in_tenant_home_jail"
        assert "exec timeout 900 runuser" not in block


def test_v6_broken_zip_bin_stub_triggers_npm_install_before_worker_start(tmp_path, monkeypatch):
    from app.api import nodejs_manager as nm
    from app.core.config import settings

    app_dir = tmp_path / "nextapp"
    (app_dir / "node_modules" / ".bin").mkdir(parents=True)
    (app_dir / "package.json").write_text(
        '{"name":"demo","dependencies":{"next":"15.0.0"},"scripts":{"start":"next start","build":"next build"}}\n',
        encoding="utf-8",
    )
    # Zip-extracted dangling symlink stub: target ../next/dist/bin/next does NOT exist!
    (app_dir / "node_modules" / ".bin" / "next").write_text("../next/dist/bin/next\n", encoding="utf-8")
    (app_dir / ".next").mkdir()
    (app_dir / ".next" / "BUILD_ID").write_text("abc123\n", encoding="utf-8")

    monkeypatch.setattr(settings, "command_dry_run", False)
    monkeypatch.setattr(nm, "_app_path_exists", lambda d, rel, u, sr: (d / rel).exists())
    monkeypatch.setattr(
        nm,
        "_read_app_file",
        lambda d, rel, linux_user=None, site_root=None: (d / rel).read_text(encoding="utf-8") if (d / rel).is_file() else "",
    )

    install_calls: list[str] = []
    monkeypatch.setattr(
        nm,
        "_run_npm_install_with_fallback",
        lambda u, d, node_major="22", timeout=300: install_calls.append("npm install") or (0, "ok", "npm install"),
    )

    # 1. Broken stub -> MUST trigger npm install
    nm._ensure_nodejs_app_ready("alice", app_dir, app_dir, node_major="22", run_build_if_needed=False)
    assert install_calls == ["npm install"]

    # 2. Valid target exists -> MUST NOT re-run npm install unnecessarily
    install_calls.clear()
    (app_dir / "node_modules" / "next" / "dist" / "bin").mkdir(parents=True)
    (app_dir / "node_modules" / "next" / "dist" / "bin" / "next").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    nm._ensure_nodejs_app_ready("alice", app_dir, app_dir, node_major="22", run_build_if_needed=False)
    assert install_calls == []
