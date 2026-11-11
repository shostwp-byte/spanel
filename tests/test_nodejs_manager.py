"""Behavioral and security regression tests for the End-User Node.js Manager module."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from _isolation_bash import BASH, helper_functions, run_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import nodejs_manager as nm
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.models.entities import DatabaseAccount, NodejsInstance, User, UserPackage, Website
from app.services import addons, mariadb, site_apps

needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


@pytest.fixture(autouse=True)
def _enable_application_addon_and_feature(monkeypatch):
    monkeypatch.setattr(addons, "is_installed", lambda slug: True)
    if hasattr(settings, "nodejs_manager_enabled"):
        monkeypatch.setattr(settings, "nodejs_manager_enabled", True)


def _make_request(headers: Optional[Dict[str, str]] = None):
    return SimpleNamespace(headers=headers or {})


def _setup_test_db():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return eng, SessionLocal


def _build_test_client(SessionLocal, current_user_holder: dict) -> TestClient:
    app = FastAPI()
    app.include_router(nm.router, prefix="/api")

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _override_user():
        return current_user_holder["user"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    return TestClient(app)


def test_rbac_access_role_and_admin_only_node_runtime_install():
    enabled_pkg = SimpleNamespace(node_apps_limit=5, node_app_memory_mb=1024)
    admin_user = SimpleNamespace(id=1, username="admin", role="admin", package=None)
    reseller_user = SimpleNamespace(id=2, username="reseller1", role="reseller", package=enabled_pkg)
    end_user = SimpleNamespace(id=3, username="alice", role="end_user", package=enabled_pkg)

    with pytest.raises(HTTPException) as exc_info:
        nm._require_end_user_nodejs_access(admin_user, _make_request({}))
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        nm._require_end_user_nodejs_access(reseller_user, _make_request({"x-access-role": "reseller"}))
    assert exc_info.value.status_code == 403

    nm._require_end_user_nodejs_access(admin_user, _make_request({"x-access-role": "user"}))
    nm._require_end_user_nodejs_access(end_user, _make_request({}))

    # 1.7: End User cannot call POST /node-versions/install (403 Forbidden), only Admin can
    _, SessionLocal = _setup_test_db()
    holder = {"user": end_user}
    client = _build_test_client(SessionLocal, holder)
    res_end_user = client.post("/api/nodejs-manager/node-versions/install", json={"node_major": "22"})
    assert res_end_user.status_code == 403

    holder["user"] = admin_user
    with patch("app.services.site_apps.install_node", return_value="installed v22"):
        res_admin = client.post("/api/nodejs-manager/node-versions/install", json={"node_major": "22"})
    assert res_admin.status_code == 200
    assert res_admin.json()["node_major"] == "22"


@needs_bash
def test_p0_1_p1_1_p1_2_1_7_real_helper_worker_sync_and_status(tmp_path, monkeypatch):
    """Execute the real spanel-helper.sh functions via bash to verify:
    - 1.2 (P1-1): Python _worker_sync_helper_args order matches sync_nodejs_worker_unit in spanel-helper.sh.
    - 1.1 (P0-1): Customer .env is copied as the site Linux user into ${SPANEL_DATA_DIR}/nodejs/<base>.env;
      a symlinked .env pointing to a 0600 root secret cannot be read by the site user and is never passed
      directly to systemd EnvironmentFile=.
    - 1.3 (P1-2): status_nodejs_worker_unit outputs main_pid, memory_current_bytes, restarts, and
      _query_nodejs_worker_status reads them while preserving config from panel state.
    - 1.7: resolve_or_install_node_bin_dir refuses uninstalled Node versions without calling install_node_major.
    """
    monkeypatch.setattr(settings, "command_dry_run", False)

    home_root = tmp_path / "home"
    site_root = home_root / "alice" / "app.example.com"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True, exist_ok=True)
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "spanel-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    node22_bin = tmp_path / "node22" / "bin"
    node22_bin.mkdir(parents=True, exist_ok=True)
    fake_node = node22_bin / "node"
    fake_node.write_text("#!/bin/sh\necho 22.14.0\n", encoding="utf-8")
    os.chmod(fake_node, 0o755)

    # Simulate symlinked .env -> /root/secret.env (unreadable by site user alice)
    secret_file = tmp_path / "root-secret.env"
    secret_file.write_text("SECRET_KEY=PANEL_ROOT_MASTER_SECRET_999\n", encoding="utf-8")
    env_path = app_dir / ".env"
    try:
        os.symlink(secret_file, env_path)
    except OSError:
        env_path.write_text("SECRET_KEY=PANEL_ROOT_MASTER_SECRET_999\n", encoding="utf-8")

    funcs = helper_functions(
        "is_in",
        "require_linux_user",
        "require_node_major",
        "resolve_node_bin_dir",
        "resolve_or_install_node_bin_dir",
        "nodejs_worker_unit_base",
        "nodejs_env_file",
        "copy_site_env_as_user",
        "resolve_or_install_node_bin_dir",
        "sync_nodejs_worker_unit",
        "status_nodejs_worker_unit",
        "delete_nodejs_worker_units",
    )

    stubs = f"""
HOME_ROOT='{home_root.as_posix()}'
SYSTEMD_UNIT_DIR='{unit_dir.as_posix()}'
SPANEL_DATA_DIR='{data_dir.as_posix()}'
deny() {{ echo "deny: $*" >&2; exit 1; }}
sha256sum() {{ echo "0123456789abcdef0123456789abcdef  -"; }}
id() {{ echo 1005; }}
require_managed_path() {{ printf '%s' "$1"; }}
require_bound_managed_path() {{ printf '%s' "$3"; }}
require_app_port() {{
  [[ "$1" =~ ^[0-9]+$ ]] && (( "$1" >= 20000 && "$1" <= 29999 )) || deny "invalid app port: $1"
}}
require_app_memory() {{
  [[ "$1" =~ ^[0-9]+$ ]] && (( "$1" >= 64 && "$1" <= 16384 )) || deny "invalid app memory: $1"
}}
install_node_major() {{
  echo "UNEXPECTED_ROOT_INSTALL_NODE_$1" >>'{(tmp_path / "unexpected.log").as_posix()}'
  return 1
}}
resolve_node_bin_dir() {{
  if [[ "$1" == "22" ]]; then
    printf '%s' '{node22_bin.as_posix()}'
    return 0
  fi
  return 1
}}
runuser() {{
  # Simulate unprivileged user 'alice' who cannot read root-secret.env
  local target_file="${{@: -1}}"
  if [[ "$target_file" == *".env" ]] && grep -q "PANEL_ROOT_MASTER_SECRET_999" "$target_file" 2>/dev/null; then
    return 1
  fi
  shift 3
  "$@"
}}
install() {{
  local is_dir=0 arg
  for arg in "$@"; do
    [[ "$arg" == "-d" ]] && is_dir=1
  done
  if (( is_dir == 1 )); then
    mkdir -p "${{@: -1}}"
  else
    : > "${{@: -1}}"
  fi
}}
chown() {{ :; }}
chmod() {{ :; }}
systemctl() {{
  case "$1" in
    is-enabled|is-active) return 0 ;;
    is-failed) return 1 ;;
    show)
      case "$3" in
        MainPID) echo 43210 ;;
        MemoryCurrent) echo 67108864 ;;
        NRestarts) echo 3 ;;
      esac
      ;;
    *) return 0 ;;
  esac
}}
"""

    def fake_privileged(action: str, *, helper_args: Optional[List[str]] = None, check: bool = True, timeout: int = 60):
        args = helper_args or []
        quoted_args = " ".join(f"'{a}'" for a in args)
        dispatch = {
            "nodejs-worker-sync": f"sync_nodejs_worker_unit {quoted_args}",
            "nodejs-worker-status": f"status_nodejs_worker_unit {quoted_args}",
            "nodejs-worker-delete": f"delete_nodejs_worker_units {quoted_args}",
        }[action]
        script = "\n".join(["set -euo pipefail", funcs, stubs, dispatch, ""])
        return run_bash(tmp_path, script, check=False)

    with patch.object(nm.shell, "privileged", side_effect=fake_privileged):
        sync_res = nm._sync_nodejs_worker_unit(
            "alice",
            site_root,
            app_dir,
            node_major="22",
            start_mode="node",
            start_arg="server.js",
            port=21005,
            memory_mb=512,
            node_env="production",
            enabled=True,
            strict=True,
        )
        assert sync_res["status"] == "running"
        assert sync_res["port"] == 21005
        assert sync_res["unit"].startswith("spanel-nodejs-alice-")

        unit_files = list(unit_dir.glob("spanel-nodejs-alice-*.service"))
        assert len(unit_files) == 1
        unit_text = unit_files[0].read_text(encoding="utf-8")
        # 1.1 (P0-1): EnvironmentFile points to SPANEL_DATA_DIR/nodejs, never customer home
        assert f"EnvironmentFile=-{data_dir.as_posix()}/nodejs/" in unit_text
        assert home_root.as_posix() not in unit_text.split("EnvironmentFile=")[1].splitlines()[0]
        # Root secret was rejected by runuser as 'alice', so safe_env_file does not contain it
        safe_env_files = list((data_dir / "nodejs").glob("*.env"))
        assert not any("PANEL_ROOT_MASTER_SECRET_999" in f.read_text(encoding="utf-8") for f in safe_env_files)

        # P3-1: Regular .env with reserved keys (PORT, HOST, NODE_ENV, PATH, HOME, npm_config_cache)
        # has those keys stripped by copy_site_env_as_user while keeping custom variables.
        if env_path.is_symlink() or env_path.exists():
            env_path.unlink()
        env_path.write_text(
            "PORT=9999\nHOST=0.0.0.0\nNODE_ENV=development\nPATH=/tmp/evil\nCUSTOM_VAR=kept_ok\n",
            encoding="utf-8",
        )
        nm._sync_nodejs_worker_unit(
            "alice",
            site_root,
            app_dir,
            node_major="22",
            start_mode="node",
            start_arg="server.js",
            port=21005,
            memory_mb=512,
            node_env="production",
            enabled=True,
            strict=True,
        )
        copied_env_text = safe_env_files[0].read_text(encoding="utf-8")
        assert "CUSTOM_VAR=kept_ok" in copied_env_text
        assert "PORT=" not in copied_env_text
        assert "HOST=" not in copied_env_text
        assert "NODE_ENV=" not in copied_env_text
        assert "PATH=" not in copied_env_text

        # 1.3 (P1-2): _query_nodejs_worker_status reads live main_pid, memory_current_bytes, restarts
        status_res = nm._query_nodejs_worker_status(
            "alice",
            site_root,
            {"port": 21005, "node_major": "22", "start_mode": "node", "start_arg": "server.js", "memory_mb": 512},
        )
        assert status_res["enabled"] is True
        assert status_res["state"] == "active"
        assert status_res["status"] == "running"
        assert status_res["main_pid"] == 43210
        assert status_res["memory_current_bytes"] == 67108864
        assert status_res["restarts"] == 3
        assert status_res["port"] == 21005
        assert status_res["node_major"] == "22"

        # 1.7: Uninstalled Node version (24) fails and never calls install_node_major
        with pytest.raises(HTTPException) as exc_uninstalled:
            nm._sync_nodejs_worker_unit(
                "alice",
                site_root,
                app_dir,
                node_major="24",
                start_mode="node",
                start_arg="server.js",
                port=21005,
                memory_mb=512,
                node_env="production",
                enabled=True,
                strict=True,
            )
        assert exc_uninstalled.value.status_code == 500
        assert "Node 24 is not installed" in exc_uninstalled.value.detail
        assert not (tmp_path / "unexpected.log").exists()


def test_p0_2_db_mode_ownership_extra_forbid_quota_and_rollback(tmp_path, monkeypatch):
    """1.4 (P0-2) & 1.2 rollback:
    - extra="forbid" rejects legacy db_name/db_user/db_password fields with 422.
    - db_mode="existing" with another user's DatabaseAccount returns 403 and executes zero GRANTs.
    - db_mode="new" enforces database_limit quota (403) before touching target files.
    - mariadb.create_database_credentials refuses existing DB by default unless allow_existing_database=True.
    - When nodejs-worker-sync fails during install, newly created DB and NodejsInstance row are rolled back.
    """
    monkeypatch.setattr(settings, "command_dry_run", True)

    # 1.4a: create_database_credentials refuses existing DB by default
    with patch.object(mariadb, "database_exists", return_value=True), patch.object(
        mariadb, "_run_sql"
    ) as mock_sql:
        with pytest.raises(ValueError, match="already exists"):
            mariadb.create_database_credentials("wp_existing", "u_attacker", "StrongPass123!")
        mock_sql.assert_not_called()

        # Restore callers passing allow_existing_database=True are allowed
        mariadb.create_database_credentials(
            "wp_existing", "u_restore", "StrongPass123!", allow_existing_database=True
        )
        assert mock_sql.called

    _, SessionLocal = _setup_test_db()
    with patch.object(nm, "SessionLocal", SessionLocal), patch(
        "app.api.websites._rewrite_website_vhost"
    ):
        db = SessionLocal()
        pkg_1db = UserPackage(name="starter-1db", database_limit=1, node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg_1db)
        db.commit()
        db.refresh(pkg_1db)

        alice = User(
            username="alice",
            email="alice@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg_1db.id,
        )
        bob = User(
            username="bob",
            email="bob@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg_1db.id,
        )
        db.add_all([alice, bob])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)

        alice_site = Website(
            domain="alice.example.com",
            root_path=str(tmp_path / "alice"),
            document_root="public_html",
            linux_user="alice",
            owner_id=alice.id,
        )
        bob_site = Website(
            domain="bob.example.com",
            root_path=str(tmp_path / "bob"),
            document_root="public_html",
            linux_user="bob",
            owner_id=bob.id,
        )
        db.add_all([alice_site, bob_site])
        db.commit()
        db.refresh(alice_site)
        db.refresh(bob_site)
        bob_pub = Path(bob_site.root_path) / "public_html"
        bob_pub.mkdir(parents=True, exist_ok=True)
        (bob_pub / "index.html").write_text("welcome", encoding="utf-8")

        alice_db = DatabaseAccount(
            owner_id=alice.id,
            website_id=alice_site.id,
            db_name="wp_alice_secret",
            db_user="u_alice_secret",
            db_password="enc",
        )
        db.add(alice_db)
        db.commit()
        alice_db_id = alice_db.id
        db.close()

        holder = {"user": bob}
        client = _build_test_client(SessionLocal, holder)

        # 1. Passing legacy db_name field -> 422 Unprocessable Entity (extra="forbid")
        r_extra = client.post(
            "/api/nodejs-manager/install",
            json={
                "website_id": bob_site.id,
                "app_name": "Bob App",
                "db_name": "wp_alice_secret",
            },
        )
        assert r_extra.status_code == 422

        # 2. Bob tries to bind Alice's database via db_mode="existing" -> 403, zero SQL, index.html intact
        with patch.object(mariadb, "_run_sql") as mock_sql:
            r_cross = client.post(
                "/api/nodejs-manager/install",
                json={
                    "website_id": bob_site.id,
                    "app_name": "Bob App",
                    "db_mode": "existing",
                    "existing_database_id": alice_db_id,
                },
            )
            assert r_cross.status_code == 403
            mock_sql.assert_not_called()
            assert (bob_pub / "index.html").is_file()

        # 3. Bob already has 1 DB and package limit is 1 -> db_mode="new" returns 403 before purging index.html
        with SessionLocal() as s2:
            s2.add(
                DatabaseAccount(
                    owner_id=bob.id,
                    website_id=None,
                    db_name="bob_existing_db",
                    db_user="u_bob_existing",
                    db_password="enc",
                )
            )
            s2.commit()

        r_quota = client.post(
            "/api/nodejs-manager/install",
            json={
                "website_id": bob_site.id,
                "app_name": "Bob App",
                "db_mode": "new",
            },
        )
        assert r_quota.status_code == 403
        assert (bob_pub / "index.html").is_file()

        # 4. Rollback test: Alice installs with db_mode="new", but _sync_nodejs_worker_unit fails -> DB & row rolled back
        with SessionLocal() as s_quota:
            p = s_quota.query(UserPackage).filter_by(id=pkg_1db.id).first()
            p.database_limit = 5
            s_quota.commit()
        holder["user"] = alice
        (Path(alice_site.root_path) / "public_html").mkdir(parents=True, exist_ok=True)
        with patch.object(
            mariadb,
            "create_database",
            return_value={"db_name": "nd_alice_rb", "db_user": "u_nd_alice_rb", "db_password": "pw"},
        ), patch("app.services.teardown.drop_database_record") as mock_drop_rec, patch.object(
            nm,
            "_sync_nodejs_worker_unit",
            side_effect=HTTPException(status_code=500, detail="systemd sync failed"),
        ):
            r_fail = client.post(
                "/api/nodejs-manager/install",
                json={
                    "website_id": alice_site.id,
                    "app_name": "Alice Fail App",
                    "db_mode": "new",
                },
            )
            assert r_fail.status_code == 500
            mock_drop_rec.assert_called_once()

        with SessionLocal() as s3:
            assert s3.query(NodejsInstance).filter_by(website_id=alice_site.id).first() is None


def test_p0_3_env_edit_and_remove_cannot_drop_other_tenant_or_unrelated_database(tmp_path, monkeypatch):
    """1.5 (P0-3) & Round 16 P1-A:
    - Poisoning .env with another user's DB or another website's DB of the same user never drops that DB on remove.
    - P1-A: Installing with db_mode='existing' (wp_a bound to site 1) onto site 2:
      (a) DELETE /api/websites/2 does not DROP wp_a;
      (b) remove_nodejs_instance with drop_database=True does not DROP wp_a;
      (c) after remove, wp_a.website_id == site1.id.
    """
    monkeypatch.setattr(settings, "command_dry_run", True)

    _, SessionLocal = _setup_test_db()
    with patch.object(nm, "SessionLocal", SessionLocal), patch(
        "app.api.websites._rewrite_website_vhost"
    ):
        db = SessionLocal()
        pkg = UserPackage(name="node-pkg", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.commit()
        db.refresh(pkg)
        alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user", package_id=pkg.id)
        bob = User(username="bob", email="bob@example.com", hashed_password="x", role="end_user", package_id=pkg.id)
        db.add_all([alice, bob])
        db.commit()
        db.refresh(alice)
        db.refresh(bob)

        alice_site = Website(
            domain="alice.example.com",
            root_path=str(tmp_path / "alice"),
            document_root="public_html",
            linux_user="alice",
            owner_id=alice.id,
        )
        bob_site = Website(
            domain="bob.example.com",
            root_path=str(tmp_path / "bob"),
            document_root="public_html",
            linux_user="bob",
            owner_id=bob.id,
        )
        bob_other_site = Website(
            domain="bob2.example.com",
            root_path=str(tmp_path / "bob2"),
            document_root="public_html",
            linux_user="bob",
            owner_id=bob.id,
        )
        db.add_all([alice_site, bob_site, bob_other_site])
        db.commit()
        db.refresh(alice_site)
        db.refresh(bob_site)
        db.refresh(bob_other_site)
        (Path(bob_site.root_path) / "public_html").mkdir(parents=True, exist_ok=True)
        (Path(bob_other_site.root_path) / "public_html").mkdir(parents=True, exist_ok=True)

        alice_db = DatabaseAccount(
            owner_id=alice.id,
            website_id=alice_site.id,
            db_name="wp_alice_prod",
            db_user="u_alice_prod",
            db_password=nm.encrypt("AlicePass123!"),
        )
        bob_other_db = DatabaseAccount(
            owner_id=bob.id,
            website_id=bob_other_site.id,
            db_name="wp_bob_other",
            db_user="u_bob_other",
            db_password=nm.encrypt("BobPass123!"),
        )
        db.add_all([alice_db, bob_other_db])
        db.commit()
        db.refresh(bob_other_db)
        bob_other_db_id = bob_other_db.id
        bob_other_site_id = bob_other_site.id
        db.close()

        holder = {"user": bob}
        client = _build_test_client(SessionLocal, holder)

        res_install = client.post(
            "/api/nodejs-manager/install",
            json={
                "website_id": bob_site.id,
                "app_name": "Bob Node",
                "db_mode": "none",
            },
        )
        assert res_install.status_code == 200, res_install.text

        # Bob edits .env to point at Alice's DB
        res_env = client.put(
            f"/api/nodejs-manager/instances/{bob_site.id}/env",
            json={
                "mode": "raw",
                "raw_content": (
                    "NODE_ENV=production\n"
                    "PORT=21000\n"
                    "DB_DATABASE=wp_alice_prod\n"
                    "DB_USERNAME=u_alice_prod\n"
                ),
                "restart_after_save": False,
            },
        )
        assert res_env.status_code == 200, res_env.text
        assert res_env.json()["instance"]["db_name"] == ""

        with patch("app.services.teardown.drop_database_record") as mock_drop:
            res_rm = client.post(
                f"/api/nodejs-manager/instances/{bob_site.id}/remove",
                json={"delete_files": True, "drop_database": True},
            )
            assert res_rm.status_code == 200, res_rm.text
            mock_drop.assert_not_called()

        # P1-A: Bob installs Node.js on bob_site (site 2) reusing bob_other_db (bound to bob_other_site / site 1)
        res_existing = client.post(
            "/api/nodejs-manager/install",
            json={
                "website_id": bob_site.id,
                "app_name": "Bob Reuse DB",
                "db_mode": "existing",
                "existing_database_id": bob_other_db_id,
            },
        )
        assert res_existing.status_code == 200, res_existing.text
        with SessionLocal() as check_db1:
            reused = check_db1.query(DatabaseAccount).filter_by(id=bob_other_db_id).first()
            assert reused is not None
            assert reused.website_id == bob_other_site_id

        # (b) & (c): Removing Node.js with drop_database=True does NOT drop bob_other_db and keeps website_id
        with patch("app.services.teardown.drop_database_record") as mock_drop_existing:
            res_rm_existing = client.post(
                f"/api/nodejs-manager/instances/{bob_site.id}/remove",
                json={"delete_files": True, "drop_database": True},
            )
            assert res_rm_existing.status_code == 200, res_rm_existing.text
            mock_drop_existing.assert_not_called()

        with SessionLocal() as check_db2:
            reused_after_rm = check_db2.query(DatabaseAccount).filter_by(id=bob_other_db_id).first()
            assert reused_after_rm is not None
            assert reused_after_rm.website_id == bob_other_site_id

        # (a): Re-install with existing DB, then DELETE /api/websites/{bob_site.id} -> must NOT drop bob_other_db
        res_existing2 = client.post(
            "/api/nodejs-manager/install",
            json={
                "website_id": bob_site.id,
                "app_name": "Bob Reuse DB 2",
                "db_mode": "existing",
                "existing_database_id": bob_other_db_id,
            },
        )
        assert res_existing2.status_code == 200, res_existing2.text

        from app.api import websites as websites_api

        with SessionLocal() as del_session, patch("app.services.db_backends.LocalBackend.drop_database") as mock_backend_drop:
            dummy_req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
            websites_api.delete_website(
                bob_site.id,
                request=dummy_req,
                delete_files=True,
                delete_database=True,
                db=del_session,
                current_user=bob,
            )
            mock_backend_drop.assert_not_called()

        with SessionLocal() as check_db:
            assert check_db.query(DatabaseAccount).filter_by(db_name="wp_alice_prod").first() is not None
            still_there = check_db.query(DatabaseAccount).filter_by(id=bob_other_db_id).first()
            assert still_there is not None
            assert still_there.website_id == bob_other_site_id


def test_1_6_package_node_apps_limit_and_memory_ceiling(tmp_path, monkeypatch):
    """1.6:
    - node_apps_limit=0 returns 403 on GET /instances and POST /install.
    - node_apps_limit=1 allows 1 app and rejects the 2nd app with 403.
    - memory_mb=4096 on a package with node_app_memory_mb=512 returns 400.
    """
    monkeypatch.setattr(settings, "command_dry_run", True)
    _, SessionLocal = _setup_test_db()
    with patch.object(nm, "SessionLocal", SessionLocal), patch(
        "app.api.websites._rewrite_website_vhost"
    ):
        db = SessionLocal()
        pkg_zero = UserPackage(name="no-node", node_apps_limit=0, node_app_memory_mb=512)
        pkg_one = UserPackage(name="one-node", node_apps_limit=1, node_app_memory_mb=512)
        db.add_all([pkg_zero, pkg_one])
        db.commit()
        db.refresh(pkg_zero)
        db.refresh(pkg_one)

        u_zero = User(username="uzero", email="uzero@example.com", hashed_password="x", role="end_user", package_id=pkg_zero.id)
        u_one = User(username="uone", email="uone@example.com", hashed_password="x", role="end_user", package_id=pkg_one.id)
        db.add_all([u_zero, u_one])
        db.commit()
        db.refresh(u_zero)
        db.refresh(u_one)

        s_zero = Website(domain="zero.example.com", root_path=str(tmp_path / "zero"), document_root="public_html", linux_user="uzero", owner_id=u_zero.id)
        s_one_a = Website(domain="onea.example.com", root_path=str(tmp_path / "onea"), document_root="public_html", linux_user="uone", owner_id=u_one.id)
        s_one_b = Website(domain="oneb.example.com", root_path=str(tmp_path / "oneb"), document_root="public_html", linux_user="uone", owner_id=u_one.id)
        db.add_all([s_zero, s_one_a, s_one_b])
        db.commit()
        for s in (s_zero, s_one_a, s_one_b):
            db.refresh(s)
            (Path(s.root_path) / "public_html").mkdir(parents=True, exist_ok=True)

        # Attach package relationship for in-memory user objects
        u_zero.package = pkg_zero
        u_one.package = pkg_one
        db.close()

        holder = {"user": u_zero}
        client = _build_test_client(SessionLocal, holder)

        # node_apps_limit=0 -> 403 on GET /instances and POST /install
        assert client.get("/api/nodejs-manager/instances").status_code == 403
        assert client.post("/api/nodejs-manager/install", json={"website_id": s_zero.id}).status_code == 403

        # Switch to u_one (node_apps_limit=1, node_app_memory_mb=512)
        holder["user"] = u_one
        r_mem = client.post(
            "/api/nodejs-manager/install",
            json={"website_id": s_one_a.id, "memory_mb": 4096},
        )
        assert r_mem.status_code == 400

        r_ok = client.post(
            "/api/nodejs-manager/install",
            json={"website_id": s_one_a.id, "memory_mb": 512},
        )
        assert r_ok.status_code == 200

        # Second app exceeds node_apps_limit=1 -> 403
        r_second = client.post(
            "/api/nodejs-manager/install",
            json={"website_id": s_one_b.id, "memory_mb": 512},
        )
        assert r_second.status_code == 403


def test_stage_2_webhook_hmac_signature_token_async_and_deploy_lock(tmp_path, monkeypatch):
    """Stage 2 (P1-3):
    - Webhook is disabled by default (webhook_enabled=False, webhook_path="").
    - Enabling webhook generates random token + HMAC signing secret.
    - Missing X-Hub-Signature-256 / X-Gitlab-Token returns 401.
    - Invalid signature returns 403.
    - Valid signature returns 202 Accepted and runs deploy in BackgroundTasks.
    - Holding per-site deploy lockfile causes concurrent webhook/git-deploy to return 409 Conflict.
    """
    monkeypatch.setattr(settings, "command_dry_run", True)
    monkeypatch.setattr(settings, "secret_key", "unit-test-secret-key-for-hmac-32b")

    _, SessionLocal = _setup_test_db()
    with patch.object(nm, "SessionLocal", SessionLocal), patch(
        "app.api.websites._rewrite_website_vhost"
    ):
        db = SessionLocal()
        pkg = UserPackage(name="hook-pkg", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.commit()
        db.refresh(pkg)
        alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user", package_id=pkg.id)
        db.add(alice)
        db.commit()
        db.refresh(alice)

        site = Website(
            domain="hook.example.com",
            root_path=str(tmp_path / "hook"),
            document_root="public_html",
            linux_user="alice",
            owner_id=alice.id,
        )
        db.add(site)
        db.commit()
        db.refresh(site)
        (Path(site.root_path) / "public_html").mkdir(parents=True, exist_ok=True)
        db.close()

        holder = {"user": alice}
        client = _build_test_client(SessionLocal, holder)

        inst_res = client.post(
            "/api/nodejs-manager/install",
            json={
                "website_id": site.id,
                "app_name": "Hook App",
                "install_source": "template",
                "template": "express-api",
                "node_major": "22",
            },
        ).json()["instance"]

        # 1. Disabled by default
        assert inst_res["webhook_enabled"] is False
        assert inst_res["webhook_path"] == ""
        r_disabled = client.post(
            f"/api/nodejs-manager/webhook/{site.id}/any-token",
            headers={"X-Gitlab-Token": "any-secret"},
        )
        assert r_disabled.status_code == 403

        # 2. Enable webhook -> returns webhook_token and webhook_secret
        cfg_res = client.post(
            f"/api/nodejs-manager/instances/{site.id}/webhook",
            json={"enabled": True, "rotate_token": False},
        )
        assert cfg_res.status_code == 200
        cfg_json = cfg_res.json()
        token = cfg_json["webhook_token"]
        wh_secret = cfg_json["webhook_secret"]
        assert len(token) >= 24
        assert len(wh_secret) == 64

        # 3. Missing signature header -> 401
        r_no_sig = client.post(f"/api/nodejs-manager/webhook/{site.id}/{token}", content=b"{}")
        assert r_no_sig.status_code == 401

        # 4. Invalid signature -> 403
        r_bad_sig = client.post(
            f"/api/nodejs-manager/webhook/{site.id}/{token}",
            content=b'{"ref":"refs/heads/main"}',
            headers={"X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000"},
        )
        assert r_bad_sig.status_code == 403

        body = b'{"ref":"refs/heads/main"}'
        valid_sig = "sha256=" + hmac.new(wh_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        # 5. Holding per-site deploy lock causes concurrent webhook to return 409 Conflict
        lock_path = nm._acquire_deploy_lock(site.id)
        try:
            r_busy = client.post(
                f"/api/nodejs-manager/webhook/{site.id}/{token}",
                content=body,
                headers={"X-Hub-Signature-256": valid_sig, "X-GitHub-Event": "push"},
            )
            assert r_busy.status_code == 409
        finally:
            nm._release_deploy_lock(lock_path)

        # 6. Valid X-Hub-Signature-256 when lock is free -> 202 Accepted
        with patch.object(nm, "_execute_git_deploy_pipeline", return_value={"message": "ok"}) as mock_deploy:
            r_ok = client.post(
                f"/api/nodejs-manager/webhook/{site.id}/{token}",
                content=body,
                headers={"X-Hub-Signature-256": valid_sig, "X-GitHub-Event": "push"},
            )
            assert r_ok.status_code == 202
            assert r_ok.json()["status"] == "accepted"
            mock_deploy.assert_called_once()


def test_stage_3_port_uniqueness_retry_and_git_branch_hardening(tmp_path, monkeypatch):
    """Stage 3 (3.1 & 3.2):
    - GIT_BRANCH_RE & _validate_git_branch reject leading `-`, `..`, and `//`.
    - NodejsInstance port column is UNIQUE; if IntegrityError occurs on port commit, _update_instance_state
      retries with the next available port.
    """
    monkeypatch.setattr(settings, "command_dry_run", True)

    for valid_b in ("main", "release/1.0.9", "feat_node-22"):
        assert nm._validate_git_branch(valid_b) == valid_b

    for bad_b in ("-upload-pack", "--help", "main..evil", "feat//bad", "/leading", "trailing/", "ref.lock"):
        with pytest.raises(HTTPException) as exc_b:
            nm._validate_git_branch(bad_b)
        assert exc_b.value.status_code == 400

    _, SessionLocal = _setup_test_db()
    with patch.object(nm, "SessionLocal", SessionLocal):
        db = SessionLocal()
        pkg = UserPackage(name="hook-pkg", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.commit()
        db.refresh(pkg)
        alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user", package_id=pkg.id)
        db.add(alice)
        db.commit()
        db.refresh(alice)

        s1 = Website(domain="s1.example.com", root_path=str(tmp_path / "s1"), document_root="public_html", linux_user="alice", owner_id=alice.id)
        s2 = Website(domain="s2.example.com", root_path=str(tmp_path / "s2"), document_root="public_html", linux_user="alice", owner_id=alice.id)
        db.add_all([s1, s2])
        db.commit()
        db.refresh(s1)
        db.refresh(s2)

        # Insert s1 holding port 21000 directly
        db.add(NodejsInstance(website_id=s1.id, owner_id=alice.id, port=21000, detached=False))
        db.commit()

        # Attempt to save s2 initially requesting colliding port 21000 with allow_port_retry=True
        # _update_instance_state catches IntegrityError on port=21000 and retries with 21001
        state = nm._update_instance_state(
            s2.id,
            lambda inst: inst.setdefault("port", 21000),
            db=db,
            allow_port_retry=True,
        )
        assert state["instances"][str(s2.id)]["port"] == 21001
        db.close()


def test_full_nodejs_lifecycle_install_process_npm_env_security_remove(monkeypatch):
    monkeypatch.setattr(settings, "command_dry_run", True)

    _, SessionLocal = _setup_test_db()
    with tempfile.TemporaryDirectory() as tmpdir, patch.object(nm, "SessionLocal", SessionLocal):
        nm.invalidate_nodejs_inspect_cache()
        db = SessionLocal()
        try:
            pkg = UserPackage(name="lifecycle-pkg", node_apps_limit=5, node_app_memory_mb=1024)
            db.add(pkg)
            db.commit()
            db.refresh(pkg)
            user = User(
                username="nodeuser",
                email="nodeuser@example.com",
                hashed_password="x",
                role="end_user",
                package_id=pkg.id,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            user.package = pkg

            site_root = Path(tmpdir) / "domains" / "nodeapp.example.com"
            pub_dir = site_root / "public_html"
            pub_dir.mkdir(parents=True, exist_ok=True)

            site = Website(
                domain="nodeapp.example.com",
                root_path=str(site_root),
                document_root="public_html",
                linux_user="nodeuser",
                php_version="8.3",
                app_type="php",
                ssl_enabled=True,
                owner_id=user.id,
            )
            db.add(site)
            db.commit()
            db.refresh(site)

            with patch.object(
                mariadb,
                "create_database",
                return_value={
                    "db_name": "nd_nodeapp",
                    "db_user": "u_nd_nodeapp",
                    "db_password": "StrongPassword123!",
                },
            ) as mock_db_create, patch("app.api.websites._rewrite_website_vhost"):
                install_res = nm.install_nodejs_instance(
                    nm.NodejsInstallRequest(
                        website_id=site.id,
                        app_name="My Express API",
                        install_source="template",
                        template="express-api",
                        node_major="22",
                        start_mode="node",
                        start_arg="server.js",
                        memory_mb=512,
                        db_mode="new",
                    ),
                    db=db,
                    current_user=user,
                )

            assert install_res["instance"]["domain"] == "nodeapp.example.com"
            assert install_res["instance"]["framework"] == "Express.js"
            assert install_res["instance"]["node_major"] == "22"
            assert install_res["instance"]["status"] == "running"
            assert 21000 <= install_res["instance"]["port"] <= 21999
            assert install_res["credentials"]["db_created"] is True
            assert install_res["instance"]["db_name"] == "nd_nodeapp"
            assert (pub_dir / "package.json").is_file()
            assert (pub_dir / "server.js").is_file()
            assert (pub_dir / ".env").is_file()
            mock_db_create.assert_called_once()

            allocated_port = install_res["instance"]["port"]
            assert nm.get_nodejs_port_for_website(site.id, db=db) == allocated_port
            assert allocated_port in nm.reserved_nodejs_ports(db=db)
            assert site_apps.app_port_for_website(site, db=db) == allocated_port

            list_res = nm.list_nodejs_instances(refresh=False, db=db, current_user=user)
            assert len(list_res["instances"]) == 1
            assert list_res["instances"][0]["port"] == allocated_port

            stop_res = nm.manage_nodejs_process(
                site.id,
                nm.ProcessControlRequest(action="stop"),
                db=db,
                current_user=user,
            )
            assert stop_res["instance"]["status"] == "stopped"

            cfg_res = nm.manage_nodejs_process(
                site.id,
                nm.ProcessControlRequest(
                    action="start",
                    node_major="20",
                    start_mode="npm",
                    start_arg="start",
                    memory_mb=512,
                ),
                db=db,
                current_user=user,
            )
            assert cfg_res["instance"]["status"] == "running"
            assert cfg_res["instance"]["node_major"] == "20"
            assert cfg_res["instance"]["start_mode"] == "npm"
            assert cfg_res["instance"]["memory_mb"] == 512

            npm_add = nm.run_npm_action(
                site.id,
                nm.NpmActionRequest(action="add", package="cors@^2.8.5"),
                db=db,
                current_user=user,
            )
            pkg_names = [p["name"] for p in npm_add["instance"]["packages"]]
            assert "cors" in pkg_names

            npm_rem = nm.run_npm_action(
                site.id,
                nm.NpmActionRequest(action="remove", package="cors"),
                db=db,
                current_user=user,
            )
            pkg_names_after = [p["name"] for p in npm_rem["instance"]["packages"]]
            assert "cors" not in pkg_names_after

            env_get = nm.get_nodejs_env(site.id, db=db, current_user=user)
            assert env_get["parsed"]["app_name"] == "My Express API"

            env_put = nm.update_nodejs_env(
                site.id,
                nm.EnvUpdateRequest(
                    mode="structured",
                    updates={"APP_NAME": "Updated Node API", "LOG_LEVEL": "debug"},
                ),
                db=db,
                current_user=user,
            )
            assert env_put["parsed"]["app_name"] == "Updated Node API"
            assert env_put["parsed"]["log_level"] == "debug"

            sec_res = nm.update_nodejs_security(
                site.id,
                nm.SecurityActionRequest(apply_all=True, enabled=True),
                db=db,
                current_user=user,
            )
            assert sec_res["instance"]["security_score"] >= 85

            logs_res = nm.get_nodejs_logs(site.id, kind="systemd", lines=50, db=db, current_user=user)
            assert "nodeapp.example.com" in logs_res["content"]
            nm.clear_nodejs_logs(site.id, db=db, current_user=user)

            nm.detach_nodejs_instance(site.id, db=db, current_user=user)
            after_detach = nm.list_nodejs_instances(refresh=True, db=db, current_user=user)
            assert len(after_detach["instances"]) == 0

            scan_res = nm.scan_nodejs_instances(db=db, current_user=user)
            assert len(scan_res["instances"]) == 1

            with patch("app.services.teardown.drop_database_record") as mock_drop_db, patch(
                "app.api.websites._rewrite_website_vhost"
            ):
                nm.remove_nodejs_instance(
                    site.id,
                    nm.RemoveNodejsRequest(delete_files=True, drop_database=True),
                    db=db,
                    current_user=user,
                )
            mock_drop_db.assert_called_once()
            after_remove = nm.list_nodejs_instances(refresh=True, db=db, current_user=user)
            assert len(after_remove["instances"]) == 0
        finally:
            db.close()


def test_stage_4_nodejs_manager_feature_flag_default_disabled(monkeypatch):
    """4.3:
    - Settings().nodejs_manager_enabled defaults to False.
    - When nodejs_manager_enabled is False, router returns 404 for all endpoints.
    """
    from app.core.config import Settings

    assert Settings().nodejs_manager_enabled is False
    monkeypatch.setattr(settings, "nodejs_manager_enabled", False)

    _, SessionLocal = _setup_test_db()
    db = SessionLocal()
    pkg = UserPackage(name="node-pkg", node_apps_limit=5, node_app_memory_mb=1024)
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    user = User(
        username="alice",
        email="alice@example.com",
        hashed_password="x",
        role="end_user",
        package_id=pkg.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()

    holder = {"user": user}
    client = _build_test_client(SessionLocal, holder)
    assert client.get("/api/nodejs-manager/instances").status_code == 404
    assert client.post("/api/nodejs-manager/webhook/1", json={}).status_code == 404


def test_round16_p2_and_p3_regressions(tmp_path, monkeypatch):
    """Verify Round 16 P2-1..3 and P3-3..5 fixes:
    - P2-1: delete_website(..., delete_files=False) invokes nodejs-worker-delete when NodejsInstance exists.
    - P2-2: _rollback_failed_install invokes _delete_nodejs_worker_unit when install fails mid-way.
    - P2-3: _update_instance_state raises 409 immediately on non-port IntegrityError and raises 409 after 8 port collisions.
    - P3-3: public_nodejs_webhook_deploy rejects with 403 when package node_apps_limit == 0 or addon is disabled.
    - P3-4: _DEPLOY_LOCK_TTL_SECONDS is 1800.
    - P3-5: _execute_git_deploy_pipeline runs `git remote set-url origin -- <repo>` when `.git` exists.
    """
    from app.api import websites as websites_api

    monkeypatch.setattr(settings, "command_dry_run", True)
    monkeypatch.setattr(settings, "secret_key", "round16-test-secret-key-0123456789")

    assert nm._DEPLOY_LOCK_TTL_SECONDS == 1800

    _, SessionLocal = _setup_test_db()
    with patch.object(nm, "SessionLocal", SessionLocal), patch(
        "app.api.websites._rewrite_website_vhost"
    ):
        db = SessionLocal()
        pkg = UserPackage(name="pro", database_limit=5, node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.commit()
        db.refresh(pkg)

        alice = User(
            username="alice",
            email="alice@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg.id,
        )
        db.add(alice)
        db.commit()
        db.refresh(alice)

        site1 = Website(
            domain="site1.example.com",
            root_path=str(tmp_path / "site1"),
            document_root="public_html",
            linux_user="alice",
            owner_id=alice.id,
        )
        site2 = Website(
            domain="site2.example.com",
            root_path=str(tmp_path / "site2"),
            document_root="public_html",
            linux_user="alice",
            owner_id=alice.id,
        )
        db.add_all([site1, site2])
        db.commit()
        db.refresh(site1)
        db.refresh(site2)
        (Path(site1.root_path) / "public_html").mkdir(parents=True, exist_ok=True)
        (Path(site2.root_path) / "public_html").mkdir(parents=True, exist_ok=True)

        # P2-2: When _sync_nodejs_worker_unit fails during install, _delete_nodejs_worker_unit is called in rollback
        with patch.object(
            nm, "_sync_nodejs_worker_unit", side_effect=HTTPException(status_code=500, detail="systemctl restart failed")
        ), patch.object(nm, "_delete_nodejs_worker_unit") as mock_del_unit:
            with pytest.raises(HTTPException) as exc_install:
                nm.install_nodejs_instance(
                    nm.NodejsInstallRequest(
                        website_id=site1.id,
                        app_name="Fail Restart App",
                        db_mode="none",
                    ),
                    db=db,
                    current_user=alice,
                )
            assert exc_install.value.status_code == 500
            mock_del_unit.assert_called_once_with("alice", Path(site1.root_path))

        # Install successfully on site1 and site2
        nm.install_nodejs_instance(
            nm.NodejsInstallRequest(website_id=site1.id, app_name="Site1 App", db_mode="none"),
            db=db,
            current_user=alice,
        )
        nm.install_nodejs_instance(
            nm.NodejsInstallRequest(website_id=site2.id, app_name="Site2 App", db_mode="none"),
            db=db,
            current_user=alice,
        )

        # P2-3a: Non-port IntegrityError (e.g. website_id PK collision) raises 409 immediately on first attempt
        pk_calls = []

        def _fail_pk():
            pk_calls.append(1)
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed: nodejs_instances.website_id"))

        with patch.object(db, "commit", side_effect=_fail_pk):
            with pytest.raises(HTTPException) as exc_pk:
                nm._update_instance_state(site1.id, lambda inst: None, db=db, allow_port_retry=True)
            assert exc_pk.value.status_code == 409
            assert len(pk_calls) == 1

        # P2-3b: 8 consecutive port IntegrityErrors hit for-else and raise 409 instead of silently returning
        port_calls = []

        def _fail_port():
            port_calls.append(1)
            raise IntegrityError("UPDATE", {}, Exception("UNIQUE constraint failed: nodejs_instances.port"))

        with patch.object(db, "commit", side_effect=_fail_port):
            with pytest.raises(HTTPException) as exc_port8:
                nm._update_instance_state(site1.id, lambda inst: None, db=db, allow_port_retry=True)
            assert exc_port8.value.status_code == 409
            assert len(port_calls) == 8

        # P3-5: When .git already exists, _execute_git_deploy_pipeline runs `git remote set-url origin -- <new_repo>`
        git_dir = Path(site1.root_path) / "public_html" / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        recorded_cmds: List[List[str]] = []

        def _fake_run_site_user(linux_user, cwd, argv, *, node_major="22", extra_env=None, timeout=180):
            recorded_cmds.append(list(argv))
            return 0, "ok"

        with patch.object(nm, "_run_as_site_user", side_effect=_fake_run_site_user):
            nm._execute_git_deploy_pipeline(
                site1,
                db,
                git_repo="https://github.com/example/new-repo.git",
                git_branch="main",
                run_npm_install=False,
                build_script="",
                restart_process=False,
            )
        assert ["git", "remote", "set-url", "origin", "--", "https://github.com/example/new-repo.git"] in recorded_cmds

        # P3-3: Webhook returns 403 when package node_apps_limit is downgraded to 0
        wh_cfg = nm.configure_nodejs_webhook(
            site1.id,
            nm.WebhookConfigRequest(enabled=True, rotate_token=True),
            db=db,
            current_user=alice,
        )
        raw_token = wh_cfg["webhook_token"]
        wh_secret = wh_cfg["webhook_secret"]
        body_bytes = json.dumps({"ref": "refs/heads/main"}).encode("utf-8")
        sig = "sha256=" + hmac.new(wh_secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

        pkg.node_apps_limit = 0
        db.commit()
        db.refresh(alice)
        db.refresh(site1)
        db.refresh(site2)
        site1_id = site1.id
        site2_id = site2.id
        site2_root = str(site2.root_path)
        db.close()

        holder = {"user": alice}
        client = _build_test_client(SessionLocal, holder)
        r_wh_downgraded = client.post(
            f"/api/nodejs-manager/webhook/{site1_id}/{raw_token}",
            content=body_bytes,
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
        )
        assert r_wh_downgraded.status_code == 403

        # P2-1: delete_website(..., delete_files=False) still calls nodejs-worker-delete for site2
        app_full = FastAPI()
        app_full.include_router(websites_api.router, prefix="/api")

        def _override_db():
            s = SessionLocal()
            try:
                yield s
            finally:
                s.close()

        app_full.dependency_overrides[get_db] = _override_db
        app_full.dependency_overrides[get_current_user] = lambda: alice
        full_client = TestClient(app_full)

        priv_calls: List[tuple] = []

        def _spy_priv(action: str, **kwargs):
            priv_calls.append((action, kwargs.get("helper_args")))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(websites_api.shell, "privileged", side_effect=_spy_priv), patch.object(
            websites_api.nginx, "delete_wordpress_vhost"
        ), patch.object(websites_api.ssl, "release_site_certificates", return_value=""), patch.object(
            websites_api.waf, "remove_site_rules"
        ):
            r_del = full_client.delete(f"/api/websites/{site2_id}?delete_files=false&delete_database=false")
            assert r_del.status_code == 200
            assert ("nodejs-worker-delete", ["alice", site2_root]) in priv_calls


