"""Tests for SPanel Webmail service and mail_install_webmail helper hardening."""

from __future__ import annotations

import email
import importlib.util
import time
from pathlib import Path
from fastapi.testclient import TestClient

from mail_fixtures import REPO_ROOT, helper_function

WEBMAIL_MAIN_PATH = REPO_ROOT / "installer" / "files" / "webmail" / "backend" / "main.py"


def load_webmail():
    spec = importlib.util.spec_from_file_location("spanel_webmail_main", WEBMAIL_MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeIMAP:
    """In-memory IMAP4 double for testing Webmail endpoints."""

    def __init__(self, auth_user: str, auth_pass: str):
        self.auth_user = auth_user
        self.auth_pass = auth_pass
        self.selected = "INBOX"
        self.stored: list[tuple[str, str, str]] = []
        self.copied: list[tuple[str, str]] = []
        self.appended: list[tuple[str, str, bytes]] = []
        raw_msg = (
            b"From: Nguyen Van A <sender@example.vn>\r\n"
            b"To: info@khach.vn\r\n"
            b"Subject: =?UTF-8?B?WMOhYyBuaOG6rW4gxJHGoW4gaMOgbmc=?=\r\n"
            b"Date: Tue, 28 Oct 2026 04:30:00 +0700\r\n"
            b"Message-ID: <msg-101@example.vn>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Xin chao, day la noi dung email thu nghiem.\r\n"
        )
        self.messages = {"101": (b"1 (UID 101 FLAGS () RFC822.SIZE 312)", raw_msg)}

    def select(self, mailbox='"INBOX"', readonly=False):
        self.selected = mailbox.strip('"')
        return "OK", [b"1"]

    def create(self, mailbox):
        return "OK", [b"Completed"]

    def subscribe(self, mailbox):
        return "OK", [b"Completed"]

    def status(self, mailbox, names):
        box = mailbox.strip('"')
        if box == "INBOX":
            return "OK", [b'"INBOX" (MESSAGES 1 UNSEEN 1)']
        return "OK", [f'"{box}" (MESSAGES 0 UNSEEN 0)'.encode()]

    def getquotaroot(self, mailbox):
        return "OK", [[b"INBOX Mailbox"], [b"Mailbox (STORAGE 2048 1048576)"]]

    def uid(self, command, *args):
        cmd = command.upper()
        if cmd == "SEARCH":
            if self.selected == "INBOX":
                return "OK", [b"101"]
            return "OK", [b""]
        if cmd == "FETCH":
            uid_set = str(args[0])
            results = []
            for u in uid_set.split(","):
                if u in self.messages:
                    results.append(self.messages[u])
            return "OK", results
        if cmd == "STORE":
            self.stored.append((str(args[0]), str(args[1]), str(args[2])))
            return "OK", [b"Completed"]
        if cmd == "COPY":
            self.copied.append((str(args[0]), str(args[1])))
            return "OK", [b"Completed"]
        return "OK", []

    def append(self, mailbox, flags, date_time, message):
        self.appended.append((mailbox, flags, message))
        return "OK", [b"APPEND completed"]

    def expunge(self):
        return "OK", [b"Completed"]

    def logout(self):
        return "BYE", [b"Logging out"]


def _find_bash() -> str | None:
    import os
    import shutil

    if os.name == "nt":
        gh_sh = Path(r"C:\Users\quang\AppData\Local\GitHubDesktop\app-3.6.10\resources\app\git\usr\bin\sh.exe")
        if gh_sh.is_file():
            return str(gh_sh)
    found = shutil.which("bash")
    if found and "windowsapps" not in found.lower() and "system32" not in found.lower():
        return found
    return shutil.which("sh") or found


def test_helper_locks_mail_install_and_repairs_broken_webmail_venv(tmp_path):
    import subprocess

    install_body = helper_function("install_mail")
    remove_body = helper_function("remove_mail")
    webmail_body = helper_function("mail_install_webmail")
    reconfig_body = helper_function("mail_reconfigure")
    packages_body = helper_function("mail_install_packages")

    # Concurrent mail-install / mail-uninstall runs are serialized via flock
    assert "/run/spanel-mail-install.lock" in install_body
    assert "flock -w 300 9" in install_body
    assert "/run/spanel-mail-install.lock" in remove_body

    # Broken/interrupted venv with missing pip is detected and recreated cleanly
    assert "python3-pip" in packages_body
    assert '! -x "$WEBMAIL_HOME/venv/bin/pip"' in webmail_body
    assert 'rm -rf "$WEBMAIL_HOME/venv"' in webmail_body
    assert "--only-binary :all:" in webmail_body

    # P0-A: WEBMAIL_REPO from .env and APP_DIR fallback are completely absent
    assert "WEBMAIL_REPO" not in webmail_body
    assert "git clone" not in webmail_body
    assert "${APP_DIR}" not in webmail_body
    assert "${APP_DIR}" not in reconfig_body

    # Execute mail_install_webmail in bash with a malicious WEBMAIL_REPO in ENV_FILE
    # and a planted payload in APP_DIR/installer/files/webmail/backend.
    bash_bin = _find_bash()
    assert bash_bin is not None, "bash/sh binary required to execute mail_install_webmail"

    env_file = tmp_path / ".env"
    env_file.write_text("WEBMAIL_REPO=https://evil.example/pwn.git\nWEBMAIL_REF=main\n", encoding="utf-8")

    app_dir = tmp_path / "app_dir"
    evil_backend = app_dir / "installer" / "files" / "webmail" / "backend"
    evil_backend.mkdir(parents=True)
    (evil_backend / "main.py").write_text("# EVIL APP_DIR PAYLOAD\n", encoding="utf-8")
    (evil_backend / "requirements.txt").write_text("evil-pkg==1.0.0\n", encoding="utf-8")

    bundled_dir = tmp_path / "bundled_webmail"
    trusted_backend = bundled_dir / "backend"
    trusted_backend.mkdir(parents=True)
    (trusted_backend / "main.py").write_text("# TRUSTED ROOT BUNDLED WEBMAIL\n", encoding="utf-8")
    (trusted_backend / "requirements.txt").write_text("fastapi==0.141.1\n", encoding="utf-8")

    webmail_home = tmp_path / "webmail_home"
    git_marker = tmp_path / "git_called"
    pip_args_file = tmp_path / "pip_args.txt"
    log_file = tmp_path / "mail-install.log"
    webmail_env = tmp_path / "SPanel-webmail.env"
    sso_key = tmp_path / "webmail-sso.key"
    unit_file = tmp_path / "spanel-webmail.service"
    dovecot_dir = tmp_path / "dovecot"
    dovecot_dir.mkdir(parents=True)
    etc_spanel = tmp_path / "etc_spanel"

    env_get_fn = helper_function("env_get")
    mail_env_val_fn = helper_function("mail_env_value")

    import textwrap

    harness = (
        textwrap.dedent(
            f"""
            export PATH="/usr/bin:/bin:$PATH"
            set -euo pipefail
            ENV_FILE={env_file.as_posix()!r}
            APP_DIR={app_dir.as_posix()!r}
            WEBMAIL_BUNDLED_DIR={bundled_dir.as_posix()!r}
            WEBMAIL_HOME={webmail_home.as_posix()!r}
            MAIL_INSTALL_LOG={log_file.as_posix()!r}
            WEBMAIL_ENV={webmail_env.as_posix()!r}
            WEBMAIL_SSO_KEY_FILE={sso_key.as_posix()!r}
            WEBMAIL_UNIT={unit_file.as_posix()!r}
            MAIL_DOVECOT_DIR={dovecot_dir.as_posix()!r}
            WEBMAIL_PORT=8096
            WEBMAIL_MASTER_USER="spanel-webmail"
            MAIL_MARKER="# Managed by SPanel (Email addon)."

            deny() {{ echo "DENY: $*" >&2; exit 42; }}
            id() {{ return 0; }}
            useradd() {{ return 0; }}
            chown() {{ return 0; }}
            stat() {{ echo "0"; }}
            openssl() {{ echo "deadbeefcafebabe0123456789abcdef"; }}
            systemctl() {{ return 0; }}
            git() {{ touch {git_marker.as_posix()!r}; exit 99; }}
            runuser() {{
              shift 2
              [[ "${{1:-}}" == "--" ]] && shift
              "$@"
            }}
            install() {{
              local dir_mode=0 args=() arg
              while [[ $# -gt 0 ]]; do
                case "$1" in
                  -d) dir_mode=1; shift ;;
                  -m|-o|-g) shift 2 ;;
                  *)
                    arg="$1"
                    [[ "$arg" == "/etc/spanel" ]] && arg={etc_spanel.as_posix()!r}
                    args+=("$arg")
                    shift
                    ;;
                esac
              done
              if [[ "$dir_mode" -eq 1 ]]; then
                mkdir -p "${{args[@]}}"
              else
                mkdir -p "$(dirname "${{args[1]}}")"
                if [[ "${{args[0]}}" == "/dev/null" ]]; then
                  : > "${{args[1]}}"
                else
                  cp "${{args[0]}}" "${{args[1]}}"
                fi
              fi
            }}
            python3() {{
              if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
                mkdir -p "$3/bin"
                cat >"$3/bin/python" <<'PYEOF'
            #!/bin/sh
            if [ "${{1:-}}" = "-m" ] && [ "${{2:-}}" = "pip" ] && [ "${{3:-}}" = "--version" ]; then
              echo "pip 24.0"
              exit 0
            fi
            if [ "${{1:-}}" = "-m" ] && [ "${{2:-}}" = "pip" ] && [ "${{3:-}}" = "install" ]; then
              printf '%s\\n' "$@" > {pip_args_file.as_posix()!r}
              exit 0
            fi
            exit 0
            PYEOF
                chmod +x "$3/bin/python" 2>/dev/null || true
                cp "$3/bin/python" "$3/bin/pip"
                chmod +x "$3/bin/pip" 2>/dev/null || true
              fi
            }}
            """
        )
        + "\n"
        + env_get_fn
        + "\n"
        + mail_env_val_fn
        + "\n"
        + webmail_body
        + "\nmail_install_webmail\n"
    ).replace("\r\n", "\n")
    script_path = tmp_path / "run_webmail_test.sh"
    script_path.write_bytes(harness.encode("utf-8"))
    res = subprocess.run([bash_bin, script_path.as_posix()], capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, f"mail_install_webmail failed: {res.stderr}\n{res.stdout}"
    assert not git_marker.exists(), "git must never be invoked even when WEBMAIL_REPO is set in .env"
    deployed_main = (webmail_home / "src" / "backend" / "main.py").read_text(encoding="utf-8")
    assert deployed_main == "# TRUSTED ROOT BUNDLED WEBMAIL\n"
    assert "--only-binary" in pip_args_file.read_text(encoding="utf-8")

    # Now remove both bundled_dir and deployed main.py so only APP_DIR has main.py:
    # mail_install_webmail must refuse (exit 42) and never copy from APP_DIR.
    import shutil as _shutil

    _shutil.rmtree(bundled_dir)
    _shutil.rmtree(webmail_home / "src")
    res_untrusted = subprocess.run([bash_bin, script_path.as_posix()], capture_output=True, text=True, timeout=15)
    assert res_untrusted.returncode == 42
    assert "bundled SPanel Webmail files not found" in res_untrusted.stderr
    assert not (webmail_home / "src" / "backend" / "main.py").exists()


def test_webmail_healthcheck_and_sso_flow(tmp_path, monkeypatch):
    from app.services import mail as panel_mail

    wm = load_webmail()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTH_SECRET", "test-auth-secret-key-1234567890")
    monkeypatch.setenv("SSO_SECRET", "shared-sso-secret-key-9876543210")
    monkeypatch.setenv("SSO_MASTER_USER", "spanel-webmail")
    monkeypatch.setenv("SSO_MASTER_PASSWORD", "master-pass-secret")
    monkeypatch.setenv("SSO_MASTER_SEPARATOR", "*")

    fake_imaps: list[FakeIMAP] = []

    def fake_open_imap(auth_user: str, auth_pass: str):
        inst = FakeIMAP(auth_user, auth_pass)
        fake_imaps.append(inst)
        return inst

    monkeypatch.setattr(wm, "_open_imap_with_creds", fake_open_imap)

    client = TestClient(wm.app)

    # 1. Healthcheck endpoint (/api/auth/me) returns 200 OK when unauthenticated
    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json() == {"authenticated": False, "email": None}

    # 2. SSO login with token generated by SPanel's mail.sso_token()
    token = panel_mail.sso_token("info@khach.vn", now=time.time(), key="shared-sso-secret-key-9876543210")
    r_sso = client.get(f"/api/auth/sso?token={token}", follow_redirects=False)
    assert r_sso.status_code == 302
    assert r_sso.headers["location"] == "/"
    assert fake_imaps[-1].auth_user == "info@khach.vn*spanel-webmail"
    assert fake_imaps[-1].auth_pass == "master-pass-secret"

    # Replaying the same SSO token is refused
    r_replay = client.get(f"/api/auth/sso?token={token}", follow_redirects=False)
    assert r_replay.status_code == 401

    # 3. Authenticated session works for /api/auth/me, /api/folders, /api/messages, and reading a message
    client.cookies.update(r_sso.cookies)
    r_me_auth = client.get("/api/auth/me")
    assert r_me_auth.status_code == 200
    assert r_me_auth.json()["authenticated"] is True
    assert r_me_auth.json()["email"] == "info@khach.vn"

    r_folders = client.get("/api/folders")
    assert r_folders.status_code == 200
    folder_map = {f["id"]: f for f in r_folders.json()["folders"]}
    assert folder_map["INBOX"]["total"] == 1 and folder_map["INBOX"]["unseen"] == 1
    assert r_folders.json()["quota"] == {"used_kb": 2048, "limit_kb": 1048576}

    r_list = client.get("/api/messages?folder=INBOX")
    assert r_list.status_code == 200
    items = r_list.json()["items"]
    assert len(items) == 1
    assert items[0]["uid"] == "101"
    assert items[0]["subject"] == "Xác nhận đơn hàng"
    assert items[0]["from_address"] == "sender@example.vn"

    r_detail = client.get("/api/messages/101?folder=INBOX")
    assert r_detail.status_code == 200
    detail = r_detail.json()
    assert "Xin chao, day la noi dung email thu nghiem." in detail["body_text"]
    assert detail["seen"] is True


def test_sso_session_invalidated_on_domain_reownership_and_mailbox_deletion(tmp_path, monkeypatch):
    import json
    from app.services import mail as panel_mail

    wm = load_webmail()
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTH_SECRET", "test-auth-secret-key-1234567890")
    monkeypatch.setenv("SSO_SECRET", "shared-sso-secret-key-9876543210")
    monkeypatch.setenv("SSO_MASTER_USER", "spanel-webmail")
    monkeypatch.setenv("SSO_MASTER_PASSWORD", "master-pass-secret")
    monkeypatch.setenv("SSO_MASTER_SEPARATOR", "*")

    monkeypatch.setattr(wm, "_open_imap_with_creds", lambda u, p: FakeIMAP(u, p))

    # Customer A signs in via SSO with mailbox generation "101:10:1730000000"
    client_a = TestClient(wm.app)
    token_a = panel_mail.sso_token(
        "info@khach.vn",
        now=time.time(),
        key="shared-sso-secret-key-9876543210",
        gen="101:10:1730000000",
    )
    r_sso_a = client_a.get(f"/api/auth/sso?token={token_a}", follow_redirects=False)
    assert r_sso_a.status_code == 302
    assert f"Max-Age={wm.MASTER_SESSION_TTL_SECONDS}" in r_sso_a.headers.get("set-cookie", "")
    assert wm.MASTER_SESSION_TTL_SECONDS <= 7200

    client_a.cookies.update(r_sso_a.cookies)
    assert client_a.get("/api/messages?folder=INBOX").status_code == 200

    # Customer A deletes domain, Customer B creates info@khach.vn (new generation "202:20:1730009999").
    # Even before Customer B opens webmail, mail-sync writes mailbox_generations.json:
    (data_dir / "mailbox_generations.json").write_text(
        json.dumps({"info@khach.vn": "202:20:1730009999"}),
        encoding="utf-8",
    )
    # Customer A's cookie is now rejected with 401 and its session file is deleted
    r_stale = client_a.get("/api/messages?folder=INBOX")
    assert r_stale.status_code == 401

    # Customer B signs in with the new generation and can read their mailbox
    client_b = TestClient(wm.app)
    token_b = panel_mail.sso_token(
        "info@khach.vn",
        now=time.time(),
        key="shared-sso-secret-key-9876543210",
        gen="202:20:1730009999",
    )
    r_sso_b = client_b.get(f"/api/auth/sso?token={token_b}", follow_redirects=False)
    assert r_sso_b.status_code == 302
    client_b.cookies.update(r_sso_b.cookies)
    assert client_b.get("/api/messages?folder=INBOX").status_code == 200

    # Deleting the domain/mailbox purges active sessions immediately
    removed = wm.purge_sessions_for_domain("khach.vn")
    assert removed == 1
    assert client_b.get("/api/messages?folder=INBOX").status_code == 401


def test_webmail_login_rate_limiting_by_ip_and_mailbox(tmp_path, monkeypatch):
    from fastapi import HTTPException

    wm = load_webmail()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTH_SECRET", "test-auth-secret-key-1234567890")

    imap_attempts: list[tuple[str, str]] = []

    def fake_open_imap(auth_user: str, auth_pass: str):
        imap_attempts.append((auth_user, auth_pass))
        if auth_pass != "correct-password-123":
            raise HTTPException(status_code=401, detail="Sai mật khẩu")
        return FakeIMAP(auth_user, auth_pass)

    monkeypatch.setattr(wm, "_open_imap_with_creds", fake_open_imap)
    client = TestClient(wm.app)

    attacker_headers = {"X-Forwarded-For": "203.0.113.50"}
    for _ in range(wm.LOGIN_MAX_FAILURES_PER_PAIR):
        r = client.post(
            "/api/auth/login",
            json={"email": "info@khach.vn", "password": "wrong"},
            headers=attacker_headers,
        )
        assert r.status_code == 401

    count_before = len(imap_attempts)
    r_blocked = client.post(
        "/api/auth/login",
        json={"email": "info@khach.vn", "password": "correct-password-123"},
        headers=attacker_headers,
    )
    assert r_blocked.status_code == 429
    assert len(imap_attempts) == count_before  # IMAP not even contacted while rate-limited

    # Legitimate user from another IP can still sign in
    owner_headers = {"X-Forwarded-For": "198.51.100.77"}
    r_ok = client.post(
        "/api/auth/login",
        json={"email": "info@khach.vn", "password": "correct-password-123"},
        headers=owner_headers,
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["authenticated"] is True
