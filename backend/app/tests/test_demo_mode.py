"""Demo mode addon (operator, 2026-09-28: "phát triển addon: chế độ demo mode").

Chosen with the operator: view only; one administrator and one customer demo
account; one-click sign-in buttons on the login page.

The demo passwords are public, so these tests go through the real sign-in path
(a real token against get_current_user) rather than overriding the user: the
check that matters is the one every request passes through.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.entities import User
from app.services import addons, demo_mode, panel_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")

# Every GET route a demo visitor may call, reviewed on 2026-09-28: none returns
# a stored secret, so each shows no more than its page does. A new GET route
# fails test_every_get_route_has_been_looked_at until it is added here or to
# demo_mode.BLOCKED_READS.
REVIEWED_READS = {
    "/api/addons", "/api/auth/2fa/status", "/api/auth/csrf", "/api/auth/passkey/status",
    "/api/auth/session", "/api/auth/sso/{token}", "/api/dashboard/summary", "/api/databases",
    "/api/demo-mode", "/api/demo-mode/public",
    # DNS Manager (2026-09-29): zones, records and settings; no key or secret.
    # The overview is the nameservers, default TTL and server addresses; the
    # delegation check is what public DNS already answers.
    "/api/dns/zones", "/api/dns/zones/{zone}/records", "/api/dns/settings",
    "/api/dns/overview", "/api/dns/zones/{zone}/delegation",
    # Email (2026-09-29): mail domains, mailboxes, forwarders, a domain's DNS
    # records (the DKIM key is public) and client settings. No password or
    # hash; the webmail link is a POST.
    "/api/mail/overview", "/api/mail/domains/{domain_id}/dns", "/api/mail/mailboxes",
    "/api/mail/forwarders",
    # The mail server's settings and relays, and Rspamd's counters: a relay's
    # password is never sent, only whether one is saved. (Exim's and Rspamd's
    # logs and the scan history name real correspondents and are in
    # demo_mode.BLOCKED_READS.)
    "/api/mail/settings", "/api/mail/relays", "/api/mail/rspamd/stat",
    "/api/fail2ban/banned", "/api/fail2ban/status", "/api/firewall/blocklists", "/api/firewall/status",
    "/api/health", "/api/maintenance/app-files/{app_id}", "/api/maintenance/backup-jobs",
    "/api/maintenance/backup-jobs/{job_id}", "/api/maintenance/backup-schedules",
    "/api/maintenance/backups/{website_id}", "/api/maintenance/cron/{website_id}",
    "/api/maintenance/da-import/backups", "/api/maintenance/da-import/bulk-jobs/{job_id}",
    "/api/maintenance/da-import/jobs/{job_id}", "/api/maintenance/files/jobs",
    "/api/maintenance/files/jobs/{job_id}", "/api/maintenance/files/{website_id}",
    "/api/maintenance/php-config", "/api/maintenance/php-extensions", "/api/maintenance/php-tune",
    "/api/maintenance/php-versions", "/api/maintenance/sftp-targets",
    "/api/maintenance/user-backups/{user_id}", "/api/maintenance/user-restore-backups",
    "/api/malware/jobs", "/api/malware/jobs/latest", "/api/malware/jobs/{job_id}",
    "/api/malware/schedule", "/api/malware/status", "/api/mcp", "/api/mcp/tokens",
    "/api/notifications/log", "/api/notifications/me", "/api/notifications/settings",
    "/api/packages", "/api/panel-settings", "/api/panel-settings/public",
    "/api/provisioning/v1/accounts/{external_id}", "/api/provisioning/v1/accounts/{external_id}/usage",
    "/api/provisioning/v1/plans", "/api/provisioning/v1/tokens", "/api/services/list",
    "/api/services/resource-usage", "/api/services/system-info", "/api/sftp-accounts",
    "/api/sftp-accounts/limits", "/api/site-apps", "/api/site-apps/suggest-port",
    "/api/site-apps/{app_id}/logs", "/api/site-apps/{app_id}/status", "/api/site-runtimes/status",
    "/api/terminal/allowed-commands", "/api/updates/status", "/api/users", "/api/users/audit/log",
    "/api/users/me", "/api/users/storage-usage", "/api/waf/access-logs", "/api/waf/bots",
    "/api/waf/crs", "/api/waf/orphans", "/api/waf/rules", "/api/waf/status",
    "/api/waf/websites/{website_id}", "/api/websites", "/api/websites/{website_id}/aliases",
    "/api/websites/{website_id}/logs", "/api/websites/{website_id}/nginx-config",
    "/api/websites/{website_id}/nginx-custom", "/api/websites/{website_id}/ssl/cloudflare-zone",
    "/api/websites/{website_id}/ssl/sources",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    stored: dict = {}
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(stored))
    monkeypatch.setattr(panel_settings, "_read_raw_lenient", lambda: dict(stored))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: stored.update(data))
    monkeypatch.setattr(addons, "ADDONS_DIR", tmp_path)
    monkeypatch.setattr(addons, "ADDONS_FILE", tmp_path / "addons.json")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for name, role in (("owner", "admin"), ("demo", "admin"), ("khachhang", "end_user"), ("other", "end_user")):
        db.add(User(username=name, email=f"{name}@example.test", hashed_password=hash_password("original-" + name),
                    role=role, is_active=True, token_version=0))
    db.commit()

    # Imported here: importing app.main runs the migrations.
    from app.main import app

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    client = TestClient(app)

    def user(name):
        db.expire_all()
        return db.query(User).filter(User.username == name).one()

    def as_(name, **claims):
        token = create_access_token(name, {"role": user(name).role, "tv": user(name).token_version, **claims})
        return {"Authorization": f"Bearer {token}"}

    def turn_on():
        addons.install(addons.DEMO)
        demo_mode.configure(db, user("owner"), {
            "admin": {"username": "demo", "password": "demo-admin"},
            "customer": {"username": "khachhang", "password": "demo-customer"},
        })

    yield SimpleNamespace(client=client, db=db, user=user, as_=as_, turn_on=turn_on, stored=stored)
    app.dependency_overrides.pop(get_db, None)
    db.close()


def test_it_is_an_addon_and_off_by_default(env):
    entry = addons.CATALOGUE[addons.DEMO]
    assert entry["keeps_data_on_uninstall"] is True
    assert not addons.is_installed(addons.DEMO)
    assert demo_mode.public_view() == {"enabled": False, "accounts": []}


def test_the_settings_routes_need_the_addon(env):
    response = env.client.get("/api/demo-mode", headers=env.as_("owner"))
    assert response.status_code == 409


def test_a_demo_admin_can_look_but_not_change(env):
    env.turn_on()
    headers = env.as_("demo")
    assert env.client.get("/api/users", headers=headers).status_code == 200
    refused = env.client.post("/api/users", headers=headers, json={
        "username": "intruder", "email": "x@example.test", "password": "longenough123", "role": "admin"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == demo_mode.REFUSED_CHANGE
    assert env.client.delete(f"/api/users/{env.user('other').id}", headers=headers).status_code == 403
    assert env.user("other") is not None


def test_a_demo_customer_is_held_to_the_same_rule(env):
    env.turn_on()
    headers = env.as_("khachhang")
    assert env.client.get("/api/auth/session", headers=headers).json()["user"]["demo"] is True
    refused = env.client.post(f"/api/users/{env.user('khachhang').id}/password", headers=headers,
                              json={"password": "changed-by-a-visitor", "current_password": "demo-customer"})
    assert refused.status_code == 403
    assert verify_password("demo-customer", env.user("khachhang").hashed_password)


@pytest.mark.parametrize("path", [
    "/api/maintenance/files/1/read?path=wp-config.php",
    "/api/maintenance/files/1/download?path=wp-config.php",
    "/api/databases/1/download",
    "/api/maintenance/backups/1/download?filename=x.tar.gz",
])
def test_reads_that_hand_over_contents_are_refused(env, path):
    env.turn_on()
    response = env.client.get(path, headers=env.as_("demo"))
    assert response.status_code == 403
    assert response.json()["detail"] == demo_mode.REFUSED_READ


def test_the_real_administrator_is_untouched(env):
    env.turn_on()
    headers = env.as_("owner")
    created = env.client.post("/api/packages", headers=headers, json={"name": "Owner made this"})
    assert created.status_code != 403
    assert env.client.get("/api/auth/session", headers=headers).json()["user"]["demo"] is False


def test_an_administrator_logged_in_as_the_demo_customer_can_set_it_up(env):
    env.turn_on()
    headers = env.as_("khachhang", imp=True)
    assert env.client.get("/api/auth/session", headers=headers).json()["user"]["demo"] is False
    assert not demo_mode.is_demo_session(env.user("khachhang"), {"imp": True})


def test_signing_out_of_a_demo_account_does_not_sign_out_the_other_visitors(env):
    env.turn_on()
    other_visitor = env.as_("demo")
    assert env.client.post("/api/auth/logout", headers=env.as_("demo")).status_code == 200
    assert env.user("demo").token_version == 0
    assert env.client.get("/api/users", headers=other_visitor).status_code == 200


def test_signing_out_of_an_ordinary_account_still_ends_every_session(env):
    env.turn_on()
    assert env.client.post("/api/auth/logout", headers=env.as_("owner")).status_code == 200
    assert env.user("owner").token_version == 1


def test_nothing_is_restricted_while_the_addon_is_off(env):
    env.turn_on()
    addons.uninstall(addons.DEMO)
    assert not demo_mode.is_demo_user(env.user("demo"))
    assert env.client.get("/api/auth/session", headers=env.as_("demo")).json()["user"]["demo"] is False


def test_the_sign_in_page_gets_the_accounts_only_while_it_is_on(env):
    env.turn_on()
    public = env.client.get("/api/demo-mode/public").json()
    assert public == {"enabled": True, "accounts": [
        {"slot": "admin", "username": "demo", "password": "demo-admin"},
        {"slot": "customer", "username": "khachhang", "password": "demo-customer"},
    ]}
    addons.uninstall(addons.DEMO)
    assert env.client.get("/api/demo-mode/public").json() == {"enabled": False, "accounts": []}


def test_saving_publishes_the_passwords(env):
    env.turn_on()
    assert verify_password("demo-admin", env.user("demo").hashed_password)
    assert verify_password("demo-customer", env.user("khachhang").hashed_password)


@pytest.mark.parametrize("requested, message", [
    ({"admin": {"username": "owner", "password": "longenough"}}, "your own account"),
    ({"admin": {"username": "khachhang", "password": "longenough"}}, "must be an administrator"),
    ({"customer": {"username": "demo", "password": "longenough"}}, "must be a customer"),
    ({"admin": {"username": "demo", "password": "short"}}, "6 to 128"),
    ({"admin": {"username": "nobody", "password": "longenough"}}, "does not exist"),
])
def test_what_cannot_be_a_demo_account(env, requested, message):
    addons.install(addons.DEMO)
    response = env.client.put("/api/demo-mode", headers=env.as_("owner"), json=requested)
    assert response.status_code == 400
    assert message in response.json()["detail"]


def test_an_account_that_leaves_the_demo_stops_accepting_its_public_password(env):
    env.turn_on()
    response = env.client.put("/api/demo-mode", headers=env.as_("owner"),
                              json={"admin": {"username": "demo", "password": "demo-admin"}})
    assert response.status_code == 200
    assert not verify_password("demo-customer", env.user("khachhang").hashed_password)
    assert env.user("khachhang").token_version == 1


def test_removing_the_addon_retires_the_passwords_and_installing_restores_them(env):
    env.turn_on()
    removed = env.client.post("/api/addons/demo/uninstall", headers=env.as_("owner"))
    assert removed.status_code == 200
    assert "random passwords" in removed.json()["kept"]
    assert not verify_password("demo-admin", env.user("demo").hashed_password)
    assert not verify_password("demo-customer", env.user("khachhang").hashed_password)
    assert env.client.post("/api/addons/demo/install", headers=env.as_("owner")).status_code == 200
    assert verify_password("demo-admin", env.user("demo").hashed_password)
    assert verify_password("demo-customer", env.user("khachhang").hashed_password)


def test_a_demo_admin_cannot_turn_the_addon_off(env):
    env.turn_on()
    assert env.client.post("/api/addons/demo/uninstall", headers=env.as_("demo")).status_code == 403
    assert addons.is_installed(addons.DEMO)


def test_visitors_do_not_see_each_others_addresses(env):
    assert demo_mode.mask_ips("login | ip=203.0.113.7 ua=Mozilla") == "login | ip=hidden ua=Mozilla"
    assert demo_mode.mask_ips("allow 198.51.100.23") == "allow 198.51.x.x"
    assert demo_mode.mask_ips("ip=2001:db8::1 ua=x") == "ip=hidden ua=x"


def test_the_terminal_websocket_asks_the_same_question():
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "terminal.py").read_text(encoding="utf-8")
    assert "demo_mode.is_demo_session(current_user, payload)" in source


def test_every_get_route_has_been_looked_at():
    from app.main import app

    gets = {path for path, operations in app.openapi()["paths"].items() if "get" in operations}
    unreviewed = gets - REVIEWED_READS - demo_mode.BLOCKED_READS
    assert not unreviewed, (
        "New GET route(s) a demo visitor can call. If one returns file contents, a download, "
        "a secret or a way into something, add it to demo_mode.BLOCKED_READS; otherwise add it "
        f"to REVIEWED_READS here: {sorted(unreviewed)}"
    )
    assert demo_mode.BLOCKED_READS <= gets, "a blocked route no longer exists; update the list"


def test_the_panel_offers_the_accounts_and_says_it_is_a_demo():
    assert "/demo-mode/public" in APP
    assert "demo-strip" in APP
    assert "addon.slug === 'demo'" in APP
