"""phpMyAdmin reaches remote MySQL hosts: one server entry per host, written
by the helper from validated tuples, and an SSO token that names the server."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from rdb_fakes import make_host, make_session, make_user

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
SIGNON = REPO_ROOT / "installer" / "files" / "phpmyadmin-spanel-signon.php"


def test_server_indexes_follow_host_ids_and_match_the_specs():
    from app.services import remote_mysql

    db = make_session()
    a = make_host(db, name="a", host="10.0.0.5")
    b = make_host(db, name="b", host="db2.example.com", port=3307, use_tls=True)
    c = make_host(db, name="c", host="2001:db8::5", use_tls=True, ca_cert="-----BEGIN CERTIFICATE-----\nx\n")
    assert [remote_mysql.phpmyadmin_server_index(db, h) for h in (None, a, b, c)] == [1, 2, 3, 4]
    assert remote_mysql.phpmyadmin_server_specs([c, a, b]) == [
        "2,10.0.0.5,3306,0,0", "3,db2.example.com,3307,1,0", "4,2001:db8::5,3306,1,1",
    ]


def test_sync_sends_ca_on_stdin_then_the_server_list(monkeypatch):
    from app.services import remote_mysql
    from app.services.shell import CommandResult

    db = make_session()
    make_host(db, name="a", host="10.0.0.5")
    make_host(db, name="b", host="10.0.0.6", use_tls=True, ca_cert="PEM-B")
    calls = []

    def fake_privileged(command, helper_args=None, check=True, input=None, sensitive=False, fallback=None, timeout=None):
        calls.append((command, list(helper_args or []), input))
        return CommandResult(command, 0, "", "")

    monkeypatch.setattr(remote_mysql.shell, "privileged", fake_privileged)
    assert remote_mysql.sync_phpmyadmin_servers(db) is True
    assert calls == [
        ("phpmyadmin-remote-ca", ["3"], "PEM-B"),
        ("phpmyadmin-remote-servers", ["2,10.0.0.5,3306,0,0", "3,10.0.0.6,3306,1,1"], None),
    ]


def test_sso_token_carries_the_server_index(monkeypatch, tmp_path):
    from app.api import databases as databases_api
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount
    from app.services import sso_tokens

    monkeypatch.setattr(sso_tokens, "TOKEN_DIR", tmp_path / "sso")
    monkeypatch.setattr(databases_api, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(databases_api.panel_urls, "tools_base_url", lambda request: "https://panel.example")
    db = make_session()
    user = make_user(db)
    make_host(db, name="first", host="10.0.0.4")
    host = make_host(db, name="second", host="10.0.0.5")
    remote = DatabaseAccount(owner_id=user.id, db_name="shop", db_user="shop", db_password=encrypt("pw"), db_host_id=host.id)
    local = DatabaseAccount(owner_id=user.id, db_name="blog", db_user="blog", db_password=encrypt("pw"))
    db.add_all([remote, local])
    db.commit()

    for account, expected in ((remote, 3), (local, 1)):
        out = databases_api.create_phpmyadmin_sso(account.id, request=SimpleNamespace(), db=db, current_user=user)
        token = out["url"].rsplit("spanel_sso=", 1)[1]
        data = sso_tokens.consume_phpmyadmin_token(token)
        assert data["server"] == expected and data["db_name"] == account.db_name

    with pytest.raises(ValueError):
        sso_tokens.create_phpmyadmin_token("u", "p", "d", server=0)


def test_signon_script_lets_each_server_keep_its_own_host():
    text = SIGNON.read_text(encoding="utf-8")
    assert "PMA_single_signon_host" not in text and "PMA_single_signon_port" not in text
    assert "index.php?server=' . $server" in text
    assert "$data['server'] >= 1 && $data['server'] <= 99" in text
    install = (REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    update = (REPO_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    assert 'files/phpmyadmin-spanel-signon.php" /usr/share/phpmyadmin/spanel-signon.php' in install
    assert 'files/phpmyadmin-spanel-signon.php" /usr/share/phpmyadmin/spanel-signon.php' in update
    assert "remote_mysql.sync_phpmyadmin_servers" in update


def _helper_functions() -> str:
    text = HELPER.read_text(encoding="utf-8")
    start = text.index('PMA_CONF_DIR="/etc/phpmyadmin/conf.d"')
    end = text.index('  echo "phpMyAdmin remote servers: $#"\n}\n', start) + len('  echo "phpMyAdmin remote servers: $#"\n}\n')
    return text[start:end]


def _run_helper(tmp_path, call: str, stdin: bytes = b""):
    conf = tmp_path / "conf.d"
    conf.mkdir(exist_ok=True)
    (conf / "spanel-signon.php").write_text("'secure' => true,\n", encoding="utf-8")
    script = "\n".join([
        "set -euo pipefail",
        'deny() { echo "DENY: $*" >&2; exit 1; }',
        "chown() { :; }",
        'install() { mkdir -p "${@: -1}"; }',
        _helper_functions(),
        f'PMA_CONF_DIR="{conf.as_posix()}"',
        'PMA_REMOTE_SERVERS_CONF="${PMA_CONF_DIR}/spanel-remote-servers.php"',
        f'PMA_REMOTE_CA_DIR="{(tmp_path / "ca").as_posix()}"',
        call,
    ])
    script_file = tmp_path / "run.sh"
    script_file.write_bytes(script.encode("utf-8"))
    return subprocess.run([shutil.which("bash"), script_file.as_posix()], input=stdin, capture_output=True)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_helper_writes_one_server_entry_per_host(tmp_path):
    (tmp_path / "ca").mkdir()
    (tmp_path / "ca" / "3.pem").write_text("pem", encoding="utf-8")
    (tmp_path / "ca" / "9.pem").write_text("stale", encoding="utf-8")
    out = _run_helper(tmp_path, "write_phpmyadmin_remote_servers 2,10.0.0.5,3306,0,0 3,db2.example.com,3307,1,1 4,2001:db8::5,3306,1,0")
    assert out.returncode == 0, out.stderr
    php = (tmp_path / "conf.d" / "spanel-remote-servers.php").read_text(encoding="utf-8")
    assert "$cfg['Servers'][2] = [" in php and "'host' => '10.0.0.5'," in php
    assert "'host' => 'db2.example.com'," in php and "'port' => '3307'," in php
    assert f"'ssl_ca' => '{(tmp_path / 'ca').as_posix()}/3.pem'," in php
    assert "'ssl_ca_path' => '/etc/ssl/certs'," in php
    assert php.count("'ssl_verify' => true,") == 2
    assert "'secure' => true," in php and "'auth_type' => 'signon'," in php
    assert not (tmp_path / "ca" / "9.pem").exists() and (tmp_path / "ca" / "3.pem").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("spec", [
    "2,10.0.0.5',3306,0,0", "1,10.0.0.5,3306,0,0", "2,-x,3306,0,0", "2,10.0.0.5,70000,0,0",
    "2,10.0.0.5,3306,2,0", "2,a b,3306,0,0", "2,10.0.0.5,3306,0,0,extra", "2,$(id),3306,0,0",
])
def test_helper_refuses_anything_but_the_tuple(tmp_path, spec):
    out = _run_helper(tmp_path, f"write_phpmyadmin_remote_servers '{spec}'" if "'" not in spec else f'write_phpmyadmin_remote_servers "{spec}"')
    assert out.returncode != 0 and b"DENY" in out.stderr
    assert not (tmp_path / "conf.d" / "spanel-remote-servers.php").exists()


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None, reason="bash/openssl not available")
def test_helper_keeps_only_certificates_from_the_ca_payload(tmp_path):
    from test_rdb_lifecycle import _self_signed_ca_pem

    pem = _self_signed_ca_pem()
    out = _run_helper(tmp_path, "write_phpmyadmin_remote_ca 2", stdin=(pem + "\nroot:x:0:0:secret\n").encode("ascii"))
    assert out.returncode == 0, out.stderr
    written = (tmp_path / "ca" / "2.pem").read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in written and "secret" not in written

    bad = _run_helper(tmp_path, "write_phpmyadmin_remote_ca 3", stdin=b"root:x:0:0:not a cert\n")
    assert bad.returncode != 0 and not (tmp_path / "ca" / "3.pem").exists()
