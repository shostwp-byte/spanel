import logging
import json
import hmac
import hashlib
import urllib.request
import urllib.error
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from app.models.entities import WebhookEndpoint, User

logger = logging.getLogger("spanel.webhooks")


def _send_webhook(url: str, secret: Optional[str], payload: dict) -> tuple[bool, int, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SPanel-Webhook-Dispatcher/1.0",
    }
    body = json.dumps(payload).encode("utf-8")

    if secret:
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-SPanel-Signature"] = signature

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=8) as response:
            code = getattr(response, "status", 200) or 200
            return True, int(code), "OK"
    except urllib.error.HTTPError as e:
        logger.warning(f"Webhook {url} returned status {e.code}")
        return False, int(e.code), f"HTTP {e.code}"
    except Exception as e:
        logger.warning(f"Failed to send webhook to {url}: {e}")
        return False, 0, str(e)


def dispatch_webhook(db: Session, user_id: Optional[int], action: str, target: str, detail: str = ""):
    user = db.query(User).filter(User.id == user_id).first() if user_id else None

    # Find endpoints for the user, their reseller (if applicable), and all administrators
    endpoint_users: set[int] = set()
    if user:
        endpoint_users.add(user.id)
        if user.reseller_id:
            endpoint_users.add(user.reseller_id)

    admin_ids = [row.id for row in db.query(User.id).filter(User.role == "admin", User.is_active == True).all()]  # noqa: E712
    endpoint_users.update(admin_ids)

    if not endpoint_users:
        return

    endpoints = db.query(WebhookEndpoint).filter(
        WebhookEndpoint.user_id.in_(endpoint_users),
        WebhookEndpoint.is_active == True,  # noqa: E712
    ).all()

    if not endpoints:
        return

    payload = {
        "event": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user.id if user else None,
        "username": user.username if user else "",
        "target": target,
        "detail": detail,
    }

    for ep in endpoints:
        allowed_events = [ev.strip() for ev in (ep.events or "*").split(",") if ev.strip()]
        if "*" not in allowed_events and action not in allowed_events:
            continue

        # Dispatch in background thread so API doesn't block
        threading.Thread(target=_send_webhook, args=(ep.url, ep.secret, payload), daemon=True).start()
