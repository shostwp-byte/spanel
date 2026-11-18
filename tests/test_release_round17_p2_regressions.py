"""Regression tests for Round 17 P2 findings (P2-1 .. P2-5)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import nodejs_manager as nm
from app.api import packages as packages_api
from app.api import resources as resources_api
from app.api import users as users_api
from app.api import websites as websites_api
from app.core.config import settings
from app.core.database import Base, get_db
from app.api.deps import get_current_user
from app.models.entities import (
    NodejsInstance,
    ResellerPackage,
    User,
    UserPackage,
    Website,
    WebsiteAlias,
)
from app.services import addons


def _make_db():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)
    return eng, SessionLocal


def _build_nm_client(SessionLocal, current_user_holder):
    app = FastAPI()
    app.include_router(nm.router, prefix="/api")

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user_holder["user"]
    return TestClient(app)


# --------------------------------------------------------------------------- P2-1


def test_p2_1_nodejs_instance_endpoints_reject_uninitialized_and_detached_sites(tmp_path, monkeypatch):
    """POST /instances/{id}/permissions-policy (and sibling /security, /npm, /env, /webhook)
    must return 404 when no active NodejsInstance exists on the website, never turning a
    WordPress/PHP website into a reverse proxy or implicitly minting a NodejsInstance row."""
    _, SessionLocal = _make_db()
    monkeypatch.setattr(addons, "is_installed", lambda slug: True)
    monkeypatch.setattr(settings, "nodejs_manager_enabled", True)
    monkeypatch.setattr(settings, "command_dry_run", True)

    site_root = tmp_path / "wp.example.com"
    (site_root / "public_html").mkdir(parents=True)

    with SessionLocal() as db:
        pkg = UserPackage(name="starter", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.flush()
        alice = User(
            username="alice",
            email="alice@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg.id,
        )
        db.add(alice)
        db.flush()
        site = Website(
            domain="wp.example.com",
            owner_id=alice.id,
            root_path=str(site_root),
            document_root="public_html",
            linux_user="alice",
            app_type="wordpress",
            status="active",
        )
        db.add(site)
        db.commit()
        db.refresh(alice)
        db.refresh(site)
        site_id = site.id
        alice_ref = alice

    client = _build_nm_client(SessionLocal, {"user": alice_ref})
    vhost_calls = []

    with patch.object(nm, "_sync_nodejs_nginx_vhost", side_effect=lambda *a, **k: vhost_calls.append(a)), \
         patch.object(nm, "_resolve_nodejs_paths", return_value=("alice", site_root, site_root / "public_html")):
        # 1. Uninitialized WordPress site -> all instance routes return 404
        assert client.post(
            f"/api/nodejs-manager/instances/{site_id}/permissions-policy",
            json={"permissions_policy": "microphone=(self)"},
        ).status_code == 404
        assert client.post(
            f"/api/nodejs-manager/instances/{site_id}/security",
            json={"apply_all": True, "enabled": True},
        ).status_code == 404
        assert client.post(
            f"/api/nodejs-manager/instances/{site_id}/npm",
            json={"action": "cache_clean"},
        ).status_code == 404
        assert client.put(
            f"/api/nodejs-manager/instances/{site_id}/env",
            json={"mode": "raw", "raw_content": "NODE_ENV=production\n"},
        ).status_code == 404
        assert client.post(
            f"/api/nodejs-manager/instances/{site_id}/webhook",
            json={"enabled": True, "rotate_token": True},
        ).status_code == 404
        assert vhost_calls == []

        with SessionLocal() as db:
            refreshed = db.query(Website).filter(Website.id == site_id).first()
            assert refreshed.app_type == "wordpress"
            assert db.query(NodejsInstance).count() == 0

            # 2. Detached instance -> permissions-policy & security still return 404
            db.add(NodejsInstance(website_id=site_id, owner_id=alice_ref.id, port=None, detached=True))
            db.commit()

        assert client.post(
            f"/api/nodejs-manager/instances/{site_id}/permissions-policy",
            json={"permissions_policy": "microphone=(self)"},
        ).status_code == 404
        assert client.post(
            f"/api/nodejs-manager/instances/{site_id}/security",
            json={"apply_all": True, "enabled": True},
        ).status_code == 404
        assert vhost_calls == []


# --------------------------------------------------------------------------- P2-4


def test_p2_4_pid_aware_deploy_lock_reclaims_dead_pids_and_holds_live_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path)
    assert nm._DEPLOY_LOCK_TTL_SECONDS == 2400

    # 1. Live PID (current process) and fresh timestamp -> second caller gets 409 Conflict
    lock = nm._acquire_deploy_lock(42, operation="git-deploy")
    try:
        meta = nm._read_deploy_lock_meta(lock)
        assert meta["pid"] == os.getpid()
        assert meta["operation"] == "git-deploy"
        with pytest.raises(HTTPException) as exc_info:
            nm._acquire_deploy_lock(42, operation="process:start")
        assert exc_info.value.status_code == 409
        assert "git-deploy" in exc_info.value.detail
    finally:
        nm._release_deploy_lock(lock)

    # 2. Dead PID written moments ago -> reclaimed immediately without waiting for TTL
    dead_lock = tmp_path / "nodejs-deploy-43.lock"
    dead_lock.write_text(
        json.dumps({"pid": 99999991, "started_at": time.time(), "operation": "crashed-worker"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nm, "_pid_is_alive", lambda pid: False if pid == 99999991 else (pid == os.getpid()))
    reclaimed = nm._acquire_deploy_lock(43, operation="process:restart")
    try:
        assert nm._read_deploy_lock_meta(reclaimed)["pid"] == os.getpid()
        assert nm._read_deploy_lock_meta(reclaimed)["operation"] == "process:restart"
    finally:
        nm._release_deploy_lock(reclaimed)

    # 3. Live PID whose started_at exceeded stale_seconds -> reclaimed
    expired_lock = tmp_path / "nodejs-deploy-44.lock"
    expired_lock.write_text(
        json.dumps({"pid": os.getpid(), "started_at": time.time() - 3000, "operation": "stuck"}) + "\n",
        encoding="utf-8",
    )
    reclaimed_exp = nm._acquire_deploy_lock(44, stale_seconds=2400, operation="git-deploy")
    nm._release_deploy_lock(reclaimed_exp)


# --------------------------------------------------------------------------- P2-2


def test_p2_2_process_start_runs_in_background_with_site_lock_and_operation_status(tmp_path, monkeypatch):
    _, SessionLocal = _make_db()
    monkeypatch.setattr(addons, "is_installed", lambda slug: True)
    monkeypatch.setattr(settings, "nodejs_manager_enabled", True)
    monkeypatch.setattr(settings, "command_dry_run", False)
    monkeypatch.setattr(nm, "SessionLocal", SessionLocal)
    monkeypatch.setattr(nm, "_deploy_locks_dir", lambda: tmp_path / "locks")
    (tmp_path / "locks").mkdir(parents=True, exist_ok=True)

    site_root = tmp_path / "app.example.com"
    pub = site_root / "public_html"
    pub.mkdir(parents=True)
    (pub / "package.json").write_text('{"name":"demo","scripts":{"start":"node server.js"}}', encoding="utf-8")
    (pub / "server.js").write_text("console.log('ok');", encoding="utf-8")

    with SessionLocal() as db:
        pkg = UserPackage(name="pro", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.flush()
        alice = User(
            username="alice",
            email="alice@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg.id,
        )
        db.add(alice)
        db.flush()
        site = Website(
            domain="app.example.com",
            owner_id=alice.id,
            root_path=str(site_root),
            document_root="public_html",
            linux_user="alice",
            app_type="application",
            status="active",
        )
        db.add(site)
        db.flush()
        db.add(
            NodejsInstance(
                website_id=site.id,
                owner_id=alice.id,
                port=21030,
                detached=False,
                process_enabled=False,
                node_major="22",
                start_mode="npm",
                start_arg="start",
                memory_mb=512,
            )
        )
        db.commit()
        db.refresh(alice)
        site_id = site.id
        alice_ref = alice

    client = _build_nm_client(SessionLocal, {"user": alice_ref})
    ready_calls = []
    sync_calls = []

    with patch.object(nm, "_resolve_nodejs_paths", return_value=("alice", site_root, pub)), \
         patch.object(nm, "_read_app_file", return_value=""), \
         patch.object(nm, "_is_nodejs_project_dir", return_value=True), \
         patch.object(nm, "_detect_package_json_metadata", return_value={"framework": "Node.js", "scripts": {"start": "node server.js"}, "packages": []}), \
         patch.object(nm, "_evaluate_security_checks", return_value=[]), \
         patch.object(nm, "_query_nodejs_worker_status", return_value={"status": "running", "state": "active", "enabled": True, "port": 21030, "node_major": "22", "start_mode": "npm", "start_arg": "start", "memory_mb": 512, "main_pid": 987, "memory_current_bytes": 1024, "restarts": 0}), \
         patch.object(nm, "_ensure_nodejs_app_ready", side_effect=lambda *a, **k: ready_calls.append((a, k)) or {"installed": True}), \
         patch.object(nm, "_ensure_nodejs_local_db_grants"), \
         patch.object(nm, "_sync_nodejs_worker_unit", side_effect=lambda *a, **k: sync_calls.append((a, k)) or {"status": "running"}), \
         patch.object(nm, "_sync_nodejs_nginx_vhost"):
        # 1. Holding the per-site lock blocks concurrent process start with 409 Conflict
        held = nm._acquire_deploy_lock(site_id, operation="git-deploy")
        try:
            r_conflict = client.post(
                f"/api/nodejs-manager/instances/{site_id}/process",
                json={"action": "start"},
            )
            assert r_conflict.status_code == 409
        finally:
            nm._release_deploy_lock(held)

        # 2. HTTP POST /process with action=start returns 202 Accepted and runs background task
        r_start = client.post(
            f"/api/nodejs-manager/instances/{site_id}/process",
            json={"action": "start"},
        )
        assert r_start.status_code == 202, r_start.text
        body = r_start.json()
        assert body["queued"] is True
        assert body["status"] == "accepted"
        assert len(ready_calls) == 1
        assert len(sync_calls) == 1
        assert not nm._deploy_lock_path(site_id).exists()

        # 3. GET /operation-status reports completed state and fresh instance payload
        r_status = client.get(f"/api/nodejs-manager/instances/{site_id}/operation-status")
        assert r_status.status_code == 200
        st_data = r_status.json()
        assert st_data["operation"]["active"] is False
        assert st_data["operation"]["status"] == "completed"
        assert st_data["instance"]["status"] == "running"


# --------------------------------------------------------------------------- P2-3


def test_p2_3_rename_preserves_wildcard_ssl_and_alias_ssl_and_keeps_covering_cert(tmp_path):
    _, SessionLocal = _make_db()
    with SessionLocal() as db:
        alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user")
        db.add(alice)
        db.flush()
        site = Website(
            domain="app1.example.com",
            owner_id=alice.id,
            root_path=websites_api.site_users.site_root_for_panel_user("alice", "app1.example.com"),
            document_root="public_html",
            linux_user="alice",
            app_type="php",
            php_version="8.4",
            ssl_enabled=True,
            ssl_mode="cloudflare",
            ssl_source_domain="example.com",
        )
        db.add(site)
        db.flush()
        alias = WebsiteAlias(
            website_id=site.id,
            domain="portal.example.com",
            mode="alias",
            ssl_enabled=True,
        )
        db.add(alias)
        db.commit()
        db.refresh(site)

        deleted_ssl = []
        with patch.object(websites_api.site_users, "move_site_runtime"), \
             patch.object(websites_api, "_ensure_default_waf_file"), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api.waf, "remove_site_rules"), \
             patch.object(websites_api, "_rewrite_website_vhost"), \
             patch.object(websites_api.nginx, "delete_wordpress_vhost"), \
             patch.object(websites_api, "_refresh_placeholder_page_if_unchanged"), \
             patch.object(websites_api.dns, "zone_for_new_website"), \
             patch.object(websites_api.ssl, "cert_info", return_value={"sans": ["example.com", "*.example.com"]}), \
             patch.object(websites_api.ssl, "delete_ssl", side_effect=lambda d: deleted_ssl.append(d) or SimpleNamespace(returncode=0, stdout="", stderr="")):
            websites_api._rename_single_website(db, site, "app2.example.com", alice, alice.id)

        assert site.domain == "app2.example.com"
        assert site.ssl_enabled is True
        assert site.ssl_mode == "cloudflare"
        assert site.ssl_source_domain == "example.com"
        db.refresh(alias)
        assert alias.ssl_enabled is True
        # Certificate still covers app2.example.com and portal.example.com -> never deleted
        assert deleted_ssl == []


def test_p2_3_rename_rollback_restores_old_waf_before_vhost_and_cleans_new_files():
    _, SessionLocal = _make_db()
    with SessionLocal() as db:
        alice = User(username="alice", email="alice@example.com", hashed_password="x", role="end_user")
        db.add(alice)
        db.flush()
        site = Website(
            domain="old.example.com",
            owner_id=alice.id,
            root_path=websites_api.site_users.site_root_for_panel_user("alice", "old.example.com"),
            document_root="public_html",
            linux_user="alice",
            app_type="php",
            php_version="8.4",
            ssl_enabled=True,
            ssl_mode="manual",
            ssl_cert_path="/etc/ssl/old.crt",
            ssl_key_path="/etc/ssl/old.key",
        )
        db.add(site)
        db.flush()
        alias = WebsiteAlias(website_id=site.id, domain="alias.old.example.com", mode="alias", ssl_enabled=True)
        db.add(alias)
        db.commit()
        db.refresh(site)

        events: list[tuple[str, str]] = []

        with patch.object(websites_api.site_users, "move_site_runtime", side_effect=lambda o, n, u, p=None: events.append(("move", f"{o}->{n}"))), \
             patch.object(websites_api, "_ensure_default_waf_file", side_effect=lambda d: events.append(("ensure_waf", d))), \
             patch.object(websites_api.waf, "sync_website_rules", side_effect=lambda w: events.append(("sync_waf", w.domain)) or SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api.waf, "remove_site_rules", side_effect=lambda d: events.append(("remove_waf", d))), \
             patch.object(websites_api, "_rewrite_website_vhost", side_effect=lambda w, **kw: events.append(("rewrite_vhost", w.domain))), \
             patch.object(websites_api.nginx, "delete_wordpress_vhost", side_effect=lambda d: events.append(("delete_vhost", d))), \
             patch.object(websites_api, "_refresh_placeholder_page_if_unchanged", side_effect=RuntimeError("boom after waf.remove_site_rules")), \
             patch.object(websites_api.ssl, "cert_info", return_value={"sans": ["old.example.com"]}):
            with pytest.raises(HTTPException):
                websites_api._rename_single_website(db, site, "new.example.com", alice, alice.id)

        assert site.domain == "old.example.com"
        assert site.ssl_enabled is True
        assert site.ssl_mode == "manual"
        assert alias.ssl_enabled is True
        # Rollback must restore old WAF rules BEFORE rewriting old vhost, and delete new vhost + new WAF rules
        rollback_events = events[events.index(("remove_waf", "old.example.com")) + 1:]
        assert ("sync_waf", "old.example.com") in rollback_events
        assert ("rewrite_vhost", "old.example.com") in rollback_events
        assert rollback_events.index(("sync_waf", "old.example.com")) < rollback_events.index(("rewrite_vhost", "old.example.com"))
        assert ("delete_vhost", "new.example.com") in rollback_events
        assert ("remove_waf", "new.example.com") in rollback_events


def test_p2_3_rename_legacy_site_without_linux_user_moves_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "command_dry_run", False)
    monkeypatch.setattr(
        websites_api.site_users,
        "legacy_site_root_for_domain",
        lambda dom: tmp_path / dom,
    )
    old_dir = tmp_path / "legacy-old.example.com"
    (old_dir / "public_html").mkdir(parents=True)
    (old_dir / "public_html" / "index.html").write_text("legacy", encoding="utf-8")

    _, SessionLocal = _make_db()
    with SessionLocal() as db:
        site = Website(
            domain="legacy-old.example.com",
            owner_id=1,
            root_path=str(old_dir),
            document_root="public_html",
            linux_user=None,
            app_type="static",
        )
        db.add(site)
        db.commit()
        db.refresh(site)

        with patch.object(websites_api, "_ensure_default_waf_file"), \
             patch.object(websites_api.waf, "sync_website_rules", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")), \
             patch.object(websites_api.waf, "remove_site_rules"), \
             patch.object(websites_api, "_rewrite_website_vhost"), \
             patch.object(websites_api.nginx, "delete_wordpress_vhost"), \
             patch.object(websites_api, "_refresh_placeholder_page_if_unchanged"), \
             patch.object(websites_api.ssl, "release_site_certificates"), \
             patch.object(websites_api.dns, "zone_for_new_website"):
            websites_api._rename_single_website(db, site, "legacy-new.example.com", None, None)

        new_dir = tmp_path / "legacy-new.example.com"
        assert (new_dir / "public_html" / "index.html").read_text(encoding="utf-8") == "legacy"
        assert not old_dir.exists()


# --------------------------------------------------------------------------- P2-5


def test_p2_5_reseller_cannot_bypass_pool_or_ceilings_via_resource_limits_or_package_update(monkeypatch):
    from app.api.resources import UserResourceLimitsUpdate
    from app.schemas.schemas import UserPackageUpdate
    from app.services import resource_monitor

    monkeypatch.setattr(resource_monitor, "apply_user_cgroup_limits_async", lambda uid: None)
    monkeypatch.setattr(resource_monitor, "collect_and_enforce_mysql_quotas", lambda db: {})

    _, SessionLocal = _make_db()
    fake_req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), url=SimpleNamespace(scheme="https"))

    with SessionLocal() as db:
        rpkg = ResellerPackage(
            name="Reseller10G",
            slug="res-10g",
            user_limit=5,
            total_storage_mb=10240,
            total_website_limit=10,
            total_database_limit=5,
            overselling_enabled=False,
        )
        other_res_pkg = UserPackage(name="OtherResPkg", owner_id=999, storage_limit_mb=1024)
        res_tier_pkg = UserPackage(name="ResellerTier", is_reseller_package=True, storage_limit_mb=50000)
        db.add_all([rpkg, other_res_pkg, res_tier_pkg])
        db.flush()

        reseller = User(
            username="res1",
            email="res1@example.com",
            hashed_password="x",
            role="reseller",
            reseller_package_id=rpkg.id,
            memory_max_mb=2048,
            cpu_quota_percent=100,
            tasks_max=2048,
        )
        db.add(reseller)
        db.flush()

        own_pkg = UserPackage(
            name="Res1Starter",
            owner_id=reseller.id,
            website_limit=2,
            storage_limit_mb=4096,
            database_limit=2,
            memory_max_mb=1024,
        )
        db.add(own_pkg)
        db.flush()

        cust1 = User(
            username="cust1",
            email="c1@example.com",
            hashed_password="x",
            role="end_user",
            reseller_id=reseller.id,
            package_id=own_pkg.id,
            website_limit=2,
            storage_limit_mb=4096,
            database_limit=2,
            memory_max_mb=1024,
        )
        cust2 = User(
            username="cust2",
            email="c2@example.com",
            hashed_password="x",
            role="end_user",
            reseller_id=reseller.id,
            package_id=own_pkg.id,
            website_limit=2,
            storage_limit_mb=4096,
            database_limit=2,
            memory_max_mb=1024,
        )
        db.add_all([cust1, cust2])
        db.commit()
        db.refresh(reseller)
        db.refresh(cust1)
        db.refresh(cust2)

        # 1. Unlimited storage (-1 or 0) via POST /resources/users/{id}/limits -> 403
        for bad_storage in (-1, 0, 7000):  # 4096 (cust2) + 7000 > 10240 pool
            with pytest.raises(HTTPException) as exc_s:
                resources_api.update_user_resource_limits(
                    cust1.id,
                    UserResourceLimitsUpdate(storage_limit_mb=bad_storage),
                    fake_req,
                    db,
                    reseller,
                )
            assert exc_s.value.status_code == 403
            db.rollback()

        # 2. Exceeding database pool (2 + 4 > 5) or memory ceiling (4096 > 2048 or -1) -> 403
        with pytest.raises(HTTPException) as exc_db:
            resources_api.update_user_resource_limits(
                cust1.id,
                UserResourceLimitsUpdate(database_limit=4),
                fake_req,
                db,
                reseller,
            )
        assert exc_db.value.status_code == 403
        db.rollback()

        for bad_mem in (-1, 0, 4096):
            with pytest.raises(HTTPException) as exc_m:
                resources_api.update_user_resource_limits(
                    cust1.id,
                    UserResourceLimitsUpdate(memory_max_mb=bad_mem),
                    fake_req,
                    db,
                    reseller,
                )
            assert exc_m.value.status_code == 403
            db.rollback()

        # 3. Assigning another reseller's package or a reseller-tier package -> 403
        for forbidden_pkg_id in (other_res_pkg.id, res_tier_pkg.id):
            with pytest.raises(HTTPException) as exc_p:
                resources_api.update_user_resource_limits(
                    cust1.id,
                    UserResourceLimitsUpdate(package_id=forbidden_pkg_id),
                    fake_req,
                    db,
                    reseller,
                )
            assert exc_p.value.status_code == 403
            db.rollback()

        # 4. Updating own package so cust1 + cust2 exceed 10240 MB pool (6000 * 2 = 12000) -> 403
        with pytest.raises(HTTPException) as exc_pu:
            packages_api.update_package(
                own_pkg.id,
                UserPackageUpdate(storage_limit_mb=6000),
                fake_req,
                db,
                reseller,
            )
        assert exc_pu.value.status_code == 403
        db.rollback()

        # 5. Valid resource adjustment within pool & ceilings -> 200 OK
        ok_res = resources_api.update_user_resource_limits(
            cust1.id,
            UserResourceLimitsUpdate(storage_limit_mb=5120, database_limit=3, memory_max_mb=1536),
            fake_req,
            db,
            reseller,
        )
        assert ok_res["ok"] is True
        db.refresh(cust1)
        assert cust1.storage_limit_mb == 5120
        assert cust1.database_limit == 3
        assert cust1.memory_max_mb == 1536




