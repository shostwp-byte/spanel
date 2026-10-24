"""DMARC policy editor: p, sp, pct, rua/ruf, adkim/aspf, fo.

The record was hard-coded to p=quarantine with no reports; the owner had no
way to start at p=none, ask for reports or move to reject.
"""
import json

import pytest

from mail_fixtures import db, mail_env, make_domain, make_user, panel_settings_file  # noqa: F401 - fixtures


def test_default_record_is_unchanged():
    from app.services import mail

    assert mail.render_dmarc(mail.DMARC_DEFAULT_POLICY) == "v=DMARC1; p=quarantine; adkim=r; aspf=r"
    assert mail.render_dmarc(mail.DMARC_DEFAULT_POLICY) == mail.DEFAULT_DMARC


def test_every_field_is_rendered_in_order():
    from app.services import mail

    policy = mail.normalize_dmarc({"p": "reject", "sp": "quarantine", "pct": "50", "rua": "mailto:Agg@Example.com, x@reports.test",
                                   "ruf": ["fail@example.com"], "adkim": "s", "aspf": "S", "fo": "1:d"})
    assert mail.render_dmarc(policy) == (
        "v=DMARC1; p=reject; sp=quarantine; pct=50; rua=mailto:agg@example.com,mailto:x@reports.test; "
        "ruf=mailto:fail@example.com; adkim=s; aspf=s; fo=1:d")


def test_parse_and_render_round_trip():
    from app.services import mail

    for text in ("v=DMARC1; p=none; rua=mailto:a@example.com; adkim=r; aspf=r",
                 "v=DMARC1; p=reject; sp=none; pct=10; adkim=s; aspf=r; fo=0:s"):
        assert mail.render_dmarc(mail.parse_dmarc(text)) == text
    with pytest.raises(mail.MailError):
        mail.parse_dmarc("v=spf1 a mx ~all")


@pytest.mark.parametrize("payload", [
    {"p": "block"},
    {"p": "none", "sp": "maybe"},
    {"p": "none", "pct": 101},
    {"p": "none", "pct": "x"},
    {"p": "none", "adkim": "x"},
    {"p": "none", "fo": "2"},
    {"p": "none", "fo": "1;rua=mailto:x@y.z"},
    {"p": "none", "rua": ["not an address"]},
    {"p": "none", "rua": ["a@b.example; p=none"]},
    {"p": "none", "rua": [f"r{n}@example.com" for n in range(6)]},
])
def test_bad_policies_are_refused(payload):
    from app.services import mail

    with pytest.raises(mail.MailError):
        mail.normalize_dmarc(payload)


def test_saved_policy_is_the_record_and_the_check(db, mail_env):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner)
    mail.set_dmarc(db, owner, row.id, {"p": "none", "rua": ["dmarc@reports.test"]})
    record = next(item for item in mail.dns_records(row) if item["key"] == "dmarc")
    assert record["value"] == "v=DMARC1; p=none; rua=mailto:dmarc@reports.test; adkim=r; aspf=r"
    view = mail.dmarc_view(row)
    assert view["custom"] is True
    assert view["external"] == [{"domain": "reports.test", "name": "example.com._report._dmarc.reports.test",
                                 "value": "v=DMARC1"}]

    def lookup(answer):
        return lambda name, rtype: answer if rtype == "TXT" else []

    # With a saved policy, an older DMARC record in DNS is "different".
    checked = {item["key"]: item["status"] for item in mail.check_dns(row, lookup(["v=DMARC1; p=quarantine"]))}
    assert checked["dmarc"] == "different"
    checked = {item["key"]: item["status"] for item in mail.check_dns(row, lookup([record["value"]]))}
    assert checked["dmarc"] == "ok"

    # Back to the default: any DMARC record will do again.
    mail.set_dmarc(db, owner, row.id, None)
    assert row.dmarc_policy == ""
    checked = {item["key"]: item["status"] for item in mail.check_dns(row, lookup(["v=DMARC1; p=quarantine"]))}
    assert checked["dmarc"] == "ok"


def test_publishing_replaces_the_dmarc_record_in_the_zone(db, mail_env, monkeypatch):
    from app.models.entities import DnsZone
    from app.services import dns, mail

    owner = make_user(db)
    row = make_domain(db, owner)
    db.add(DnsZone(name="example.com", owner_id=owner.id))
    db.commit()
    monkeypatch.setattr(dns, "active", lambda: True)
    monkeypatch.setattr(mail, "server_ip", lambda: "192.0.2.10")
    monkeypatch.setattr(mail, "server_addresses", lambda: (["192.0.2.10"], []))
    zone = {"rrsets": [{"name": "_dmarc.example.com.", "type": "TXT", "ttl": 3600,
                        "records": [{"content": '"v=DMARC1; p=quarantine; adkim=r; aspf=r"'}]}]}
    patches = []
    monkeypatch.setattr(dns, "_zone_data", lambda name: zone)
    monkeypatch.setattr(dns, "settings", lambda: {"ttl": 3600})
    monkeypatch.setattr(dns, "_serial_follows_edits", lambda name, data: None)
    monkeypatch.setattr(dns, "_patch", lambda name, changes: patches.append(changes))
    mail.set_dmarc(db, owner, row.id, {"p": "reject"})
    written = {key: value for change in patches for key, value in change.items()}
    assert written[("_dmarc.example.com.", "TXT")][1] == ['"v=DMARC1; p=reject; adkim=r; aspf=r"']


def test_backup_carries_the_policy(db, mail_env, monkeypatch):
    from types import SimpleNamespace

    from app.models.entities import MailDomain
    from app.services import mail

    monkeypatch.setattr(mail.shell, "privileged", lambda *a, **k: SimpleNamespace(returncode=0, stdout="{}", stderr=""))
    owner = make_user(db)
    row = make_domain(db, owner)
    mail.set_dmarc(db, owner, row.id, {"p": "reject", "pct": 20})
    manifest = json.loads(json.dumps(mail.backup_manifest(db, owner)))
    db.query(MailDomain).delete()
    db.commit()
    mail.restore_manifest(db, owner, manifest, lambda domain, local: None)
    restored = db.query(MailDomain).one()
    assert mail.render_dmarc(mail.dmarc_policy(restored)) == "v=DMARC1; p=reject; pct=20; adkim=r; aspf=r"
