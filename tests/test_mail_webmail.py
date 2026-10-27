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


def test_helper_locks_mail_install_and_repairs_broken_webmail_venv():
    install_body = helper_function("install_mail")
    remove_body = helper_function("remove_mail")
    webmail_body = helper_function("mail_install_webmail")
    packages_body = helper_function("mail_install_packages")

    # Concurrent mail-install / mail-uninstall runs are serialized via flock
    assert "/run/spanel-mail-install.lock" in install_body
    assert "flock -w 300 9" in install_body
    assert "/run/spanel-mail-install.lock" in remove_body

    # Broken/interrupted venv with missing pip is detected and recreated cleanly
    assert 'python3-pip' in packages_body
    assert '! -x "$WEBMAIL_HOME/venv/bin/pip"' in webmail_body
    assert 'rm -rf "$WEBMAIL_HOME/venv"' in webmail_body
    assert '"$WEBMAIL_HOME/venv/bin/python" -m pip install' in webmail_body

    # Bundled SPanel Webmail is deployed instead of cloning spanel.git into webmail
    assert "WEBMAIL_BUNDLED_DIR" in webmail_body
    assert 'install -m 0755 -o root -g root "${bundled_src}/main.py" "$WEBMAIL_HOME/src/backend/main.py"' in webmail_body


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
