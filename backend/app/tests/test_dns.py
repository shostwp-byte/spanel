"""DNS Manager addon (operator, 2026-09-29: "Phát triển thêm addon DNS Manager").

Chosen with the operator: PowerDNS on the server itself, as DirectAdmin does;
administrators edit every zone and a customer the zones of their own websites;
a new website gets a zone, and the zone stays when the website goes.

PowerDNS is replaced here by a small fake that keeps RRsets the way its HTTP
API does, so what is tested is what the panel sends it.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.models.entities import DnsZone, User
from app.services import addons, dns, panel_settings, server_network

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")


class FakePowerDNS:
    """Just enough of PowerDNS's API: zones of RRsets, REPLACE and DELETE."""

    def __init__(self):
        self.zones: dict[str, list[dict]] = {}
        self.soa_edit_api: dict[str, str] = {}

    def __call__(self, method, path, body=None):
        if path == "/zones" and method == "GET":
            return [{"name": name} for name in self.zones]
        if path == "/zones" and method == "POST":
            if body["name"] in self.zones:
                raise dns.DnsError("Conflict", status=409)
            self.zones[body["name"]] = [dict(rrset) for rrset in body["rrsets"]]
            self.soa_edit_api[body["name"]] = body.get("soa_edit_api", "")
            return {}
        name = path.removeprefix("/zones/")
        if name not in self.zones:
            raise dns.DnsError("Could not find domain", status=404)
        if method == "GET":
            return {"name": name, "rrsets": self.zones[name], "soa_edit_api": self.soa_edit_api.get(name, "")}
        if method == "PUT":
            self.soa_edit_api[name] = body["soa_edit_api"]
            return {}
        if method == "DELETE":
            del self.zones[name]
            return {}
        if method == "PATCH":
            for change in body["rrsets"]:
                kept = [r for r in self.zones[name] if (r["name"], r["type"]) != (change["name"], change["type"])]
                if change["changetype"] == "REPLACE":
                    kept.append({"name": change["name"], "type": change["type"], "ttl": change["ttl"],
                                 "records": change["records"]})
                self.zones[name] = kept
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def rrset(self, zone, name, rtype):
        for rrset in self.zones[zone + "."]:
            if rrset["name"] == name and rrset["type"] == rtype:
                return [record["content"] for record in rrset["records"]]
        return None


@pytest.fixture
def env(monkeypatch, tmp_path):
    stored: dict = {"dns": {"nameservers": ["ns1.spanel.io", "ns2.spanel.io"], "zone_ip": "203.0.113.10",
                            "ttl": 3600, "auto_zone": True}}
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(stored))
    monkeypatch.setattr(panel_settings, "_read_raw_lenient", lambda: dict(stored))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: stored.update(data))
    monkeypatch.setattr(addons, "ADDONS_DIR", tmp_path)
    monkeypatch.setattr(addons, "ADDONS_FILE", tmp_path / "addons.json")
    monkeypatch.setattr(server_network, "ipv4_addresses", lambda: ["203.0.113.10"])
    monkeypatch.setattr(server_network, "ipv6_addresses", lambda: [])
    fake = FakePowerDNS()
    monkeypatch.setattr(dns, "_request", fake)
    monkeypatch.setattr(dns, "server_status", lambda: {"installed": True, "running": True, "api": True,
                                                          "port_open": True, "listen": ["127.0.0.1"]})
    addons.install(addons.DNS)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for name, role in (("owner", "admin"), ("khach", "end_user"), ("other", "end_user")):
        db.add(User(username=name, email=f"{name}@example.test", hashed_password=hash_password("pw-" + name),
                    role=role, is_active=True, token_version=0))
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

    yield SimpleNamespace(client=TestClient(app), db=db, fake=fake, user=user, as_=as_, stored=stored)
    app.dependency_overrides.pop(get_db, None)
    db.close()


# --- what a record becomes ---------------------------------------------------------

@pytest.mark.parametrize("record, expected", [
    ({"type": "A", "name": "@", "content": "203.0.113.5"}, ("example.com.", "A", "203.0.113.5")),
    ({"type": "AAAA", "name": "www", "content": "2001:0db8::0010"}, ("www.example.com.", "AAAA", "2001:db8::10")),
    ({"type": "CNAME", "name": "blog", "content": "example.com"}, ("blog.example.com.", "CNAME", "example.com.")),
    ({"type": "CNAME", "name": "shop", "content": "@"}, ("shop.example.com.", "CNAME", "example.com.")),
    ({"type": "MX", "name": "@", "content": "mail", "priority": 10}, ("example.com.", "MX", "10 mail.example.com.")),
    ({"type": "TXT", "name": "@", "content": 'v=spf1 a "x" ~all'}, ("example.com.", "TXT", '"v=spf1 a \\"x\\" ~all"')),
    ({"type": "SRV", "name": "_sip._tcp", "content": "5 5060 sip.example.net", "priority": 10},
     ("_sip._tcp.example.com.", "SRV", "10 5 5060 sip.example.net.")),
    ({"type": "CAA", "name": "@", "content": "0 issue letsencrypt.org"}, ("example.com.", "CAA", '0 issue "letsencrypt.org"')),
    ({"type": "A", "name": "*.shop", "content": "203.0.113.5"}, ("*.shop.example.com.", "A", "203.0.113.5")),
    ({"type": "A", "name": "www.example.com", "content": "203.0.113.5"}, ("www.example.com.", "A", "203.0.113.5")),
])
def test_a_record_as_typed_becomes_what_powerdns_stores(record, expected):
    name, rtype, _, content = dns.to_pdns("example.com", {"ttl": 3600, **record})
    assert (name, rtype, content) == expected


def test_a_long_txt_record_is_split_into_strings_of_255():
    key = "v=DKIM1; k=rsa; p=" + "A" * 400
    _, _, _, content = dns.to_pdns("example.com", {"type": "TXT", "name": "sel._domainkey", "content": key})
    assert content.count('"') == 4
    assert dns.to_form("example.com", "sel._domainkey.example.com.", "TXT", 3600, content)["content"] == key


@pytest.mark.parametrize("record, message", [
    ({"type": "A", "content": "300.1.1.1"}, "IPv4"),
    ({"type": "AAAA", "content": "203.0.113.5"}, "IPv6"),
    ({"type": "MX", "content": "mail.example.com", "priority": 70000}, "Priority"),
    ({"type": "SRV", "content": "sip.example.com", "priority": 1}, "weight, port and target"),
    ({"type": "CAA", "content": "0 steal example"}, "flags, tag and value"),
    ({"type": "TXT", "content": ""}, "some text"),
    ({"type": "PTR", "content": "x"}, "Choose a record type"),
    ({"type": "A", "name": "bad name", "content": "203.0.113.5"}, "record name"),
    ({"type": "A", "content": "203.0.113.5", "ttl": 5}, "TTL"),
])
def test_what_is_not_a_record_is_refused(record, message):
    with pytest.raises(dns.DnsInputError, match=message):
        dns.to_pdns("example.com", {"name": "@", "ttl": 3600, **record})


@pytest.mark.parametrize("zone", ["", "localhost", "-bad.com", "a..com", "exa mple.com"])
def test_a_zone_must_be_a_domain(zone):
    with pytest.raises(dns.DnsInputError):
        dns.normalize_zone(zone)


def test_a_vietnamese_domain_becomes_its_punycode():
    assert dns.normalize_zone("Tênmiền.VN").startswith("xn--")


# --- zones and records -------------------------------------------------------------

def test_a_new_zone_is_a_full_zone(env):
    dns.create_zone(env.db, "example.com", env.user("khach").id)
    assert env.fake.rrset("example.com", "example.com.", "NS") == ["ns1.spanel.io.", "ns2.spanel.io."]
    assert env.fake.rrset("example.com", "example.com.", "A") == ["203.0.113.10"]
    assert env.fake.rrset("example.com", "www.example.com.", "A") == ["203.0.113.10"]
    assert env.fake.rrset("example.com", "mail.example.com.", "A") == ["203.0.113.10"]
    assert env.fake.rrset("example.com", "example.com.", "MX") == ["10 mail.example.com."]
    # This server by name and by address (the same SPF the Email addon suggests).
    assert env.fake.rrset("example.com", "example.com.", "TXT") == ['"v=spf1 mx a ip4:203.0.113.10 ~all"']
    assert env.fake.rrset("example.com", "example.com.", "SOA")[0].startswith("ns1.spanel.io. hostmaster.example.com.")
    assert env.db.query(DnsZone).filter_by(name="example.com").one().owner_id == env.user("khach").id
    assert "SOA" not in {record["type"] for record in dns.records("example.com")}


def test_the_serial_counts_up_with_every_edit(env):
    """Seen on .88: INCEPTION-INCREMENT is accepted as SOA-EDIT-API and never
    applied, so the serial sat at 1. DEFAULT makes it YYYYMMDDnn and moves."""
    dns.create_zone(env.db, "example.com", None)
    assert env.fake.soa_edit_api["example.com."] == "DEFAULT"


def test_a_zone_made_before_the_fix_is_put_right_on_its_next_edit(env):
    dns.create_zone(env.db, "example.com", None)
    env.fake.soa_edit_api["example.com."] = "INCEPTION-INCREMENT"
    dns.add_record("example.com", {"name": "x", "type": "A", "content": "203.0.113.7"}, admin=True)
    assert env.fake.soa_edit_api["example.com."] == "DEFAULT"


def test_no_zone_before_the_nameservers_are_set(env):
    env.stored["dns"] = {"nameservers": [], "zone_ip": "203.0.113.10"}
    with pytest.raises(dns.DnsInputError, match="nameservers"):
        dns.create_zone(env.db, "example.com", None)


def test_records_are_added_changed_and_deleted_within_their_rrset(env):
    dns.create_zone(env.db, "example.com", None)
    dns.add_record("example.com", {"name": "@", "type": "A", "ttl": 3600, "content": "203.0.113.11"}, admin=True)
    assert env.fake.rrset("example.com", "example.com.", "A") == ["203.0.113.10", "203.0.113.11"]
    dns.update_record("example.com",
                      {"name": "@", "type": "A", "ttl": 3600, "content": "203.0.113.11"},
                      {"name": "@", "type": "A", "ttl": 600, "content": "203.0.113.12"}, admin=True)
    assert env.fake.rrset("example.com", "example.com.", "A") == ["203.0.113.10", "203.0.113.12"]
    dns.update_record("example.com",
                      {"name": "@", "type": "A", "ttl": 600, "content": "203.0.113.12"},
                      {"name": "api", "type": "A", "ttl": 600, "content": "203.0.113.12"}, admin=True)
    assert env.fake.rrset("example.com", "api.example.com.", "A") == ["203.0.113.12"]
    dns.delete_record("example.com", {"name": "api", "type": "A", "ttl": 600, "content": "203.0.113.12"}, admin=True)
    assert env.fake.rrset("example.com", "api.example.com.", "A") is None
    with pytest.raises(dns.DnsError):
        dns.delete_record("example.com", {"name": "api", "type": "A", "ttl": 600, "content": "203.0.113.12"}, admin=True)


def test_a_duplicate_is_refused(env):
    dns.create_zone(env.db, "example.com", None)
    with pytest.raises(dns.DnsInputError, match="already exists"):
        dns.add_record("example.com", {"name": "www", "type": "A", "ttl": 3600, "content": "203.0.113.10"}, admin=True)


def test_a_cname_stands_alone(env):
    dns.create_zone(env.db, "example.com", None)
    with pytest.raises(dns.DnsInputError, match="own name cannot be a CNAME"):
        dns.add_record("example.com", {"name": "@", "type": "CNAME", "content": "other.net"}, admin=True)
    with pytest.raises(dns.DnsInputError, match="no other records"):
        dns.add_record("example.com", {"name": "www", "type": "CNAME", "content": "other.net"}, admin=True)
    dns.add_record("example.com", {"name": "blog", "type": "CNAME", "content": "other.net"}, admin=True)
    with pytest.raises(dns.DnsInputError, match="no other records"):
        dns.add_record("example.com", {"name": "blog", "type": "TXT", "content": "hello"}, admin=True)
    dns.update_record("example.com", {"name": "blog", "type": "CNAME", "content": "other.net"},
                      {"name": "blog", "type": "CNAME", "content": "third.net"}, admin=True)
    assert env.fake.rrset("example.com", "blog.example.com.", "CNAME") == ["third.net."]


def test_a_customer_cannot_change_the_zones_own_nameservers(env):
    dns.create_zone(env.db, "example.com", None)
    with pytest.raises(dns.DnsInputError, match="administrator"):
        dns.add_record("example.com", {"name": "@", "type": "NS", "content": "ns.evil.net"}, admin=False)
    dns.add_record("example.com", {"name": "sub", "type": "NS", "content": "ns.other.net"}, admin=False)


# --- who sees what (through the real sign-in path) -----------------------------------

def test_the_routes_need_the_addon(env):
    addons.uninstall(addons.DNS)
    assert env.client.get("/api/dns/zones", headers=env.as_("owner")).status_code == 409


def test_a_customer_sees_and_edits_only_their_own_zones(env):
    dns.create_zone(env.db, "mine.vn", env.user("khach").id)
    dns.create_zone(env.db, "theirs.vn", env.user("other").id)
    listed = env.client.get("/api/dns/zones", headers=env.as_("khach")).json()
    assert [zone["name"] for zone in listed["zones"]] == ["mine.vn"]
    assert listed["can_manage"] is False
    assert env.client.get("/api/dns/zones/theirs.vn/records", headers=env.as_("khach")).status_code == 404
    refused = env.client.post("/api/dns/zones/theirs.vn/records", headers=env.as_("khach"),
                              json={"name": "x", "type": "A", "content": "203.0.113.9"})
    assert refused.status_code == 404
    added = env.client.post("/api/dns/zones/mine.vn/records", headers=env.as_("khach"),
                            json={"name": "x", "type": "A", "content": "203.0.113.9"})
    assert added.status_code == 200
    row = next(r for r in added.json()["records"] if r["name"] == "x")
    assert (row["type"], row["ttl"], row["content"], row["value"], row["priority"], row["locked"]) == (
        "A", 3600, "203.0.113.9", "203.0.113.9", None, False)
    assert row["fqdn"] == "x.mine.vn" and row["mail"] == ""


def test_only_an_administrator_creates_zones_and_changes_settings(env):
    assert env.client.post("/api/dns/zones", headers=env.as_("khach"), json={"name": "new.vn"}).status_code == 403
    assert env.client.get("/api/dns/settings", headers=env.as_("khach")).status_code == 403
    made = env.client.post("/api/dns/zones", headers=env.as_("owner"),
                           json={"name": "new.vn", "owner_id": env.user("khach").id})
    assert made.status_code == 200
    assert [zone["name"] for zone in env.client.get("/api/dns/zones", headers=env.as_("khach")).json()["zones"]] == ["new.vn"]


def test_a_zone_powerdns_serves_but_the_panel_did_not_make_is_shown_to_administrators_only(env):
    env.fake.zones["legacy.vn."] = []
    admin = [zone["name"] for zone in env.client.get("/api/dns/zones", headers=env.as_("owner")).json()["zones"]]
    customer = [zone["name"] for zone in env.client.get("/api/dns/zones", headers=env.as_("khach")).json()["zones"]]
    assert "legacy.vn" in admin and "legacy.vn" not in customer


def test_settings_need_two_nameservers_and_an_ipv4_address(env):
    headers = env.as_("owner")
    one = env.client.put("/api/dns/settings", headers=headers, json={"nameservers": ["ns1.spanel.io"], "zone_ip": "203.0.113.10"})
    assert one.status_code == 400 and "two nameservers" in one.json()["detail"]
    v6 = env.client.put("/api/dns/settings", headers=headers, json={"nameservers": ["ns1.a.vn", "ns2.a.vn"], "zone_ip": "2001:db8::1"})
    assert v6.status_code == 400
    saved = env.client.put("/api/dns/settings", headers=headers,
                           json={"nameservers": ["NS1.A.VN.", "ns2.a.vn"], "zone_ip": "203.0.113.20", "ttl": 600, "auto_zone": False})
    assert saved.status_code == 200
    assert saved.json()["nameservers"] == ["ns1.a.vn", "ns2.a.vn"] and saved.json()["auto_zone"] is False


# --- every domain on the server (operator, 2026-09-29) ----------------------------------

def _website(env, domain, owner, aliases=()):
    from app.models.entities import Website, WebsiteAlias

    site = Website(domain=domain, owner_id=env.user(owner).id, root_path=f"/home/{owner}/{domain}",
                   linux_user=owner, status="active")
    env.db.add(site)
    env.db.flush()
    for alias in aliases:
        env.db.add(WebsiteAlias(website_id=site.id, domain=alias, mode="alias"))
    env.db.commit()
    return site


def test_installing_the_addon_gives_every_domain_already_here_its_zone(env):
    _website(env, "shop.vn", "khach", aliases=["shop-alias.vn"])
    _website(env, "blog.shop.vn", "khach")
    _website(env, "other.vn", "other")
    summary = dns.sync(env.db)
    assert sorted(summary["zones"]) == ["other.vn", "shop-alias.vn", "shop.vn"]
    assert summary["records"] == ["blog.shop.vn"], "a subdomain of its owner's zone is a record in it"
    assert env.fake.rrset("shop.vn", "blog.shop.vn.", "A") == ["203.0.113.10"]
    owners = {row.name: row.owner_id for row in env.db.query(DnsZone)}
    assert owners["shop.vn"] == owners["shop-alias.vn"] == env.user("khach").id
    assert owners["other.vn"] == env.user("other").id
    again = dns.sync(env.db)
    assert again["zones"] == [] and again["records"] == [] and again["owners"] == []


def test_a_zone_belongs_to_whoever_owns_the_website(env):
    dns.create_zone(env.db, "shop.vn", None)
    _website(env, "shop.vn", "khach")
    assert dns.sync(env.db)["owners"] == ["shop.vn"]
    assert env.db.query(DnsZone).filter_by(name="shop.vn").one().owner_id == env.user("khach").id


def test_a_customer_edits_the_dns_of_every_domain_in_their_account(env):
    dns.create_zone(env.db, "shop.vn", env.user("other").id)
    _website(env, "shop.vn", "khach")
    assert dns.may_edit(env.db, env.user("khach"), "shop.vn") == "shop.vn"


def test_nothing_is_made_until_the_nameservers_and_address_are_set(env):
    _website(env, "shop.vn", "khach")
    env.stored["dns"] = {"nameservers": [], "zone_ip": "203.0.113.10"}
    summary = dns.sync(env.db)
    assert summary["ready"] is False and not env.fake.zones


def test_only_an_administrator_deletes_a_zone(env):
    dns.create_zone(env.db, "mine.vn", env.user("khach").id)
    assert env.client.delete("/api/dns/zones/mine.vn", headers=env.as_("khach")).status_code == 403
    assert env.client.delete("/api/dns/zones/mine.vn", headers=env.as_("owner")).status_code == 200


def test_the_sync_route_reports_what_it_did(env):
    _website(env, "shop.vn", "khach")
    done = env.client.post("/api/dns/sync", headers=env.as_("owner"))
    assert done.status_code == 200 and done.json()["zones"] == ["shop.vn"]
    assert env.client.post("/api/dns/sync", headers=env.as_("khach")).status_code == 403


@pytest.mark.parametrize("path, call", [
    ("backend/app/api/websites.py", "dns.zone_for_new_website(db, website)"),
    ("backend/app/api/websites.py", "dns.zone_for_new_domain(db, payload.domain, website.owner_id)"),
    ("backend/app/api/provisioning.py", "dns.zone_for_new_domain(db, payload.domain, user.id)"),
    ("backend/app/services/backup.py", "dns.sync_quietly(db)"),
    ("backend/app/services/da_import.py", "dns.sync_quietly(dns_db)"),
    ("backend/app/main.py", "dns.sync_quietly(db)"),
    ("backend/app/api/addons.py", "dns.sync_quietly(db)"),
])
def test_every_way_a_domain_arrives_gives_it_dns(path, call):
    assert call in (PROJECT_ROOT / path).read_text(encoding="utf-8")


# --- websites -------------------------------------------------------------------------

def _site(domain, owner):
    return SimpleNamespace(domain=domain, owner_id=owner.id)


def test_a_new_website_gets_its_zone(env):
    assert dns.zone_for_new_website(env.db, _site("shop.vn", env.user("khach"))) == "shop.vn"
    assert env.fake.rrset("shop.vn", "shop.vn.", "A") == ["203.0.113.10"]
    assert env.db.query(DnsZone).filter_by(name="shop.vn").one().owner_id == env.user("khach").id


def test_a_subdomain_of_your_own_zone_is_a_record_in_it(env):
    dns.zone_for_new_website(env.db, _site("shop.vn", env.user("khach")))
    assert dns.zone_for_new_website(env.db, _site("blog.shop.vn", env.user("khach"))) == "shop.vn"
    assert env.fake.rrset("shop.vn", "blog.shop.vn.", "A") == ["203.0.113.10"]
    assert "blog.shop.vn." not in env.fake.zones


def test_a_subdomain_of_someone_elses_zone_gets_its_own(env):
    dns.zone_for_new_website(env.db, _site("shop.vn", env.user("khach")))
    assert dns.zone_for_new_website(env.db, _site("blog.shop.vn", env.user("other"))) == "blog.shop.vn"
    assert env.fake.rrset("shop.vn", "blog.shop.vn.", "A") is None


def test_no_zone_while_the_addon_is_off_or_told_not_to(env):
    addons.uninstall(addons.DNS)
    assert dns.zone_for_new_website(env.db, _site("a.vn", env.user("khach"))) is None
    addons.install(addons.DNS)
    env.stored["dns"]["auto_zone"] = False
    assert dns.zone_for_new_website(env.db, _site("a.vn", env.user("khach"))) is None
    assert not env.fake.zones


def test_a_zone_powerdns_already_serves_is_not_claimed_by_a_website(env):
    env.fake.zones["taken.vn."] = []
    assert dns.zone_for_new_website(env.db, _site("taken.vn", env.user("khach"))) is None
    assert env.db.query(DnsZone).filter_by(name="taken.vn").first() is None


def test_a_broken_dns_server_never_stops_a_website(env, monkeypatch):
    def down(*args, **kwargs):
        raise dns.DnsError("The DNS server is not answering.")
    monkeypatch.setattr(dns, "_request", down)
    assert dns.zone_for_new_website(env.db, _site("a.vn", env.user("khach"))) is None


def test_the_website_route_asks_for_a_zone():
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "websites.py").read_text(encoding="utf-8")
    assert "dns.zone_for_new_website(db, website)" in source


# --- the helper -------------------------------------------------------------------------

def test_the_helper_installs_proves_and_removes_powerdns():
    install = HELPER.split("install_dns() {")[1].split("\n}\n")[0]
    assert "policy-rc.d" in install, "apt must not start PowerDNS on its stock settings"
    assert 'status: REFUSED' in install, "installed means it answers DNS, not just that it runs"
    assert "local-address=${listen}" in install and "0.0.0.0" not in install.split("cat >")[1]  # noqa: S104
    assert "webserver-address=127.0.0.1" in install
    remove = HELPER.split("remove_dns() {")[1].split("\n}\n")[0]
    assert "PDNS_DB" not in remove.replace("${PDNS_DB}.", ""), "removing the addon deletes no zone"
    for verb in ("dns-install)", "dns-remove)", "dns-status)"):
        assert verb in HELPER


def test_powerdns_is_installed_without_the_bind_backend_and_never_taken_over():
    """Seen on the demo server: apt added the recommended pdns-backend-bind,
    whose pdns.d/bind.conf stopped PowerDNS with 'unknown setting bind-config'."""
    install = HELPER.split("install_dns() {")[1].split("\n}\n")[0]
    assert "apt-get install -y --no-install-recommends pdns-server pdns-backend-sqlite3" in install
    assert "mv -f /etc/powerdns/pdns.d/bind.conf /etc/powerdns/pdns.d/bind.conf.spanel-disabled" in install
    assert 'systemctl is-active --quiet pdns 2>/dev/null && [[ ! -f "$PDNS_CONF" ]]' in install


def test_an_install_failure_reads_as_the_helpers_sentence():
    from app.api.addons import _install_failure

    failure = RuntimeError("Command failed: sudo -n spanel-helper dns-install\nRestarting services...\n"
                           "Job for pdns.service failed.\nspanel-helper: PowerDNS did not start")
    assert _install_failure(failure) == "PowerDNS did not start"


def test_port_53_opens_with_the_addon_and_closes_without_it():
    assert "done < <(cat \"$FIREWALL_ADDON_PORTS_DIR\"/*.ports 2>/dev/null || true)" in HELPER
    install = HELPER.split("install_dns() {")[1].split("\n}\n")[0]
    remove = HELPER.split("remove_dns() {")[1].split("\n}\n")[0]
    assert "printf '53 tcp\\n53 udp\\n' >\"$FIREWALL_ADDON_PORTS_DIR/dns.ports\"" in install
    assert 'rm -f "$FIREWALL_ADDON_PORTS_DIR/dns.ports"' in remove


# --- the template for new zones (operator, 2026-09-29: "Custom spf/dns mẫu") ------------------

def test_the_default_template_makes_the_zone_directadmin_would(env):
    zone = dns.create_zone(env.db, "mau.vn", None)
    assert env.fake.rrset(zone, "mau.vn.", "A") == ["203.0.113.10"]
    assert env.fake.rrset(zone, "www.mau.vn.", "A") == ["203.0.113.10"]
    assert env.fake.rrset(zone, "mail.mau.vn.", "A") == ["203.0.113.10"]
    assert env.fake.rrset(zone, "mau.vn.", "MX") == ["10 mail.mau.vn."]
    assert env.fake.rrset(zone, "mau.vn.", "TXT") == ['"v=spf1 mx a ip4:203.0.113.10 ~all"']


def test_a_template_of_ones_own_is_used_for_new_zones(env):
    template = "@ A {ip}\nshop CNAME {domain}\n@ MX 20 mx.provider.net\n@ TXT v=spf1 include:_spf.google.com ~all\n@ CAA 0 issue letsencrypt.org\n"
    response = env.client.put("/api/dns/settings", headers=env.as_("owner"), json={
        "nameservers": ["ns1.spanel.io", "ns2.spanel.io"], "zone_ip": "203.0.113.10", "ttl": 3600, "auto_zone": True,
        "template": template})
    assert response.status_code == 200 and response.json()["template"] == template
    zone = dns.create_zone(env.db, "rieng.vn", None)
    assert env.fake.rrset(zone, "shop.rieng.vn.", "CNAME") == ["rieng.vn."]
    assert env.fake.rrset(zone, "rieng.vn.", "MX") == ["20 mx.provider.net."]
    assert env.fake.rrset(zone, "rieng.vn.", "CAA") == ['0 issue "letsencrypt.org"']
    assert env.fake.rrset(zone, "www.rieng.vn.", "A") is None


def test_a_template_line_that_makes_no_record_is_refused_with_its_number(env):
    response = env.client.put("/api/dns/settings", headers=env.as_("owner"), json={
        "nameservers": ["ns1.spanel.io", "ns2.spanel.io"], "zone_ip": "203.0.113.10", "ttl": 3600, "auto_zone": True,
        "template": "@ A {ip}\n# a comment\nwww A not-an-ip\n"})
    assert response.status_code == 400
    assert response.json()["detail"].startswith("Line 3: An A record points at an IPv4 address")


def test_saving_the_settings_from_an_older_page_keeps_the_template(env):
    env.stored["dns"]["template"] = "@ A {ip}\n"
    response = env.client.put("/api/dns/settings", headers=env.as_("owner"), json={
        "nameservers": ["ns1.spanel.io", "ns2.spanel.io"], "zone_ip": "203.0.113.10", "ttl": 3600, "auto_zone": True})
    assert response.json()["template"] == "@ A {ip}\n"


def test_template_lines_needing_an_address_wait_for_one(env):
    records = dns.template_records(dns.DEFAULT_TEMPLATE, "x.vn", "", 3600)
    assert [(r["name"], r["type"]) for r in records] == [("@", "MX"), ("@", "TXT")]


# --- the DNS page, laid out like OPanel's ------------------------------------------------------------

def test_the_overview_gives_every_account_the_nameservers_and_default_ttl(env):
    view = env.client.get("/api/dns/overview", headers=env.as_("khach")).json()
    assert view["nameservers"] == ["ns1.spanel.io", "ns2.spanel.io"] and view["default_ttl"] == 3600
    assert view["is_admin"] is False and view["addresses"]["ipv4"][0] == "203.0.113.10"


def test_the_zone_list_is_searched_paged_and_says_what_left_the_panel(env):
    for name in ("khach.vn", "zzz-gone.vn", "other.vn"):
        dns.create_zone(env.db, name, env.user("owner").id)
    _website(env, "khach.vn", "khach")
    _website(env, "shop.other.vn", "owner")
    listed = env.client.get("/api/dns/zones?q=vn&per_page=2&page=1", headers=env.as_("owner")).json()
    assert listed["total"] == 3 and [z["name"] for z in listed["items"]] == ["khach.vn", "other.vn"]
    second = env.client.get("/api/dns/zones?q=vn&per_page=2&page=2", headers=env.as_("owner")).json()
    assert [(z["name"], z["on_panel"]) for z in second["items"]] == [("zzz-gone.vn", False)]
    assert listed["items"][0]["on_panel"] is True and listed["items"][0]["created_at"]
    assert [z["name"] for z in env.client.get("/api/dns/zones?q=other", headers=env.as_("owner")).json()["items"]] == ["other.vn"]


def test_restoring_puts_back_the_panels_records_and_keeps_the_owners(env):
    zone = dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    dns.delete_record(zone, {"name": "www", "type": "A", "content": "203.0.113.10"}, admin=False)
    dns.add_record(zone, {"name": "shop", "type": "CNAME", "content": "shops.example.net"}, admin=False)
    apex = "khach.vn."
    env.fake.zones[apex] = [r for r in env.fake.zones[apex] if not (r["name"] == apex and r["type"] == "NS")]
    restored = env.client.post("/api/dns/zones/khach.vn/defaults", headers=env.as_("khach"))
    assert restored.status_code == 200
    assert env.fake.rrset("khach.vn", "www.khach.vn.", "A") == ["203.0.113.10"]
    assert sorted(env.fake.rrset("khach.vn", apex, "NS")) == ["ns1.spanel.io.", "ns2.spanel.io."]
    assert env.fake.rrset("khach.vn", "shop.khach.vn.", "CNAME") == ["shops.example.net."]


def test_new_nameservers_go_into_every_zone(env):
    dns.create_zone(env.db, "khach.vn", env.user("khach").id)
    saved = env.client.put("/api/dns/settings", headers=env.as_("owner"), json={
        "nameservers": ["ns1.newhost.vn", "ns2.newhost.vn"], "zone_ip": "203.0.113.10", "ttl": 3600, "auto_zone": True})
    assert saved.status_code == 200, saved.text
    assert sorted(env.fake.rrset("khach.vn", "khach.vn.", "NS")) == ["ns1.newhost.vn.", "ns2.newhost.vn."]
    assert env.fake.rrset("khach.vn", "khach.vn.", "SOA")[0].startswith("ns1.newhost.vn. ")
