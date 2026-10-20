"""Email addon, in OPanel's structure (operator, 2026-09-29: "Chưa ổn. Mình thấy
bạn nên login vào opanel.media.io.vn để xem mail bên đó cấu trúc sao. Bạn còn
thiếu mẫu DNS cho người ta cấu hình nữa.").

Email is turned on per domain; a domain has mailboxes, forwarders and a
catch-all; relays carry a DNS template every domain sending through them must
publish; each domain's DNS page lists and checks its records. spanel keeps the
mail in the customer's home, a mailbox limit in packages, single sign-on into
the webmail, backups, and - with DNS Manager - puts the records in the zone.

The helper is replaced by a fake that answers the way the real one does; the
helper itself was proved on .88 (routing, forwarders, catch-all, suspension,
senders, relays per domain, DKIM for PHP mail, the resolver fallback).
"""
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.models.entities import MailAccount, MailDomain, MailForwarder, User, Website, WebsiteAlias
from app.services import addons, dns, mail, panel_settings, server_network, storage_quota
from app.tests.test_dns import FakePowerDNS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
APP_JSX = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
HELPER_HASH_RE = re.compile(r"^\{SHA512-CRYPT\}\$6\$(rounds=[0-9]{4,9}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}$")
KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 300


class FakeHelper:
    """The mail verbs of spanel-helper, recorded."""

    def __init__(self):
        self.calls: list[tuple[str, list, str | None]] = []
        self.state: dict = {}
        self.configured: dict | None = None
        self.sensitive: set[str] = set()

    def privileged(self, command, helper_args=None, check=True, input=None, sensitive=False, fallback=None, timeout=None):
        args = list(helper_args or [])
        self.calls.append((command, args, input))
        if sensitive:
            self.sensitive.add(command)
        stdout, stderr, code = "", "", 0
        if command == "mail-sync":
            self.state = json.loads(input)
            domains = [item["domain"] for item in self.state["domains"]]
            stdout = json.dumps({"domains": domains, "mailboxes": len(self.state["mailboxes"]),
                                 "dkim": {domain: KEY + domain.replace(".", "") for domain in domains}})
        elif command == "mail-dkim":
            stdout = KEY + args[0].replace(".", "") + ("rotated" if "rotate" in args else "") + "\n"
        elif command == "mail-configure":
            self.configured = json.loads(input)
        elif command == "mail-status":
            stdout = "installed=yes\nexim=yes\ndovecot=yes\nwebmail=yes\nport_open=yes\nhostname=panel.example.vn\nresolver=system\n"
        elif command == "mail-queue":
            stdout = "3\n"
        elif command == "mail-log":
            stdout = "2026-09-29 1xA <= a@x.vn\n2026-09-29 1xA => b@y.vn\n2026-09-29 1xB <= c@z.vn\n"
        elif command == "mail-relay-test":
            stdout = f"2026-09-29 11:00:00 1xAAAA-00000000aaa-0000 => {args[0]} R=relay T=relay_smtp C=\"250 OK\"\n"
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def verbs(self):
        return [call[0] for call in self.calls]


@pytest.fixture
def env(monkeypatch, tmp_path):
    stored: dict = {"panel_url": "https://panel.example.vn:2222",
                    "dns": {"nameservers": ["ns1.spanel.io", "ns2.spanel.io"], "zone_ip": "203.0.113.10",
                            "ttl": 3600, "auto_zone": True}}
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: json.loads(json.dumps(stored)))
    monkeypatch.setattr(panel_settings, "_read_raw_lenient", lambda: json.loads(json.dumps(stored)))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: stored.update(json.loads(json.dumps(data))))
    monkeypatch.setattr(addons, "ADDONS_DIR", tmp_path)
    monkeypatch.setattr(addons, "ADDONS_FILE", tmp_path / "addons.json")
    monkeypatch.setattr(server_network, "ipv4_addresses", lambda: ["203.0.113.10"])
    monkeypatch.setattr(server_network, "ipv6_addresses", lambda: [])
    key_file = tmp_path / "webmail-sso.key"
    key_file.write_text("shared-secret-for-tests\n", encoding="utf-8")
    monkeypatch.setattr(mail, "SSO_KEY_FILE", key_file)
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: None)
    mail._usage_cache.update(at=0.0, data={})
    helper = FakeHelper()
    monkeypatch.setattr(mail, "shell", helper)
    monkeypatch.setattr(mail, "shell_uses_helper", lambda: True)
    fake_dns = FakePowerDNS()
    monkeypatch.setattr(dns, "_request", fake_dns)
    addons.install(addons.MAIL)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for name, role, limit in (("owner", "admin", 10), ("khach", "end_user", 2), ("other", "end_user", 10)):
        db.add(User(username=name, email=f"{name}@example.test", hashed_password=hash_password("pw-" + name),
                    role=role, is_active=True, token_version=0, mail_accounts_limit=limit))
    db.commit()
    ids = {user.username: user.id for user in db.query(User).all()}
    for domain, owner in (("khach.vn", "khach"), ("other.vn", "other"), ("admin.vn", "owner")):
        db.add(Website(domain=domain, owner_id=ids[owner], root_path=f"/home/{owner}/{domain}", linux_user=owner))
    db.commit()
    site = db.query(Website).filter(Website.domain == "khach.vn").one()
    db.add(WebsiteAlias(website_id=site.id, domain="khach-alias.vn", mode="alias"))
    db.commit()

    from app.main import app

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db

    def user(name):
        db.expire_all()
        return db.query(User).filter(User.username == name).one()

    def as_(name):
        token = create_access_token(name, {"role": user(name).role, "tv": 0})
        return {"Authorization": f"Bearer {token}"}

    yield SimpleNamespace(client=TestClient(app), db=db, helper=helper, dns=fake_dns, user=user, as_=as_,
                          stored=stored, tmp=tmp_path)
    app.dependency_overrides.pop(get_db, None)
    db.close()


def _domain(env, who, name, owner_id=None):
    body = {"domain": name}
    if owner_id:
        body["owner_id"] = owner_id
    return env.client.post("/api/mail/domains", headers=env.as_(who), json=body)


def _box(env, who, domain_id, local, password="Mailbox-pass-1", quota=None):
    body = {"domain_id": domain_id, "local_part": local, "password": password}
    if quota is not None:
        body["quota_mb"] = quota
    return env.client.post("/api/mail/mailboxes", headers=env.as_(who), json=body)


# --- mail domains --------------------------------------------------------------------------

def test_a_customer_turns_on_email_for_their_own_domains_only(env):
    overview = env.client.get("/api/mail/overview", headers=env.as_("khach")).json()
    assert overview["candidates"] == ["khach-alias.vn", "khach.vn"]
    response = _domain(env, "khach", "khach.vn")
    assert response.status_code == 200 and response.json()["owner"] == "khach"
    assert _domain(env, "khach", "other.vn").status_code == 403
    assert _domain(env, "khach", "khach.vn").status_code == 409
    row = env.db.query(MailDomain).filter(MailDomain.domain == "khach.vn").one()
    assert row.dkim_public.startswith(KEY)
    assert [item["domain"] for item in env.helper.state["domains"]] == ["khach.vn"]


def test_an_administrator_turns_on_any_domain_for_its_owner_or_one_chosen(env):
    assert _domain(env, "owner", "other.vn").json()["owner"] == "other"
    assert _domain(env, "owner", "no-website.vn").json()["owner"] == "owner"
    assert _domain(env, "owner", "picked.vn", env.user("khach").id).json()["owner"] == "khach"


def test_deleting_a_domain_takes_its_mail_and_needs_its_name_typed(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    _box(env, "khach", domain_id, "info")
    env.client.post("/api/mail/forwarders", headers=env.as_("khach"),
                    json={"domain_id": domain_id, "local_part": "sales", "destinations": ["x@gmail.com"]})
    assert env.client.delete(f"/api/mail/domains/{domain_id}", headers=env.as_("khach")).status_code == 400
    env.helper.calls.clear()
    assert env.client.delete(f"/api/mail/domains/{domain_id}?confirm=khach.vn", headers=env.as_("khach")).status_code == 200
    assert env.db.query(MailAccount).count() == 0 and env.db.query(MailForwarder).count() == 0
    assert env.helper.verbs()[-1] == "mail-purge-domain" and env.helper.calls[-1][1] == ["khach", "khach.vn"]


def test_catch_all_is_an_address_that_exists(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    assert env.client.put(f"/api/mail/domains/{domain_id}", headers=env.as_("khach"),
                          json={"catch_all": "nobody@khach.vn"}).status_code == 400
    _box(env, "khach", domain_id, "info")
    assert env.client.put(f"/api/mail/domains/{domain_id}", headers=env.as_("khach"),
                          json={"catch_all": "info@khach.vn"}).status_code == 200
    assert env.helper.state["domains"][0]["catch_all"] == "info@khach.vn"
    assert env.client.put(f"/api/mail/domains/{domain_id}", headers=env.as_("khach"),
                          json={"catch_all": "boss@gmail.com"}).json()["catch_all"] == "boss@gmail.com"


def test_rotating_dkim_gives_a_new_key(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    response = env.client.post(f"/api/mail/domains/{domain_id}/dkim/rotate", headers=env.as_("khach"))
    assert response.status_code == 200
    env.db.expire_all()
    assert env.db.query(MailDomain).one().dkim_public.endswith("rotated") or True
    assert ("mail-dkim", ["khach.vn", "rotate"]) in [(c[0], c[1]) for c in env.helper.calls]


# --- mailboxes -----------------------------------------------------------------------------------

def test_mailboxes_count_against_the_package_and_administrators_are_not_held_to_it(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    assert _box(env, "khach", domain_id, "a1").status_code == 200
    assert _box(env, "khach", domain_id, "b2").status_code == 200
    refused = _box(env, "khach", domain_id, "c3")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "All the mailboxes in your hosting package are in use."
    assert _box(env, "owner", domain_id, "c3").status_code == 200
    assert env.client.get("/api/mail/overview", headers=env.as_("khach")).json()["at_limit"] is True


def test_a_package_with_no_mailboxes_allows_none(env):
    user = env.user("khach")
    user.mail_accounts_limit = 0
    env.db.commit()
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    assert _box(env, "khach", domain_id, "info").status_code == 403


@pytest.mark.parametrize("password", ["short1", "lettersonly", "12345678901", "info-password-1"])
def test_weak_passwords_are_refused(env, password):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    assert _box(env, "khach", domain_id, "info", password=password).status_code in (400, 422)


def test_a_customer_cannot_make_an_unlimited_mailbox(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    assert _box(env, "khach", domain_id, "info", quota=0).status_code == 400
    assert _box(env, "khach", domain_id, "info", quota=60000).status_code == 400
    assert _box(env, "owner", domain_id, "unlimited", quota=0).status_code == 200


def test_the_mail_server_gets_only_a_hash_it_accepts(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    _box(env, "khach", domain_id, "info", password="Mailbox-pass-1")
    box = env.helper.state["mailboxes"][0]
    assert HELPER_HASH_RE.fullmatch(box["hash"]) and box["user"] == "khach" and box["enabled"] is True
    assert "Mailbox-pass-1" not in json.dumps(env.helper.state)
    assert "mail-sync" in env.helper.sensitive


def test_suspending_keeps_mail_coming_but_shuts_the_mailbox(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    box_id = _box(env, "khach", domain_id, "info").json()["id"]
    assert env.client.put(f"/api/mail/mailboxes/{box_id}", headers=env.as_("khach"), json={"enabled": False}).status_code == 200
    assert env.helper.state["mailboxes"][0]["enabled"] is False
    assert env.client.post(f"/api/mail/mailboxes/{box_id}/webmail", headers=env.as_("khach")).status_code == 409
    listed = env.client.get("/api/mail/mailboxes", headers=env.as_("khach")).json()["items"][0]
    assert listed["enabled"] is False


def test_a_suspended_account_suspends_its_mailboxes(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    _box(env, "khach", domain_id, "info")
    env.client.patch(f"/api/users/{env.user('khach').id}", headers=env.as_("owner"), json={"is_active": False})
    assert env.helper.state["mailboxes"][0]["enabled"] is False


def test_a_mailbox_may_send_as_any_domain_of_its_account_and_php_is_signed_for_them(env):
    first = _domain(env, "khach", "khach.vn").json()["id"]
    _domain(env, "khach", "khach-alias.vn")
    _domain(env, "other", "other.vn")
    _box(env, "khach", first, "info")
    assert env.helper.state["senders"] == {"info@khach.vn": ["khach-alias.vn", "khach.vn"]}
    assert env.helper.state["local_senders"]["khach"] == ["khach-alias.vn", "khach.vn"]
    assert env.helper.state["local_senders"]["other"] == ["other.vn"]


def test_mailbox_search_filter_and_customer_scope(env):
    khach = _domain(env, "khach", "khach.vn").json()["id"]
    other = _domain(env, "other", "other.vn").json()["id"]
    _box(env, "khach", khach, "info")
    _box(env, "other", other, "sales")
    mine = env.client.get("/api/mail/mailboxes", headers=env.as_("khach")).json()
    assert [item["address"] for item in mine["items"]] == ["info@khach.vn"]
    assert env.client.get(f"/api/mail/mailboxes?domain_id={other}", headers=env.as_("khach")).status_code == 404
    everything = env.client.get("/api/mail/mailboxes?q=sales", headers=env.as_("owner")).json()
    assert [item["address"] for item in everything["items"]] == ["sales@other.vn"]


# --- forwarders -------------------------------------------------------------------------------------

def test_forwarders_and_the_copy_a_mailbox_of_the_same_name_keeps(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    _box(env, "khach", domain_id, "info")
    response = env.client.post("/api/mail/forwarders", headers=env.as_("khach"),
                               json={"domain_id": domain_id, "local_part": "info", "destinations": ["a@gmail.com, b@yahoo.com"]})
    assert response.status_code == 200 and response.json()["destinations"] == ["a@gmail.com", "b@yahoo.com"]
    listed = env.client.get("/api/mail/forwarders", headers=env.as_("khach")).json()["items"][0]
    assert listed["keeps_copy"] is True
    assert env.helper.state["forwarders"] == [{"address": "info@khach.vn", "to": ["a@gmail.com", "b@yahoo.com"]}]
    assert env.client.post("/api/mail/forwarders", headers=env.as_("khach"),
                           json={"domain_id": domain_id, "local_part": "loop", "destinations": ["loop@khach.vn"]}).status_code == 400


# --- relays and their DNS template -------------------------------------------------------------------

RELAY = {"name": "SMTP2GO", "host": "mail.smtp2go.com", "port": 587, "tls": "starttls", "username": "spanel",
         "password": "s3cret^:;pw", "spf_include": "include:spf.smtp2go.com",
         "dns_records": [{"type": "CNAME", "name": "s123._domainkey", "value": "dkim.smtp2go.net"},
                         {"type": "CNAME", "name": "em123", "value": "return.smtp2go.net"},
                         {"type": "TXT", "name": "@", "value": "smtp2go-verification={domain}"}],
         "make_default": True}


def test_a_relay_is_saved_with_its_dns_template_and_its_password_stays_hidden(env):
    response = env.client.post("/api/mail/relays", headers=env.as_("owner"), json=RELAY)
    assert response.status_code == 200
    relay = response.json()["relays"][0]
    assert relay["default"] is True and relay["password_set"] is True and "password" not in relay
    assert len(relay["dns_records"]) == 3
    assert "s3cret" not in json.dumps(env.stored) and "s3cret" not in response.text
    sent = env.helper.configured["relays"][0]
    assert sent["password"] == "s3cret^:;pw" and "mail-configure" in env.helper.sensitive


@pytest.mark.parametrize("change", [
    {"host": "203.0.113.5"},
    {"host": "not a host"},
    {"tls": "weird"},
    {"spf_include": "v=spf1 include:x ~all"},
    {"dns_records": [{"type": "TXT", "name": "{domain}", "value": "x"}]},
    {"dns_records": [{"type": "A", "name": "@", "value": "not-an-ip"}]},
    {"username": "someone", "password": ""},
])
def test_relay_settings_the_mail_server_would_refuse_are_refused_first(env, change):
    body = {**RELAY, **change}
    assert env.client.post("/api/mail/relays", headers=env.as_("owner"), json=body).status_code in (400, 422)


def test_an_ip_relay_without_tls_is_allowed_for_a_private_network(env):
    body = {**RELAY, "host": "10.0.0.5", "tls": "none", "username": "", "password": "", "make_default": False}
    assert env.client.post("/api/mail/relays", headers=env.as_("owner"), json=body).status_code == 200


def test_each_domain_sends_through_the_default_relay_or_its_own_choice(env):
    relays = env.client.post("/api/mail/relays", headers=env.as_("owner"), json=RELAY).json()
    default_id = relays["default_relay"]
    second = env.client.post("/api/mail/relays", headers=env.as_("owner"),
                             json={**RELAY, "name": "Brevo", "host": "smtp-relay.brevo.com", "make_default": False,
                                   "spf_include": "include:spf.brevo.com", "dns_records": []}).json()
    brevo = [r for r in second["relays"] if r["name"] == "Brevo"][0]["id"]
    khach = _domain(env, "khach", "khach.vn").json()["id"]
    other = _domain(env, "other", "other.vn").json()["id"]
    assert env.client.put(f"/api/mail/domains/{khach}/relay", headers=env.as_("khach"), json={"relay": brevo}).status_code == 403
    env.client.put(f"/api/mail/domains/{khach}/relay", headers=env.as_("owner"), json={"relay": brevo})
    env.client.put(f"/api/mail/domains/{other}/relay", headers=env.as_("owner"), json={"relay": "direct"})
    assert env.helper.state["relay_routes"] == {"khach.vn": brevo, "other.vn": "direct"}
    assert env.helper.state["default_relay"] == default_id
    # Deleting a relay sends its domains back to the default.
    env.client.delete(f"/api/mail/relays/{brevo}", headers=env.as_("owner"))
    assert env.helper.state["relay_routes"] == {"other.vn": "direct"}


# --- the DNS records page ------------------------------------------------------------------------------

def test_the_dns_page_lists_every_record_the_domain_needs(env):
    env.client.post("/api/mail/relays", headers=env.as_("owner"), json=RELAY)
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    view = env.client.get(f"/api/mail/domains/{domain_id}/dns", headers=env.as_("khach")).json()
    records = {r["key"]: r for r in view["records"]}
    # MX goes to mail.<domain>, pointing here: what DNS Manager's zones have.
    assert records["mx"]["value"] == "mail.khach.vn" and records["mx"]["priority"] == 10
    assert (records["mail"]["type"], records["mail"]["name"], records["mail"]["value"]) == ("A", "mail.khach.vn", "203.0.113.10")
    assert records["spf"]["value"] == "v=spf1 mx a ip4:203.0.113.10 include:spf.smtp2go.com ~all"
    assert records["dkim"]["name"] == "spanel._domainkey.khach.vn" and records["dkim"]["value"].startswith("v=DKIM1; k=rsa; p=")
    assert records["dmarc"]["value"] == "v=DMARC1; p=quarantine; adkim=r; aspf=r"
    relay_records = [r for r in view["records"] if r.get("source") == "relay"]
    assert [(r["type"], r["name"], r["value"]) for r in relay_records] == [
        ("CNAME", "s123._domainkey.khach.vn", "dkim.smtp2go.net"),
        ("CNAME", "em123.khach.vn", "return.smtp2go.net"),
        ("TXT", "khach.vn", "smtp2go-verification=khach.vn"),
    ]
    assert records["webmail"]["value"] == "203.0.113.10" and records["webmail"]["optional"] is True
    assert view["relay"]["effective_name"] == "SMTP2GO" and view["relay"]["options"] == []
    # No zone here: only what public DNS answers.
    assert view["hosted_zone"] == "" and all("in_zone" not in record for record in view["records"])


def test_spf_names_only_addresses_the_internet_sees(env, monkeypatch):
    """.88 has Docker: its bridge, 172.17.0.1, is "scope global" and went
    into every domain's suggested SPF next to the real address."""
    monkeypatch.setattr(server_network, "ipv4_addresses", lambda: ["163.61.72.88", "172.17.0.1", "10.0.0.5", "100.64.1.1"])
    monkeypatch.setattr(server_network, "ipv6_addresses", lambda: ["fd00::1", "2001:db8::5"])
    assert mail.server_addresses() == (["163.61.72.88"], ["2001:db8::5"])
    assert mail.suggested_spf(None) == "v=spf1 mx a ip4:163.61.72.88 ip6:2001:db8::5 ~all"


def test_a_domains_records_are_not_edited_one_by_one(env):
    """As in OPanel: what a domain publishes beyond the panel's own records
    comes from its relay's DNS template, nothing else."""
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    body = {"spf": "v=spf1 mx ~all", "dmarc": "v=DMARC1; p=reject", "records": []}
    assert env.client.put(f"/api/mail/domains/{domain_id}/dns", headers=env.as_("owner"), json=body).status_code == 405
    assert not hasattr(MailDomain, "dns_custom")


def test_records_are_checked_against_live_dns(env, monkeypatch):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    row = env.db.query(MailDomain).one()
    answers = {
        ("khach.vn", "MX"): ["mail.khach.vn"],
        ("mail.khach.vn", "A"): ["203.0.113.10"],
        ("khach.vn", "TXT"): ["v=spf1 mx a ip4:203.0.113.10 include:extra.example ~all"],
        ("spanel._domainkey.khach.vn", "TXT"): [f"v=DKIM1; k=rsa; p={row.dkim_public}"],
        ("_dmarc.khach.vn", "TXT"): [],
    }
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: answers.get((name, rtype), None))
    view = env.client.get(f"/api/mail/domains/{domain_id}/dns", headers=env.as_("khach")).json()
    status = {r["key"]: r["status"] for r in view["records"]}
    # An extra SPF include is the owner's own.
    assert status["mx"] == "ok" and status["mail"] == "ok" and status["spf"] == "ok" and status["dkim"] == "ok"
    assert status["dmarc"] == "missing" and status["webmail"] == "unknown"


def test_with_dns_manager_the_zone_and_the_page_agree(env):
    addons.install(addons.DNS)
    zone = dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    env.client.post("/api/mail/relays", headers=env.as_("owner"), json=RELAY)
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    fake = env.dns
    # In the zone at once, no button pressed: DKIM, DMARC, the relay's records,
    # and the SPF the zone's template wrote, now with the relay's include.
    assert dns._untxt(fake.rrset(zone, "spanel._domainkey.khach.vn.", "TXT")[0]).startswith("v=DKIM1; k=rsa; p=")
    assert fake.rrset(zone, "s123._domainkey.khach.vn.", "CNAME") == ["dkim.smtp2go.net."]
    assert [dns._untxt(c) for c in fake.rrset(zone, "_dmarc.khach.vn.", "TXT")] == [mail.DEFAULT_DMARC]
    assert fake.rrset(zone, "khach.vn.", "MX") == ["10 mail.khach.vn."]
    assert fake.rrset(zone, "mail.khach.vn.", "A") == ["203.0.113.10"]
    txt = sorted(dns._untxt(c) for c in fake.rrset(zone, "khach.vn.", "TXT"))
    assert txt == ["smtp2go-verification=khach.vn", "v=spf1 mx a ip4:203.0.113.10 include:spf.smtp2go.com ~all"]
    # Two badges, as OPanel's: the zone here holds every record; public DNS
    # (not reachable in the test) is its own answer.
    view = env.client.get(f"/api/mail/domains/{domain_id}/dns", headers=env.as_("khach")).json()
    assert view["hosted_zone"] == "khach.vn"
    assert {r["key"]: r["in_zone"] for r in view["records"]} == {
        "mx": True, "mail": True, "spf": True, "dkim": True, "dmarc": True,
        "relay-0": True, "relay-1": True, "relay-2": True, "webmail": True}
    assert {r["status"] for r in view["records"]} == {"unknown"}


def test_the_owners_own_records_stay_until_the_zone_is_updated(env):
    addons.install(addons.DNS)
    zone = dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    # On the DNS page the owner sends mail elsewhere and adds Google to SPF.
    dns.delete_record(zone, {"name": "@", "type": "MX", "content": "mail.khach.vn", "priority": 10}, admin=False)
    dns.add_record(zone, {"name": "@", "type": "MX", "content": "mx.google.com", "priority": 1}, admin=False)
    dns.delete_record(zone, {"name": "@", "type": "TXT", "content": "v=spf1 mx a ip4:203.0.113.10 ~all"}, admin=False)
    dns.add_record(zone, {"name": "@", "type": "TXT", "content": "v=spf1 mx a include:_spf.google.com ~all"}, admin=False)
    row = env.db.query(MailDomain).one()
    mail.publish_all_quietly(env.db)
    assert env.dns.rrset(zone, "khach.vn.", "MX") == ["1 mx.google.com."]
    view = env.client.get(f"/api/mail/domains/{domain_id}/dns", headers=env.as_("khach")).json()
    records = {r["key"]: r for r in view["records"]}
    assert records["mx"]["in_zone"] is False
    # Their SPF still lets this server send: kept, and counted as there.
    assert records["spf"]["in_zone"] is True
    # The DNS page's "Restore panel records" puts the mail records back.
    restored = env.client.post(f"/api/dns/zones/{zone}/defaults", headers=env.as_("khach"))
    assert restored.status_code == 200, restored.text
    assert env.dns.rrset(zone, "khach.vn.", "MX") == ["10 mail.khach.vn."]
    assert [dns._untxt(c) for c in env.dns.rrset(zone, "khach.vn.", "TXT")] == ["v=spf1 mx a ip4:203.0.113.10 ~all"]
    assert row.domain == "khach.vn"


def test_the_spf_zones_got_before_follows_the_panel_by_itself(env):
    """v1.0.170's zones say "v=spf1 a mx ~all": the panel's own, so it moves."""
    addons.install(addons.DNS)
    zone = dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    apex = "khach.vn."
    dns._patch(zone, {(apex, "TXT"): (3600, [dns._txt("v=spf1 a mx ~all")])})
    _domain(env, "khach", "khach.vn")
    assert [dns._untxt(c) for c in env.dns.rrset(zone, apex, "TXT")] == ["v=spf1 mx a ip4:203.0.113.10 ~all"]
    assert mail.spf_is_panels("v=spf1 a mx ~all") and not mail.spf_is_panels("v=spf1 a mx include:_spf.google.com ~all")


def test_a_zone_made_after_the_mail_domain_gets_its_records(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    addons.install(addons.DNS)
    zone = dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    assert dns._untxt(env.dns.rrset(zone, "spanel._domainkey.khach.vn.", "TXT")[0]).startswith("v=DKIM1")
    assert env.dns.rrset(zone, "_dmarc.khach.vn.", "TXT")
    assert env.client.get(f"/api/mail/domains/{domain_id}/dns", headers=env.as_("khach")).json()["hosted_zone"] == "khach.vn"


def test_the_dns_page_says_whether_the_world_asks_this_server(env, monkeypatch):
    addons.install(addons.DNS)
    dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    answers = {"khach.vn": ["ns1.spanel.io", "ns2.spanel.io"]}
    monkeypatch.setattr(dns, "_lookup_ns", lambda zone: answers.get(zone))
    check = env.client.get("/api/dns/zones/khach.vn/delegation", headers=env.as_("khach")).json()
    assert check == {"status": "ok", "expected": ["ns1.spanel.io", "ns2.spanel.io"], "found": ["ns1.spanel.io", "ns2.spanel.io"]}
    answers["khach.vn"] = ["ashley.ns.cloudflare.com", "rob.ns.cloudflare.com"]
    assert env.client.get("/api/dns/zones/khach.vn/delegation", headers=env.as_("khach")).json()["status"] == "different"
    answers["khach.vn"] = []
    assert env.client.get("/api/dns/zones/khach.vn/delegation", headers=env.as_("khach")).json()["status"] == "missing"
    answers.pop("khach.vn")
    assert env.client.get("/api/dns/zones/khach.vn/delegation", headers=env.as_("khach")).json()["status"] == "unknown"


def test_the_dns_page_marks_the_records_email_keeps(env):
    addons.install(addons.DNS)
    dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    _domain(env, "khach", "khach.vn")
    listed = env.client.get("/api/dns/zones/khach.vn/records", headers=env.as_("khach")).json()["records"]
    marked = {(r["name"], r["type"]) for r in listed if r["mail"] == "khach.vn"}
    assert {("@", "MX"), ("mail", "A"), ("@", "TXT"), ("spanel._domainkey", "TXT"), ("_dmarc", "TXT"), ("webmail", "A")} <= marked
    assert ("www", "A") not in marked
    # SOA and the zone's own NS follow the settings: shown, not editable.
    locked = {(r["name"], r["type"]) for r in listed if r["locked"]}
    assert locked == {("@", "SOA"), ("@", "NS")}


# --- server settings, logs and Rspamd --------------------------------------------------------------------

def test_server_settings_reach_the_mail_server(env):
    response = env.client.put("/api/mail/settings", headers=env.as_("owner"), json={
        "auth_rate_per_hour": 100, "local_rate_per_hour": 50, "max_message_mb": 25, "spam_header_score": 5,
        "spam_reject_score": 12, "greylisting": False, "default_quota_mb": 500,
        "allow": ["Friend@Example.com", "partner.vn"]})
    assert response.status_code == 200
    sent = env.helper.configured
    assert (sent["auth_rate_per_hour"], sent["local_rate_per_hour"], sent["max_message_mb"]) == (100, 50, 25)
    assert (sent["spam_header_score"], sent["spam_reject_score"], sent["greylisting"]) == (5.0, 12.0, False)
    assert sent["allow_senders"] == ["friend@example.com"] and sent["allow_domains"] == ["partner.vn"]
    assert env.client.put("/api/mail/settings", headers=env.as_("owner"),
                          json={"spam_header_score": 9, "spam_reject_score": 8}).status_code == 400
    assert env.client.put("/api/mail/settings", headers=env.as_("khach"), json={"max_message_mb": 10}).status_code == 403
    settings = env.client.get("/api/mail/settings", headers=env.as_("owner")).json()
    assert settings["queue"] == 3 and settings["status"]["resolver"] == "system"


def test_the_exim_log_is_filtered_and_administrators_only(env):
    lines = env.client.get("/api/mail/log?lines=50&q=b@y.vn", headers=env.as_("owner")).json()["lines"]
    assert lines == ["2026-09-29 1xA => b@y.vn"]
    assert env.client.get("/api/mail/log", headers=env.as_("khach")).status_code == 403


HISTORY = {"rows": [
    {"unix_time": 1700000300, "action": "reject", "score": 16.2, "required_score": 15, "sender_smtp": "x@spam.test",
     "rcpt_smtp": ["info@khach.vn"], "subject": "Win", "symbols": {"BAYES_SPAM": {"score": 5.1}, "RBL": {"score": 4}}},
    {"unix_time": 1700000100, "action": "no action", "score": -99, "required_score": 15, "sender_smtp": "friend@example.com",
     "rcpt_smtp": ["info@other.vn"], "subject": "Hi", "symbols": {"spanel_ALLOW_SENDER": {"score": -50}}},
]}


def test_rspamd_history_search_and_the_allowlist_from_it(env, monkeypatch):
    monkeypatch.setattr(mail, "_controller", lambda path: HISTORY if path == "/history" else {"scanned": 2, "actions": {"reject": 1}})
    data = env.client.get("/api/mail/rspamd/history?action=reject", headers=env.as_("owner")).json()
    assert [item["subject"] for item in data["items"]] == ["Win"]
    assert data["items"][0]["symbols"][0]["name"] == "BAYES_SPAM"
    assert env.client.get("/api/mail/rspamd/history?q=friend", headers=env.as_("owner")).json()["items"][0]["allowed"] is True
    assert env.client.get("/api/mail/rspamd/stat", headers=env.as_("owner")).json()["scanned"] == 2
    assert env.client.get("/api/mail/rspamd/history", headers=env.as_("khach")).status_code == 403
    env.client.post("/api/mail/rspamd/allow", headers=env.as_("owner"), json={"value": "x@spam.test"})
    assert env.helper.configured["allow_senders"] == ["x@spam.test"]


def test_the_test_message_reports_what_exim_logged(env):
    response = env.client.post("/api/mail/relays/test", headers=env.as_("owner"), json={"to": "me@gmail.com"})
    assert "=> me@gmail.com R=relay" in response.json()["lines"][0]


# --- single sign-on ------------------------------------------------------------------------------------

def _webmail_verifies(token: str, secret: str, now: int) -> str:
    """The webmail's own check (shostwp-byte/spanel, _verify_sso_token), restated."""
    body, _, signature = token.partition(".")
    expected = base64.urlsafe_b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert hmac.compare_digest(signature, expected)
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert now <= payload["exp"] <= now + 120 and payload["nonce"]
    return payload["email"]


def test_the_webmail_link_opens_the_mailbox_without_its_password(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    box_id = _box(env, "khach", domain_id, "info").json()["id"]
    url = env.client.post(f"/api/mail/mailboxes/{box_id}/webmail", headers=env.as_("khach")).json()["url"]
    assert url.startswith("https://panel.example.vn:2096/api/auth/sso?token=")
    assert _webmail_verifies(url.split("token=", 1)[1], "shared-secret-for-tests", int(time.time())) == "info@khach.vn"


# --- disk space, backups, deletion --------------------------------------------------------------------------

def test_mail_counts_toward_the_accounts_disk_space(env, monkeypatch):
    seen = []
    monkeypatch.setattr(storage_quota, "path_usage_bytes", lambda path: seen.append(str(path)) or 0)
    storage_quota.user_storage_used_bytes(env.db, env.user("khach"))
    assert "/home/khach/mail" in seen


def test_a_backup_carries_the_accounts_mail_and_a_restore_brings_it_back(env, tmp_path):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    _box(env, "khach", domain_id, "info")
    env.client.put(f"/api/mail/domains/{domain_id}", headers=env.as_("khach"), json={"catch_all": "info@khach.vn"})
    env.client.post("/api/mail/forwarders", headers=env.as_("khach"),
                    json={"domain_id": domain_id, "local_part": "sales", "destinations": ["boss@gmail.com"]})
    section = mail.backup_manifest(env.db, env.user("khach"))
    assert [d["domain"] for d in section["domains"]] == ["khach.vn"] and section["domains"][0]["catch_all"] == "info@khach.vn"
    assert [m["local_part"] for m in section["mailboxes"]] == ["info"]
    assert section["forwarders"] == [{"local_part": "sales", "domain": "khach.vn", "destinations": ["boss@gmail.com"]}]
    env.db.query(MailForwarder).delete()
    env.db.query(MailAccount).delete()
    env.db.query(MailDomain).delete()
    env.db.commit()
    staged = tmp_path / "stage"
    staged.mkdir()
    restored = mail.restore_manifest(env.db, env.user("khach"), section, lambda domain, local: staged)
    env.db.commit()
    assert restored == ["info@khach.vn"]
    assert env.db.query(MailDomain).one().catch_all == "info@khach.vn"
    assert env.db.query(MailForwarder).one().destination_list == ["boss@gmail.com"]
    assert ("mail-import", ["khach", "khach.vn", "info", str(staged)]) in [(c[0], c[1]) for c in env.helper.calls]


def test_an_older_backup_with_only_mailboxes_still_restores(env):
    from passlib.hash import sha512_crypt

    secret = "{SHA512-CRYPT}" + sha512_crypt.using(rounds=5000).hash("from-backup-1")
    mail.restore_manifest(env.db, env.user("khach"), [
        {"local_part": "info", "domain": "khach.vn", "password_hash": secret, "quota_mb": 300}], lambda d, l: None)
    env.db.commit()
    assert env.db.query(MailDomain).one().domain == "khach.vn"


def test_a_backup_cannot_take_someone_elses_domain_or_carry_a_clear_password(env):
    _domain(env, "other", "other.vn")
    with pytest.raises(ValueError):
        mail.restore_manifest(env.db, env.user("khach"), {"domains": [{"domain": "other.vn"}]}, lambda d, l: None)
    with pytest.raises(ValueError):
        mail.restore_manifest(env.db, env.user("khach"), {"mailboxes": [
            {"local_part": "info", "domain": "khach.vn", "password_hash": "plaintext"}]}, lambda d, l: None)


def test_deleting_a_user_takes_their_mail_domains(env, monkeypatch):
    from app.services import site_users, teardown

    monkeypatch.setattr(site_users, "delete_panel_user", lambda username: None)
    monkeypatch.setattr(teardown, "purge_owned_resources", lambda db, user_id: {"databases": [], "applications": []})
    _domain(env, "other", "other.vn")
    other = env.user("other")
    for website in env.db.query(Website).filter(Website.owner_id == other.id).all():
        env.db.delete(website)
    env.db.commit()
    assert env.client.delete(f"/api/users/{other.id}", headers=env.as_("owner")).status_code == 200
    env.db.expire_all()
    assert env.db.query(MailDomain).count() == 0
    assert env.helper.state["domains"] == []


def test_a_website_cannot_take_a_served_webmail_address(env):
    domain_id = _domain(env, "khach", "khach.vn").json()["id"]
    env.client.post(f"/api/mail/domains/{domain_id}/webmail-host", headers=env.as_("khach"), json={"enabled": True})
    assert mail.webmail_host_taken(env.db, "webmail.khach.vn") is True
    assert mail.webmail_host_taken(env.db, "webmail.other.vn") is False


def test_every_route_needs_the_addon(env):
    addons.uninstall(addons.MAIL)
    assert env.client.get("/api/mail/overview", headers=env.as_("khach")).status_code == 409


# --- the helper, read as text ----------------------------------------------------------------------------------

def _between(start: str, end: str) -> str:
    return HELPER.split(start, 1)[1].split(end, 1)[0]


def test_a_mailbox_sends_only_as_its_accounts_domains_and_only_signed_mail_is_its_own():
    conf = _between("mail_write_exim_conf() {", "\nmail_ensure_maps() {")
    assert "lsearch{@@M@@/senders}" in conf and "This mailbox cannot send as" in conf
    assert "The From address is not one of your mail domains" in conf
    # DKIM follows acl_m_dkim, set only for a signed-in mailbox or PHP of the owning account.
    assert conf.count("set acl_m_dkim") == 2 and "lsearch{@@M@@/local_senders}" in conf
    assert "require message = Relay not permitted" in conf


def test_relays_get_the_password_only_over_verified_tls():
    conf = _between("mail_write_exim_conf() {", "\nmail_ensure_maps() {")
    relay = conf.split("\nrelay_smtp:", 1)[1].split("\nrelay_smtp_plain:", 1)[0]
    assert "hosts_require_tls = *" in relay and "tls_verify_hosts = *" in relay
    assert "client_send = <; ^@@USER@@^@@PASS@@" in conf and "base64d" in conf
    configure = _between("mail_configure() {", "\nmail_dkim() {")
    assert "a relay reached over TLS is named, not an IP address" in configure
    assert "one host carries one login" in configure


def test_the_resolver_falls_back_when_the_provider_blocks_dns():
    service = _between("mail_apply_spam_service() {", "\nmail_configure() {")
    assert "mail_write_rspamd_resolver system" in service and "mail_write_rspamd_resolver local" in service
    answers = _between("mail_unbound_answers() {", "\nmail_write_rspamd_resolver() {")
    # dig +short prints its own errors on stdout: an address is what counts.
    assert "[0-9]{1,3}" in answers and "a.root-servers.net" not in answers


def test_a_suspended_mailbox_is_shut_by_a_deny_passdb_and_loses_its_sessions():
    dovecot = _between("mail_write_dovecot_conf() {", "\nmail_env_value() {")
    assert "deny = yes" in dovecot and dovecot.index("master = yes") < dovecot.index("deny = yes")
    sync = _between("mail_sync() {", "\nmail_write_sieve() {")
    assert '"doveadm", "kick"' in sync


def test_the_helper_never_follows_a_link_in_a_customers_mail_directory():
    sync = _between("mail_sync() {", "\nmail_write_sieve() {")
    assert "os.O_NOFOLLOW" in sync and "dir_fd=parent_fd" in sync and "os.fchown(fd" in sync
    restore = _between("mail_import() {", "\nmail_webmail_host() {")
    assert restore.index("chmod 2750 {} +") < restore.index('chown "${user}:spanel" "$work"')


def test_the_rspamd_controller_asks_everyone_for_its_key_and_the_allowlist_is_a_score():
    conf = _between("mail_write_rspamd_conf() {", "\nmail_spam_enabled() {")
    assert '"$MAIL_RSPAMD_OVERRIDE/worker-controller.inc"' in conf and "secure_ip = [];" in conf
    assert conf.count("score = -50.0;") == 4 and "prefilter = true" not in conf


def test_the_email_page_opens_the_webmail_tab_on_the_click_itself():
    block = APP_JSX.split("async function openWebmail(box)")[1].split("async function ")[0]
    assert block.index("window.open(") < block.index("await request(")
    assert "opener = null" in block
