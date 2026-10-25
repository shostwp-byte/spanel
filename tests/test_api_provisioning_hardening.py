"""Provisioning API hardening: scopes, CIDR allowlists, expiry, rotation,
rate limits, Idempotency-Key and the rollback of failed creates."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import api_testkit as kit
from app.core.database import SessionLocal
from app.models.entities import ApiToken, ProvisioningAccount, ProvisioningIdempotencyKey, User
from app.services import provisioning as prov

BASE = "/api/provisioning/v1"


@pytest.fixture(autouse=True)
def _clean_db():
    kit.reset_db()
    with patch("app.services.audit._write_audit_anchor"):
        yield


def _seed_account(db, external_id="whmcs:1", username="custone", status="active"):
    package = kit.make_package(db)
    user = kit.make_user(db, username, role="end_user", package_id=package.id)
    db.add(ProvisioningAccount(external_id=external_id, user_id=user.id, package_id=package.id, status=status))
    db.commit()
    return package, user


# --- scopes -----------------------------------------------------------------

def test_legacy_scopes_keep_their_reach_and_nothing_more():
    token = ApiToken(scopes="provisioning:read,provisioning:write")
    granted = prov.token_scopes(token)
    assert {"plans:read", "accounts:read", "usage:read", "accounts:write", "sso"} <= granted
    assert "sso:admin" not in granted
    assert "tokens:rotate" not in granted


def test_unknown_scopes_are_refused():
    assert prov.normalize_scopes(" accounts:read , usage:read,accounts:read") == "accounts:read,usage:read"
    with pytest.raises(ValueError):
        prov.normalize_scopes("accounts:read,root")
    with pytest.raises(ValueError):
        prov.normalize_scopes(" , ")


def test_fine_scope_limits_endpoints():
    with SessionLocal() as db:
        _seed_account(db)
        raw = kit.make_token(db, scopes="usage:read")
    c = kit.client()
    assert c.get(f"{BASE}/plans", headers=kit.bearer(raw)).status_code == 403
    assert c.get(f"{BASE}/accounts/whmcs:1", headers=kit.bearer(raw)).status_code == 403
    with patch("app.services.storage_quota.storage_usage_summary", return_value={
        "storage_used_bytes": 0, "storage_limit_bytes": None, "storage_percent": 0.0,
    }):
        assert c.get(f"{BASE}/accounts/whmcs:1/usage", headers=kit.bearer(raw)).status_code == 200


def test_admin_cannot_create_token_with_bad_scope_or_ip():
    with SessionLocal() as db:
        admin = kit.make_user(db, "boss", role="admin")
    c = kit.client(admin.id)
    assert c.post(f"{BASE}/tokens", json={"name": "x", "scopes": "everything"}).status_code == 400
    assert c.post(f"{BASE}/tokens", json={"name": "x", "allowed_ips": "10.0.0.0/33"}).status_code == 400
    ok = c.post(f"{BASE}/tokens", json={
        "name": "whmcs", "scopes": "accounts:read,accounts:write", "allowed_ips": "203.0.113.7/24, 2001:db8::1",
        "expires_in_days": 30, "rate_limit_per_minute": 60,
    })
    assert ok.status_code == 200, ok.text
    info = ok.json()["info"]
    assert info["allowed_ips"] == "203.0.113.0/24,2001:db8::1"
    assert info["rate_limit_per_minute"] == 60
    assert info["expires_at"] is not None


# --- CIDR allowlist -----------------------------------------------------------

@pytest.mark.parametrize("allowed,client,expected", [
    ("", "198.51.100.9", True),
    ("203.0.113.7", "203.0.113.7", True),
    ("203.0.113.7", "203.0.113.8", False),
    ("203.0.113.0/24", "203.0.113.200", True),
    ("203.0.113.0/24", "203.0.114.1", False),
    ("203.0.113.0/24", "::ffff:203.0.113.5", True),
    ("2001:db8::/32", "2001:db8:1::5", True),
    ("2001:db8::/32", "203.0.113.5", False),
    ("10.0.0.0/8,192.168.1.10", "192.168.1.10", True),
    ("10.0.0.0/8", "not-an-ip", False),
])
def test_ip_allowlist_understands_cidr(allowed, client, expected):
    assert prov.check_ip_allowed(ApiToken(allowed_ips=allowed), client) is expected


def test_request_from_outside_allowlist_is_refused():
    with SessionLocal() as db:
        raw = kit.make_token(db, allowed_ips="203.0.113.0/24")
    # TestClient reports its peer as "testclient", which is in no range.
    assert kit.client().get(f"{BASE}/plans", headers=kit.bearer(raw)).status_code == 403


# --- expiry and rotation --------------------------------------------------------

def test_expired_token_is_refused():
    with SessionLocal() as db:
        raw = kit.make_token(db, expires_at=datetime.utcnow() - timedelta(minutes=1))
        live = kit.make_token(db, expires_at=datetime.utcnow() + timedelta(days=1))
    c = kit.client()
    assert c.get(f"{BASE}/plans", headers=kit.bearer(raw)).status_code == 401
    assert c.get(f"{BASE}/plans", headers=kit.bearer(live)).status_code == 200


def test_admin_rotation_replaces_token_immediately():
    with SessionLocal() as db:
        admin = kit.make_user(db, "boss", role="admin")
        old_raw = kit.make_token(db, rate_limit_per_minute=77)
        old_id = db.query(ApiToken).first().id
    c = kit.client(admin.id)
    resp = c.post(f"{BASE}/tokens/{old_id}/rotate", json={})
    assert resp.status_code == 200, resp.text
    new_raw = resp.json()["token"]
    assert resp.json()["info"]["rate_limit_per_minute"] == 77
    assert c.get(f"{BASE}/plans", headers=kit.bearer(old_raw)).status_code == 401
    assert c.get(f"{BASE}/plans", headers=kit.bearer(new_raw)).status_code == 200
    # A revoked token cannot be rotated again.
    assert c.post(f"{BASE}/tokens/{old_id}/rotate", json={}).status_code == 400


def test_rotation_grace_keeps_old_token_until_cutoff():
    with SessionLocal() as db:
        kit.make_token(db)
        token = db.query(ApiToken).first()
        _, replacement = prov.rotate_api_token(db, token, grace_minutes=30)
        db.refresh(token)
        assert token.is_active and token.revoked_at is None
        assert token.expires_at is not None
        assert timedelta(minutes=29) < token.expires_at - datetime.utcnow() <= timedelta(minutes=30)
        assert replacement.id != token.id and replacement.scopes == token.scopes


def test_self_rotation_needs_its_own_scope():
    with SessionLocal() as db:
        legacy = kit.make_token(db)
        rotating = kit.make_token(db, scopes="accounts:read,tokens:rotate")
    c = kit.client()
    assert c.post(f"{BASE}/token/rotate", json={}, headers=kit.bearer(legacy)).status_code == 403
    resp = c.post(f"{BASE}/token/rotate", json={"grace_minutes": 5}, headers=kit.bearer(rotating))
    assert resp.status_code == 200, resp.text
    assert resp.json()["token"].startswith("bp_")
    # Still valid during the grace period, as is the replacement.
    assert c.get(f"{BASE}/accounts/none", headers=kit.bearer(rotating)).status_code == 404
    assert c.get(f"{BASE}/accounts/none", headers=kit.bearer(resp.json()["token"])).status_code == 404


# --- rate limits -----------------------------------------------------------------

def test_per_token_rate_limit():
    with SessionLocal() as db:
        raw = kit.make_token(db, rate_limit_per_minute=3)
        other = kit.make_token(db, rate_limit_per_minute=3)
    c = kit.client()
    codes = [c.get(f"{BASE}/plans", headers=kit.bearer(raw)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    # Another token has its own budget.
    assert c.get(f"{BASE}/plans", headers=kit.bearer(other)).status_code == 200


def test_repeated_bad_tokens_are_throttled():
    c = kit.client()
    codes = [c.get(f"{BASE}/plans", headers=kit.bearer("bp_wrong")).status_code for _ in range(21)]
    assert codes[:20] == [401] * 20
    assert codes[20] == 429


# --- Idempotency-Key ------------------------------------------------------------------

def test_idempotent_retry_replays_first_answer():
    with SessionLocal() as db:
        _seed_account(db)
        raw = kit.make_token(db)
    headers = {**kit.bearer(raw), "Idempotency-Key": "order-42-suspend"}
    c = kit.client()
    with patch("app.api.provisioning.suspend_account") as do_suspend:
        def _flip(db, account, reason=""):
            account.status = "suspended"
            db.commit()
        do_suspend.side_effect = _flip
        first = c.post(f"{BASE}/accounts/whmcs:1/suspend", json={"reason": "unpaid"}, headers=headers)
        second = c.post(f"{BASE}/accounts/whmcs:1/suspend", json={"reason": "unpaid"}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert do_suspend.call_count == 1

    other = c.post(f"{BASE}/accounts/whmcs:1/suspend", json={"reason": "abuse"}, headers=headers)
    assert other.status_code == 422


def test_failed_request_does_not_burn_the_key():
    with SessionLocal() as db:
        raw = kit.make_token(db)
    headers = {**kit.bearer(raw), "Idempotency-Key": "k1"}
    c = kit.client()
    assert c.post(f"{BASE}/accounts/whmcs:404/unsuspend", headers=headers).status_code == 404
    with SessionLocal() as db:
        assert db.query(ProvisioningIdempotencyKey).count() == 0
        _seed_account(db, external_id="whmcs:404", status="suspended")
    with patch("app.api.provisioning.unsuspend_account"):
        assert c.post(f"{BASE}/accounts/whmcs:404/unsuspend", headers=headers).status_code == 200


def test_stored_response_is_encrypted_and_expires():
    from app.services import provisioning_idempotency as idem

    with SessionLocal() as db:
        _seed_account(db, status="suspended")
        raw = kit.make_token(db)
    c = kit.client()
    with patch("app.api.provisioning.unsuspend_account"):
        c.post(f"{BASE}/accounts/whmcs:1/unsuspend", headers={**kit.bearer(raw), "Idempotency-Key": "k2"})
    with SessionLocal() as db:
        row = db.query(ProvisioningIdempotencyKey).one()
        assert row.status_code == 200
        assert row.response_body.startswith("fernet:")
        assert idem.purge_expired(db, now=datetime.utcnow() + timedelta(hours=25)) == 1


def test_bad_idempotency_key_is_rejected():
    with SessionLocal() as db:
        raw = kit.make_token(db)
    resp = kit.client().post(
        f"{BASE}/accounts/x/unsuspend",
        headers={**kit.bearer(raw), "Idempotency-Key": "has spaces"},
    )
    assert resp.status_code == 400


# --- rollback of a failed create ---------------------------------------------------

def _create_payload(**extra):
    body = {
        "external_id": "whmcs:55",
        "username": "newcust",
        "password": "a-very-long-password-1",
        "package_id": 0,
        "domain": "shop.example.com",
    }
    body.update(extra)
    return body


def test_failed_create_leaves_no_user_and_can_be_retried():
    with SessionLocal() as db:
        package = kit.make_package(db)
        raw = kit.make_token(db)
    c = kit.client()
    payload = _create_payload(package_id=package.id)
    with patch("app.services.site_users.ensure_site_runtime", side_effect=RuntimeError("disk full")), \
            patch("app.api.provisioning.linux_user_exists", return_value=False), \
            patch("app.services.site_users.delete_panel_user") as delete_linux, \
            patch("app.services.nginx.vhost_exists", return_value=False), \
            patch("app.services.nginx.delete_wordpress_vhost") as delete_vhost, \
            patch("app.services.waf.remove_site_rules"):
        resp = c.post(f"{BASE}/accounts", json=payload, headers=kit.bearer(raw))
    assert resp.status_code == 400
    assert "disk full" in resp.text
    delete_linux.assert_called_once_with("newcust")
    delete_vhost.assert_called_once_with("shop.example.com")
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "newcust").count() == 0
        account = db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == "whmcs:55").one()
        assert account.status == "failed" and account.user_id is None
        assert "disk full" in account.last_message

    # The retry is not refused as a duplicate username.
    with patch("app.api.provisioning.linux_user_exists", return_value=False):
        retry = c.post(f"{BASE}/accounts", json=_create_payload(package_id=package.id, domain=None), headers=kit.bearer(raw))
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "active"
    assert retry.json()["sftp_password"]


def test_preexisting_linux_user_is_not_deleted_on_failure():
    with SessionLocal() as db:
        package = kit.make_package(db)
        raw = kit.make_token(db)
    with patch("app.services.site_users.ensure_site_runtime", side_effect=ValueError("bad path")), \
            patch("app.api.provisioning.linux_user_exists", return_value=True), \
            patch("app.services.site_users.delete_panel_user") as delete_linux, \
            patch("app.services.nginx.vhost_exists", return_value=False), \
            patch("app.services.nginx.delete_wordpress_vhost"), \
            patch("app.services.waf.remove_site_rules"):
        resp = kit.client().post(f"{BASE}/accounts", json=_create_payload(package_id=package.id), headers=kit.bearer(raw))
    assert resp.status_code == 400
    delete_linux.assert_not_called()


def test_retry_cleans_orphan_left_by_old_failed_create():
    with SessionLocal() as db:
        package, user = _seed_account(db, external_id="whmcs:55", username="newcust", status="failed")
        raw = kit.make_token(db)
    with patch("app.api.provisioning.linux_user_exists", return_value=False), \
            patch("app.services.site_users.delete_panel_user") as delete_linux:
        resp = kit.client().post(
            f"{BASE}/accounts", json=_create_payload(package_id=package.id, domain=None), headers=kit.bearer(raw),
        )
    assert resp.status_code == 200, resp.text
    delete_linux.assert_any_call("newcust")
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "newcust").count() == 1
        assert db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == "whmcs:55").one().status == "active"
