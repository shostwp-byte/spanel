"""Per-domain TLS: mail.<domain> with its own certificate, answered by SNI.

Exim, Dovecot and port 2096 only ever had the panel's certificate, while the
MX was published as mail.<domain>: no mail app or MTA-STS sender could see
a certificate that matched the name it connected to.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mail_fixtures import db, helper_function, load_tool, mail_env, make_domain, make_user, \
    panel_settings_file  # noqa: F401 - fixtures

tool = load_tool()


# --- the helper's checks and renderers -------------------------------------------------------

def test_mail_hosts_request_is_checked():
    item = tool.check_mail_hosts({"domain": "example.com", "hosts": ["autodiscover", "mail", "mta-sts", "mail"],
                                  "email": "admin@example.net"})
    assert item["names"] == ["mail.example.com", "mta-sts.example.com", "autodiscover.example.com"]
    assert item["cert_name"] == "mail.example.com"
    for bad in ({"domain": "example.com", "hosts": ["mta-sts"]},
                {"domain": "example.com", "hosts": ["mail", "www"]},
                {"domain": "example.com", "hosts": []},
                {"domain": "exa mple.com", "hosts": ["mail"]},
                {"domain": "example.com", "hosts": ["mail"], "email": "x; rm -rf /"},
                ["mail"]):
        with pytest.raises(tool.ToolError):
            tool.check_mail_hosts(bad)


def test_nginx_blocks_before_and_after_the_certificate():
    names = ["mail.example.com", "autoconfig.example.com"]
    http = tool.render_mailhosts_nginx("example.com", names, "mail.example.com", "/var/www/spanel-mailhosts/example.com", False)
    assert "server_name mail.example.com autoconfig.example.com;" in http
    assert "root /var/www/spanel-acme;" in http
    assert "listen 443" not in http
    https = tool.render_mailhosts_nginx("example.com", names, "mail.example.com", "/var/www/spanel-mailhosts/example.com", True)
    assert "return 301 https://$host$request_uri;" in https
    assert "ssl_certificate /etc/letsencrypt/live/mail.example.com/fullchain.pem;" in https
    assert "root /var/www/spanel-mailhosts/example.com;" in https
    with pytest.raises(tool.ToolError):
        tool.render_mailhosts_nginx("example.com", ["mail.other.test"], "mail.example.com", "/var/www/x", True)
    with pytest.raises(tool.ToolError):
        tool.render_mailhosts_nginx("example.com", names, "mail.example.com", "/var/www/../etc", True)


def test_sni_maps_and_dovecot_blocks():
    certs, keys, dovecot = tool.render_sni({"mail.b.test": "mail.b.test", "mail.a.test": "mail.a.test"}, "/etc/spanel-mail-tls")
    assert certs == ["mail.a.test: /etc/spanel-mail-tls/mail.a.test/fullchain.pem",
                     "mail.b.test: /etc/spanel-mail-tls/mail.b.test/fullchain.pem"]
    assert keys[0] == "mail.a.test: /etc/spanel-mail-tls/mail.a.test/privkey.pem"
    assert "local_name mail.a.test {\n  ssl_cert = </etc/spanel-mail-tls/mail.a.test/fullchain.pem\n" in dovecot
    assert "  ssl_key = </etc/spanel-mail-tls/mail.a.test/privkey.pem\n}" in dovecot
    with pytest.raises(tool.ToolError):
        tool.render_sni({"mail.a.test }\nssl = no": "x"}, "/etc/spanel-mail-tls")
    assert tool.parse_tls_hosts("mail.a.test: mail.a.test\njunk\n../x: y\n") == {"mail.a.test": "mail.a.test"}


def test_sni_apply_copies_certificates_and_writes_maps(tmp_path):
    live, tls, exim = tmp_path / "live", tmp_path / "tls", tmp_path / "exim"
    for path in (live / "mail.a.test", tls, exim):
        path.mkdir(parents=True)
    (live / "mail.a.test" / "fullchain.pem").write_text("CERT")
    (live / "mail.a.test" / "privkey.pem").write_text("KEY")
    registry = exim / "tls_hosts"
    registry.write_text("mail.a.test: mail.a.test\nmail.gone.test: mail.gone.test\n")
    dovecot = tmp_path / "sni.conf"
    args = (str(registry), str(live), str(tls), str(exim), str(dovecot))
    assert tool.sni_apply(*args, owned=False) is True
    assert (tls / "mail.a.test" / "privkey.pem").read_text() == "KEY"
    # A host whose certificate is missing is left out, not half set up.
    assert (exim / "tls_certs").read_text() == f"mail.a.test: {tls}/mail.a.test/fullchain.pem\n"
    assert "local_name mail.a.test" in dovecot.read_text() and "gone" not in dovecot.read_text()
    assert tool.sni_apply(*args, owned=False) is False
    (live / "mail.a.test" / "fullchain.pem").write_text("RENEWED")
    assert tool.sni_apply(*args, owned=False) is True
    assert (tls / "mail.a.test" / "fullchain.pem").read_text() == "RENEWED"


def test_exim_and_dovecot_pick_the_certificate_by_sni():
    conf = helper_function("mail_write_exim_conf")
    assert "tls_certificate = ${lookup{${lc:$tls_in_sni}}lsearch{@@M@@/tls_certs}{$value}{@@TLS@@/fullchain.pem}}" in conf
    assert "tls_privatekey = ${lookup{${lc:$tls_in_sni}}lsearch{@@M@@/tls_keys}{$value}{@@TLS@@/privkey.pem}}" in conf
    assert "!include_try ${MAIL_DOVECOT_DIR}/sni.conf" in helper_function("mail_write_dovecot_conf")
    script = helper_function("mail_write_tls_script")
    assert "sni-apply /etc/exim4/spanel/tls_hosts" in script
    apply = helper_function("mail_hosts_apply")
    # Renewals go through the same copy.
    assert '--deploy-hook "$MAIL_TLS_SCRIPT"' in apply
    assert 'mail_hosts_register "mail.${domain}" "$cert_name"' in apply


# --- the panel ---------------------------------------------------------------------------------

def test_serving_the_mail_hosts(db, mail_env, monkeypatch):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner)
    sent = []
    monkeypatch.setattr(mail, "_helper", lambda verb, *args, **kw: sent.append((verb, args, kw.get("input"))) or SimpleNamespace(stdout="", stderr="", returncode=0))
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: ["192.0.2.10"] if not name.startswith("autodiscover.") else ["203.0.113.9"])
    assert mail.client_host(row) == "panel.example.net"

    with pytest.raises(mail.MailError, match="Point autodiscover.example.com at this server first"):
        mail.set_mail_hosts(db, owner, row.id, ["mta-sts", "autodiscover"])
    assert sent == []

    mail.set_mail_hosts(db, owner, row.id, ["mta-sts"])
    verb, args, payload = sent[-1]
    assert verb == "mail-hosts-apply"
    assert json.loads(payload) == {"domain": "example.com", "hosts": ["mail", "mta-sts"],
                                   "email": mail.app_settings.ssl_email or "", "mta_sts": None}
    # What the panel sends, the helper's check accepts.
    assert tool.check_mail_hosts(json.loads(payload))["names"] == ["mail.example.com", "mta-sts.example.com"]
    assert row.mail_hosts == "mail,mta-sts"
    assert mail.client_host(row) == "mail.example.com"
    assert mail.client_settings(row)["host"] == "mail.example.com"

    mail.remove_mail_hosts(db, owner, row.id)
    assert sent[-1][:2] == ("mail-hosts-remove", ("example.com",))
    assert row.mail_hosts == ""


def test_a_website_of_that_name_is_not_taken_over(db, mail_env, monkeypatch):
    from app.models.entities import Website
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner)
    db.add(Website(domain="mail.example.com", owner_id=owner.id, root_path="/home/x", php_version="8.3"))
    db.commit()
    try:
        with pytest.raises(mail.MailError, match="already a website"):
            mail.set_mail_hosts(db, owner, row.id, ["mail"])
    finally:
        db.query(Website).delete()
        db.commit()


def test_host_records_are_only_published_once_served(db, mail_env, monkeypatch):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner)
    monkeypatch.setattr(mail, "server_ip", lambda: "192.0.2.10")
    keys = {record["key"]: record for record in mail.dns_records(row)}
    assert keys["mta-sts"]["host"] == "mta-sts" and keys["mta-sts"]["optional"] is True
    assert mail.served_hosts(row) == []
    row.mail_hosts = "mail,autoconfig,bogus"
    assert mail.served_hosts(row) == ["mail", "autoconfig"]
