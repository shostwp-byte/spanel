"""New provisioning endpoints: account list, real bandwidth, limits from the
billing system, reseller packages, SFTP reset, backups and admin SSO."""
from unittest.mock import patch

import pytest

import api_testkit as kit
from app.core.database import SessionLocal
from app.models.entities import ProvisioningAccount, ResellerPackage, User
from app.services import provisioning as prov

BASE = "/api/provisioning/v1"
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_db():
    kit.reset_db()
    with patch("app.services.audit._write_audit_anchor"), \
            patch("app.services.resource_monitor.apply_user_cgroup_limits_async"):
        yield


def _account(db, external_id, username, status="active", role="end_user", package=None, **user_extra):
    user_id = None
    if status != "terminated":
        user = kit.make_user(db, username, role=role, package_id=package.id if package else None, **user_extra)
        user_id = user.id
    db.add(ProvisioningAccount(
        external_id=external_id, user_id=user_id, package_id=package.id if package else None, status=status,
    ))
    db.commit()
    return user_id


def _summary(used=0, limit=None):
    return {"storage_used_bytes": used, "storage_limit_bytes": limit, "storage_percent": 0.0}


def test_list_accounts_paginates_and_filters():
    with SessionLocal() as db:
        package = kit.make_package(db)
        for i in range(5):
            _account(db, f"whmcs:{i}", f"user{i}x", package=package)
        _account(db, "whmcs:90", "gone", status="terminated")
        _account(db, "whmcs:91", "paused", status="suspended", package=package)
        raw = kit.make_token(db, scopes="accounts:read")
    c = kit.client()

    first = c.get(f"{BASE}/accounts", params={"per_page": 4}, headers=kit.bearer(raw)).json()
    assert first["total"] == 6 and first["page"] == 1 and len(first["items"]) == 4
    second = c.get(f"{BASE}/accounts", params={"per_page": 4, "page": 2}, headers=kit.bearer(raw)).json()
    assert len(second["items"]) == 2
    ids = [i["external_id"] for i in first["items"] + second["items"]]
    assert "whmcs:90" not in ids and len(set(ids)) == 6

    every = c.get(f"{BASE}/accounts", params={"status": "all"}, headers=kit.bearer(raw)).json()
    assert every["total"] == 7
    suspended = c.get(f"{BASE}/accounts", params={"status": "suspended"}, headers=kit.bearer(raw)).json()
    assert [i["username"] for i in suspended["items"]] == ["paused"]
    found = c.get(f"{BASE}/accounts", params={"q": "user3"}, headers=kit.bearer(raw)).json()
    assert [i["external_id"] for i in found["items"]] == ["whmcs:3"]


def test_usage_reports_monthly_bandwidth_and_limit():
    with SessionLocal() as db:
        package = kit.make_package(db)
        user_id = _account(db, "whmcs:1", "bwuser", package=package, bandwidth_limit_mb=2048)
        raw = kit.make_token(db)
    with patch("app.services.storage_quota.storage_usage_summary", return_value=_summary(10)), \
            patch("app.services.resource_monitor.cached_user_bandwidth_bytes", side_effect=lambda uid: 300 * MB if uid == user_id else 0):
        one = kit.client().get(f"{BASE}/accounts/whmcs:1/usage", headers=kit.bearer(raw)).json()
        bulk = kit.client().get(f"{BASE}/accounts/usage", headers=kit.bearer(raw)).json()
    assert one["bandwidth_used_bytes"] == 300 * MB
    assert one["bandwidth_limit_bytes"] == 2048 * MB
    assert bulk == [one]


def test_reseller_usage_includes_owned_accounts():
    with SessionLocal() as db:
        package = kit.make_package(db)
        reseller_id = _account(db, "whmcs:5", "resell", role="reseller", package=package)
        child = kit.make_user(db, "rchild", role="end_user", reseller_id=reseller_id)
        child_id = child.id
        raw = kit.make_token(db)

    def _bw(uid):
        return {reseller_id: 1 * MB, child_id: 4 * MB}.get(uid, 0)

    def _storage(db, user, use_cache=False):
        return _summary(100 if user.id == reseller_id else 50, 1000)

    with patch("app.services.storage_quota.storage_usage_summary", side_effect=_storage), \
            patch("app.services.resource_monitor.cached_user_bandwidth_bytes", side_effect=_bw):
        usage = kit.client().get(f"{BASE}/accounts/whmcs:5/usage", headers=kit.bearer(raw)).json()
    assert usage["user_count"] == 1
    assert usage["bandwidth_used_bytes"] == 5 * MB
    assert usage["storage_used_bytes"] == 150
    assert usage["storage_percent"] == 15.0


def test_create_applies_package_limits_then_billing_overrides():
    with SessionLocal() as db:
        package = kit.make_package(db, terminal_enabled=True, mail_accounts_limit=4, storage_limit_mb=1000, bandwidth_limit_mb=5000)
        reseller_package = ResellerPackage(name="R1", slug="r1")
        db.add(reseller_package)
        db.commit()
        reseller_package_id = reseller_package.id
        raw = kit.make_token(db)
    body = {
        "external_id": "whmcs:8",
        "username": "limited",
        "password": "a-very-long-password-1",
        "package_id": package.id,
        "is_reseller": True,
        "reseller_package_id": reseller_package_id,
        "limits": {"storage_limit_mb": 20480, "website_limit": 0},
    }
    resp = kit.client().post(f"{BASE}/accounts", json=body, headers=kit.bearer(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_reseller"] is True
    assert resp.json()["reseller_package_id"] == reseller_package_id
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "limited").one()
        assert user.terminal_enabled is True and user.mail_accounts_limit == 4
        assert user.storage_limit_mb == 20480 and user.website_limit == 0
        assert user.bandwidth_limit_mb == 5000

    # A reseller package on a normal account is refused.
    body.update(external_id="whmcs:9", username="plainone", is_reseller=False)
    assert kit.client().post(f"{BASE}/accounts", json=body, headers=kit.bearer(raw)).status_code == 400


def test_change_package_reapplies_limits_with_overrides():
    with SessionLocal() as db:
        small = kit.make_package(db, name="Small", storage_limit_mb=100, bandwidth_limit_mb=1)
        big = kit.make_package(db, name="Big", storage_limit_mb=900, bandwidth_limit_mb=9000, website_limit=7)
        _account(db, "whmcs:2", "grower", package=small)
        raw = kit.make_token(db)
    resp = kit.client().patch(
        f"{BASE}/accounts/whmcs:2/package",
        json={"package_id": big.id, "limits": {"bandwidth_limit_mb": 0}},
        headers=kit.bearer(raw),
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "grower").one()
        assert (user.package_id, user.storage_limit_mb, user.website_limit, user.bandwidth_limit_mb) == (big.id, 900, 7, 0)


def test_sftp_password_reset_returns_new_secret_once():
    with SessionLocal() as db:
        _account(db, "whmcs:3", "sftpuser")
        raw = kit.make_token(db)
    with patch("app.services.site_users.rotate_panel_user_password", return_value="NewSecret_123456") as rotate:
        resp = kit.client().post(f"{BASE}/accounts/whmcs:3/sftp-password", headers=kit.bearer(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "sftp_password": "NewSecret_123456", "sftp_username": "sftpuser"}
    rotate.assert_called_once_with("sftpuser")
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "sftpuser").one().sftp_password_set_at is not None


def test_backup_is_queued_and_recorded():
    with SessionLocal() as db:
        _account(db, "whmcs:4", "backme")
        raw = kit.make_token(db)
    with patch("app.services.backup.create_user_backup", return_value="/var/backups/backme.tar.gz") as create:
        resp = kit.client().post(f"{BASE}/accounts/whmcs:4/backup", headers=kit.bearer(raw))
        assert resp.status_code == 200, resp.text
        assert resp.json()["queued"] is True
        prov._backup_executor.submit(lambda: None).result(timeout=30)
    create.assert_called_once()
    with SessionLocal() as db:
        account = db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == "whmcs:4").one()
        assert account.last_action == "backup"
        assert account.last_message == "backup=/var/backups/backme.tar.gz"


def test_second_backup_while_running_is_refused():
    with SessionLocal() as db:
        _account(db, "whmcs:6", "busy")
        account_id = db.query(ProvisioningAccount).one().id
        raw = kit.make_token(db)
    with prov._backup_lock:
        prov._backups_running.add(account_id)
    try:
        resp = kit.client().post(f"{BASE}/accounts/whmcs:6/backup", headers=kit.bearer(raw))
    finally:
        with prov._backup_lock:
            prov._backups_running.discard(account_id)
    assert resp.status_code == 409


def test_admin_sso_needs_explicit_scope_and_issuer():
    with SessionLocal() as db:
        admin = kit.make_user(db, "chief", role="admin")
        legacy = kit.make_token(db)
        orphan = kit.make_token(db, scopes="sso:admin")
        good = kit.make_token(db, scopes="sso:admin", created_by_id=admin.id)
    c = kit.client()
    with patch("app.api.provisioning.create_panel_login_token", return_value="tok123") as mint:
        assert c.post(f"{BASE}/admin/login", headers=kit.bearer(legacy)).status_code == 403
        assert c.post(f"{BASE}/admin/login", headers=kit.bearer(orphan)).status_code == 403
        resp = c.post(f"{BASE}/admin/login", headers=kit.bearer(good))
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == "/api/auth/sso/tok123"
    mint.assert_called_once_with("chief")


def test_reseller_plans_are_listed():
    with SessionLocal() as db:
        db.add(ResellerPackage(name="Gold", slug="gold", user_limit=25))
        db.commit()
        raw = kit.make_token(db, scopes="plans:read")
    plans = kit.client().get(f"{BASE}/reseller-plans", headers=kit.bearer(raw)).json()
    assert [(p["name"], p["user_limit"]) for p in plans] == [("Gold", 25)]
