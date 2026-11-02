"""Regression tests for standardized user creation with primary domain and
unified Panel + SFTP password."""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

import api_testkit as kit
from app.api import provisioning, users, websites
from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.core.security import verify_password
from app.models.entities import ResellerPackage, User, Website
from app.services import dns, mail, multi_server, nginx, provisioning as prov_svc, resource_monitor, site_users


def _build_panel_client(acting_user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(users.router, prefix="/api")
    app.include_router(websites.router, prefix="/api")
    app.include_router(provisioning.router, prefix="/api")

    def _current(db=Depends(get_db)):
        return db.get(User, acting_user_id)

    app.dependency_overrides[get_current_user] = _current
    return TestClient(app)


@pytest.fixture()
def runtime_fakes(monkeypatch, tmp_path):
    kit.reset_db()
    calls = {
        "ensure_panel_user": [],
        "set_panel_user_password": [],
        "ensure_site_runtime": [],
        "write_vhost": [],
        "undo_failed_create": [],
    }

    monkeypatch.setattr(multi_server, "check_cluster_conflict", lambda **kw: {"conflict": False})
    monkeypatch.setattr(prov_svc, "linux_user_exists", lambda username: False)
    monkeypatch.setattr(
        prov_svc,
        "undo_failed_create",
        lambda **kw: calls["undo_failed_create"].append(kw),
    )
    monkeypatch.setattr(
        site_users,
        "ensure_panel_user",
        lambda username, password=None: (calls["ensure_panel_user"].append((username, password)) or username),
    )
    monkeypatch.setattr(
        site_users,
        "set_panel_user_password",
        lambda username, password: calls["set_panel_user_password"].append((username, password)),
    )
    monkeypatch.setattr(
        site_users,
        "ensure_site_runtime",
        lambda domain, root_path, php_ver, linux_user=None: (
            calls["ensure_site_runtime"].append((domain, root_path, php_ver, linux_user)) or (linux_user or "spuser")
        ),
    )
    monkeypatch.setattr(site_users, "fix_site_path", lambda path, linux_user: None)
    monkeypatch.setattr(websites, "_write_placeholder_page", lambda *a, **kw: None)
    monkeypatch.setattr(websites, "_ensure_default_waf_file", lambda domain: None)
    monkeypatch.setattr(nginx, "vhost_exists", lambda domain: False)
    monkeypatch.setattr(
        nginx,
        "write_vhost",
        lambda domain, root_path, **kw: calls["write_vhost"].append((domain, root_path, kw)),
    )
    monkeypatch.setattr(mail, "webmail_host_taken", lambda db, domain: False)
    monkeypatch.setattr(dns, "zone_for_new_website", lambda db, site: None)
    monkeypatch.setattr(resource_monitor, "apply_user_cgroup_limits_async", lambda user_id: None)
    return calls


def test_reseller_create_user_assigns_primary_domain_and_unified_password(runtime_fakes):
    with SessionLocal() as db:
        admin = kit.make_user(db, "rootadmin", role="admin")
        rpkg = ResellerPackage(
            name="Reseller Pro",
            user_limit=20,
            total_website_limit=50,
            total_storage_mb=51200,
            total_database_limit=50,
        )
        db.add(rpkg)
        db.commit()
        db.refresh(rpkg)
        reseller = kit.make_user(db, "quangdungvn", role="reseller", reseller_package_id=rpkg.id)
        upkg = kit.make_package(
            db,
            name="User 5GB",
            owner_id=reseller.id,
            website_limit=5,
            storage_limit_mb=5120,
        )
        reseller_id = reseller.id
        upkg_id = upkg.id
        _ = admin.id

    reseller_client = _build_panel_client(reseller_id)
    resp = reseller_client.post(
        "/api/users",
        json={
            "username": "reno26",
            "domain": "renolabs-audio.com",
            "email": "admin@renolabs-audio.com",
            "password": "UnifiedPass_2026!",
            "role": "end_user",
            "package_id": upkg_id,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "reno26"
    assert body["primary_domain"] == "renolabs-audio.com"
    assert body["domains"] == ["renolabs-audio.com"]
    assert body["domains_count"] == 1
    assert body["sftp_password"] == "UnifiedPass_2026!"
    assert body["sftp_password_set_at"] is None
    assert runtime_fakes["ensure_panel_user"] == [("reno26", "UnifiedPass_2026!")]
    assert len(runtime_fakes["write_vhost"]) == 1
    assert runtime_fakes["write_vhost"][0][0] == "renolabs-audio.com"

    # List Users endpoint returns domain metadata without N+1 omissions.
    list_resp = reseller_client.get("/api/users")
    assert list_resp.status_code == 200
    listed = [u for u in list_resp.json() if u["username"] == "reno26"]
    assert len(listed) == 1
    assert listed[0]["primary_domain"] == "renolabs-audio.com"
    assert listed[0]["domains"] == ["renolabs-audio.com"]
    assert listed[0]["domains_count"] == 1

    # When the end user logs in, the domain is already present under Domains (/api/websites).
    user_client = _build_panel_client(body["id"])
    sites_resp = user_client.get("/api/websites")
    assert sites_resp.status_code == 200
    sites = sites_resp.json()
    assert [s["domain"] for s in sites] == ["renolabs-audio.com"]
    assert sites[0]["owner_id"] == body["id"]
    assert sites[0]["root_path"] == site_users.site_root_for_panel_user("reno26", "renolabs-audio.com")


def test_duplicate_domain_rejected_before_creating_user(runtime_fakes):
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        pkg = kit.make_package(db, name="Starter", website_limit=5, storage_limit_mb=2048)
        db.add(
            Website(
                domain="existing-site.com",
                owner_id=admin.id,
                root_path="/home/admin1/existing-site.com",
                document_root="public_html",
                linux_user="admin1",
                php_version="8.4",
                app_type="php",
                status="active",
            )
        )
        db.commit()
        admin_id, pkg_id = admin.id, pkg.id

    c = _build_panel_client(admin_id)
    resp = c.post(
        "/api/users",
        json={
            "username": "newclient1",
            "domain": "existing-site.com",
            "email": "admin@existing-site.com",
            "password": "UnifiedPass_2026!",
            "role": "end_user",
            "package_id": pkg_id,
        },
    )
    assert resp.status_code == 409
    assert "Domain already exists" in resp.json()["detail"]
    assert runtime_fakes["ensure_panel_user"] == []
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "newclient1").first() is None


def test_website_provisioning_failure_rolls_back_user_and_system_state(runtime_fakes, monkeypatch):
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin2", role="admin")
        pkg = kit.make_package(db, name="Starter2", website_limit=5, storage_limit_mb=2048)
        admin_id, pkg_id = admin.id, pkg.id

    def _boom(*_a, **_kw):
        raise RuntimeError("nginx vhost reload failed")

    monkeypatch.setattr(nginx, "write_vhost", _boom)

    c = _build_panel_client(admin_id)
    resp = c.post(
        "/api/users",
        json={
            "username": "rollbackusr",
            "domain": "rollback-domain.com",
            "email": "admin@rollback-domain.com",
            "password": "UnifiedPass_2026!",
            "role": "end_user",
            "package_id": pkg_id,
        },
    )
    assert resp.status_code == 500
    assert len(runtime_fakes["undo_failed_create"]) == 1
    undo_call = runtime_fakes["undo_failed_create"][0]
    assert undo_call["username"] == "rollbackusr"
    assert undo_call["domain"] == "rollback-domain.com"
    assert undo_call["delete_linux_user"] is True
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "rollbackusr").first() is None
        assert db.query(Website).filter(Website.domain == "rollback-domain.com").first() is None


def test_validation_rejects_colon_password_and_domain_on_reseller_role(runtime_fakes):
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin3", role="admin")
        pkg = kit.make_package(db, name="Starter3", website_limit=5, storage_limit_mb=2048)
        admin_id, pkg_id = admin.id, pkg.id

    c = _build_panel_client(admin_id)
    bad_pw = c.post(
        "/api/users",
        json={
            "username": "colonuser",
            "domain": "colon-test.com",
            "email": "admin@colon-test.com",
            "password": "Bad:Password_2026!",
            "role": "end_user",
            "package_id": pkg_id,
        },
    )
    assert bad_pw.status_code == 422

    bad_role = c.post(
        "/api/users",
        json={
            "username": "resellerdom",
            "domain": "reseller-dom.com",
            "email": "admin@reseller-dom.com",
            "password": "UnifiedPass_2026!",
            "role": "reseller",
        },
    )
    assert bad_role.status_code == 422


def test_password_updates_preserve_and_restore_unified_sftp_state(runtime_fakes):
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin4", role="admin")
        pkg = kit.make_package(db, name="Starter4", website_limit=5, storage_limit_mb=2048)
        admin_id, pkg_id = admin.id, pkg.id

    c = _build_panel_client(admin_id)
    created = c.post(
        "/api/users",
        json={
            "username": "unifyusr",
            "domain": "unify-site.com",
            "email": "admin@unify-site.com",
            "password": "InitialUnified_12!",
            "role": "end_user",
            "package_id": pkg_id,
        },
    ).json()
    uid = created["id"]

    # Updating password while unified syncs SFTP automatically and keeps sftp_password_set_at == None.
    r1 = c.post(f"/api/users/{uid}/password", json={"password": "SecondUnified_12!"})
    assert r1.status_code == 200
    assert "sftp_password_rotated" not in r1.json()
    assert runtime_fakes["set_panel_user_password"][-1] == ("unifyusr", "SecondUnified_12!")
    with SessionLocal() as db:
        u = db.get(User, uid)
        assert u.sftp_password_set_at is None
        assert verify_password("SecondUnified_12!", u.hashed_password)

    # Setting a DIFFERENT explicit SFTP password marks sftp_password_set_at non-null.
    r_sep = c.post(f"/api/users/{uid}/sftp-password", json={"password": "SeparateSftp_99!"})
    assert r_sep.status_code == 200
    assert r_sep.json()["separated_from_panel_password"] is True
    with SessionLocal() as db:
        assert db.get(User, uid).sftp_password_set_at is not None

    # Re-unifying via Admin/Reseller password reset (/password + /sftp-password with same secret) clears sftp_password_set_at.
    assert c.post(f"/api/users/{uid}/password", json={"password": "ReUnified_2026!"}).status_code == 200
    r_reunify = c.post(f"/api/users/{uid}/sftp-password", json={"password": "ReUnified_2026!"})
    assert r_reunify.status_code == 200
    assert r_reunify.json()["separated_from_panel_password"] is False
    with SessionLocal() as db:
        u = db.get(User, uid)
        assert u.sftp_password_set_at is None
        assert isinstance(u.created_at, datetime) or u.id == uid


def test_provisioning_api_creates_account_with_unified_password(runtime_fakes, monkeypatch):
    from app.services import php as php_svc

    monkeypatch.setattr(php_svc, "list_installed_php", lambda: ["8.4"])
    with SessionLocal() as db:
        kit.make_user(db, "provadmin", role="admin")
        pkg = kit.make_package(db, name="Basic", website_limit=5, storage_limit_mb=5120)
        token = kit.make_token(db, scopes="accounts:write")
        pkg_id = pkg.id

    prov_client = kit.client()
    resp = prov_client.post(
        "/api/provisioning/v1/accounts",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "idem-unified-001"},
        json={
            "external_id": "whmcs:901",
            "username": "sp901_client",
            "email": "owner@whmcs-client.com",
            "password": "WhmcsUnified_2026!",
            "domain": "whmcs-client.com",
            "package_id": pkg_id,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sftp_password"] == "WhmcsUnified_2026!"
    assert ("sp901_client", "WhmcsUnified_2026!") in runtime_fakes["ensure_panel_user"]
    with SessionLocal() as db:
        created = db.query(User).filter(User.username == "sp901_client").one()
        assert created.sftp_password_set_at is None
