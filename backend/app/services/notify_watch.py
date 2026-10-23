"""The Notifications addon's watcher: conditions nobody's click reports.

Run every five minutes by spanel-notify.timer (python -m
app.services.notify_watch). Exits at once while the addon is off or no
channel is set up. Each check keeps what it last saw in a small state file so
a condition is reported when it starts, not every five minutes while it lasts:

- a service not running at two checks in a row (one failed check is often a
  restart in progress), and again when it is back;
- the root disk past the threshold, then not again until it has dropped 5
  points below it;
- the firewall off;
- once a day: certificates about to expire (read from what nginx actually
  serves, so no certificate file needs to be readable) and a new panel
  release.

Everything goes to administrators; customers get no notifications.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import ssl
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core.database import SessionLocal
from app.models.entities import Website
from app.services import notifications
from app.services.shell import shell

logger = logging.getLogger("spanel.notify_watch")
STATE_FILE = notifications.DATA_DIR / "notify-state.json"


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(STATE_FILE.parent), delete=False) as tmp:
        json.dump(state, tmp, indent=2, sort_keys=True)
        path = Path(tmp.name)
    path.replace(STATE_FILE)


# --- every run --------------------------------------------------------------

def check_services(state: dict) -> None:
    from app.services.system import list_services

    names = list_services()
    result = shell.run(["systemctl", "is-active", *names], check=False)
    answers = (result.stdout or "").split()[: len(names)]
    answers += ["unknown"] * (len(names) - len(answers))
    before = state.get("services", {})
    after, stopped, back = {}, [], []
    for name, answer in zip(names, answers, strict=True):
        seen = before.get(name, {})
        if answer == "active":
            if seen.get("notified"):
                back.append(name)
            continue
        count = int(seen.get("count", 0)) + 1
        notified = bool(seen.get("notified"))
        if count >= 2 and not notified:
            stopped.append(name)
            notified = True
        after[name] = {"count": count, "notified": notified}
    state["services"] = after
    if stopped:
        notifications.notify("service_down", {"services": stopped}, admins=True)
    if back:
        notifications.notify("service_down", {"services": back, "recovered": True}, admins=True)


def check_disk(state: dict, threshold: int) -> None:
    usage = shutil.disk_usage("/")
    percent = round(usage.used * 100 / usage.total) if usage.total else 0
    if percent >= threshold and not state.get("disk_alerted"):
        notifications.notify("disk_high", {"percent": percent, "used": _human(usage.used),
                                           "total": _human(usage.total), "free": _human(usage.free)}, admins=True)
        state["disk_alerted"] = True
    elif percent < threshold - 5:
        state["disk_alerted"] = False


def check_firewall(state: dict) -> None:
    from app.services import firewall

    enabled = firewall.is_enabled()
    if enabled is False and not state.get("firewall_alerted"):
        notifications.notify("firewall_off", {}, admins=True)
        state["firewall_alerted"] = True
    elif enabled is True:
        state["firewall_alerted"] = False


# --- once a day -------------------------------------------------------------

def served_certificate_expiry(domain: str) -> datetime | None:
    """When the certificate nginx serves for `domain` runs out, or None.

    None too when what comes back is not this domain's certificate (nginx
    answers an unknown name with its default one): that is a different
    problem, and not one to report as "expires in N days".
    """
    from cryptography import x509

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    # Only the certificate is wanted, and every nginx speaks TLS 1.2 or 1.3;
    # there is no reason to offer anything older.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError:
        return None
    if not der:
        return None
    cert = x509.load_der_x509_certificate(der)
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    covered = any(n == domain or (n.startswith("*.") and domain.endswith(n[1:]) and domain.count(".") == n.count("."))
                  for n in names)
    return cert.not_valid_after_utc if covered else None


def check_certificates(days_limit: int) -> None:
    db = SessionLocal()
    try:
        sites = db.query(Website).filter(Website.ssl_enabled.is_(True)).all()
        rows = [(site.domain, site.status) for site in sites]
    finally:
        db.close()
    now = datetime.now(UTC)
    expiring: list[dict] = []
    for domain, status in rows:
        if status == "suspended":
            continue
        expires = served_certificate_expiry(domain)
        if expires is None:
            continue
        days = (expires - now).days
        if days <= days_limit:
            expiring.append({"domain": domain, "days": max(days, 0),
                             "expires": expires.astimezone().strftime("%d/%m/%Y")})
    if not expiring:
        return
    notifications.notify("ssl_expiring_admin", {"sites": expiring}, admins=True,
                         dedupe_key=f"ssl:{date.today().isoformat()}")


def check_update(state: dict) -> None:
    from app.services import updates

    status = updates.panel_release_status(force_refresh=True)
    latest = status.get("latest_version") or ""
    if status.get("update_available") and latest and state.get("update_notified") != latest:
        notifications.notify("update_available", {"version": latest, "current": status.get("current_version", "")},
                             admins=True)
        state["update_notified"] = latest


def run() -> str:
    if not notifications.is_enabled():
        return "notifications addon is off"
    config = notifications.load_config()
    if not (notifications.smtp_ready(config) or notifications.telegram_ready(config)):
        return "no channel is set up"
    thresholds = config["thresholds"]
    state = load_state()
    done = []
    for name, check in (("services", lambda: check_services(state)),
                        ("disk", lambda: check_disk(state, int(thresholds["disk_percent"]))),
                        ("firewall", lambda: check_firewall(state))):
        try:
            check()
            done.append(name)
        except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
            logger.warning("notify watch: %s check failed: %s", name, exc)
    today = date.today().isoformat()
    if state.get("daily") != today:
        for name, check in (("certificates", lambda: check_certificates(int(thresholds["ssl_days"]))),
                            ("update", lambda: check_update(state))):
            try:
                check()
                done.append(name)
            except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
                logger.warning("notify watch: %s check failed: %s", name, exc)
        state["daily"] = today
    save_state(state)
    return "checked: " + ", ".join(done)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("spanel_LOG_LEVEL", "WARNING"))
    print(run())
