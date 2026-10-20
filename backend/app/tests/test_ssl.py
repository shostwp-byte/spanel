from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services import nginx, ssl

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


def _cert_pair(domain="example.test", *, days=30, key=None, aliases=None):
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.now(timezone.utc)
    san_names = [x509.DNSName(domain)]
    for alias in aliases or []:
        san_names.append(x509.DNSName(alias))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def test_validate_manual_ssl_accepts_matching_cert_key_and_optional_ca():
    cert_pem, key_pem = _cert_pair()

    ssl.validate_manual_ssl("example.test", cert_pem, key_pem, cert_pem)


def test_validate_manual_ssl_accepts_alias_san():
    cert_pem, key_pem = _cert_pair("example.test", aliases=["www.example.test"])

    ssl.validate_manual_ssl("example.test", cert_pem, key_pem, aliases=["www.example.test"])


def test_validate_manual_ssl_rejects_mismatched_private_key():
    cert_pem, _key_pem = _cert_pair()
    _other_cert, other_key = _cert_pair("other.test")

    with pytest.raises(ValueError, match="private_key does not match"):
        ssl.validate_manual_ssl("example.test", cert_pem, other_key)


def test_validate_manual_ssl_rejects_wrong_domain():
    cert_pem, key_pem = _cert_pair("other.test")

    with pytest.raises(ValueError, match="CN/SAN"):
        ssl.validate_manual_ssl("example.test", cert_pem, key_pem)


def test_validate_manual_ssl_rejects_missing_alias_domain():
    cert_pem, key_pem = _cert_pair()

    with pytest.raises(ValueError, match="CN/SAN"):
        ssl.validate_manual_ssl("example.test", cert_pem, key_pem, aliases=["alias.example.test"])


def test_validate_manual_ssl_rejects_expired_certificate():
    cert_pem, key_pem = _cert_pair(days=-1)

    with pytest.raises(ValueError, match="expired"):
        ssl.validate_manual_ssl("example.test", cert_pem, key_pem)


def test_apply_manual_ssl_config_adds_https_server_and_ca():
    rendered = nginx.apply_manual_ssl_config(
        nginx.render_vhost(
        "example.test",
        "/home/bp_example_test/example.test",
        app_type="wordpress",
        php_version="8.3",
        ),
        "/etc/nginx/spanel/ssl/sites/example.test/cert.crt",
        "/etc/nginx/spanel/ssl/sites/example.test/privkey.key",
        "/etc/nginx/spanel/ssl/sites/example.test/ca.crt",
    )

    assert "return 301 https://$host$request_uri;" in rendered
    assert "listen 443 ssl http2;" in rendered
    assert "ssl_certificate /etc/nginx/spanel/ssl/sites/example.test/fullchain.crt;" in rendered
    assert "ssl_certificate_key /etc/nginx/spanel/ssl/sites/example.test/privkey.key;" in rendered
    assert "ssl_trusted_certificate /etc/nginx/spanel/ssl/sites/example.test/ca.crt;" in rendered


def test_install_manual_ssl_uses_helper_without_logging_key(monkeypatch):
    cert_pem, key_pem = _cert_pair()
    captured = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        captured["helper_command"] = helper_command
        captured["helper_args"] = helper_args
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)

    paths = ssl.install_manual_ssl("example.test", cert_pem, key_pem)

    assert paths == {
        "cert": "/etc/nginx/spanel/ssl/sites/example.test/cert.crt",
        "key": "/etc/nginx/spanel/ssl/sites/example.test/privkey.key",
        "ca": None,
    }
    assert captured["helper_command"] == "manual-ssl-install"
    assert captured["helper_args"] == ["example.test"]
    assert captured["kwargs"]["sensitive"] is True
    assert "PRIVATE KEY" in captured["kwargs"]["input"]


def test_cert_covers_matches_wildcard_and_exact_but_not_deeper():
    assert ssl.cert_covers(["example.test", "*.example.test"], "blog.example.test") is True
    assert ssl.cert_covers(["example.test", "*.example.test"], "example.test") is True
    assert ssl.cert_covers(["*.example.test"], "a.b.example.test") is False
    assert ssl.cert_covers(["shop.example.test"], "blog.example.test") is False


def test_wildcard_issue_sends_the_token_on_stdin_never_in_argv(monkeypatch):
    captured = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        captured.update(helper_command=helper_command, helper_args=helper_args, kwargs=kwargs)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)
    ssl.issue_wildcard_ssl("example.test", "cf-token-secret", email="admin@example.test")

    assert captured["helper_command"] == "cloudflare-ssl-issue"
    assert captured["helper_args"] == ["example.test", "admin@example.test"]
    assert captured["kwargs"]["input"] == "cf-token-secret"
    assert captured["kwargs"]["sensitive"] is True
    assert "cf-token-secret" not in str(captured["helper_args"])


def test_helper_builds_the_wildcard_arg_itself_and_locks_the_ini():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("cloudflare_ssl_issue() {", 1)[1].split("\nssl_cert_info", 1)[0]
    assert '-d "$zone" -d "*.${zone}"' in body      # the star is never from argv
    assert "chmod 0600" in body and "/etc/spanel/cloudflare/" in body
    assert "token=\"$(cat)\"" in body               # token arrives on stdin
    assert "cloudflare-ssl-issue)" in helper and "certbot-dns-cloudflare-install)" in helper


def test_certbot_calls_are_idempotent_when_the_cert_is_not_due_yet():
    # spanel-helper.sh runs under `set -euo pipefail`. `certbot certonly`
    # exits 1 for "certificate not yet due for renewal" - even with
    # --keep-until-expiring - so a second click on an SSL button (or a panel
    # reinstall) used to abort panel-ssl-install / certbot-issue before their
    # install/env_set/nginx steps ran, and the panel reported a working
    # certificate as a failure. Reproduced live: certbot 2.9.0 exits 1 there
    # even though the certificate file is present and fine.
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    panel_ssl_install = helper.split("\n  panel-ssl-install)", 1)[1].split("\n  # ---- certbot", 1)[0]
    assert "--keep-until-expiring" in panel_ssl_install
    assert "env_set PANEL_SSL_MODE letsencrypt" in panel_ssl_install
    assert '[[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]] || exit "$rc"' in panel_ssl_install

    certbot_issue = helper.split("\n  certbot-issue)", 1)[1].split("\n  certbot-renew)", 1)[0]
    assert "--keep-until-expiring" in certbot_issue
    assert '[[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]] || exit "$rc"' in certbot_issue
    # nginx always listens on www.<domain> too (see nginx._server_names) and
    # issue_ssl now always requests it - www DNS that does not point here yet
    # must not turn a working bare-domain issuance into a total failure.
    assert "--allow-subset-of-names" in certbot_issue


def test_the_nginx_installer_is_never_pointed_at_an_alias_or_redirect_domain(monkeypatch):
    # Asking `certbot install --nginx -d X` for a domain with no server block
    # of its own (a redirect-domain alias: spanel wires those up itself, see
    # nginx._append_certbot_redirect_vhosts, once the panel re-renders the
    # vhost) makes certbot's nginx plugin fall back to the first server block
    # it can find - the default_server tools vhost here - and clone it under
    # that name: wrong certificate, and phpMyAdmin exposed under an unrelated
    # domain. Reproduced live on a customer box, twice: once for a redirect
    # domain with no DNS here, once for one whose DNS got fixed and made it
    # into the certificate - the missing server block was the real cause
    # both times, not a missing SAN, so install --nginx now only ever
    # targets the primary domain (whose block already covers www too, from
    # the same server_name line - see nginx._server_names).
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    certbot_issue = helper.split("\n  certbot-issue)", 1)[1].split("\n  certbot-renew)", 1)[0]
    assert 'install_args=(install --nginx --cert-name "$domain" --non-interactive --redirect --expand -d "$domain")' in certbot_issue
    install_block = certbot_issue.split("install_args=(install --nginx", 1)[1].split("certbot \"${install_args[@]}\"", 1)[0]
    assert "domains[@]" not in install_block
    assert "for cert_domain" not in install_block


def test_reapplying_the_firewall_for_a_port_change_cannot_kill_the_caller():
    # allow_panel_port is called from panel-ssl-install / panel-url-set as a
    # best-effort side effect ("open this port too"), guarded by `|| true`.
    # That guard does nothing against a hard `exit` (firewall_apply ->
    # firewall_require_tools -> deny() -> exit) raised by a plain command in
    # the same shell - reproduced live on a box missing `ipset`: the whole
    # panel-ssl-install call died right there, after certbot and the cert
    # install had already succeeded. A subshell makes `exit` end only the
    # subshell, so `|| true` actually catches it.
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("\nallow_panel_port() {", 1)[1].split("\n}", 1)[0]
    assert "( firewall_apply ) >/dev/null 2>&1 || true" in body


def test_issue_ssl_passes_aliases_and_email(monkeypatch):
    captured = {}

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        captured["helper_command"] = helper_command
        captured["helper_args"] = helper_args
        captured["kwargs"] = kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)
    monkeypatch.setattr(ssl.settings, "ssl_email", "admin@example.test")

    result = ssl.issue_ssl("example.test", aliases=["www.example.test"])

    assert result.returncode == 0
    assert captured["helper_command"] == "certbot-issue"
    assert captured["helper_args"] == ["example.test", "www.example.test", "admin@example.test"]


def test_issue_ssl_adds_www_by_default(monkeypatch):
    # Adding a website https://minhtrungmedia.vn/ never asked for
    # www.minhtrungmedia.vn - nginx serves it anyway (every vhost listens on
    # www.<domain> automatically), so a visitor typing "www." got a hard TLS
    # mismatch instead of the site. Reported live on a customer's box.
    captured = {}
    monkeypatch.setattr(ssl.shell, "privileged", lambda helper_command, helper_args=None, **kw: (
        captured.update(helper_args=helper_args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    ))
    monkeypatch.setattr(ssl.settings, "ssl_email", "")

    ssl.issue_ssl("minhtrungmedia.vn")

    assert captured["helper_args"] == ["minhtrungmedia.vn", "www.minhtrungmedia.vn"]


def test_issue_ssl_does_not_double_up_www_already_given_as_an_alias(monkeypatch):
    captured = {}
    monkeypatch.setattr(ssl.shell, "privileged", lambda helper_command, helper_args=None, **kw: (
        captured.update(helper_args=helper_args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    ))
    monkeypatch.setattr(ssl.settings, "ssl_email", "")

    ssl.issue_ssl("example.test", aliases=["www.example.test", "shop.example.test"])

    assert captured["helper_args"] == ["example.test", "www.example.test", "shop.example.test"]


def test_issue_ssl_does_not_add_a_double_www_for_a_www_host(monkeypatch):
    captured = {}
    monkeypatch.setattr(ssl.shell, "privileged", lambda helper_command, helper_args=None, **kw: (
        captured.update(helper_args=helper_args) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    ))
    monkeypatch.setattr(ssl.settings, "ssl_email", "")

    ssl.issue_ssl("www.example.test")

    assert captured["helper_args"] == ["www.example.test"]
