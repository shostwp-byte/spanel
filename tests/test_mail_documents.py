"""MTA-STS, TLS-RPT, Thunderbird autoconfig, Outlook autodiscover and the
SRV records mail apps look for.

None existed: client settings were a page to read, and nothing told other
mail servers to insist on TLS or where to report when it failed.
"""
import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from mail_fixtures import db, load_tool, mail_env, make_domain, make_user, panel_settings_file  # noqa: F401

tool = load_tool()


# --- documents -------------------------------------------------------------------------

def test_mta_sts_policy_is_rfc_8461_text():
    assert tool.render_mta_sts_policy("enforce", ["mail.example.com"], 604800) == (
        "version: STSv1\r\nmode: enforce\r\nmx: mail.example.com\r\nmax_age: 604800\r\n")
    for args in (("block", ["mail.example.com"], 604800), ("testing", [], 604800),
                 ("testing", ["mail.example.com"], 60), ("testing", ["*.x\nmode: none"], 86400)):
        with pytest.raises(tool.ToolError):
            tool.render_mta_sts_policy(*args)


def test_the_policy_names_only_the_panels_mx():
    item = tool.check_mail_hosts({"domain": "example.com", "hosts": ["mail", "mta-sts"],
                                  "mta_sts": {"mode": "testing", "mx": ["evil.test"], "max_age": 86400}})
    assert item["mta_sts"] == {"mode": "testing", "mx": ["mail.example.com"], "max_age": 86400}
    with pytest.raises(tool.ToolError):
        tool.check_mail_hosts({"domain": "example.com", "hosts": ["mail"], "mta_sts": {"mode": "off"}})


NS_AD = "{http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a}"


def test_autoconfig_document():
    root = ET.fromstring(tool.render_autoconfig("example.com", "mail.example.com"))
    provider = root.find("emailProvider")
    assert provider.get("id") == "example.com"
    servers = {(node.tag, node.get("type"), node.findtext("port")): node for node in provider
               if node.tag in ("incomingServer", "outgoingServer")}
    assert set(servers) == {("incomingServer", "imap", "993"), ("incomingServer", "pop3", "995"),
                            ("outgoingServer", "smtp", "465"), ("outgoingServer", "smtp", "587")}
    for node in servers.values():
        assert node.findtext("hostname") == "mail.example.com"
        assert node.findtext("username") == "%EMAILADDRESS%"
    assert servers[("outgoingServer", "smtp", "587")].findtext("socketType") == "STARTTLS"
    with pytest.raises(tool.ToolError):
        tool.render_autoconfig("example.com", "<script>")


def test_autodiscover_document():
    root = ET.fromstring(tool.render_autodiscover("example.com", "mail.example.com"))
    protocols = {node.findtext(f"{NS_AD}Type"): node for node in root.iter(f"{NS_AD}Protocol")}
    assert set(protocols) == {"IMAP", "SMTP"}
    assert protocols["IMAP"].findtext(f"{NS_AD}Server") == "mail.example.com"
    assert protocols["IMAP"].findtext(f"{NS_AD}Port") == "993"
    assert protocols["SMTP"].findtext(f"{NS_AD}Port") == "465"
    assert protocols["SMTP"].findtext(f"{NS_AD}SSL") == "on"


def test_only_the_documents_of_hosts_that_are_on(tmp_path):
    item = tool.check_mail_hosts({"domain": "example.com", "hosts": ["mail", "autoconfig"],
                                  "mta_sts": {"mode": "testing", "max_age": 86400}})
    assert set(tool.mailhost_documents(item)) == {"config-v1.1.xml"}
    (tmp_path / "autodiscover.xml").write_text("old")
    assert tool.write_mailhost_documents(str(tmp_path), item) == ["config-v1.1.xml"]
    assert not (tmp_path / "autodiscover.xml").exists()
    item = tool.check_mail_hosts({"domain": "example.com", "hosts": ["mail", "mta-sts", "autodiscover"],
                                  "mta_sts": {"mode": "enforce", "max_age": 604800}})
    assert tool.write_mailhost_documents(str(tmp_path), item) == ["autodiscover.xml", "mta-sts.txt"]
    assert (tmp_path / "mta-sts.txt").read_bytes().startswith(b"version: STSv1\r\nmode: enforce\r\n")
    assert not (tmp_path / "config-v1.1.xml").exists()


def test_nginx_serves_the_documents_and_answers_outlooks_post():
    https = tool.render_mailhosts_nginx("example.com", ["mail.example.com", "mta-sts.example.com"], "mail.example.com",
                                        "/var/www/spanel-mailhosts/example.com", True)
    assert "location = /.well-known/mta-sts.txt { try_files /mta-sts.txt =404; }" in https
    assert "location = /.well-known/autoconfig/mail/config-v1.1.xml {" in https
    assert "location ~* ^/autodiscover/autodiscover\\.xml$ {" in https
    assert "error_page 405 =200 $uri;" in https


# --- the panel: records and settings ---------------------------------------------------------

def _records(row):
    from app.services import mail

    return {record["key"]: record for record in mail.dns_records(row)}


def test_srv_records_follow_the_client_host(db, mail_env, monkeypatch):
    from app.services import mail

    monkeypatch.setattr(mail, "server_ip", lambda: "192.0.2.10")
    owner = make_user(db)
    row = make_domain(db, owner)
    records = _records(row)
    assert records["srv-imaps"]["name"] == "_imaps._tcp.example.com"
    assert records["srv-imaps"]["value"] == "1 993 panel.example.net"
    assert records["srv-submission"]["value"] == "1 587 panel.example.net"
    assert records["srv-autodiscover"]["host"] == "autodiscover"
    assert "mta-sts-txt" not in records and "tls-rpt" not in records
    row.mail_hosts = "mail,mta-sts,autodiscover"
    assert _records(row)["srv-submissions"]["value"] == "1 465 mail.example.com"


def test_mta_sts_needs_its_hosts_and_publishes_an_id(db, mail_env, monkeypatch):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner)
    applied = []
    monkeypatch.setattr(mail, "_helper", lambda verb, *a, **kw: applied.append((verb, kw.get("input"))) or SimpleNamespace(stdout="", stderr="", returncode=0))
    with pytest.raises(mail.MailError, match="Serve mail.example.com and mta-sts.example.com first"):
        mail.set_mta_sts(db, owner, row.id, "testing", [])
    row.mail_hosts = "mail,mta-sts"
    db.commit()
    mail.set_mta_sts(db, owner, row.id, "testing", ["tls@reports.test"])
    assert row.mta_sts_mode == "testing" and len(row.mta_sts_id) == 14
    payload = json.loads(applied[-1][1])
    assert payload["mta_sts"] == {"mode": "testing", "max_age": mail.MTA_STS_MAX_AGE}
    records = _records(row)
    assert records["mta-sts-txt"]["value"] == f"v=STSv1; id={row.mta_sts_id}"
    assert records["mta-sts-txt"]["name"] == "_mta-sts.example.com"
    assert records["tls-rpt"] == {"key": "tls-rpt", "type": "TXT", "name": "_smtp._tls.example.com",
                                  "value": "v=TLSRPTv1; rua=mailto:tls@reports.test"}
    # A TLS-RPT address change alone needs no new policy.
    first_id = row.mta_sts_id
    mail.set_mta_sts(db, owner, row.id, "testing", [])
    assert row.mta_sts_id == first_id and "tls-rpt" not in _records(row)
    with pytest.raises(mail.MailError):
        mail.set_mta_sts(db, owner, row.id, "strict", [])

    mail.set_mta_sts(db, owner, row.id, "enforce", [])
    with pytest.raises(mail.MailError, match="Set MTA-STS to none"):
        mail.remove_mail_hosts(db, owner, row.id)


def test_srv_records_are_checked_and_published(db, mail_env, monkeypatch):
    from app.models.entities import DnsZone
    from app.services import dns, mail

    owner = make_user(db)
    row = make_domain(db, owner)
    monkeypatch.setattr(mail, "server_ip", lambda: "192.0.2.10")
    checked = {item["key"]: item["status"] for item in mail.check_dns(
        row, lambda name, rtype: ["0 1 993 panel.example.net."] if rtype == "SRV" and name.startswith("_imaps") else [])}
    assert checked["srv-imaps"] == "ok" and checked["srv-pop3s"] == "missing"

    db.add(DnsZone(name="example.com", owner_id=owner.id))
    db.commit()
    monkeypatch.setattr(dns, "active", lambda: True)
    zone = {"rrsets": [{"name": "_imaps._tcp.example.com.", "type": "SRV", "ttl": 3600,
                        "records": [{"content": "0 1 993 old.example.net."}]}]}
    patches = []
    monkeypatch.setattr(dns, "_zone_data", lambda name: zone)
    monkeypatch.setattr(dns, "settings", lambda: {"ttl": 3600})
    monkeypatch.setattr(dns, "_serial_follows_edits", lambda name, data: None)
    monkeypatch.setattr(dns, "_patch", lambda name, changes: patches.append(changes))
    mail.publish_dns(db, row, replace=False)
    written = {key: value for change in patches for key, value in change.items()}
    # The panel's SRV records follow it, the owner's old target included.
    assert written[("_imaps._tcp.example.com.", "SRV")][1] == ["0 1 993 panel.example.net."]
    assert written[("_submission._tcp.example.com.", "SRV")][1] == ["0 1 587 panel.example.net."]
    # Hosts that are not served are not published.
    assert ("_autodiscover._tcp.example.com.", "SRV") not in written
    assert ("autoconfig.example.com.", "A") not in written
