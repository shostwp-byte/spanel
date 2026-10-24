"""The Exim configuration the helper writes, rendered for real.

mail_write_exim_conf's Python is run as the helper runs it (minus the
`exim4 -bV` check that needs Exim), so the SNI certificate lookup, the DKIM
selector map and the sending limits are checked in the file Exim would read.
"""
import json
import subprocess
import sys

from mail_fixtures import TOOL_PATH, helper_function


def _render(tmp_path, settings: dict) -> str:
    body = helper_function("mail_write_exim_conf")
    # The heredoc: from the line after <<'PY' to the closing PY.
    script = body.split("<<'PY'", 1)[1].split("\n", 1)[1].split("\nPY\n", 1)[0]
    (tmp_path / "render.py").write_text(script, encoding="utf-8")
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    out = tmp_path / "exim4.conf"
    subprocess.run([sys.executable, str(tmp_path / "render.py"), str(tmp_path / "settings.json"), "/etc/exim4/spanel",
                    "/etc/spanel-mail-tls", str(out), str(TOOL_PATH.parent)], check=True)
    return out.read_text(encoding="utf-8")


def test_sni_dkim_and_hold_in_the_rendered_configuration(tmp_path):
    conf = _render(tmp_path, {"hostname": "panel.example.net", "auth_rate_per_hour": 100, "local_rate_per_hour": 50,
                              "domain_rate_per_hour": 0, "auto_hold_on_rate_exceed": True})
    assert "primary_hostname = panel.example.net" in conf
    assert ("tls_certificate = ${lookup{${lc:$tls_in_sni}}lsearch{/etc/exim4/spanel/tls_certs}{$value}"
            "{/etc/spanel-mail-tls/fullchain.pem}}") in conf
    assert "dkim_selector = ${lookup{$acl_m_dkim}lsearch{/etc/exim4/spanel/dkim_selectors}{$value}{spanel}}" in conf
    assert "dkim_private_key = ${lookup{$acl_m_dkim}lsearch{/etc/exim4/spanel/dkim_domains}{/etc/exim4/spanel/dkim/$value.pem}{0}}" in conf
    data_acl = conf.split("acl_check_data:", 1)[1].split("acl_check_not_smtp:", 1)[0]
    assert data_acl.index("control = freeze/no_tell") < data_acl.index("accept  authenticated = *")
    assert "ratelimit = 200 / 1h / per_rcpt / readonly / $authenticated_id" in conf
    assert "ratelimit = 100 / 1h / per_mail / readonly / $sender_ident" in conf
    assert "@@" not in conf


def test_without_auto_hold_nothing_is_frozen(tmp_path):
    conf = _render(tmp_path, {"auth_rate_per_hour": 100, "local_rate_per_hour": 50, "auto_hold_on_rate_exceed": False})
    assert "freeze" not in conf
    assert "ratelimit = 100 / 1h / per_rcpt / $authenticated_id" in conf
    assert "@@" not in conf
