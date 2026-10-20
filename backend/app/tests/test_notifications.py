"""The Notifications addon: who hears what, on which channel, and how often.

The operator asked for an addon with two channels, email over SMTP and
Telegram, and left the content to be worked out (2026-09-27): administrators
hear about the server and about their own account. The same day it became an
administrators' feature only: "Phần thông báo không dành cho end user nữa" -
customers are sent nothing and do not see the page.
"""
from datetime import UTC, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import LoginSource, NotificationLog, NotificationPref, User, Website
from app.services import addons, notifications, notify_messages, notify_watch

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    monkeypatch.setattr(notify_watch, "SessionLocal", Session)
    monkeypatch.setattr(notifications, "DATA_DIR", tmp_path)
    monkeypatch.setattr(notifications, "CONFIG_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(notify_watch, "STATE_FILE", tmp_path / "notify-state.json")
    monkeypatch.setattr(addons, "ADDONS_DIR", tmp_path)
    monkeypatch.setattr(addons, "ADDONS_FILE", tmp_path / "addons.json")
    monkeypatch.setattr(notifications, "server_label", lambda: "panel.test")
    db = Session()
    people = {}
    for name, role in (("boss", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    sent = []
    monkeypatch.setattr(notifications, "send_email", lambda cfg, to, subject, body: sent.append(("email", to, subject, body)))
    monkeypatch.setattr(notifications, "send_telegram", lambda cfg, chat, title, body: sent.append(("telegram", chat, title, body)))
    yield SimpleNamespace(db=db, Session=Session, people=people, sent=sent)
    db.close()


def _turn_on(smtp=True):
    addons.install(addons.NOTIFICATIONS)
    config = notifications.load_config()
    if smtp:
        config["smtp"].update({"host": "smtp.example.test", "from_email": "noreply@example.test"})
    notifications._write_config(config)


# --- what is said ----------------------------------------------------------------

def test_every_event_reads_in_both_languages():
    samples = {
        "service_down": {"services": ["nginx"]}, "disk_high": {"percent": 93, "used": "9 GB", "total": "10 GB", "free": "1 GB"},
        "firewall_off": {}, "update_available": {"version": "1.0.170", "current": "1.0.164"},
        "backup_failed_admin": {"errors": ["alice: disk full"], "when": "now"},
        "malware_found_admin": {"threats": [{"path": "/home/a/x.php", "signature": "php.shell"}], "count": 1},
        "ssl_expiring_admin": {"sites": [{"domain": "a.test", "days": 3, "expires": "30/09/2026"}]},
        "login_new_ip": {"username": "boss", "ip": "203.0.113.9", "when": "now", "agent": "Firefox"},
        "security_change": {"kind": "2fa_off", "username": "boss", "when": "now"},
    }
    assert set(samples) == set(notify_messages.EVENTS)
    for event, params in samples.items():
        vi = notify_messages.render(event, params, "vi")
        en = notify_messages.render(event, params, "en")
        assert vi[0] and vi[1] and en[0] and en[1] and vi != en, event


def test_a_customer_is_offered_nothing():
    assert notify_messages.events_for(False) == []
    assert notify_messages.events_for(True) == list(notify_messages.EVENTS)
    for gone in ("account_status", "backup_failed", "malware_found", "ssl_expiring", "quota_high"):
        assert gone not in notify_messages.EVENTS, gone


# --- who hears it ------------------------------------------------------------------

def test_nothing_is_sent_while_the_addon_is_off(env):
    assert notifications.notify("firewall_off", {}, admins=True) == 0
    assert env.sent == []


def test_administrators_hear_and_customers_never_do(env):
    _turn_on()
    assert notifications.notify("firewall_off", {}, admins=True) == 1
    assert [s[1] for s in env.sent] == ["boss@example.test"]
    assert env.sent[0][2].startswith("[panel.test] ")
    params = {"username": "x", "ip": "198.51.100.5", "when": "now"}
    assert notifications.notify("login_new_ip", params, user_ids=[env.people["alice"].id]) == 0
    assert notifications.notify("login_new_ip", params, user_ids=[env.people["boss"].id]) == 1
    assert [s[1] for s in env.sent] == ["boss@example.test", "boss@example.test"]


def test_muted_events_and_switched_off_channels_are_respected(env):
    _turn_on()
    boss = env.people["boss"].id
    pref = notifications.prefs_for(env.db, boss)
    pref.muted_events = "login_new_ip"
    pref.telegram_chat_id = "12345"
    env.db.commit()
    params = {"username": "boss", "ip": "198.51.100.5", "when": "now"}
    assert notifications.notify("login_new_ip", params, user_ids=[boss]) == 0
    pref.muted_events = ""
    pref.email_enabled = False
    env.db.commit()
    config = notifications.load_config()
    config["telegram"]["bot_token_enc"] = notifications.secret_box.encrypt("123:abc")
    notifications._write_config(config)
    assert notifications.notify("login_new_ip", params, user_ids=[boss]) == 1
    assert env.sent[-1][0] == "telegram" and env.sent[-1][1] == "12345"


def test_a_repeated_condition_is_not_repeated(env):
    _turn_on()
    for _ in range(3):
        notifications.notify("ssl_expiring_admin", {"sites": []}, admins=True, dedupe_key="ssl:2026-09-27")
    assert len(env.sent) == 1
    rows = env.db.query(NotificationLog).all()
    assert [(r.event, r.channel, r.status) for r in rows] == [("ssl_expiring_admin", "email", "sent")]


def test_a_failed_delivery_is_logged_and_never_raised(env, monkeypatch):
    _turn_on()

    def refuse(*_args):
        raise OSError("connection refused")

    monkeypatch.setattr(notifications, "send_email", refuse)
    assert notifications.notify("firewall_off", {}, admins=True) == 0
    row = env.db.query(NotificationLog).one()
    assert row.status == "failed" and "connection refused" in row.detail


# --- settings ----------------------------------------------------------------------

def test_secrets_are_stored_encrypted_and_never_shown(env, monkeypatch):
    monkeypatch.setattr(notifications, "telegram_call", lambda token, method, payload, timeout=15: {"username": "spanel_bot"})
    result = notifications.save_config({"smtp": {"host": "smtp.example.test", "password": "hunter22", "from_email": "noreply@example.test"},
                                        "telegram": {"bot_token": "123456789:" + "A" * 35}})
    stored = notifications.CONFIG_FILE.read_text(encoding="utf-8")
    assert "hunter22" not in stored and ("A" * 35) not in stored
    assert result["smtp"]["password_set"] is True and "password_enc" not in result["smtp"]
    assert result["telegram"] == {"bot_username": "spanel_bot", "token_set": True, "chat_id": ""}
    # Saving the form again without retyping the password keeps it.
    notifications.save_config({"smtp": {"host": "smtp2.example.test", "password": None}})
    assert notifications.public_config()["smtp"]["password_set"] is True


def test_a_bad_token_or_address_is_refused(env, monkeypatch):
    with pytest.raises(ValueError, match="BotFather"):
        notifications.save_config({"telegram": {"bot_token": "not-a-token"}})

    def reject(*_a, **_k):
        raise RuntimeError("Unauthorized")

    monkeypatch.setattr(notifications, "telegram_call", reject)
    with pytest.raises(ValueError, match="Unauthorized"):
        notifications.save_config({"telegram": {"bot_token": "123456789:" + "B" * 35}})
    with pytest.raises(ValueError, match="email address"):
        notifications.save_config({"smtp": {"from_email": "nobody"}})


# --- Telegram linking ----------------------------------------------------------------

def test_a_start_message_with_the_code_links_the_chat(env, monkeypatch):
    _turn_on(smtp=False)
    config = notifications.load_config()
    config["telegram"].update({"bot_token_enc": notifications.secret_box.encrypt("123:abc"), "bot_username": "spanel_bot"})
    notifications._write_config(config)
    boss = env.people["boss"].id
    link = notifications.start_telegram_link(env.db, boss)
    assert link["link"] == f"https://t.me/spanel_bot?start={link['code']}"
    calls = []

    def fake_call(token, method, payload, timeout=15):
        calls.append(method)
        if method == "getUpdates":
            return [{"update_id": 41, "message": {"text": "hello", "chat": {"id": 1}}},
                    {"update_id": 42, "message": {"text": f"/start {link['code']}", "chat": {"id": 777}}}]
        return {}

    monkeypatch.setattr(notifications, "telegram_call", fake_call)
    assert notifications.collect_telegram_links(env.db) == [boss]
    pref = env.db.get(NotificationPref, boss)
    assert pref.telegram_chat_id == "777" and pref.telegram_link_code is None
    assert notifications.load_config()["telegram"]["update_offset"] == 43
    assert calls == ["getUpdates", "sendMessage"]


# --- sign-ins ------------------------------------------------------------------------

def test_a_new_network_is_reported_and_the_first_sign_in_is_not(env, monkeypatch):
    _turn_on()
    heard = []
    monkeypatch.setattr(notifications, "notify", lambda event, params, **kw: heard.append((event, params["ip"])))

    class Now:  # run the background thread inline
        def __init__(self, target, **_kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(notifications.threading, "Thread", Now)
    uid = env.people["boss"].id
    notifications.record_login(uid, "boss", "203.0.113.9")     # first ever: silent
    notifications.record_login(uid, "boss", "203.0.113.77")    # same /24: silent
    notifications.record_login(uid, "boss", "198.51.100.5")    # new network: reported
    notifications.record_login(uid, "boss", "198.51.100.5")    # seen: silent
    assert heard == [("login_new_ip", "198.51.100.5")]
    assert env.db.query(LoginSource).filter(LoginSource.user_id == uid).count() == 3


# --- the watcher ---------------------------------------------------------------------

def test_a_service_is_reported_after_two_checks_and_again_when_back(env, monkeypatch):
    heard = []
    monkeypatch.setattr(notifications, "notify", lambda event, params, **kw: heard.append((event, tuple(params["services"]), params.get("recovered", False))))
    monkeypatch.setattr("app.services.system.list_services", lambda: ["nginx", "mariadb"])
    answers = iter(["active\ninactive\n", "active\ninactive\n", "active\ninactive\n", "active\nactive\n"])
    monkeypatch.setattr(notify_watch.shell, "run", lambda args, check=False: SimpleNamespace(stdout=next(answers)))
    state = {}
    for _ in range(4):
        notify_watch.check_services(state)
    assert heard == [("service_down", ("mariadb",), False), ("service_down", ("mariadb",), True)]


def test_the_disk_warns_once_until_it_has_dropped_back(env, monkeypatch):
    heard = []
    monkeypatch.setattr(notifications, "notify", lambda event, params, **kw: heard.append(params["percent"]))
    usage = iter([(100, 93, 7), (100, 95, 5), (100, 87, 13), (100, 84, 16), (100, 91, 9)])
    monkeypatch.setattr(notify_watch.shutil, "disk_usage", lambda path: SimpleNamespace(**dict(zip(("total", "used", "free"), next(usage), strict=True))))
    state = {}
    for _ in range(5):
        notify_watch.check_disk(state, 90)
    assert heard == [93, 91]


def test_expiring_certificates_go_to_admins_only(env, monkeypatch):
    from datetime import datetime

    alice, bob = env.people["alice"], env.people["bob"]
    for owner, domain in ((alice, "a.test"), (bob, "b.test"), (bob, "ok.test")):
        env.db.add(Website(domain=domain, owner_id=owner.id, root_path=f"/home/{owner.username}/{domain}",
                           document_root="public_html", linux_user=owner.username, php_version="8.3",
                           app_type="php", status="active", ssl_enabled=True))
    env.db.commit()
    soon = datetime.now(UTC) + timedelta(days=3, hours=1)
    later = datetime.now(UTC) + timedelta(days=60)
    monkeypatch.setattr(notify_watch, "served_certificate_expiry", lambda d: later if d == "ok.test" else soon)
    heard = []
    monkeypatch.setattr(notifications, "notify", lambda event, params, **kw: heard.append((event, [s["domain"] for s in params["sites"]], kw.get("user_ids"))))
    notify_watch.check_certificates(7)
    assert heard == [("ssl_expiring_admin", ["a.test", "b.test"], None)], "and not to the owners"


def test_the_watcher_stays_quiet_until_it_is_wanted(env):
    assert notify_watch.run() == "notifications addon is off"
    addons.install(addons.NOTIFICATIONS)
    assert notify_watch.run() == "no channel is set up"


# --- the API ---------------------------------------------------------------------------

def test_every_route_needs_the_addon_and_an_administrator():
    """One router-wide dependency, so a route added later cannot forget it."""
    from fastapi import HTTPException

    from app.api import notifications as api

    source = (PROJECT_ROOT / "backend" / "app" / "api" / "notifications.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_notifications), Depends(require_admin)]" in source
    with pytest.raises(HTTPException) as refused:
        api.require_admin(SimpleNamespace(role="end_user"))
    assert refused.value.status_code == 403
    api.require_admin(SimpleNamespace(role="admin"))


# --- where the events come from ---------------------------------------------------------

def test_the_events_are_raised_where_they_happen():
    app_dir = PROJECT_ROOT / "backend" / "app"
    auth = (app_dir / "api" / "auth.py").read_text(encoding="utf-8")
    assert auth.count("_note_sign_in(request, user)") == 2, "password/passkey sign-in and SSO"
    assert '"kind": "2fa_off"' in auth
    users = (app_dir / "api" / "users.py").read_text(encoding="utf-8")
    assert '"kind": "password"' in users and '"kind": "2fa_reset"' in users
    assert "if not is_admin_role(user.role):\n        return\n    notifications.record_login(" in auth, \
        "a customer's sign-ins are not even recorded"
    prov = (app_dir / "services" / "provisioning.py").read_text(encoding="utf-8")
    assert "account_status" not in users and "account_status" not in prov, "customers are told nothing"
    backup = (app_dir / "services" / "backup_scheduler.py").read_text(encoding="utf-8")
    assert backup.count("_notify_failure(schedule,") == 2
    scans = (app_dir / "services" / "panel_settings.py").read_text(encoding="utf-8")
    assert scans.count("_notify_threats(job_id, ") == 3, "clamd server scan, LMD scan, clamd site scan"


def test_the_watcher_is_scheduled_by_both_installers():
    for name in ("install.sh", "update.sh"):
        script = (PROJECT_ROOT / "installer" / name).read_text(encoding="utf-8")
        assert "cat >/etc/systemd/system/spanel-notify.service" in script, name
        assert "ExecStart=${APP_DIR}/backend/.venv/bin/python -m app.services.notify_watch" in script, name
        assert "OnUnitActiveSec=5min" in script.split("spanel-notify.timer")[1], name
        assert "systemctl enable --now spanel-notify.timer" in script, name


# --- Telegram: a token and a chat ID (operator, 2026-09-27) ---------------------------

def _telegram_on(chat_id="-1001234567890"):
    addons.install(addons.NOTIFICATIONS)
    config = notifications.load_config()
    config["telegram"].update({"bot_token_enc": notifications.secret_box.encrypt("123:abc"),
                               "bot_username": "spanel_bot", "chat_id": chat_id})
    notifications._write_config(config)


def test_a_server_event_goes_to_the_admin_chat_once(env):
    _telegram_on()
    second = User(username="boss2", email="boss2@example.test", role="admin", is_active=True,
                  hashed_password=hash_password("PasswordLongEnough1"))
    env.db.add(second)
    env.db.commit()
    for admin in (env.people["boss"], second):
        notifications.prefs_for(env.db, admin.id).telegram_chat_id = f"55{admin.id}"
    env.db.commit()
    notifications.notify("firewall_off", {}, admins=True)
    assert [s[1] for s in env.sent if s[0] == "telegram"] == ["-1001234567890"], "once, in the admin chat"


def test_the_admin_chat_stays_quiet_when_every_admin_muted_the_event(env):
    _telegram_on()
    notifications.prefs_for(env.db, env.people["boss"].id).muted_events = "firewall_off"
    env.db.commit()
    notifications.notify("firewall_off", {}, admins=True)
    assert env.sent == []


def test_own_events_go_to_ones_own_chat_and_an_admin_falls_back_to_the_admin_chat(env):
    _telegram_on()
    second = User(username="boss2", email="boss2@example.test", role="admin", is_active=True,
                  hashed_password=hash_password("PasswordLongEnough1"))
    env.db.add(second)
    env.db.commit()
    notifications.prefs_for(env.db, second.id).telegram_chat_id = "777"
    notifications.prefs_for(env.db, env.people["alice"].id).telegram_chat_id = "888"
    env.db.commit()
    for user in (second, env.people["boss"], env.people["alice"]):
        notifications.notify("login_new_ip", {"username": user.username, "ip": "198.51.100.5", "when": "now"},
                             user_ids=[user.id])
    chats = [s[1] for s in env.sent if s[0] == "telegram"]
    # The second administrator to their own chat, the first (no chat of their
    # own) to the admin chat, Alice - a customer - nowhere, chat or not.
    assert chats == ["777", "-1001234567890"]


def test_a_chat_id_is_a_number_or_a_channel_name(env):
    for good in ("123456789", "-1001234567890", "@spanel_alerts"):
        assert notifications.valid_chat_id(good) == good
    for bad in ("12", "hello", "@ab", "123 456"):
        with pytest.raises(ValueError):
            notifications.valid_chat_id(bad)


def test_the_bot_lists_who_wrote_to_it_without_using_up_the_messages(env, monkeypatch):
    _telegram_on(chat_id="")
    seen = []

    def fake_call(token, method, payload, timeout=15):
        seen.append((method, payload.get("offset")))
        return [{"update_id": 9, "message": {"chat": {"id": 42, "type": "private", "first_name": "Giang"}}},
                {"update_id": 10, "my_chat_member": {"chat": {"id": -100555, "type": "supergroup", "title": "SPanel ops"}}}]

    monkeypatch.setattr(notifications, "telegram_call", fake_call)
    assert notifications.recent_chats() == [
        {"id": "42", "type": "private", "name": "Giang"},
        {"id": "-100555", "type": "supergroup", "name": "SPanel ops"},
    ]
    assert notifications.load_config()["telegram"]["update_offset"] == 0, "the offset did not move"


def test_the_page_and_its_menu_entry_are_for_administrators():
    app = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "...(notificationsAddonInstalled && isAdmin ? [['notifications', 'Notifications', Bell]] : [])," in app
    assert "if (page === 'notifications') return !isAdmin ? renderAdminOnly() :" in app
    assert "quota_percent" not in app
