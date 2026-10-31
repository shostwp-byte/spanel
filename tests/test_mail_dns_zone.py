"""DNS Manager: more record types, BIND zone files in and out, secondary
nameservers and per-package templates.

Only A, AAAA, CNAME, MX, TXT, NS, SRV and CAA could be edited; a zone could
not be moved in or out as a file; PowerDNS refused every transfer.
"""
import pytest

from mail_fixtures import db, helper_function, make_user, panel_settings_file  # noqa: F401 - fixtures


class FakePdns:
    """PowerDNS's zone API, enough for records(), import and export."""

    def __init__(self, zone, rrsets):
        self.zone = zone
        self.rrsets = {(r["name"], r["type"]): r for r in rrsets}
        self.metadata = {}
        self.calls = []

    def data(self, name):
        return {"name": self.zone + ".", "kind": "Native", "soa_edit_api": "DEFAULT",
                "rrsets": [dict(r) for r in self.rrsets.values()]}

    def patch(self, name, changes):
        for (rname, rtype), (ttl, contents) in changes.items():
            if contents:
                self.rrsets[(rname, rtype)] = {"name": rname, "type": rtype, "ttl": ttl,
                                               "records": [{"content": c, "disabled": False} for c in contents]}
            else:
                self.rrsets.pop((rname, rtype), None)

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if "/metadata/" in path:
            kind = path.rsplit("/", 1)[1]
            if method == "GET":
                return {"kind": kind, "metadata": self.metadata.get(kind, [])}
            if method == "PUT":
                self.metadata[kind] = body["metadata"]
            if method == "DELETE":
                self.metadata.pop(kind, None)
        return {}


@pytest.fixture
def pdns(monkeypatch, panel_settings_file):
    from app.services import dns

    fake = FakePdns("example.com", [
        {"name": "example.com.", "type": "SOA", "ttl": 3600,
         "records": [{"content": "ns1.panel.test. hostmaster.example.com. 2026102401 10800 3600 604800 3600", "disabled": False}]},
        {"name": "example.com.", "type": "NS", "ttl": 3600,
         "records": [{"content": "ns1.panel.test.", "disabled": False}, {"content": "ns2.panel.test.", "disabled": False}]},
        {"name": "example.com.", "type": "A", "ttl": 3600, "records": [{"content": "192.0.2.10", "disabled": False}]},
        {"name": "example.com.", "type": "MX", "ttl": 3600, "records": [{"content": "10 mail.example.com.", "disabled": False}]},
        {"name": "example.com.", "type": "TXT", "ttl": 3600, "records": [{"content": '"v=spf1 mx -all"', "disabled": False}]},
        {"name": "www.example.com.", "type": "CNAME", "ttl": 300, "records": [{"content": "example.com.", "disabled": False}]},
    ])
    monkeypatch.setattr(dns, "_zone_data", fake.data)
    monkeypatch.setattr(dns, "_patch", fake.patch)
    monkeypatch.setattr(dns, "_request", fake.request)
    monkeypatch.setattr(dns, "_serial_follows_edits", lambda zone, data: None)
    return fake


# --- record types -----------------------------------------------------------------------------

TLSA_HASH = "a" * 64


@pytest.mark.parametrize("zone, record, content", [
    ("example.com", {"name": "_443._tcp.www", "type": "TLSA", "content": f"3 1 1 {TLSA_HASH.upper()}"},
     f"3 1 1 {TLSA_HASH}"),
    ("example.com", {"name": "host", "type": "SSHFP", "content": "4 2 " + "b" * 64}, "4 2 " + "b" * 64),
    ("example.com", {"name": "sub", "type": "DS", "content": "12345 13 2 " + "c" * 64}, "12345 13 2 " + "c" * 64),
    ("example.com", {"name": "@", "type": "NAPTR", "content": '100 10 "s" "SIP+D2U" "" _sip._udp.example.com'},
     '100 10 "S" "SIP+D2U" "" _sip._udp.example.com.'),
    ("example.com", {"name": "@", "type": "HTTPS", "priority": 1, "content": ". alpn=h3,h2 ipv4hint=192.0.2.1 port=443"},
     "1 . alpn=h3,h2 ipv4hint=192.0.2.1 port=443"),
    ("example.com", {"name": "_api", "type": "SVCB", "priority": 0, "content": "svc.example.net"},
     "0 svc.example.net."),
    ("2.0.192.in-addr.arpa", {"name": "10", "type": "PTR", "content": "mail.example.com"}, "mail.example.com."),
    ("8.b.d.0.1.0.0.2.ip6.arpa", {"name": "1.0.0.0", "type": "PTR", "content": "host.example.com."}, "host.example.com."),
])
def test_new_types_are_written_as_powerdns_content(zone, record, content):
    from app.services import dns

    name, rtype, _, written = dns.to_pdns(zone, record)
    assert written == content
    # And read back into the same form.
    form = dns.to_form(zone, name, rtype, 3600, written)
    assert dns.to_pdns(zone, form)[3] == content


@pytest.mark.parametrize("zone, record", [
    ("example.com", {"type": "TLSA", "content": "4 1 1 " + "a" * 64}),
    ("example.com", {"type": "TLSA", "content": "3 1 1 abc"}),
    ("example.com", {"type": "TLSA", "content": "3 1 1 " + "g" * 64}),
    ("example.com", {"type": "SSHFP", "content": "5 2 " + "b" * 64}),
    ("example.com", {"type": "SSHFP", "content": "4 2 " + "b" * 40}),
    ("example.com", {"type": "DS", "name": "@", "content": "12345 13 2 " + "c" * 64}),
    ("example.com", {"type": "DS", "name": "sub", "content": "12345 13 3 " + "c" * 64}),
    ("example.com", {"type": "PTR", "name": "x", "content": "host.example.com"}),
    ("example.com", {"type": "NAPTR", "content": '100 10 "S" "SIP+D2U" "a b" x.example.com'}),
    ("example.com", {"type": "NAPTR", "content": '100 10 S SIP+D2U "" x.example.com'}),
    ("example.com", {"type": "HTTPS", "priority": 0, "content": ". alpn=h2"}),
    ("example.com", {"type": "HTTPS", "priority": 1, "content": ". alpn=h2 alpn=h3"}),
    ("example.com", {"type": "HTTPS", "priority": 1, "content": ". port=99999"}),
    ("example.com", {"type": "HTTPS", "priority": 1, "content": ". ipv4hint=2001:db8::1"}),
    ("example.com", {"type": "HTTPS", "priority": 1, "content": ". dohpath=/x"}),
    ("example.com", {"type": "LOC", "content": "x"}),
])
def test_bad_records_are_refused(zone, record):
    from app.services import dns

    with pytest.raises(dns.DnsInputError):
        dns.to_pdns(zone, {"name": "@", **record})


# --- zone files ---------------------------------------------------------------------------------

ZONE_FILE = """$ORIGIN example.com.
$TTL 1800
@       IN SOA ns1.other.test. admin.example.com. 1 7200 3600 1209600 3600
@       IN NS  ns1.other.test.
@       IN A   192.0.2.10
@       IN MX  20 backup.example.net.
@       IN TXT "v=spf1 mx -all"
blog    300 IN A 192.0.2.20
_443._tcp.www IN TLSA 3 1 1 {hash}
_sip._tcp IN SRV 10 5 5060 sip.example.com.
@       IN CAA 0 issue "letsencrypt.org"
host    IN HINFO "PC" "Linux"
""".format(hash=TLSA_HASH)


def test_import_preview_says_what_happens(pdns):
    from app.services import dns

    preview = dns.preview_import("example.com", ZONE_FILE, "merge", admin=True)
    actions = {(r["fqdn"], r["type"], r["action"]) for r in preview["records"]}
    assert ("example.com", "A", "exists") in actions
    assert ("example.com", "MX", "add") in actions
    assert ("blog.example.com", "A", "add") in actions
    assert ("_443._tcp.www.example.com", "TLSA", "add") in actions
    reasons = {(item["type"], item["reason"]) for item in preview["skipped"]}
    assert ("SOA", "kept by the DNS settings") in reasons and ("NS", "kept by the DNS settings") in reasons
    assert ("HINFO", "not a type DNS Manager edits") in reasons
    assert preview["errors"] == [] and preview["problem"] == ""
    replace = dns.preview_import("example.com", ZONE_FILE, "replace", admin=True)
    assert {(r["type"], r["content"]) for r in replace["removed"]} == {("MX", "mail.example.com")}


def test_import_merges_or_replaces(pdns):
    from app.services import dns

    dns.import_zone("example.com", ZONE_FILE, "merge", admin=True)
    mx = [r["content"] for r in pdns.rrsets[("example.com.", "MX")]["records"]]
    assert mx == ["10 mail.example.com.", "20 backup.example.net."]
    assert pdns.rrsets[("blog.example.com.", "A")]["ttl"] == 300
    assert pdns.rrsets[("example.com.", "NS")]["records"][0]["content"] == "ns1.panel.test."
    assert pdns.rrsets[("example.com.", "SOA")]["records"][0]["content"].startswith("ns1.panel.test.")
    dns.import_zone("example.com", ZONE_FILE, "replace", admin=True)
    assert [r["content"] for r in pdns.rrsets[("example.com.", "MX")]["records"]] == ["20 backup.example.net."]


def test_import_refuses_a_file_with_bad_records_or_cname_clashes(pdns):
    from app.services import dns

    with pytest.raises(dns.DnsInputError, match="cannot be imported"):
        dns.import_zone("example.com", f"@ IN DS 12345 13 2 {'c' * 64}\n", "merge", admin=True)
    with pytest.raises(dns.DnsInputError, match="cannot be imported|could not be read"):
        dns.import_zone("example.com", "bad IN TLSA 3 1 1 abcd\n", "merge", admin=True)
    with pytest.raises(dns.DnsInputError, match="CNAME beside other records"):
        dns.import_zone("example.com", "www IN A 192.0.2.30\n", "merge", admin=True)
    with pytest.raises(dns.DnsInputError, match="could not be read"):
        dns.import_zone("example.com", "@ IN A not-an-address\n", "merge", admin=True)
    with pytest.raises(dns.DnsInputError):
        dns.parse_zone_file("example.com", "$INCLUDE /etc/passwd\n")
    before = dict(pdns.rrsets)
    assert pdns.rrsets == before


def test_export_then_import_is_a_round_trip(pdns):
    from app.services import dns

    dns.import_zone("example.com", ZONE_FILE, "merge", admin=True)
    text = dns.export_zone("example.com")
    assert text.splitlines()[1] == "$ORIGIN example.com."
    assert "example.com.\t3600\tIN\tSOA\t" in text.splitlines()[3]
    snapshot = {key: sorted(item["content"] for item in value["records"]) for key, value in pdns.rrsets.items()}
    # Into an empty zone of the same name: the same record sets come back.
    pdns.rrsets = {key: value for key, value in pdns.rrsets.items() if key[1] in ("SOA", "NS") and key[0] == "example.com."}
    dns.import_zone("example.com", text, "merge", admin=True)
    restored = {key: sorted(item["content"] for item in value["records"]) for key, value in pdns.rrsets.items()}
    assert restored == snapshot


# --- secondaries and templates -------------------------------------------------------------------------

def test_secondaries_need_real_addresses():
    from app.services import dns

    assert dns.normalize_secondaries(["192.0.2.53", "192.0.2.53"], ["198.51.100.0/24", "2001:db8::53"]) == (
        ["192.0.2.53"], ["198.51.100.0/24", "2001:db8::53"])
    for notify, allow in ((["ns2.example.net"], []), ([], ["0.0.0.0/0"]), ([], ["::/0"]),
                          ([f"192.0.2.{n}" for n in range(11)], [])):
        with pytest.raises(dns.DnsInputError):
            dns.normalize_secondaries(notify, allow)


def test_secondaries_turn_transfers_on_and_make_the_zone_primary(pdns, monkeypatch):
    from types import SimpleNamespace

    from app.services import dns

    helper = []
    monkeypatch.setattr(dns.shell, "privileged", lambda verb, helper_args=None, **kw:
                        helper.append((verb, helper_args)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    view = dns.set_secondaries("example.com", ["192.0.2.53"], ["192.0.2.53"])
    assert helper == [("dns-transfers", ["on"])]
    assert view["also_notify"] == ["192.0.2.53"] and view["allow_axfr"] == ["192.0.2.53"]
    assert ("PUT", "/zones/example.com.", {"kind": "Master"}) in pdns.calls
    assert ("PUT", "/zones/example.com./notify", None) in pdns.calls
    # Once on, it stays on; clearing a zone makes it Native again.
    dns.set_secondaries("example.com", [], [])
    assert helper == [("dns-transfers", ["on"])]
    assert ("PUT", "/zones/example.com.", {"kind": "Native"}) in pdns.calls
    assert pdns.metadata == {}


def test_transfers_stay_off_in_pdns_conf_until_asked():
    transfer = helper_function("dns_transfer_lines")
    assert '"disable-axfr=yes"' in transfer
    assert '"allow-axfr-ips=127.0.0.1/32,::1/128" "primary=yes"' in transfer
    assert "the previous configuration is back" in helper_function("dns_transfers")


def test_a_package_can_have_its_own_template(db, panel_settings_file, monkeypatch):
    from app.models.entities import UserPackage
    from app.services import dns

    monkeypatch.setattr(dns, "spf_record", lambda: "v=spf1 mx ~all")
    package = UserPackage(name="mailt-pkg")
    db.add(package)
    db.commit()
    owner = make_user(db, "mailtpkg")
    owner.package_id = package.id
    db.commit()
    try:
        dns.save_settings(["ns1.panel.test", "ns2.panel.test"], "192.0.2.10", 3600, True,
                          package_templates={str(package.id): "@ A {ip}\nshop A {ip}"})
        assert "shop A {ip}" in dns.template_for_owner(db, owner.id)
        assert dns.template_for_owner(db, None) == dns.DEFAULT_TEMPLATE
        with pytest.raises(dns.DnsInputError, match="Package"):
            dns.save_settings(["ns1.panel.test", "ns2.panel.test"], "192.0.2.10", 3600, True,
                              package_templates={str(package.id): "@ BOGUS x"})
        # Saving without the field keeps them.
        dns.save_settings(["ns1.panel.test", "ns2.panel.test"], "192.0.2.10", 3600, True)
        assert str(package.id) in dns.settings()["package_templates"]
    finally:
        db.delete(owner)
        db.delete(package)
        db.commit()
