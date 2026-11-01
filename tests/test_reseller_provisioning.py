"""DirectAdmin-style dual-role (Admin & Reseller) WHMCS provisioning & SSO tests."""
from unittest.mock import patch

import pytest

import api_testkit as kit
from app.core.database import SessionLocal
from app.models.entities import ApiToken, ProvisioningAccount, ResellerPackage, User, UserPackage

BASE = "/api/provisioning/v1"


@pytest.fixture(autouse=True)
def _clean_db():
    kit.reset_db()
    with patch("app.services.audit._write_audit_anchor"), \
            patch("app.api.provisioning._apply_cgroup_limits"):
        yield


def _create_payload(**extra):
    body = {
        "external_id": "whmcs:101",
        "username": "rescust1",
        "password": "StrongPassword123!",
        "package_id": 0,
        "domain": "",
    }
    body.update(extra)
    return body


def test_admin_only_token_management_and_reseller_delegation():
    with SessionLocal() as db:
        admin = kit.make_user(db, "rootadmin", role="admin")
        res_a = kit.make_user(db, "reseller_a", role="reseller")
        res_b = kit.make_user(db, "reseller_b", role="reseller")
        kit.make_package(db, "SharedBasic")

    c_admin = kit.client(admin.id)
    c_res_a = kit.client(res_a.id)

    # Panel API & Billing configuration endpoints are strictly Admin-only (403 for Reseller sessions).
    assert c_res_a.get(f"{BASE}/scopes").status_code == 403
    assert c_res_a.get(f"{BASE}/tokens").status_code == 403
    assert c_res_a.post(f"{BASE}/tokens", json={"name": "bad", "scopes": "accounts:read"}).status_code == 403
    assert c_res_a.get("/api/resellers/whmcs-diagnostic").status_code == 403
    assert c_res_a.get("/api/resellers/webhooks").status_code == 403
    assert c_res_a.post("/api/resellers/webhooks", json={"url": "https://hooks.example.com/in"}).status_code == 403
    assert c_res_a.get("/api/resellers/webhooks/events").status_code == 403
    assert c_res_a.get("/api/resellers/webhooks/deliveries").status_code == 403

    # Admin scope catalogue exposes both sso:admin and sso:reseller.
    scopes_resp = c_admin.get(f"{BASE}/scopes")
    assert scopes_resp.status_code == 200
    scope_names = {s["name"] for s in scopes_resp.json()["scopes"]}
    assert {"sso:admin", "sso:reseller"} <= scope_names

    # Admin delegates a token to Reseller A; passing sso:admin on a Reseller owner maps cleanly to sso:reseller.
    created_a = c_admin.post(f"{BASE}/tokens", json={
        "name": "WHMCS Reseller A",
        "scopes": "plans:read,accounts:read,accounts:write,usage:read,sso,sso:admin",
        "allowed_ips": "203.0.113.10",
        "owner_user_id": res_a.id,
    })
    assert created_a.status_code == 200, created_a.text
    info_a = created_a.json()["info"]
    assert info_a["owner_username"] == "reseller_a"
    assert info_a["owner_role"] == "reseller"
    assert "sso:reseller" in info_a["scopes"]
    assert "sso:admin" not in info_a["scopes"]
    tok_a_id = info_a["id"]

    # Admin can delegate another token directly to Reseller B from Admin Settings UI.
    delegated_b = c_admin.post(f"{BASE}/tokens", json={
        "name": "Delegated WHMCS B",
        "scopes": "plans:read,accounts:read,accounts:write,sso:reseller",
        "allowed_ips": "203.0.113.20",
        "owner_user_id": res_b.id,
    })
    assert delegated_b.status_code == 200, delegated_b.text
    info_b = delegated_b.json()["info"]
    assert info_b["owner_username"] == "reseller_b"
    assert info_b["owner_role"] == "reseller"
    tok_b_id = info_b["id"]

    # Reseller sessions still get 403 when attempting to patch, rotate or revoke existing tokens.
    assert c_res_a.patch(f"{BASE}/tokens/{tok_a_id}", json={"name": "hijack"}).status_code == 403
    assert c_res_a.post(f"{BASE}/tokens/{tok_a_id}/rotate", json={}).status_code == 403
    assert c_res_a.delete(f"{BASE}/tokens/{tok_a_id}").status_code == 403

    # In-place PATCH by Admin enables sso:reseller without changing the existing bp_... secret:
    from fastapi.testclient import TestClient

    created_plain = c_admin.post(f"{BASE}/tokens", json={
        "name": "WHMCS Billing",
        "scopes": "plans:read,accounts:read,accounts:write,usage:read,sso",
        "allowed_ips": "160.191.248.77",
        "owner_user_id": res_a.id,
    })
    assert created_plain.status_code == 200
    plain_raw = created_plain.json()["token"]
    plain_id = created_plain.json()["info"]["id"]

    c_whmcs = TestClient(kit.build_app(), client=("160.191.248.77", 50000))
    assert c_whmcs.post(f"{BASE}/admin/login", headers=kit.bearer(plain_raw)).status_code == 403

    # Clearing allowed_ips while requesting sso:reseller is rejected with 400:
    assert c_admin.patch(
        f"{BASE}/tokens/{plain_id}",
        json={"scopes": "plans:read,sso,sso:reseller", "allowed_ips": ""},
    ).status_code == 400

    # Enabling sso:reseller in place keeps plain_raw working immediately for Server SSO:
    patched = c_admin.patch(
        f"{BASE}/tokens/{plain_id}",
        json={"scopes": "plans:read,accounts:read,accounts:write,usage:read,sso,sso:reseller"},
    )
    assert patched.status_code == 200, patched.text
    assert "sso:reseller" in patched.json()["scopes"]
    with patch("app.api.provisioning.create_panel_login_token", return_value="res_inplace_sso"):
        sso_now = c_whmcs.post(f"{BASE}/admin/login", headers=kit.bearer(plain_raw))
    assert sso_now.status_code == 200
    assert sso_now.json()["username"] == "reseller_a"
    assert sso_now.json()["role"] == "reseller"

    # Admin sees all tokens and WHMCS readiness diagnostic.
    list_admin = c_admin.get(f"{BASE}/tokens").json()
    assert {t["id"] for t in list_admin} == {tok_a_id, tok_b_id, plain_id}

    diag_admin = c_admin.get("/api/resellers/whmcs-diagnostic")
    assert diag_admin.status_code == 200
    assert diag_admin.json()["write_tokens_count"] == 3
    assert diag_admin.json()["ready"] is True


def test_reseller_and_admin_server_sso_isolation():
    with SessionLocal() as db:
        admin = kit.make_user(db, "chief_admin", role="admin")
        reseller = kit.make_user(db, "shost_reseller", role="reseller")

        res_no_scope = kit.make_token(db, scopes="plans:read,sso", created_by_id=reseller.id, allowed_ips="testclient")
        res_no_ip = kit.make_token(db, scopes="sso:reseller", created_by_id=reseller.id)
        res_ok = kit.make_token(db, scopes="sso:reseller", created_by_id=reseller.id, allowed_ips="testclient")
        # Admin token carrying ONLY sso:reseller must NOT sign in as Admin!
        admin_wrong_scope = kit.make_token(db, scopes="sso:reseller", created_by_id=admin.id, allowed_ips="testclient")

    c = kit.client()
    with patch("app.api.provisioning.create_panel_login_token", return_value="res_sso_999") as mint:
        r1 = c.post(f"{BASE}/admin/login", headers=kit.bearer(res_no_scope))
        assert r1.status_code == 403 and "sso:reseller" in r1.text

        r2 = c.post(f"{BASE}/admin/login", headers=kit.bearer(res_no_ip))
        assert r2.status_code == 403 and "IP allowlist" in r2.text

        r3 = c.post(f"{BASE}/admin/login", headers=kit.bearer(admin_wrong_scope))
        assert r3.status_code == 403 and "sso:admin" in r3.text

        ok = c.post(f"{BASE}/admin/login", headers=kit.bearer(res_ok))
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["path"] == "/api/auth/sso/res_sso_999"
        assert data["role"] == "reseller"
        assert data["username"] == "shost_reseller"
        mint.assert_called_once_with("shost_reseller")


def test_reseller_provisioning_ownership_quotas_and_tenant_isolation():
    with SessionLocal() as db:
        rpkg = ResellerPackage(
            name="Reseller Silver",
            slug="reseller-silver",
            user_limit=1,
            total_storage_mb=4096,
            total_website_limit=5,
            total_database_limit=5,
            overselling_enabled=False,
        )
        db.add(rpkg)
        db.commit()
        db.refresh(rpkg)

        admin = kit.make_user(db, "admin_root", role="admin")
        res_a = kit.make_user(db, "res_alpha", role="reseller", reseller_package_id=rpkg.id)
        res_b = kit.make_user(db, "res_beta", role="reseller")

        shared_pkg = kit.make_package(db, "SharedStarter", storage_limit_mb=2048, website_limit=2, terminal_enabled=True)
        own_pkg_a = kit.make_package(db, "AlphaCustom", owner_id=res_a.id, storage_limit_mb=1024, website_limit=1)
        own_pkg_b = kit.make_package(db, "BetaPrivate", owner_id=res_b.id, storage_limit_mb=1024, website_limit=1)
        reseller_tier_pkg = kit.make_package(db, "ResellerTier", is_reseller_package=True, storage_limit_mb=1024)
        huge_pkg = kit.make_package(db, "HugePlan", storage_limit_mb=10240, website_limit=2)

        admin_cust = kit.make_user(db, "admin_cust", role="end_user", package_id=shared_pkg.id)
        beta_cust = kit.make_user(db, "beta_cust", role="end_user", package_id=own_pkg_b.id, reseller_id=res_b.id)
        db.add(ProvisioningAccount(external_id="whmcs:admin1", user_id=admin_cust.id, package_id=shared_pkg.id, status="active"))
        db.add(ProvisioningAccount(external_id="whmcs:beta1", user_id=beta_cust.id, reseller_id=res_b.id, package_id=own_pkg_b.id, status="active"))
        db.commit()

        tok_a = kit.make_token(db, created_by_id=res_a.id)
        tok_b = kit.make_token(db, created_by_id=res_b.id)
        tok_admin = kit.make_token(db, created_by_id=admin.id)

    c = kit.client()

    # 1. Plans isolation: Reseller A sees SharedStarter + AlphaCustom, NOT BetaPrivate or ResellerTier.
    plans_a = c.get(f"{BASE}/plans", headers=kit.bearer(tok_a)).json()
    assert {p["name"] for p in plans_a} == {"SharedStarter", "AlphaCustom", "HugePlan"}
    assert c.get(f"{BASE}/reseller-plans", headers=kit.bearer(tok_a)).json() == []

    # 2. Reseller A cannot create a sub-reseller, use Beta's package, or exceed Reseller quota.
    with patch("app.services.site_users.ensure_panel_user", side_effect=lambda u, p: u):
        assert c.post(
            f"{BASE}/accounts",
            json=_create_payload(package_id=shared_pkg.id, is_reseller=True),
            headers=kit.bearer(tok_a),
        ).status_code == 403

        assert c.post(
            f"{BASE}/accounts",
            json=_create_payload(package_id=own_pkg_b.id),
            headers=kit.bearer(tok_a),
        ).status_code == 403

        assert c.post(
            f"{BASE}/accounts",
            json=_create_payload(package_id=reseller_tier_pkg.id),
            headers=kit.bearer(tok_a),
        ).status_code == 403

        # Exceeds total_storage_mb=4096 via HugePlan (10240 MB) or limits override:
        over_pkg = c.post(
            f"{BASE}/accounts",
            json=_create_payload(external_id="whmcs:over1", username="overcust1", package_id=huge_pkg.id),
            headers=kit.bearer(tok_a),
        )
        assert over_pkg.status_code == 403 and "storage quota" in over_pkg.text.lower()

        over_lim = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:over2",
                username="overcust2",
                package_id=own_pkg_a.id,
                limits={"storage_limit_mb": 8192},
            ),
            headers=kit.bearer(tok_a),
        )
        assert over_lim.status_code == 403

        # Valid create under Reseller A:
        ok = c.post(
            f"{BASE}/accounts",
            json=_create_payload(external_id="whmcs:alpha1", username="alphacust1", package_id=shared_pkg.id),
            headers=kit.bearer(tok_a),
        )
        assert ok.status_code == 200, ok.text

        # Second create blocked by user_limit=1 on Reseller A's ResellerPackage:
        second = c.post(
            f"{BASE}/accounts",
            json=_create_payload(external_id="whmcs:alpha2", username="alphacust2", package_id=own_pkg_a.id),
            headers=kit.bearer(tok_a),
        )
        assert second.status_code == 403 and "user limit" in second.text.lower()

    # Verify created user is strictly owned by Reseller A and terminal_enabled was capped to False.
    with SessionLocal() as db:
        u = db.query(User).filter(User.username == "alphacust1").one()
        assert u.role == "end_user"
        assert u.reseller_id == res_a.id
        assert u.terminal_enabled is False

    # 3. Tenant isolation across list, get, SSO, suspend, package, password, terminate:
    listed_a = c.get(f"{BASE}/accounts", headers=kit.bearer(tok_a)).json()
    assert [item["external_id"] for item in listed_a["items"]] == ["whmcs:alpha1"]

    # Reseller A gets 404 on Admin's and Reseller B's accounts:
    for foreign_ext in ("whmcs:admin1", "whmcs:beta1"):
        assert c.get(f"{BASE}/accounts/{foreign_ext}", headers=kit.bearer(tok_a)).status_code == 404
        assert c.post(f"{BASE}/accounts/{foreign_ext}/login", headers=kit.bearer(tok_a)).status_code == 404
        assert c.post(f"{BASE}/accounts/{foreign_ext}/suspend", json={"reason": "x"}, headers=kit.bearer(tok_a)).status_code == 404
        assert c.delete(f"{BASE}/accounts/{foreign_ext}", headers=kit.bearer(tok_a)).status_code == 404

    # Reseller A CAN manage own account (SSO + Upgrade within quota + Terminate):
    with patch("app.api.provisioning.create_panel_login_token", return_value="cust_tok"):
        sso_ok = c.post(f"{BASE}/accounts/whmcs:alpha1/login", headers=kit.bearer(tok_a))
        assert sso_ok.status_code == 200

    pkg_change = c.patch(
        f"{BASE}/accounts/whmcs:alpha1/package",
        json={"package_id": own_pkg_a.id},
        headers=kit.bearer(tok_a),
    )
    assert pkg_change.status_code == 200

    with patch("app.services.site_users.delete_panel_user"), \
            patch("app.services.teardown.purge_owned_resources"):
        term = c.delete(f"{BASE}/accounts/whmcs:alpha1", headers=kit.bearer(tok_a))
        assert term.status_code == 200 and term.json()["status"] == "terminated"
        # Idempotent retry of DELETE by Reseller A still succeeds (200), while Reseller B gets 404!
        term_retry = c.delete(f"{BASE}/accounts/whmcs:alpha1", headers=kit.bearer(tok_a))
        assert term_retry.status_code == 200
        assert c.delete(f"{BASE}/accounts/whmcs:alpha1", headers=kit.bearer(tok_b)).status_code == 404

    # Admin still sees all active accounts across the server:
    listed_admin = c.get(f"{BASE}/accounts", headers=kit.bearer(tok_admin)).json()
    assert {item["external_id"] for item in listed_admin["items"]} == {"whmcs:admin1", "whmcs:beta1"}

    # 4. Suspending Reseller A immediately disables Reseller A's API token!
    with SessionLocal() as db:
        ra = db.get(User, res_a.id)
        ra.is_active = False
        db.commit()
    assert c.get(f"{BASE}/plans", headers=kit.bearer(tok_a)).status_code == 403


def test_reseller_token_all_eight_whmcs_module_actions():
    """Verify every WHMCS Module Command button works end-to-end with a Reseller API token:
    1. Create  2. Suspend  3. Unsuspend  4. Change Package  5. Change Password
    6. Reset SFTP Password  7. Create Backup  8. Terminate (+ Admin Tab & SSO)."""
    with SessionLocal() as db:
        reseller = kit.make_user(db, "shost_reseller_ops", role="reseller")
        pkg_starter = kit.make_package(db, "Starter", storage_limit_mb=2048, website_limit=2)
        pkg_pro = kit.make_package(db, "Pro", owner_id=reseller.id, storage_limit_mb=4096, website_limit=5)
        tok = kit.make_token(
            db,
            scopes="plans:read,accounts:read,accounts:write,usage:read,sso,sso:reseller",
            created_by_id=reseller.id,
            allowed_ips="testclient",
        )

    c = kit.client()
    headers = kit.bearer(tok)
    ext = "whmcs:777"

    with patch("app.services.site_users.ensure_panel_user", side_effect=lambda u, p: u), \
            patch("app.services.site_users.lock_linux_user"), \
            patch("app.services.site_users.unlock_linux_user"), \
            patch("app.services.site_users.retire_shared_login_password", return_value="RotatedSftp123!"), \
            patch("app.services.site_users.rotate_panel_user_password", return_value="FreshSftp456!"), \
            patch("app.api.provisioning.queue_account_backup", return_value=True) as mock_backup, \
            patch("app.api.provisioning.create_panel_login_token", return_value="sso_tok_777"), \
            patch("app.services.site_users.delete_panel_user"), \
            patch("app.services.teardown.purge_owned_resources"):
        # 1. Create
        r_create = c.post(
            f"{BASE}/accounts",
            json=_create_payload(external_id=ext, username="sp777_cust0001", package_id=pkg_starter.id),
            headers=headers,
        )
        assert r_create.status_code == 200, r_create.text
        assert r_create.json()["status"] == "active"

        # Admin Tab + Usage + Client SSO + Server SSO
        assert c.get(f"{BASE}/accounts/{ext}", headers=headers).status_code == 200
        assert c.get(f"{BASE}/accounts/{ext}/usage", headers=headers).status_code == 200
        assert c.post(f"{BASE}/accounts/{ext}/login", headers=headers).status_code == 200
        assert c.post(f"{BASE}/admin/login", headers=headers).status_code == 200

        # 2. Suspend
        r_susp = c.post(f"{BASE}/accounts/{ext}/suspend", json={"reason": "Suspended by WHMCS"}, headers=headers)
        assert r_susp.status_code == 200 and r_susp.json()["status"] == "suspended"

        # 3. Unsuspend
        r_unsusp = c.post(f"{BASE}/accounts/{ext}/unsuspend", headers=headers)
        assert r_unsusp.status_code == 200 and r_unsusp.json()["status"] == "active"

        # 4. Change Package
        r_pkg = c.patch(f"{BASE}/accounts/{ext}/package", json={"package_id": pkg_pro.id}, headers=headers)
        assert r_pkg.status_code == 200 and r_pkg.json()["package_id"] == pkg_pro.id

        # 5. Change Password
        r_pwd = c.patch(f"{BASE}/accounts/{ext}/password", json={"password": "NewStrongPassword99!"}, headers=headers)
        assert r_pwd.status_code == 200 and r_pwd.json()["sftp_password"] == "RotatedSftp123!"

        # 6. Reset SFTP Password
        r_sftp = c.post(f"{BASE}/accounts/{ext}/sftp-password", headers=headers)
        assert r_sftp.status_code == 200 and r_sftp.json()["sftp_password"] == "FreshSftp456!"

        # 7. Create Backup
        r_bkp = c.post(f"{BASE}/accounts/{ext}/backup", headers=headers)
        assert r_bkp.status_code == 200 and r_bkp.json() == {"ok": True, "queued": True}
        assert mock_backup.called

        # 8. Terminate
        r_term = c.delete(f"{BASE}/accounts/{ext}", headers=headers)
        assert r_term.status_code == 200 and r_term.json()["status"] == "terminated"


def test_create_account_falls_back_to_installed_php_version_when_default_missing():
    """When WHMCS requests default php_version='8.4' on a VPS that only has PHP 8.3
    installed, provisioning automatically binds to the highest installed PHP version."""
    from app.models.entities import Website

    with SessionLocal() as db:
        reseller = kit.make_user(db, "php_fallback_res", role="reseller")
        pkg = kit.make_package(db, "StarterPHP", storage_limit_mb=2048, website_limit=2)
        tok = kit.make_token(db, created_by_id=reseller.id)

    c = kit.client()
    with patch("app.core.config.settings.command_dry_run", False), \
            patch("app.services.php.list_installed_php", return_value=["8.3"]), \
            patch("app.services.site_users.ensure_panel_user", side_effect=lambda u, p: u), \
            patch("app.services.site_users.ensure_site_runtime", side_effect=lambda d, r, php_v, lu: lu) as rt_mock, \
            patch("app.services.site_users.fix_site_path"), \
            patch("app.api.websites._ensure_default_waf_file"), \
            patch("app.api.websites._write_placeholder_page"), \
            patch("app.services.nginx.write_vhost"), \
            patch("app.services.nginx.vhost_exists", return_value=False), \
            patch("app.services.dns.zone_for_new_domain"):
        resp = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:858",
                username="sp858_fyzalqlb",
                package_id=pkg.id,
                domain="cokhihoangphan.com",
                php_version="8.4",
            ),
            headers=kit.bearer(tok),
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "admin@cokhihoangphan.com"
    assert rt_mock.call_args[0][2] == "8.3"
    with SessionLocal() as db:
        w = db.query(Website).filter(Website.domain == "cokhihoangphan.com").one()
        assert w.php_version == "8.3"


def test_create_account_default_email_uses_admin_at_user_domain():
    """Provisioned users default to admin@<user_domain> instead of @users.spanel.dev."""
    with SessionLocal() as db:
        reseller = kit.make_user(db, "email_res", role="reseller")
        pkg = kit.make_package(db, "EmailPkg", storage_limit_mb=2048, website_limit=3)
        tok = kit.make_token(db, created_by_id=reseller.id)

    c = kit.client()
    with patch("app.services.site_users.ensure_panel_user", side_effect=lambda u, p: u), \
            patch("app.services.site_users.ensure_site_runtime", side_effect=lambda d, r, php_v, lu: lu), \
            patch("app.services.nginx.write_vhost"), \
            patch("app.services.nginx.vhost_exists", return_value=False), \
            patch("app.services.dns.zone_for_new_domain"):
        # 1. Domain provided, email omitted -> admin@<domain>
        r1 = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:901",
                username="sp901_user",
                package_id=pkg.id,
                domain="khachhang.vn",
            ),
            headers=kit.bearer(tok),
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["email"] == "admin@khachhang.vn"
        assert "spanel.dev" not in r1.json()["email"]

        # 2. Domain omitted, email omitted -> valid admin@ fallback without spanel.dev
        r2 = c.post(
            f"{BASE}/accounts",
            json={
                "external_id": "whmcs:902",
                "username": "sp902_nodomain",
                "password": "StrongPassword123!",
                "package_id": pkg.id,
            },
            headers=kit.bearer(tok),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["email"].startswith("admin@")
        assert "spanel.dev" not in r2.json()["email"]

        # 3. Explicit email honored when API caller supplies one
        r3 = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:903",
                username="sp903_custom",
                package_id=pkg.id,
                domain="custommail.vn",
                email="billing@custommail.vn",
            ),
            headers=kit.bearer(tok),
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["email"] == "billing@custommail.vn"


def test_terminate_and_recreate_never_collides_on_idempotency_key():
    """Terminating and recreating an account must succeed both after normal
    termination (which purges old keys) and when a pre-existing stale key
    remains in SQLite from before the update."""
    from app.models.entities import ProvisioningIdempotencyKey

    with SessionLocal() as db:
        reseller = kit.make_user(db, "idem_res", role="reseller")
        pkg1 = kit.make_package(db, "IdemPkg1", storage_limit_mb=2048, website_limit=2)
        pkg2 = kit.make_package(db, "IdemPkg2", storage_limit_mb=4096, website_limit=5)
        tok = kit.make_token(db, created_by_id=reseller.id)

    c = kit.client()
    idem_headers = {**kit.bearer(tok), "Idempotency-Key": "whmcs-858-create-legacyhash"}

    with patch("app.services.site_users.ensure_panel_user", side_effect=lambda u, p: u), \
            patch("app.services.site_users.ensure_site_runtime", side_effect=lambda d, r, php_v, lu: lu), \
            patch("app.services.site_users.delete_panel_user"), \
            patch("app.services.teardown.purge_owned_resources"), \
            patch("app.services.nginx.write_vhost"), \
            patch("app.services.nginx.delete_wordpress_vhost"), \
            patch("app.services.ssl.release_site_certificates"), \
            patch("app.services.waf.remove_site_rules"), \
            patch("app.services.wordpress.delete_wordpress"), \
            patch("app.services.nginx.vhost_exists", return_value=False), \
            patch("app.services.dns.zone_for_new_domain"):
        # Create #1
        r1 = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:858",
                username="sp858_fyzalqlb",
                package_id=pkg1.id,
                domain="cokhihoangphan.com",
            ),
            headers=idem_headers,
        )
        assert r1.status_code == 200, r1.text

        # Terminate clears completed Idempotency-Key rows for whmcs:858
        r_term = c.delete(f"{BASE}/accounts/whmcs:858", headers=kit.bearer(tok))
        assert r_term.status_code == 200, r_term.text
        with SessionLocal() as db:
            assert db.query(ProvisioningIdempotencyKey).filter(
                ProvisioningIdempotencyKey.idem_key == "whmcs-858-create-legacyhash"
            ).count() == 0

        # Create #2 with SAME Idempotency-Key and DIFFERENT payload (e.g. pkg2) succeeds 200
        r2 = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:858",
                username="sp858_fyzalqlb",
                package_id=pkg2.id,
                domain="cokhihoangphan.com",
            ),
            headers=idem_headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.headers.get("Idempotent-Replayed") is None
        assert r2.json()["package_id"] == pkg2.id

        # Simulate pre-update state: account terminated while stale Idempotency-Key row remained in DB
        with SessionLocal() as db:
            saved_key = db.query(ProvisioningIdempotencyKey).filter(
                ProvisioningIdempotencyKey.idem_key == "whmcs-858-create-legacyhash"
            ).one()
            saved_snapshot = (
                saved_key.token_id,
                saved_key.idem_key,
                saved_key.method,
                saved_key.path,
                saved_key.request_hash,
                saved_key.status_code,
                saved_key.response_body,
                saved_key.created_at,
            )

        assert c.delete(f"{BASE}/accounts/whmcs:858", headers=kit.bearer(tok)).status_code == 200
        with SessionLocal() as db:
            db.add(ProvisioningIdempotencyKey(
                token_id=saved_snapshot[0],
                idem_key=saved_snapshot[1],
                method=saved_snapshot[2],
                path=saved_snapshot[3],
                request_hash=saved_snapshot[4],
                status_code=saved_snapshot[5],
                response_body=saved_snapshot[6],
                created_at=saved_snapshot[7],
            ))
            db.commit()

        # Recreate against leftover stale key with modified payload -> 200 (not 422!)
        r3 = c.post(
            f"{BASE}/accounts",
            json=_create_payload(
                external_id="whmcs:858",
                username="sp858_fyzalqlb",
                package_id=pkg1.id,
                domain="cokhihoangphan.com",
            ),
            headers=idem_headers,
        )
        assert r3.status_code == 200, r3.text
        assert r3.headers.get("Idempotent-Replayed") is None
        assert r3.json()["package_id"] == pkg1.id
        with SessionLocal() as db:
            u = db.query(User).filter(User.username == "sp858_fyzalqlb").one_or_none()
            assert u is not None and u.email == "admin@cokhihoangphan.com"



