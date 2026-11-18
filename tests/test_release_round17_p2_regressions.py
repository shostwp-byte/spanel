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
from app.core.deps import get_current_user
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


