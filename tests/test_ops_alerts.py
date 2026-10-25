"""Watchdog alerts over webhooks, and restoring a hot database backup."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services import alert_webhooks, system  # noqa: E402


@pytest.fixture()
def alert_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(alert_webhooks, "DATA_DIR", tmp_path)
    monkeypatch.setattr(alert_webhooks, "CONFIG_FILE", tmp_path / "alert-webhooks.json")
    monkeypatch.setattr(alert_webhooks, "_server_label", lambda: "srv1.example.com")
    return tmp_path


@pytest.fixture()
def captured(monkeypatch):
    posts, endpoint_calls = [], []
    monkeypatch.setattr(alert_webhooks, "_post_json", lambda url, payload: posts.append((url, payload)) or 200)
    endpoint = SimpleNamespace(id=4, user_id=1, url="https://billing.example.com/hook", secret="s3cret", events="*")
    monkeypatch.setattr(alert_webhooks, "_admin_endpoints", lambda db: [endpoint])

    from app.services import webhooks

    def send(url, secret, payload, **kw):
        endpoint_calls.append((url, secret, payload, kw))
        return {"status": "success"}
    monkeypatch.setattr(webhooks, "send_webhook_sync", send)
    return SimpleNamespace(posts=posts, endpoints=endpoint_calls)


@pytest.mark.parametrize("url", [
    "http://hooks.slack.com/services/x",
    "https://user:pw@hooks.example.com/x",
    "https://",
    "https://hooks.example.com/a b",
    "ftp://example.com/x",
])
def test_webhook_url_validation_rejects(url):
    with pytest.raises(ValueError):
        alert_webhooks.validate_url(url)


def test_url_is_stored_encrypted_and_never_shown(alert_cfg):
    url = "https://hooks.slack.com/services/T000/B000/XXXXSECRET"
    public = alert_webhooks.save_config({"url": url, "enabled": True, "format": "discord"})
    assert public["url_set"] and public["url_host"] == "hooks.slack.com"
    assert "XXXXSECRET" not in str(public)
    raw = (alert_cfg / "alert-webhooks.json").read_text(encoding="utf-8")
    assert "XXXXSECRET" not in raw
    with pytest.raises(ValueError):
        alert_webhooks.save_config({"format": "teams"})
    assert alert_webhooks.save_config({"clear_url": True})["url_set"] is False


def test_chat_payload_formats():
    slack = alert_webhooks.chat_payload("slack", "service_down", "Service down: nginx", "nginx: failed", {}, "srv")
    assert slack == {"text": "*[srv] Service down: nginx*\nnginx: failed"}
    discord = alert_webhooks.chat_payload("discord", "service_down", "T", "body", {}, "srv")
    assert discord["content"].startswith("**[srv] T**") and discord["allowed_mentions"] == {"parse": []}
    generic = alert_webhooks.chat_payload("generic", "service_down", "T", "body", {"services": ["nginx"]}, "srv")
    assert generic["event"] == "service_down" and generic["data"] == {"services": ["nginx"]}


def test_alert_goes_to_chat_and_admin_endpoints_once_per_cooldown(alert_cfg, captured):
    alert_webhooks.save_config({"url": "https://chat.example.com/hooks/abc", "enabled": True, "format": "slack"})
    sent = alert_webhooks.alert("service_down", "Service down: nginx", "nginx: failed", {"services": ["nginx"]},
                                dedupe_key="watchdog:down:nginx")
    assert sent == 2
    assert captured.posts[0][0] == "https://chat.example.com/hooks/abc"
    url, secret, payload, kw = captured.endpoints[0]
    assert payload["event"] == "ops.service_down" and secret == "s3cret" and kw["webhook_id"] == 4
    # Still down two minutes later: held back.
    assert alert_webhooks.alert("service_down", "Service down: nginx", "x", dedupe_key="watchdog:down:nginx") == 0
    assert len(captured.posts) == 1
    # A different set of services is news.
    assert alert_webhooks.alert("service_down", "Service down: redis", "x", dedupe_key="watchdog:down:redis-server") == 2


def test_alert_respects_event_selection_and_switches(alert_cfg, captured):
    alert_webhooks.save_config({"url": "https://chat.example.com/h", "enabled": True,
                                "events": ["service_down"], "endpoints_enabled": False})
    assert alert_webhooks.alert("service_recovered", "t", "x") == 0
    assert alert_webhooks.alert("service_down", "t", "x") == 1
    assert captured.endpoints == []
    alert_webhooks.save_config({"enabled": False, "endpoints_enabled": True})
    assert alert_webhooks.alert("service_down", "t", "x") == 1   # endpoints only
    assert len(captured.posts) == 1


def test_endpoint_event_filter():
    ep = SimpleNamespace(events="site_created,ops.service_down")
    assert alert_webhooks._endpoint_wants(ep, "service_down")
    assert not alert_webhooks._endpoint_wants(ep, "backup_verify_failed")
    assert alert_webhooks._endpoint_wants(SimpleNamespace(events="ops.*"), "backup_verify_failed")


def test_alert_never_raises(alert_cfg, monkeypatch):
    alert_webhooks.save_config({"url": "https://chat.example.com/h", "enabled": True, "endpoints_enabled": False})

    def boom(url, payload):
        raise OSError("connection refused")
    monkeypatch.setattr(alert_webhooks, "_post_json", boom)
    assert alert_webhooks.alert("service_down", "t", "x") == 0


def test_watchdog_sends_webhook_alerts(tmp_path, monkeypatch):
    """A service found down and restarted by the watchdog reaches the webhook
    even with the Notifications addon off."""
    from app.services import notifications  # imported before os.name is faked below

    monkeypatch.setattr(system, "_self_healing_path", lambda: tmp_path / "self-healing.json")
    (tmp_path / "self-healing.json").write_text('{"auto_db_backup_enabled": false}', encoding="utf-8")
    monkeypatch.setattr(system, "list_services", lambda: ["nginx", "redis-server"])
    monkeypatch.setattr(system.settings, "command_dry_run", False)
    monkeypatch.setattr(system.os, "name", "posix")
    monkeypatch.setattr(system, "_validate_service_config", lambda name: (True, "ok"))
    monkeypatch.setattr(system, "_record_watchdog_incident_db", lambda event: None)
    monkeypatch.setattr(system.shell, "privileged", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    answers = iter(["failed failed", "active", "failed"])
    monkeypatch.setattr(system.shell, "run", lambda *a, **k: SimpleNamespace(stdout=next(answers), stderr="", returncode=0))
    monkeypatch.setattr(system, "service_action", lambda name, action: SimpleNamespace(stderr="", stdout="", returncode=0))
    monkeypatch.setattr(notifications, "notify", lambda *a, **k: 0)

    calls = []
    monkeypatch.setattr(alert_webhooks, "alert", lambda event, title, text, data=None, **kw: calls.append((event, data, kw)) or 1)
    result = system.run_self_healing_check(reason="systemd-timer")
    assert result["healed_now"] == ["nginx"] and result["failed_now"] == ["redis-server"]
    events = {c[0]: c for c in calls}
    assert events["service_recovered"][1]["services"] == ["nginx"]
    assert events["service_down"][1]["services"] == ["redis-server"]
    assert events["service_down"][2]["dedupe_key"] == "watchdog:down:redis-server"


# --- hot database backup restore ---------------------------------------------------

def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
    conn.execute("INSERT INTO alembic_version VALUES ('0045_enterprise_audit_watchdog')")
    conn.commit()
    conn.close()


def test_check_sqlite_backup(tmp_path, monkeypatch):
    from app.core import database

    monkeypatch.setattr(database, "DB_BACKUP_DIR", tmp_path)
    good = "spanel-20261024T020000Z-watchdog.db"
    _make_db(tmp_path / good)
    res = database.check_sqlite_backup(good)
    assert res["ok"] and res["revision"] == "0045_enterprise_audit_watchdog"

    bad = "spanel-20261024T030000Z-manual.db"
    (tmp_path / bad).write_bytes(b"garbage" * 200)
    assert database.check_sqlite_backup(bad)["ok"] is False

    for name in ("../spanel.db", "spanel-x.db", "spanel-20261024T020000Z-watchdog.db.bak"):
        with pytest.raises(ValueError):
            database.check_sqlite_backup(name)
    with pytest.raises(FileNotFoundError):
        database.check_sqlite_backup("spanel-20261024T040000Z-manual.db")


def test_restore_endpoint_checks_then_hands_over_to_the_helper(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api import services as services_api
    from app.core import database

    monkeypatch.setattr(database, "DB_BACKUP_DIR", tmp_path)
    name = "spanel-20261024T020000Z-watchdog.db"
    _make_db(tmp_path / name)
    audit, helper = [], []
    monkeypatch.setattr(services_api, "log_action", lambda db, uid, action, target, detail="", request=None: audit.append(action))
    monkeypatch.setattr(services_api.shell, "privileged",
                        lambda cmd, helper_args=None, **kw: helper.append((cmd, helper_args)) or
                        SimpleNamespace(returncode=0, stdout=f"scheduled={name}\n", stderr=""))
    monkeypatch.setattr(services_api.alert_webhooks, "alert", lambda *a, **k: 0)
    admin = SimpleNamespace(id=1, role="admin", username="admin")
    res = services_api.restore_db_hot_backup(name, request=None, db=None, current_user=admin)
    assert res["status"] == "scheduled" and helper == [("panel-db-restore", [name])]
    assert audit == ["restore_panel_database"]

    bad = "spanel-20261024T030000Z-manual.db"
    (tmp_path / bad).write_bytes(b"garbage" * 200)
    with pytest.raises(HTTPException) as exc:
        services_api.restore_db_hot_backup(bad, request=None, db=None, current_user=admin)
    assert exc.value.status_code == 400 and len(helper) == 1   # the helper was never asked

    with pytest.raises(HTTPException):
        services_api.restore_db_hot_backup(name, request=None, db=None,
                                           current_user=SimpleNamespace(id=2, role="user", username="bob"))


def test_helper_db_restore_name_rule_matches_the_api():
    from app.core.database import DB_BACKUP_NAME_RE

    helper = (REPO_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    assert "^spanel-[0-9]{8}T[0-9]{6}Z-[a-z0-9_-]{1,24}\\.db$" in helper
    assert DB_BACKUP_NAME_RE.pattern == r"^spanel-[0-9]{8}T[0-9]{6}Z-[a-z0-9_-]{1,24}\.db$"
