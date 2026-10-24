"""Operational alerts over webhooks: the watchdog, the maintenance window,
backup verification and the panel self-backup.

Email and Telegram (app.services.notifications) only work once the
Notifications addon is installed and set up, and the watchdog had no other
way to say a service was down. Two more destinations, neither needing the
addon:

- one chat webhook URL - Slack and anything that speaks its incoming-webhook
  format (Mattermost, Rocket.Chat), Discord, or a generic JSON body. The URL
  is the credential for these services, so it is stored encrypted and the
  page only ever sees its host;
- the panel's outbound webhook endpoints that belong to administrators
  (Resellers > Webhooks), signed with their secret like every other event.

`alert()` never raises: an alert must not break the thing it is about. A
repeated alert (a service still down at the next 2-minute check) is held
back for `cooldown_minutes`.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional

from app.core import secrets as secret_box

logger = logging.getLogger("spanel.alerts")

DATA_DIR = Path(os.environ.get("SPANEL_DATA_DIR") or os.environ.get("spanel_DATA_DIR") or "/var/lib/spanel")
CONFIG_FILE = DATA_DIR / "alert-webhooks.json"
FORMATS = ("slack", "discord", "generic")
EVENTS = ("service_down", "service_recovered", "maintenance_job_failed", "backup_verify_failed",
          "panel_dr_failed", "panel_db_restored")
DEFAULTS = {
    "enabled": False,
    "url_enc": "",
    "url_host": "",
    "format": "slack",
    "endpoints_enabled": True,
    "events": list(EVENTS),
    "cooldown_minutes": 30,
    "sent": {},
}
TIMEOUT = 8


def load_config() -> dict:
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    config = json.loads(json.dumps(DEFAULTS))
    if isinstance(stored, dict):
        config.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return config


def _write(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(DATA_DIR), delete=False) as tmp:
        json.dump(config, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        path = Path(tmp.name)
    os.chmod(path, 0o600)
    path.replace(CONFIG_FILE)


def validate_url(raw: str) -> str:
    """An https URL with a host and nothing that would leak or surprise:
    no credentials in it, no control characters, a sane length."""
    url = (raw or "").strip()
    if len(url) > 500 or any(ord(ch) < 33 for ch in url):
        raise ValueError("The webhook URL is not valid")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("The webhook URL must start with https://")
    if parsed.username or parsed.password:
        raise ValueError("Put no user name or password in the webhook URL")
    return url


def public_config(config: Optional[dict] = None) -> dict:
    config = config or load_config()
    return {
        "enabled": bool(config["enabled"]),
        "url_set": bool(config["url_enc"]),
        "url_host": config.get("url_host", ""),
        "format": config["format"],
        "endpoints_enabled": bool(config["endpoints_enabled"]),
        "events": [e for e in config["events"] if e in EVENTS],
        "all_events": list(EVENTS),
        "cooldown_minutes": int(config["cooldown_minutes"]),
    }


def save_config(changes: dict) -> dict:
    config = load_config()
    if changes.get("url"):
        url = validate_url(changes["url"])
        config["url_enc"] = secret_box.encrypt(url)
        config["url_host"] = urllib.parse.urlsplit(url).hostname or ""
    if changes.get("clear_url"):
        config["url_enc"], config["url_host"] = "", ""
    if changes.get("format") is not None:
        if changes["format"] not in FORMATS:
            raise ValueError("Format must be slack, discord or generic")
        config["format"] = changes["format"]
    for key in ("enabled", "endpoints_enabled"):
        if changes.get(key) is not None:
            config[key] = bool(changes[key])
    if changes.get("events") is not None:
        config["events"] = [e for e in EVENTS if e in changes["events"]]
    if changes.get("cooldown_minutes") is not None:
        config["cooldown_minutes"] = max(0, min(1440, int(changes["cooldown_minutes"])))
    _write(config)
    return public_config(config)


def chat_payload(fmt: str, event: str, title: str, text: str, data: dict, host: str) -> dict:
    """The body for one chat webhook format."""
    line = f"[{host}] {title}"
    if fmt == "discord":
        return {"content": f"**{line}**\n{text}"[:1900], "allowed_mentions": {"parse": []}}
    if fmt == "slack":
        return {"text": f"*{line}*\n{text}"[:3500]}
    return {"event": event, "title": title, "text": text, "server": host, "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()}


def _post_json(url: str, payload: dict) -> int:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "SPanel-Alerts/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - https enforced in validate_url
        return int(getattr(response, "status", 200) or 200)


def _server_label() -> str:
    try:
        from app.services.notifications import server_label

        return server_label()
    except Exception:  # noqa: BLE001
        import socket

        return socket.gethostname()


def _admin_endpoints(db) -> list:
    from app.core.permissions import is_admin_role
    from app.models.entities import User, WebhookEndpoint

    admin_ids = [u.id for u in db.query(User).filter(User.is_active.is_(True)).all() if is_admin_role(u.role)]
    if not admin_ids:
        return []
    return db.query(WebhookEndpoint).filter(WebhookEndpoint.user_id.in_(admin_ids),
                                            WebhookEndpoint.is_active.is_(True)).all()


def _endpoint_wants(endpoint, event: str) -> bool:
    wanted = [item.strip() for item in (endpoint.events or "*").split(",") if item.strip()]
    return "*" in wanted or event in wanted or f"ops.{event}" in wanted or "ops.*" in wanted


def alert(event: str, title: str, text: str, data: Optional[dict] = None, *,
          dedupe_key: Optional[str] = None, db=None) -> int:
    """Send one operational alert to the chat webhook and to administrators'
    outbound webhooks. Returns how many deliveries succeeded."""
    try:
        config = load_config()
        if event not in EVENTS or event not in config["events"]:
            return 0
        now = time.time()
        cooldown = int(config["cooldown_minutes"]) * 60
        if dedupe_key and cooldown and now - float(config["sent"].get(dedupe_key) or 0) < cooldown:
            return 0
        host = _server_label()
        data = dict(data or {})
        sent = 0
        if config["enabled"] and config["url_enc"]:
            try:
                status = _post_json(secret_box.decrypt(config["url_enc"]),
                                    chat_payload(config["format"], event, title, text, data, host))
                sent += 1 if 200 <= status < 300 else 0
            except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
                # Never the URL itself in a log line: it is the credential.
                logger.warning("chat webhook to %s failed: %s", config.get("url_host"), type(exc).__name__)
        if config["endpoints_enabled"]:
            sent += _send_to_endpoints(event, title, text, data, host, db)
        if dedupe_key:
            fresh = load_config()
            fresh["sent"] = {k: v for k, v in (fresh.get("sent") or {}).items() if now - float(v) < 86400}
            fresh["sent"][dedupe_key] = now
            _write(fresh)
        return sent
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("alert %s not sent: %s", event, exc)
        return 0


def _send_to_endpoints(event: str, title: str, text: str, data: dict, host: str, db=None) -> int:
    from app.services import webhooks

    own_session = db is None
    if own_session:
        from app.core.database import SessionLocal

        db = SessionLocal()
    sent = 0
    try:
        payload = {"event": f"ops.{event}", "title": title, "detail": text, "server": host, "data": data,
                   "timestamp": datetime.now(timezone.utc).isoformat()}
        for endpoint in _admin_endpoints(db):
            if not _endpoint_wants(endpoint, event):
                continue
            result = webhooks.send_webhook_sync(endpoint.url, endpoint.secret, payload,
                                                webhook_id=endpoint.id, user_id=endpoint.user_id)
            sent += 1 if result.get("status") == "success" else 0
    finally:
        if own_session:
            db.close()
    return sent


def send_test() -> dict:
    """A test message to the chat webhook; raises with what went wrong."""
    config = load_config()
    if not config["url_enc"]:
        raise ValueError("Set the webhook URL first")
    try:
        status = _post_json(secret_box.decrypt(config["url_enc"]),
                            chat_payload(config["format"], "test", "SPanel alert test",
                                         "If you can read this, watchdog alerts will arrive here.", {}, _server_label()))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"The webhook answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"Could not reach {config.get('url_host')} ({type(exc).__name__})") from None
    if not 200 <= status < 300:
        raise ValueError(f"The webhook answered HTTP {status}")
    return {"ok": True, "status": status}
