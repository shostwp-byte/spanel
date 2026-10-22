from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import User, WebhookEndpoint

logger = logging.getLogger("spanel.webhooks")

_DELIVERY_LOG_PATH = Path(
    os.environ.get("SPANEL_WEBHOOK_LOG_FILE", "/var/lib/spanel/webhook-deliveries.json")
)
_FALLBACK_DELIVERY_LOG_PATH = Path(".spanel_webhook_deliveries.json")
_MAX_DELIVERIES = 100
_log_lock = threading.Lock()


def _resolve_log_path() -> Path:
    try:
        _DELIVERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if os.access(_DELIVERY_LOG_PATH.parent, os.W_OK):
            return _DELIVERY_LOG_PATH
    except OSError:
        pass
    return _FALLBACK_DELIVERY_LOG_PATH


def _read_deliveries() -> list[dict]:
    path = _resolve_log_path()
    try:
        if not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _write_deliveries(items: list[dict]) -> None:
    path = _resolve_log_path()
    try:
        trimmed = items[:_MAX_DELIVERIES]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
    except Exception as exc:
        logger.warning("Could not persist webhook delivery log: %s", exc)


def _record_delivery(entry: dict) -> dict:
    with _log_lock:
        items = _read_deliveries()
        items.insert(0, entry)
        _write_deliveries(items)
    return entry


def send_webhook_sync(
    url: str,
    secret: Optional[str],
    payload: dict,
    *,
    webhook_id: Optional[int] = None,
    user_id: Optional[int] = None,
    replayed_from: Optional[str] = None,
) -> dict:
    """Send a webhook synchronously, measure latency, and record the delivery."""
    event_name = str(payload.get("event") or "unknown")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SPanel-Webhook/1.0",
        "X-SPanel-Event": event_name,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    if secret:
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-SPanel-Signature"] = signature

    started = time.perf_counter()
    status = "failed"
    status_code: Optional[int] = None
    response_preview = ""

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=8) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            raw_resp = response.read(512).decode("utf-8", errors="replace").strip()
            response_preview = raw_resp[:240] if raw_resp else "OK"
            status = "success" if (status_code and 200 <= int(status_code) < 300) else "failed"
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            err_body = exc.read(512).decode("utf-8", errors="replace").strip()
        except Exception:
            err_body = ""
        response_preview = (err_body or f"HTTP {exc.code}: {exc.reason}")[:240]
        logger.warning("Webhook %s returned HTTP %s", url, exc.code)
    except Exception as exc:
        response_preview = str(exc)[:240]
        logger.warning("Failed to send webhook to %s: %s", url, exc)

    latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    entry = {
        "id": f"whd_{secrets.token_hex(6)}",
        "webhook_id": webhook_id,
        "user_id": user_id,
        "url": url,
        "event": event_name,
        "payload": payload,
        "status": status,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "response_preview": response_preview,
        "has_signature": bool(secret),
        "replayed_from": replayed_from,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _record_delivery(entry)


def _send_webhook(
    url: str,
    secret: Optional[str],
    payload: dict,
    webhook_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> None:
    send_webhook_sync(url, secret, payload, webhook_id=webhook_id, user_id=user_id)


def list_deliveries(user_id: Optional[int] = None, *, is_admin: bool = False, limit: int = 50) -> list[dict]:
    with _log_lock:
        items = _read_deliveries()
    if not is_admin and user_id is not None:
        items = [item for item in items if item.get("user_id") == user_id]
    return items[: max(1, min(limit, _MAX_DELIVERIES))]


def clear_deliveries(user_id: Optional[int] = None, *, is_admin: bool = False) -> int:
    with _log_lock:
        items = _read_deliveries()
        if is_admin:
            removed = len(items)
            _write_deliveries([])
            return removed
        kept = [item for item in items if item.get("user_id") != user_id]
        removed = len(items) - len(kept)
        _write_deliveries(kept)
        return removed


def replay_delivery(
    db: Session,
    delivery_id: str,
    *,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> dict:
    with _log_lock:
        items = _read_deliveries()
    target = next((item for item in items if item.get("id") == delivery_id), None)
    if not target:
        raise ValueError("Không tìm thấy bản ghi lịch sử Webhook này.")
    if not is_admin and user_id is not None and target.get("user_id") != user_id:
        raise ValueError("Không có quyền gửi lại bản ghi Webhook này.")

    url = target.get("url") or ""
    webhook_id = target.get("webhook_id")
    secret: Optional[str] = None
    if webhook_id is not None:
        ep = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id).first()
        if ep:
            url = ep.url
            secret = ep.secret

    payload = dict(target.get("payload") or {})
    payload["replayed_at"] = datetime.now(timezone.utc).isoformat()
    return send_webhook_sync(
        url,
        secret,
        payload,
        webhook_id=webhook_id,
        user_id=target.get("user_id") or user_id,
        replayed_from=delivery_id,
    )


def dispatch_webhook(db: Session, user_id: Optional[int], action: str, target: str, detail: str = "") -> None:
    if not user_id:
        return

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return

    # Find endpoints for the user, their reseller, and any admin endpoints
    endpoint_users = {user.id}
    if user.reseller_id:
        endpoint_users.add(user.reseller_id)
    for admin_row in db.query(User).filter(User.role == "admin").all():
        endpoint_users.add(admin_row.id)

    endpoints = (
        db.query(WebhookEndpoint)
        .filter(
            WebhookEndpoint.user_id.in_(list(endpoint_users)),
            WebhookEndpoint.is_active == True,  # noqa: E712
        )
        .all()
    )

    if not endpoints:
        return

    payload = {
        "event": action,
        "user_id": user.id,
        "username": user.username,
        "target": target,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    for ep in endpoints:
        allowed_events = [item.strip() for item in (ep.events or "*").split(",") if item.strip()]
        if "*" not in allowed_events and action not in allowed_events:
            continue

        threading.Thread(
            target=_send_webhook,
            args=(ep.url, ep.secret, payload, ep.id, ep.user_id),
            daemon=True,
        ).start()
