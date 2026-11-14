"""Regression tests for the round 17 review (main @ b147eb5)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import laravel_manager as lm
from app.api import nodejs_manager as nm
from app.api import websites
from app.core.database import Base
from app.models.entities import NodejsInstance, User, Website

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
UPDATE_SH = REPO_ROOT / "installer" / "update.sh"


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)()


def _alice_site(db, domain="a.example.com"):
    alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user")
    db.add(alice)
    db.commit()
    db.refresh(alice)
    site = Website(
        domain=domain,
        root_path=websites.site_users.site_root_for_panel_user("alice", domain),
        document_root="public_html",
        linux_user="alice",
        owner_id=alice.id,
        app_type="application",
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return alice, site


def _ok():
    return SimpleNamespace(returncode=0, stdout="", stderr="")


# --------------------------------------------------------------------------- P1-2


def _rename_patches(move_calls, node_calls, laravel_calls, *, vhost_effect=None, laravel_state=None):
    return [
        patch.object(websites.site_users, "move_site_runtime", side_effect=lambda o, n, u, p=None: move_calls.append((o, n, u))),
        patch.object(websites, "_ensure_default_waf_file"),
        patch.object(websites.waf, "sync_website_rules", return_value=_ok()),
        patch.object(websites.waf, "remove_site_rules"),
        patch.object(websites.nginx, "delete_wordpress_vhost"),
        patch.object(websites, "_rewrite_website_vhost", side_effect=vhost_effect),
        patch.object(websites, "_refresh_placeholder_page_if_unchanged"),
        patch.object(websites.ssl, "release_site_certificates"),
        patch.object(websites.dns, "zone_for_new_website"),
        patch.object(nm, "_sync_nodejs_worker_unit", side_effect=lambda *a, **k: node_calls.append((a, k)) or {"status": "running"}),
        patch.object(lm, "_sync_laravel_worker_unit", side_effect=lambda *a, **k: laravel_calls.append((a, k)) or {"status": "running"}),
        patch.object(lm, "_load_toolkit_state", return_value=laravel_state or {"instances": {}}),
    ]


def _apply(patches):
    for p in patches:
        p.start()


def test_p1_2_rename_recreates_nodejs_and_laravel_worker_units_at_the_new_path():
    db = _session()
    alice, site = _alice_site(db)
    db.add(NodejsInstance(
        website_id=site.id, owner_id=alice.id, port=21010, detached=False,
        process_enabled=True, node_major="22", start_mode="node", start_arg="server.js", memory_mb=768,
    ))
    db.commit()
    move_calls, node_calls, laravel_calls = [], [], []
    state = {"instances": {str(site.id): {"subpath": "", "queue_worker": {"enabled": True, "workers": 2}}}}
    patches = _rename_patches(move_calls, node_calls, laravel_calls, laravel_state=state)
    _apply(patches)
    try:
        websites._rename_single_website(db, site, "b.example.com", alice, alice.id)
    finally:
        patch.stopall()

    new_root = websites.site_users.site_root_for_panel_user("alice", "b.example.com")
    assert move_calls and move_calls[0][1] == new_root
    assert len(node_calls) == 1
    args, kwargs = node_calls[0]
    assert args[0] == "alice"
    assert str(args[1]) == str(Path(new_root))
    assert kwargs["port"] == 21010 and kwargs["memory_mb"] == 768
    assert kwargs["enabled"] is True and kwargs["strict"] is False
    assert len(laravel_calls) == 1
    assert str(laravel_calls[0][0][1]) == str(Path(new_root))


def test_p1_2_rename_rollback_recreates_units_at_the_old_path():
    db = _session()
    alice, site = _alice_site(db)
    old_root = site.root_path
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=21011, detached=False, process_enabled=True))
    db.commit()
    move_calls, node_calls, laravel_calls = [], [], []
    patches = _rename_patches(
        move_calls, node_calls, laravel_calls,
        vhost_effect=[HTTPException(status_code=500, detail="nginx failed"), None],
    )
    _apply(patches)
    try:
        with pytest.raises(HTTPException):
            websites._rename_single_website(db, site, "b.example.com", alice, alice.id)
    finally:
        patch.stopall()

    assert len(move_calls) == 2  # forward, then back
    assert len(node_calls) == 1
    assert str(node_calls[0][0][1]) == str(Path(old_root))
    assert site.domain == "a.example.com"


def test_p1_2_detached_nodejs_instance_is_left_alone():
    db = _session()
    alice, site = _alice_site(db)
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=None, detached=True))
    db.commit()
    node_calls = []
    with patch.object(nm, "_sync_nodejs_worker_unit", side_effect=lambda *a, **k: node_calls.append(a)), \
         patch.object(lm, "_load_toolkit_state", return_value={"instances": {}}):
        assert websites._resync_site_workers(db, site) == []
    assert node_calls == []


def test_p1_2_failed_resync_is_reported_not_swallowed():
    db = _session()
    alice, site = _alice_site(db)
    db.add(NodejsInstance(website_id=site.id, owner_id=alice.id, port=21012, detached=False, process_enabled=True))
    db.commit()
    with patch.object(nm, "_sync_nodejs_worker_unit", return_value={"status": "error"}), \
         patch.object(lm, "_load_toolkit_state", side_effect=RuntimeError("state unreadable")):
        assert websites._resync_site_workers(db, site) == ["nodejs", "laravel-queue"]


def test_p1_2_no_reference_to_the_missing_sync_function():
    text = (REPO_ROOT / "backend" / "app" / "api" / "websites.py").read_text(encoding="utf-8")
    assert "_sync_systemd_unit" not in text


# --------------------------------------------------------------------------- P1-3

LOOPBACK = ("localhost", "127.0.0.1", "::1")


def _capture_sql(monkeypatch):
    from app.services import mariadb

    sent: list[str] = []
    monkeypatch.setattr(mariadb.settings, "command_dry_run", False)
    monkeypatch.setattr(
        mariadb, "_run_sql",
        lambda sql, check=True: sent.append(sql) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    return sent


def _locked_tenant(db):
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount

    owner = User(
        username="carol", email="carol@example.com", hashed_password="x", role="end_user",
        is_active=False, mysql_max_queries_per_hour=900,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    acc = DatabaseAccount(
        owner_id=owner.id, db_name="carol_wp", db_user="carol_wp",
        db_password=encrypt("s3cret-pw"), write_locked=True,
    )
    db.add(acc)
    db.commit()
    return owner, acc


def _sessionmaker_for(db):
    return sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False)


def _assert_locked_on_every_host(sql: str):
    for host in LOOPBACK:
        target = f"'carol_wp'@'{host}'"
        assert f"REVOKE ALL PRIVILEGES ON `carol_wp`.* FROM {target};" in sql
        assert f"GRANT SELECT, DELETE, DROP, REFERENCES, SHOW VIEW, TRIGGER ON `carol_wp`.* TO {target};" in sql
        assert f"ALTER USER {target} ACCOUNT LOCK;" in sql
        assert f"ALTER USER IF EXISTS {target} WITH MAX_QUERIES_PER_HOUR 900" in sql
    assert "GRANT ALL" not in sql


def test_p1_3_startup_sync_keeps_write_lock_suspension_and_hourly_limits(monkeypatch):
    from app.core import database as core_db
    from app.services import mariadb

    db = _session()
    _locked_tenant(db)
    sent = _capture_sql(monkeypatch)
    monkeypatch.setattr(core_db, "SessionLocal", _sessionmaker_for(db))
    assert mariadb.sync_local_loopback_accounts() == 1
    sql = "".join(sent)
    _assert_locked_on_every_host(sql)
    # Accounts the panel does not track are no longer copied to TCP hosts.
    assert "mysql.user" not in sql


def test_p1_3_password_change_cannot_lift_the_write_lock(monkeypatch):
    from app.core import database as core_db
    from app.services import mariadb

    db = _session()
    _locked_tenant(db)
    sent = _capture_sql(monkeypatch)
    monkeypatch.setattr(core_db, "SessionLocal", _sessionmaker_for(db))
    monkeypatch.setattr(mariadb, "database_exists", lambda name: True)
    monkeypatch.setattr(mariadb, "user_exists", lambda name: True)
    # databases.change_database_password calls exactly this after the ALTER USER.
    mariadb.ensure_local_database_user_hosts("carol_wp", "carol_wp", "new-password")
    _assert_locked_on_every_host("".join(sent))


def test_p1_3_write_lock_and_unlock_cover_every_host(monkeypatch):
    from app.services import mariadb

    sent = _capture_sql(monkeypatch)
    mariadb.set_database_write_lock("carol_wp", "carol_wp", True)
    mariadb.set_database_write_lock("carol_wp", "carol_wp", False)
    locked, unlocked = sent
    for host in LOOPBACK:
        assert f"GRANT SELECT, DELETE, DROP, REFERENCES, SHOW VIEW, TRIGGER ON `carol_wp`.* TO 'carol_wp'@'{host}';" in locked
        assert f"GRANT ALL PRIVILEGES ON `carol_wp`.* TO 'carol_wp'@'{host}';" in unlocked


def test_p1_3_update_sh_no_longer_grants_all_to_tenant_loopback_hosts():
    text = UPDATE_SH.read_text(encoding="utf-8")
    body = text[text.index("refresh_spanel_mariadb_grants() {"):]
    body = body[: body.index("\n}\n")]
    assert r"GRANT ALL PRIVILEGES ON \`" not in body
    assert "${db_u}" not in body


# --------------------------------------------------------------------------- P1-4


def _env_reader(files: dict[str, str]):
    return lambda app_dir, rel, linux_user=None, site_root=None: files.get(rel, "")


def test_p1_4_env_cannot_reset_an_account_the_panel_does_not_track(monkeypatch, tmp_path):
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount
    from app.services import mariadb

    db = _session()
    alice, site = _alice_site(db)
    db.add(DatabaseAccount(owner_id=alice.id, db_name="alice_app", db_user="alice_app", db_password=encrypt("stored-pw")))
    db.commit()
    calls = []
    monkeypatch.setattr(nm.settings, "command_dry_run", False)
    monkeypatch.setattr(mariadb, "ensure_local_database_user_hosts", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(mariadb, "create_database_credentials", lambda *a, **k: calls.append(("create", a, k)))

    # ext_app exists in MariaDB but has no DatabaseAccount: the tenant's file
    # names the tenant's own database to slip past the ownership checks.
    env = "DB_HOST=127.0.0.1\nDB_DATABASE=alice_app\nDB_USERNAME=ext_app\nDB_PASSWORD=attacker-chosen\n"
    monkeypatch.setattr(nm, "_read_app_file", _env_reader({".env": env}))
    nm._ensure_nodejs_local_db_grants(site, db, "alice", tmp_path, tmp_path, {})
    assert calls == []

    # Pointing at the owner's own tracked account syncs it with the STORED password.
    env_own = "DB_HOST=127.0.0.1\nDB_DATABASE=alice_app\nDB_USERNAME=alice_app\nDB_PASSWORD=whatever-the-file-says\n"
    monkeypatch.setattr(nm, "_read_app_file", _env_reader({".env": env_own}))
    nm._ensure_nodejs_local_db_grants(site, db, "alice", tmp_path, tmp_path, {})
    assert calls == [(("alice_app", "alice_app", "stored-pw"), {})]


def test_p1_4_admin_owned_site_gets_no_bypass(monkeypatch, tmp_path):
    from app.services import mariadb

    db = _session()
    root = User(username="root1", email="r@example.com", hashed_password="x", role="admin")
    db.add(root)
    db.commit()
    db.refresh(root)
    site = Website(domain="adm.example.com", root_path="/home/root1/adm.example.com", linux_user="root1", owner_id=root.id)
    db.add(site)
    db.commit()
    calls = []
    monkeypatch.setattr(nm.settings, "command_dry_run", False)
    monkeypatch.setattr(mariadb, "ensure_local_database_user_hosts", lambda *a, **k: calls.append(a))
    env = "DB_HOST=localhost\nDB_DATABASE=pdns\nDB_USERNAME=pdns_admin\nDB_PASSWORD=x\n"
    monkeypatch.setattr(nm, "_read_app_file", _env_reader({".env": env}))
    nm._ensure_nodejs_local_db_grants(site, db, "root1", tmp_path, tmp_path, {})
    assert calls == []


# --------------------------------------------------------------------------- P1-5


def _helper_function(name: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{\n")
    return text[start: text.index("\n}\n", start)]


def test_p1_5_helper_runs_no_tenant_npm_scripts():
    """npm install / npm run build run the tenant's code. They belong to the
    jailed terminal-exec path in the API, not to a bare runuser in the helper
    (the CI helper-integration job checks a postinstall script never runs)."""
    body = _helper_function("prepare_nodejs_app_as_user")
    assert "/npm\" install" not in body
    assert " install --" not in body
    assert "run build" not in body
    assert "runuser -u \"$user\" -- \"${cpu_cap[@]}\"" not in body

