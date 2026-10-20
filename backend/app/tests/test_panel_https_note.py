"""What an update says about the panel's certificate (2026-09-28).

Seen on the new demo server: the installer got a Let's Encrypt certificate for
the panel's own hostname but did not record PANEL_SSL_MODE, so the first update
took that certificate for one the panel had not adopted yet. It copied it over
itself, recorded it as borrowed from a website, and printed "The panel now
answers over HTTPS only ... The certificate is self-signed" - on a panel that
was already on HTTPS with a real certificate.

These run update.sh's own functions in bash with the system calls stubbed out,
because the fault was in what the script does, not in how it reads.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
INSTALL = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
BASH = shutil.which("bash")

STUBS = r"""
set -u
env_get() { grep -m1 "^$1=" env | cut -d= -f2-; }
env_set() {
  if grep -q "^$1=" env; then sed -i "s#^$1=.*#$1=$2#" env; else echo "$1=$2" >> env; fi
}
log() { echo "LOG: $*"; }
install() { :; }
openssl() { return 0; }
chown() { :; }
chmod() { :; }
"""


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", UPDATE, re.M | re.S)
    assert match, f"{name} not found in update.sh"
    return match.group(0)


def _run(tmp_path: Path, env: dict, files: dict) -> dict:
    (tmp_path / "env").write_text("".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8")
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    script = STUBS + _function("ensure_panel_https") + _function("panel_https_note") + r"""
LETSENCRYPT_LIVE_DIR=./live
ensure_panel_https 2222 203.0.113.9
echo "SWITCHED=${PANEL_SWITCHED_TO_HTTPS:-}"
echo "MODE=$(env_get PANEL_SSL_MODE)"
if [[ -n "${PANEL_SWITCHED_TO_HTTPS:-}" ]]; then panel_https_note; fi
"""
    result = subprocess.run([BASH, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    return {
        "switched": re.search(r"^SWITCHED=(.*)$", out, re.M).group(1),
        "mode": re.search(r"^MODE=(.*)$", out, re.M).group(1),
        "text": out,
    }


needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")


@needs_bash
def test_a_fresh_lets_encrypt_install_is_recorded_not_switched(tmp_path):
    outcome = _run(tmp_path, {
        "PANEL_DOMAIN": "panel.example.test",
        "PANEL_SSL_CERT": "./etc/panel-fullchain.pem",
        "PANEL_SSL_KEY": "./etc/panel-privkey.pem",
        "PANEL_SSL_MODE": "",
    }, {
        "etc/panel-fullchain.pem": "CERT-A", "etc/panel-privkey.pem": "KEY-A",
        "live/panel.example.test/fullchain.pem": "CERT-A", "live/panel.example.test/privkey.pem": "KEY-A",
    })
    assert outcome["switched"] == ""
    assert outcome["mode"] == "letsencrypt"
    assert "self-signed" not in outcome["text"]
    assert "using it" not in outcome["text"]


@needs_bash
def test_moving_from_self_signed_to_the_domain_certificate_says_so_truthfully(tmp_path):
    outcome = _run(tmp_path, {
        "PANEL_DOMAIN": "panel.example.test",
        "PANEL_SSL_CERT": "./etc/selfsigned.pem",
        "PANEL_SSL_KEY": "./etc/selfsigned-key.pem",
        "PANEL_SSL_MODE": "selfsigned",
    }, {
        "etc/selfsigned.pem": "SELF", "etc/selfsigned-key.pem": "SELF-KEY",
        "live/panel.example.test/fullchain.pem": "CERT-B", "live/panel.example.test/privkey.pem": "KEY-B",
    })
    assert outcome["switched"] == "https://panel.example.test:2222"
    assert outcome["mode"] == "domain"
    assert "now uses the certificate of its domain" in outcome["text"]
    assert "real certificate" in outcome["text"]
    assert "self-signed" not in outcome["text"]
    assert "will not load" not in outcome["text"], "it was on HTTPS already"


@needs_bash
def test_a_panel_on_plain_http_gets_a_self_signed_certificate_and_is_told(tmp_path):
    outcome = _run(tmp_path, {"PANEL_DOMAIN": "", "PANEL_SSL_CERT": "", "PANEL_SSL_KEY": "", "PANEL_SSL_MODE": ""}, {})
    assert outcome["switched"] == "https://203.0.113.9:2222"
    assert outcome["mode"] == "selfsigned"
    assert "will not load" in outcome["text"]
    assert "self-signed" in outcome["text"]


@needs_bash
def test_a_panel_already_settled_is_left_alone(tmp_path):
    outcome = _run(tmp_path, {
        "PANEL_DOMAIN": "panel.example.test",
        "PANEL_SSL_CERT": "./etc/panel-fullchain.pem",
        "PANEL_SSL_KEY": "./etc/panel-privkey.pem",
        "PANEL_SSL_MODE": "letsencrypt",
    }, {
        "etc/panel-fullchain.pem": "CERT-A", "etc/panel-privkey.pem": "KEY-A",
        "live/panel.example.test/fullchain.pem": "CERT-A", "live/panel.example.test/privkey.pem": "KEY-A",
    })
    assert outcome["switched"] == ""
    assert outcome["mode"] == "letsencrypt"
    assert outcome["text"].count("\n") == 2, outcome["text"]


def test_the_installer_records_its_lets_encrypt_certificate():
    setup_ssl = INSTALL.split("setup_ssl() {")[1].split("\n}\n")[0]
    assert "certbot certonly" in setup_ssl
    assert "PANEL_SSL_MODE=letsencrypt" in setup_ssl


def test_the_closing_message_no_longer_calls_every_certificate_self_signed():
    closing = UPDATE.split("# --- Health check")[1]
    assert "self-signed" not in closing
    assert "panel_https_note" in closing
