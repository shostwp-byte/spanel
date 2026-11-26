"""Regression tests for Round 20 independent audit findings (N1..N7)."""
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


def _make_session():
    from app.core.database import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# --------------------------------------------------------------------------- N1


def test_n1_live_lock_overrides_completed_status_file_and_blocks_website_mutations(tmp_path, monkeypatch):
    """Round 20 N1:
    1) Even when a prior background deploy left a status=succeeded file, a live
       per-site lock (e.g. sync npm:install) MUST be detected as active=True by
       _get_site_operation_status and ensure_no_active_nodejs_operation.
    2) _rename_single_website, delete_website, transfer_website_owner, and
       update_website(owner_id=...) MUST hold the per-site lock across their
       entire mutation (no TOCTOU window where git-deploy can sneak in).
    """
    from app.api import nodejs_manager as nm
    from app.api import websites as websites_api
    from app.core.permissions import Role
    from app.models.entities import User, Website
    from app.schemas.schemas import WebsiteUpdate

    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    SessionLocal = _make_session()
    with SessionLocal() as db:
        admin = User(username="n1admin", email="n1a@example.com", hashed_password="x", role=Role.admin)
        alice = User(username="n1alice", email="n1al@example.com", hashed_password="x", role=Role.end_user)
        bob = User(username="n1bob", email="n1bo@example.com", hashed_password="x", role=Role.end_user)
        db.add_all([admin, alice, bob])
        db.commit()
        db.refresh(admin)
        db.refresh(alice)
        db.refresh(bob)

        site = Website(
            domain="n1-site.example.com",
            root_path="/home/n1alice/domains/n1-site.example.com",
            linux_user="n1alice",
            owner_id=alice.id,
            app_type="nodejs",
        )
        db.add(site)
        db.commit()
        db.refresh(site)

        # Simulate a prior background git-deploy that finished cleanly
        nm._write_site_operation_status(
            site.id,
            operation="git-deploy",
            status="succeeded",
            phase="completed",
            message="done",
        )
        assert nm._get_site_operation_status(site.id)["active"] is False

        # Now a synchronous endpoint holds the lock without writing a status file
        with nm.site_operation_lock(site.id, "npm:install"):
            st = nm._get_site_operation_status(site.id)
            assert st["active"] is True
            assert st["operation"] == "npm:install"

            with pytest.raises(HTTPException) as exc_chk:
                nm.ensure_no_active_nodejs_operation(site.id)
            assert exc_chk.value.status_code == 409

            with pytest.raises(HTTPException) as exc_ren:
                websites_api._rename_single_website(db, site, "n1-renamed.example.com", alice, alice.id)
            assert exc_ren.value.status_code == 409

            with pytest.raises(HTTPException) as exc_del:
                websites_api.delete_website(site.id, SimpleNamespace(), True, True, db=db, current_user=admin)
            assert exc_del.value.status_code == 409

            with pytest.raises(HTTPException) as exc_tr:
                websites_api.transfer_website_owner(
                    site.id,
                    websites_api.WebsiteTransferOwnerPayload(target_owner_id=bob.id),
                    SimpleNamespace(),
                    db=db,
                    current_user=admin,
                )
            assert exc_tr.value.status_code == 409

            with pytest.raises(HTTPException) as exc_upd:
                websites_api.update_website(
                    site.id,
                    WebsiteUpdate(owner_id=bob.id),
                    db=db,
                    current_user=admin,
                )
            assert exc_upd.value.status_code == 409

        # Verify the lock is actually HELD during mid-flight website rename (no TOCTOU)
        concurrent_blocked = {"hit": False}

        def _mid_flight_move(old_p, new_p, user, php_v=None):
            with pytest.raises(HTTPException) as exc_conc:
                nm._acquire_deploy_lock(site.id, operation="git-deploy")
            assert exc_conc.value.status_code == 409
            concurrent_blocked["hit"] = True
            raise RuntimeError("abort after verifying lock held")

        with patch.object(websites_api.site_users, "move_site_runtime", side_effect=_mid_flight_move):
            with pytest.raises(HTTPException):
                websites_api._rename_single_website(db, site, "n1-renamed.example.com", alice, alice.id)
        assert concurrent_blocked["hit"] is True
        # Lock must be released after rename exits
        assert nm._get_site_operation_status(site.id)["active"] is False

        # Verify the lock is actually HELD during mid-flight delete_website
        delete_blocked = {"hit": False}

        def _mid_flight_vhost_del(domain):
            with pytest.raises(HTTPException) as exc_conc:
                nm._acquire_deploy_lock(site.id, operation="git-deploy")
            assert exc_conc.value.status_code == 409
            delete_blocked["hit"] = True

        with patch.object(websites_api.nginx, "delete_wordpress_vhost", side_effect=_mid_flight_vhost_del), \
             patch.object(websites_api.ssl, "release_site_certificates", return_value=""), \
             patch.object(websites_api.waf, "remove_site_rules"), \
             patch.object(websites_api.site_users, "delete_site_runtime"), \
             patch.object(websites_api.teardown, "purge_website_sftp_accounts"), \
             patch.object(websites_api.storage_quota, "forget_user_storage"), \
             patch.object(websites_api, "log_action"):
            res = websites_api.delete_website(site.id, SimpleNamespace(), True, False, db=db, current_user=admin)
            assert res["ok"] is True
        assert delete_blocked["hit"] is True
        assert nm._get_site_operation_status(site.id)["active"] is False


# --------------------------------------------------------------------------- N2


def test_n2_mariadb_split_has_no_low_limit_floor_and_preserves_active_throttle_on_password_change(monkeypatch):
    """Round 20 N2:
    1) Low connection caps (e.g. under-attack 8 -> ceil(8/3)=3/host -> 9 total,
       5 -> 2/host -> 6 total) must NOT be inflated by a per-host floor of 5.
    2) change_database_password and ensure_local_database_user_hosts MUST
       preserve an active under-attack / existing MariaDB throttle instead of
       resetting back to default 25 conns / 15s.
    3) Explicitly lifting under-attack mode (preserve_existing=False) restores
       the normal 9/host (25 total) / 15s limits.
    """
    from app.services import mariadb

    monkeypatch.setattr(mariadb.settings, "command_dry_run", False)
    sent: list[str] = []
    mock_global_priv = {"stdout": ""}

    def _fake_run_sql(sql: str, *, check: bool = True):
        sent.append(sql)
        if "FROM mysql.global_priv" in sql:
            return SimpleNamespace(returncode=0, stdout=mock_global_priv["stdout"], stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mariadb, "_run_sql", _fake_run_sql)
    monkeypatch.setattr(mariadb, "_panel_user_is_suspended", lambda u: False)
    monkeypatch.setattr(mariadb, "_panel_user_under_attack", lambda u: False)

    # 1. Under-attack throttle 8 conns / 3s -> 3/host (total 9, NOT 15)
    mariadb.enforce_db_user_resource_limits("n2_u", max_user_connections=8, max_statement_time=3)
    assert "MAX_USER_CONNECTIONS 3 MAX_STATEMENT_TIME 3;" in sent[-1]

    # 2. Even tighter cap 5 -> 2/host (total 6, NOT 15)
    sent.clear()
    mariadb.enforce_db_user_resource_limits("n2_u", max_user_connections=5, max_statement_time=2)
    assert "MAX_USER_CONNECTIONS 2 MAX_STATEMENT_TIME 2;" in sent[-1]

    # 3. Password change while existing MariaDB Priv already has tight 3 / 3s -> preserved!
    sent.clear()
    mock_global_priv["stdout"] = "3\t3.0\n3\t3.0\n"
    mariadb.change_database_password("n2_u", "NewPass!2026")
    assert "MAX_USER_CONNECTIONS 3 MAX_STATEMENT_TIME 3;" in sent[-1]

    # 4. ensure_local_database_user_hosts (allow_existing_user=True) while under attack -> preserved!
    sent.clear()
    mock_global_priv["stdout"] = ""
    monkeypatch.setattr(mariadb, "_panel_user_under_attack", lambda u: True)
    mariadb.ensure_local_database_user_hosts("n2_db", "n2_u", "NewPass!2026", write_locked=False, account_locked=False, hourly_limits=(0, 0, 0))
    assert "MAX_USER_CONNECTIONS 3 MAX_STATEMENT_TIME 3;" in sent[-1]

    # 5. Explicitly lifting under-attack mode (preserve_existing=False) restores default 9/host / 15s
    sent.clear()
    monkeypatch.setattr(mariadb, "_panel_user_under_attack", lambda u: False)
    mock_global_priv["stdout"] = "3\t3.0\n"
    mariadb.enforce_db_user_resource_limits("n2_u", max_user_connections=25, max_statement_time=15)
    assert "MAX_USER_CONNECTIONS 9 MAX_STATEMENT_TIME 15;" in sent[-1]


# --------------------------------------------------------------------------- N4


def test_n4_owner_change_rollback_does_not_overwrite_db_or_vhost_when_reverse_move_fails_or_legacy_empty(tmp_path, monkeypatch):
    """Round 20 N4:
    In update_website owner_id change rollback, website.root_path/linux_user
    and Nginx vhost MUST NOT be reverted to old_root_path if reverse move fails
    or if old_linux_user was empty (legacy site).
    """
    from app.api import nodejs_manager as nm
    from app.api import websites as websites_api
    from app.core.permissions import Role
    from app.models.entities import User, Website
    from app.schemas.schemas import WebsiteUpdate

    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    SessionLocal = _make_session()
    with SessionLocal() as db:
        admin = User(username="n4admin", email="n4a@example.com", hashed_password="x", role=Role.admin)
        alice = User(username="n4alice", email="n4al@example.com", hashed_password="x", role=Role.end_user)
        bob = User(username="n4bob", email="n4bo@example.com", hashed_password="x", role=Role.end_user)
        db.add_all([admin, alice, bob])
        db.commit()
        db.refresh(admin)
        db.refresh(alice)
        db.refresh(bob)

        # Case 1: Reverse move fails -> must NOT rewrite vhost back to old_root_path or resync workers
        site = Website(
            domain="n4-fail.example.com",
            root_path="/home/n4alice/domains/n4-fail.example.com",
            linux_user="n4alice",
            owner_id=alice.id,
            app_type="php",
            php_version="8.4",
        )
        db.add(site)
        db.commit()
        db.refresh(site)

        vhost_calls: list[str] = []
        resync_calls: list[str] = []
        move_n = {"n": 0}

        def _move_fail_reverse(old_p, new_p, user, php_v=None):
            move_n["n"] += 1
            if move_n["n"] == 2:
                raise RuntimeError("target busy on reverse move")

        def _vhost_fail_forward(w, **kw):
            vhost_calls.append(kw.get("root_path") or w.root_path)
            if len(vhost_calls) == 1:
                raise RuntimeError("nginx syntax error")

        with patch.object(websites_api.storage_quota, "enforce_user_storage_quota"), \
             patch.object(websites_api.storage_quota, "website_storage_used_bytes", return_value=0), \
             patch.object(websites_api.site_users, "move_site_runtime", side_effect=_move_fail_reverse), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api, "_rewrite_website_vhost", side_effect=_vhost_fail_forward), \
             patch.object(websites_api, "_resync_site_workers", side_effect=lambda sdb, w: resync_calls.append(w.root_path) or []):
            with pytest.raises(HTTPException) as exc1:
                websites_api.update_website(site.id, WebsiteUpdate(owner_id=bob.id), db=db, current_user=admin)

        assert "target busy on reverse move" in exc1.value.detail
        # Only the 1st (failed forward) vhost call happened — NO rollback rewrite to old_root_path!
        assert len(vhost_calls) == 1
        assert resync_calls == []
        # In-memory model stays aligned with where files actually live on disk
        assert site.root_path.replace("\\", "/").endswith("/n4bob/n4-fail.example.com")
        assert site.linux_user == "n4bob"

        # Case 2: Legacy site with empty old_linux_user -> rollback skips move and does NOT rewrite vhost to old path
        legacy_site = Website(
            domain="n4-legacy.example.com",
            root_path="/var/www/n4-legacy.example.com",
            linux_user="",
            owner_id=alice.id,
            app_type="php",
            php_version="8.4",
        )
        db.add(legacy_site)
        db.commit()
        db.refresh(legacy_site)

        vhost_calls.clear()
        with patch.object(websites_api.storage_quota, "enforce_user_storage_quota"), \
             patch.object(websites_api.storage_quota, "website_storage_used_bytes", return_value=0), \
             patch.object(websites_api.site_users, "move_site_runtime"), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api, "_rewrite_website_vhost", side_effect=_vhost_fail_forward):
            with pytest.raises(HTTPException) as exc2:
                websites_api.update_website(legacy_site.id, WebsiteUpdate(owner_id=bob.id), db=db, current_user=admin)

        assert "previous linux_user was empty" in exc2.value.detail
        assert len(vhost_calls) == 1
        assert legacy_site.root_path.replace("\\", "/").endswith("/n4bob/n4-legacy.example.com")
        assert legacy_site.linux_user == "n4bob"


# --------------------------------------------------------------------------- N5


def test_n5_tenant_jail_creates_leaf_spanel_scope_applies_limits_and_logs_failures(tmp_path):
    """Round 20 N5:
    1) ensure_tenant_cgroup_scope must create user-<uid>.slice/spanel.scope on
       demand and apply CPU/memory/pids limits from /etc/spanel/limits/<user>.env.
    2) Both run_in_tenant_home_jail and terminal-exec must call
       ensure_tenant_cgroup_scope before entering unshare, only write to the
       leaf spanel.scope/cgroup.procs (never non-leaf slice cgroup.procs), and
       log a warning via logger if cgroup placement fails.
    """
    import shutil
    import subprocess

    helper_src = (REPO_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    assert "ensure_tenant_cgroup_scope()" in helper_src
    # Must NEVER attempt the EBUSY-prone fallback write to non-leaf user-<uid>.slice/cgroup.procs
    assert 'user-${jail_uid}.slice/cgroup.procs' not in helper_src
    # Both jail paths call ensure_tenant_cgroup_scope and log warnings on failure
    jail_fn = helper_src.split("run_in_tenant_home_jail() {", 1)[1].split("\nsite_open_basedir()", 1)[0]
    assert 'ensure_tenant_cgroup_scope "$user" "$uid_num"' in jail_fn
    assert "logger -t spanel-helper -p daemon.warning" in jail_fn
    term_block = helper_src.split("terminal-exec)", 1)[1].split("terminal_cli_open_basedir=", 1)[0]
    assert 'ensure_tenant_cgroup_scope "$user" "$terminal_uid"' in term_block
    assert "logger -t spanel-helper -p daemon.warning" in term_block

    # Behavioral execution of ensure_tenant_cgroup_scope in bash against a temp cgroup tree
    import os

    bash_exe = None
    for candidate in (
        shutil.which("bash"),
        "/bin/bash",
        "/usr/bin/bash",
    ):
        if not candidate or not Path(candidate).is_file():
            continue
        if os.name == "nt" and "system32" in candidate.lower():
            continue
        probe = subprocess.run([candidate, "--version"], capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            bash_exe = candidate
            break
    if not bash_exe:
        return
    fn_start = helper_src.index("ensure_tenant_cgroup_scope() {")
    fn_end = helper_src.index("\n# Run a command as a panel Linux user", fn_start)
    fn_body = helper_src[fn_start:fn_end]

    cg_root = tmp_path / "cgroup"
    (cg_root / "user.slice").mkdir(parents=True)
    limits_dir = tmp_path / "limits"
    limits_dir.mkdir(parents=True)
    (limits_dir / "alice.env").write_text(
        "CPU_PCT=150\nMEM_HIGH_MB=512\nMEM_MAX_MB=1024\nTASKS_MAX=3000\n",
        encoding="utf-8",
    )
    script = (
        f"set -e\n"
        f'CGROUP_ROOT="{cg_root.as_posix()}"\n'
        f'SPANEL_LIMITS_DIR="{limits_dir.as_posix()}"\n'
        "systemctl() { return 1; }\n"
        "tenant_io_base_device() { echo ''; }\n"
        "logger() { :; }\n"
        f"{fn_body}\n"
        'ensure_tenant_cgroup_scope "alice" "1055"\n'
    )
    res = subprocess.run([bash_exe, "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    scope = cg_root / "user.slice" / "user-1055.slice" / "spanel.scope"
    assert scope.is_dir()
    assert (scope / "cpu.max").read_text(encoding="utf-8").strip() == "150000 100000"
    assert (scope / "memory.high").read_text(encoding="utf-8").strip() == str(512 * 1048576)
    assert (scope / "memory.max").read_text(encoding="utf-8").strip() == str(1024 * 1048576)
    assert (scope / "pids.max").read_text(encoding="utf-8").strip() == "3000"


# --------------------------------------------------------------------------- N6


def test_n6_user_limits_apply_if_changed_requires_completed_stamp_and_nodejs_restarts_on_bin_repair(tmp_path):
    """Round 20 N6:
    1) user-limits-apply --if-changed must NOT return unchanged=1 unless the
       .applied stamp matches the current limits/slice digest (so a prior run
       that failed midway after writing config files is retried automatically).
    2) prepare_nodejs_app_as_user signals when it repaired broken .bin stubs so
       sync_nodejs_worker_unit in if-changed mode still restarts the app.
    """
    import os
    import shutil
    import subprocess

    helper_src = (REPO_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    ul_block = helper_src.split("  user-limits-apply)", 1)[1].split("  quota-status)", 1)[0]
    assert 'limits_applied_file="${SPANEL_LIMITS_DIR}/${u_name}.applied"' in ul_block
    assert 'rm -f -- "$limits_applied_file"' in ul_block
    assert 'limits_applied_digest" == "$limits_current_digest"' in ul_block
    assert "limits_apply_ok=0" in ul_block

    sync_fn = helper_src.split("sync_nodejs_worker_unit() {", 1)[1].split("\nstatus_nodejs_worker_unit()", 1)[0]
    assert 'if prepare_nodejs_app_as_user "$user" "$app_target" "$bin_dir"; then' in sync_fn
    assert '"$prep_changed" -eq 0' in sync_fn

    # Behavioral bash test of prepare_nodejs_app_as_user return code (0=repaired, 1=unchanged)
    bash_exe = None
    for candidate in (shutil.which("bash"), "/bin/bash", "/usr/bin/bash"):
        if not candidate or not Path(candidate).is_file():
            continue
        if os.name == "nt" and "system32" in candidate.lower():
            continue
        if subprocess.run([candidate, "--version"], capture_output=True, text=True, check=False).returncode == 0:
            bash_exe = candidate
            break
    if not bash_exe:
        return

    fn_start = helper_src.index("prepare_nodejs_app_as_user() {")
    fn_end = helper_src.index("\nresolve_or_install_node_bin_dir()", fn_start)
    fn_body = helper_src[fn_start:fn_end]

    app_dir = tmp_path / "app"
    bin_dir = tmp_path / "nodebin"
    (app_dir / "node_modules" / ".bin").mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    fake_npm = bin_dir / "npm"
    fake_npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_npm.chmod(0o755)
    # Plant a dangling zip-extracted stub (regular file pointing to missing ../pkg/cli.js)
    stub = app_dir / "node_modules" / ".bin" / "broken-cli"
    stub.write_text("../missing-pkg/cli.js\n", encoding="utf-8")

    # Mock runuser so it runs directly as the test user
    script_base = (
        'runuser() { shift 2; [[ "$1" == "--" ]] && shift; "$@"; }\n'
        f"{fn_body}\n"
    )
    # First run repairs/removes the broken stub -> exits 0 (prep_changed=1)
    r1 = subprocess.run(
        [bash_exe, "-c", script_base + f'prepare_nodejs_app_as_user "alice" "{app_dir.as_posix()}" "{bin_dir.as_posix()}"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r1.returncode == 0, r1.stderr
    assert not stub.exists()

    # Second run finds nothing to repair -> exits 1 (prep_changed=0)
    r2 = subprocess.run(
        [bash_exe, "-c", script_base + f'prepare_nodejs_app_as_user "alice" "{app_dir.as_posix()}" "{bin_dir.as_posix()}"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r2.returncode == 1, f"expected 1 (unchanged), got {r2.returncode}: {r2.stderr}"


# --------------------------------------------------------------------------- N3


def test_n3_rename_inheriting_letsencrypt_emits_renewal_warning(tmp_path, monkeypatch):
    """Round 20 N3:
    When renaming a domain with ssl_mode='letsencrypt' inherits the old domain's
    Let's Encrypt SAN lineage via ssl_mode='shared', the response warnings and
    X-SPanel-Warning header MUST warn that renewal depends on old_domain DNS.
    """
    from fastapi import Response

    from app.api import nodejs_manager as nm
    from app.api import websites as websites_api
    from app.core.permissions import Role
    from app.models.entities import User, Website
    from app.schemas.schemas import WebsiteUpdate

    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    SessionLocal = _make_session()
    with SessionLocal() as db:
        owner = User(username="n3user", email="n3@example.com", hashed_password="x", role=Role.end_user)
        db.add(owner)
        db.commit()
        db.refresh(owner)

        site = Website(
            domain="old.example.com",
            root_path="/home/n3user/old.example.com",
            linux_user="n3user",
            owner_id=owner.id,
            app_type="php",
            php_version="8.4",
            ssl_enabled=True,
            ssl_mode="letsencrypt",
        )
        db.add(site)
        db.commit()
        db.refresh(site)

        resp = Response()
        with patch.object(websites_api.site_users, "move_site_runtime"), \
             patch.object(websites_api.ssl, "cert_info", return_value={"sans": ["old.example.com", "new.example.com"]}), \
             patch.object(websites_api.ssl, "cert_covers", side_effect=lambda sans, d: d in sans), \
             patch.object(websites_api, "_ensure_default_waf_file"), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api, "_rewrite_website_vhost"), \
             patch.object(websites_api.nginx, "delete_wordpress_vhost"), \
             patch.object(websites_api.waf, "remove_site_rules"), \
             patch.object(websites_api, "_refresh_placeholder_page_if_unchanged"), \
             patch.object(websites_api, "_resync_site_workers", return_value=[]), \
             patch.object(websites_api.ssl, "release_site_certificates"), \
             patch.object(websites_api.dns, "zone_for_new_website"), \
             patch.object(websites_api.storage_quota, "forget_user_storage"), \
             patch.object(websites_api, "log_action"):
            out = websites_api.update_website(
                site.id,
                WebsiteUpdate(domain="new.example.com"),
                db=db,
                current_user=owner,
                response=resp,
            )

        assert out.ssl_enabled is True
        assert out.ssl_mode == "shared"
        assert out.ssl_source_domain == "old.example.com"
        warnings = getattr(out, "warnings", []) or []
        assert any("old.example.com" in w and "new.example.com" in w and "renewal" in w.lower() for w in warnings), warnings
        assert "old.example.com" in resp.headers.get("X-SPanel-Warning", "")
        assert "new.example.com" in resp.headers.get("X-SPanel-Warning", "")


# --------------------------------------------------------------------------- N7


def test_n7_default_user_resource_limits_derived_from_user_model_columns():
    """Round 20 N7:
    _DEFAULT_USER_RESOURCE_LIMITS must stay in lockstep with User model column
    defaults for every reseller ceiling field.
    """
    from app.api import users as users_api
    from app.models.entities import User

    assert set(users_api._DEFAULT_USER_RESOURCE_LIMITS.keys()) == set(users_api._RESELLER_CEILING_FIELDS)
    for field in users_api._RESELLER_CEILING_FIELDS:
        col_default = User.__table__.columns[field].default
        expected = int(col_default.arg or 0) if (col_default is not None and getattr(col_default, "is_scalar", False)) else 0
        assert users_api._DEFAULT_USER_RESOURCE_LIMITS[field] == expected, field
    assert users_api._DEFAULT_USER_RESOURCE_LIMITS["cpu_quota_percent"] == 100
    assert users_api._DEFAULT_USER_RESOURCE_LIMITS["memory_high_mb"] == 1024
    assert users_api._DEFAULT_USER_RESOURCE_LIMITS["memory_max_mb"] == 2048
    assert users_api._DEFAULT_USER_RESOURCE_LIMITS["tasks_max"] == 2048






