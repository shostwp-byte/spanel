"""Outbound webhooks: a persistent outbox with retries and signed requests.

Every event for every subscribed endpoint becomes one ``webhook_outbox`` row.
A worker thread delivers due rows and, when the receiver is down or answers
non-2xx, schedules the next attempt with exponential backoff (30 s, 2 min,
10 min, 30 min, 1 h, 2 h, 4 h) before giving up after ``MAX_ATTEMPTS``. Rows
survive a restart of the panel, so a billing system that is offline for a few
hours still hears about the accounts created meanwhile. The same table is the
delivery history shown in Settings.

Each request is signed when the endpoint has a secret:

- ``X-SPanel-Timestamp``: Unix seconds when this attempt was signed.
- ``X-SPanel-Signature-256``: ``sha256=`` + hex HMAC-SHA256 over
  ``"<timestamp>." + body``. Receivers should reject timestamps more than five
  minutes old, which makes a captured request useless for replay.
- ``X-SPanel-Signature``: hex HMAC-SHA256 over the body alone, kept unchanged
  for receivers written before the timestamped signature existed.

``X-SPanel-Delivery`` identifies the row (the same on every retry) and the
payload's ``id`` the event (the same for every endpoint), so receivers can
drop duplicates.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import random
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.entities import User, WebhookEndpoint, WebhookOutbox

logger = logging.getLogger("spanel.webhooks")

MAX_ATTEMPTS = 8
# Delay after failed attempt n (1-based), before jitter.
RETRY_DELAYS_SECONDS = (30, 120, 600, 1800, 3600, 7200, 14400)
REQUEST_TIMEOUT_SECONDS = 10
SIGNATURE_TOLERANCE_SECONDS = 300
POLL_SECONDS = 5
# A row stuck in "delivering" this long belonged to a worker that died.
STALE_DELIVERING_AFTER = timedelta(minutes=5)
HISTORY_RETENTION = timedelta(days=30)
HISTORY_MAX_ROWS = 5000
USER_AGENT = "SPanel-Webhook/2.0"

# Events emitted by SPanel itself (the "account.*" lifecycle of accounts
# created through the provisioning API) plus the diagnostic ping. Every audit
# log action (create_website, delete_database, provisioning_create, ...) is
# also delivered under its own name to endpoints subscribed to it or to "*".
EVENT_CATALOGUE: dict[str, str] = {
    "account.created": "A billing system created a hosting account",
    "account.suspended": "An account was suspended (data.reason says why)",
    "account.unsuspended": "A suspended account was reactivated",
    "account.terminated": "An account and its websites were deleted",
    "account.password_changed": "The panel password was changed (never included)",
    "account.package_changed": "The account moved to another package or got new limits",
    "account.sftp_password_reset": "The SFTP/SSH password was reset (never included)",
    "account.backup_completed": "A backup requested through the API finished",
    "account.backup_failed": "A backup requested through the API failed",
    "webhook_test": "Diagnostic ping sent from the panel",
}

_wake = threading.Event()
_worker_lock = threading.Lock()
_worker_started = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


# ── Signing ──────────────────────────────────────────────────────────────

def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    """The X-SPanel-Signature-256 value for ``body`` signed at ``timestamp``."""
    message = f"{timestamp}.".encode("utf-8") + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def legacy_signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> bool:
    """Reference check for receivers (and for the tests): constant-time
    comparison and a freshness window against replay."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, ts, body), signature or "")


def signed_headers(secret: Optional[str], event: str, delivery_id: str, body: bytes, *, timestamp: Optional[int] = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-SPanel-Event": event,
        "X-SPanel-Delivery": delivery_id,
    }
    ts = int(time.time()) if timestamp is None else int(timestamp)
    headers["X-SPanel-Timestamp"] = str(ts)
    if secret:
        headers["X-SPanel-Signature"] = legacy_signature(secret, body)
        headers["X-SPanel-Signature-256"] = sign(secret, ts, body)
    return headers


# ── Secrets at rest ──────────────────────────────────────────────────────

def protect_secret(secret: Optional[str]) -> Optional[str]:
    """What to store in WebhookEndpoint.secret for a secret typed by a user."""
    from app.core.secrets import encrypt

    secret = (secret or "").strip()
    return encrypt(secret) if secret else None


def endpoint_secret(endpoint: Optional[WebhookEndpoint]) -> Optional[str]:
    """The signing secret of ``endpoint`` in clear. Raises RuntimeError when
    an encrypted secret cannot be read (SECRET_KEY rotated)."""
    from app.core.secrets import decrypt, is_encrypted

    if endpoint is None or not endpoint.secret:
        return None
    if is_encrypted(endpoint.secret):
        return decrypt(endpoint.secret)
    return endpoint.secret  # written before secrets were encrypted


def encrypt_legacy_secrets(db: Session) -> int:
    """Encrypt the plaintext secrets of endpoints created before encryption."""
    from app.core.secrets import encrypt, is_encrypted

    changed = 0
    for endpoint in db.query(WebhookEndpoint).filter(WebhookEndpoint.secret.isnot(None)).all():
        if endpoint.secret and not is_encrypted(endpoint.secret):
            endpoint.secret = encrypt(endpoint.secret)
            changed += 1
    if changed:
        db.commit()
    return changed


# ── Transport ────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would let a receiver point the panel at an address the URL
    # check below never saw (an internal service, the panel itself).
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _address_is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def check_destination(url: str) -> None:
    """Refuse URLs whose host resolves to a loopback, private or link-local
    address. The endpoint form checks literal addresses only; a hostname can
    still point inside, so the check is repeated at every delivery."""
    if os.environ.get("SPANEL_WEBHOOK_ALLOW_PRIVATE", "").strip().lower() in {"1", "true", "yes"}:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Webhook URL must be http(s) with a host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve {parsed.hostname}: {exc}") from exc
    for info in infos:
        if not _address_is_public(info[4][0]):
            raise ValueError(f"{parsed.hostname} resolves to a private or local address")


def _post(url: str, body: bytes, headers: dict) -> tuple[Optional[int], str]:
    """POST and return (status code, short response preview). Raises on
    network errors; HTTP errors come back as their status code."""
    check_destination(url)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _opener.open(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            raw = response.read(512).decode("utf-8", errors="replace").strip()
            return status_code, (raw[:240] if raw else "OK")
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read(512).decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            err_body = ""
        return exc.code, (err_body or f"HTTP {exc.code}: {exc.reason}")[:240]


# Replaced in tests.
transport = _post


# ── Outbox ───────────────────────────────────────────────────────────────

def _new_delivery_id() -> str:
    return f"whd_{secrets.token_hex(8)}"


def _event_matches(subscribed: str, event: str) -> bool:
    for item in (subscribed or "*").split(","):
        pattern = item.strip()
        if not pattern:
            continue
        if pattern == "*" or pattern == event:
            return True
        if pattern.endswith(".*") and event.startswith(pattern[:-1]):
            return True
    return False


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next try after ``attempts`` failed ones (with ±10% jitter)."""
    base = RETRY_DELAYS_SECONDS[min(max(attempts, 1), len(RETRY_DELAYS_SECONDS)) - 1]
    return timedelta(seconds=base * random.uniform(0.9, 1.1))


def enqueue(db: Session, endpoint: WebhookEndpoint, payload: dict, *, replayed_from: Optional[str] = None, max_attempts: int = MAX_ATTEMPTS) -> WebhookOutbox:
    now = _utcnow()
    row = WebhookOutbox(
        delivery_id=_new_delivery_id(),
        endpoint_id=endpoint.id,
        user_id=endpoint.user_id,
        url=endpoint.url,
        event=str(payload.get("event") or "unknown")[:100],
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending",
        attempts=0,
        max_attempts=max_attempts,
        next_attempt_at=now,
        has_signature=bool(endpoint.secret),
        replayed_from=replayed_from,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    return row


def _endpoints_for(db: Session, owner_ids: Iterable[int], event: str) -> list[WebhookEndpoint]:
    owner_ids = list(set(owner_ids))
    if not owner_ids:
        return []
    endpoints = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.user_id.in_(owner_ids), WebhookEndpoint.is_active == True)  # noqa: E712
        .all()
    )
    return [ep for ep in endpoints if _event_matches(ep.events, event)]


def _admin_ids(db: Session) -> list[int]:
    return [row.id for row in db.query(User.id).filter(User.role == "admin").all()]


def _fan_out(db: Session, owner_ids: Iterable[int], payload: dict) -> list[WebhookOutbox]:
    rows = [enqueue(db, ep, payload) for ep in _endpoints_for(db, owner_ids, payload["event"])]
    if rows:
        db.commit()
        _wake.set()
    return rows


def emit_event(db: Session, event: str, data: dict, *, subject: Optional[User] = None) -> list[WebhookOutbox]:
    """Queue a catalogue event for administrators' endpoints and, when the
    subject account belongs to a reseller, the reseller's endpoints."""
    owner_ids = set(_admin_ids(db))
    if subject is not None and subject.reseller_id:
        owner_ids.add(subject.reseller_id)
    now = _utcnow()
    payload = {
        "id": f"evt_{secrets.token_hex(8)}",
        "event": event,
        "created_at": _iso(now),
        "data": data,
        # The flat fields every SPanel webhook has carried, so receivers
        # written for audit events can read these too.
        "user_id": subject.id if subject is not None else None,
        "username": subject.username if subject is not None else (data.get("username") or ""),
        "target": data.get("external_id") or "",
        "detail": "",
        "timestamp": _iso(now),
    }
    try:
        return _fan_out(db, owner_ids, payload)
    except Exception:  # noqa: BLE001 - a webhook must never fail the action it reports
        db.rollback()
        logger.exception("Could not queue webhook event %s", event)
        return []


def dispatch_webhook(db: Session, user_id: Optional[int], action: str, target: str, detail: str = "") -> None:
    """Queue an audit-log action for the endpoints allowed to see it."""
    # No acting user means the panel itself or a billing system did it: every
    # provisioning call is logged that way. Those events used to be dropped
    # here, so a billing integration never heard about the accounts it created.
    # They go to administrators' endpoints, the only owners who can see them.
    user = None
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return

    # Endpoints of the user, their reseller, and every administrator.
    endpoint_users = set(_admin_ids(db))
    if user is not None:
        endpoint_users.add(user.id)
        if user.reseller_id:
            endpoint_users.add(user.reseller_id)

    now = _utcnow()
    payload = {
        "id": f"evt_{secrets.token_hex(8)}",
        "event": action,
        "user_id": user.id if user is not None else None,
        "username": user.username if user is not None else "",
        "target": target,
        "detail": detail,
        "timestamp": _iso(now),
    }
    _fan_out(db, endpoint_users, payload)


def _claim(db: Session, row_id: int, now: datetime) -> bool:
    """Take a pending row for delivery; False if another worker got it."""
    claimed = (
        db.query(WebhookOutbox)
        .filter(WebhookOutbox.id == row_id, WebhookOutbox.status == "pending")
        .update({"status": "delivering", "updated_at": now}, synchronize_session=False)
    )
    db.commit()
    return claimed == 1


def deliver(
    db: Session,
    row: WebhookOutbox,
    *,
    secret: Optional[str] = None,
    use_endpoint_secret: bool = True,
    now: Optional[datetime] = None,
) -> WebhookOutbox:
    """Make one attempt for ``row`` (already claimed) and record the outcome."""
    body = (row.payload or "{}").encode("utf-8")
    started = time.perf_counter()
    status_code: Optional[int] = None
    preview = ""
    ok = False
    try:
        if use_endpoint_secret and row.endpoint_id is not None:
            endpoint = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == row.endpoint_id).first()
            if endpoint is not None:
                secret = endpoint_secret(endpoint)
                row.url = endpoint.url
        headers = signed_headers(secret, row.event, row.delivery_id, body)
        status_code, preview = transport(row.url, body, headers)
        ok = status_code is not None and 200 <= int(status_code) < 300
    except Exception as exc:  # noqa: BLE001 - every failure is a failed attempt
        preview = str(exc)[:240]
        logger.warning("Webhook %s to %s failed: %s", row.delivery_id, row.url, exc)

    now = now or _utcnow()
    row.attempts = int(row.attempts or 0) + 1
    row.last_status_code = status_code
    row.response_preview = preview
    row.latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    row.has_signature = bool(secret)
    row.updated_at = now
    if ok:
        row.status = "success"
        row.delivered_at = now
        row.next_attempt_at = None
    elif row.attempts >= int(row.max_attempts or MAX_ATTEMPTS):
        row.status = "failed"
        row.next_attempt_at = None
    else:
        row.status = "pending"
        row.next_attempt_at = now + retry_delay(row.attempts)
    db.commit()
    return row


def process_due(db: Session, *, now: Optional[datetime] = None, limit: int = 50) -> int:
    """Deliver every row that is due. Returns the number of attempts made."""
    now = now or _utcnow()
    db.query(WebhookOutbox).filter(
        WebhookOutbox.status == "delivering",
        WebhookOutbox.updated_at < now - STALE_DELIVERING_AFTER,
    ).update({"status": "pending", "next_attempt_at": now}, synchronize_session=False)
    db.commit()
    due_ids = [
        row.id
        for row in db.query(WebhookOutbox.id)
        .filter(WebhookOutbox.status == "pending", WebhookOutbox.next_attempt_at <= now)
        .order_by(WebhookOutbox.next_attempt_at.asc(), WebhookOutbox.id.asc())
        .limit(limit)
        .all()
    ]
    attempted = 0
    for row_id in due_ids:
        if not _claim(db, row_id, now):
            continue
        row = db.query(WebhookOutbox).filter(WebhookOutbox.id == row_id).first()
        if row is None:
            continue
        deliver(db, row, now=now)
        attempted += 1
    return attempted


def purge_history(db: Session, *, now: Optional[datetime] = None) -> int:
    now = now or _utcnow()
    finished = WebhookOutbox.status.in_(("success", "failed"))
    removed = (
        db.query(WebhookOutbox)
        .filter(finished, WebhookOutbox.created_at < now - HISTORY_RETENTION)
        .delete(synchronize_session=False)
    )
    keep_ids = [
        row.id
        for row in db.query(WebhookOutbox.id).filter(finished).order_by(WebhookOutbox.id.desc()).limit(HISTORY_MAX_ROWS).all()
    ]
    if len(keep_ids) == HISTORY_MAX_ROWS:
        removed += (
            db.query(WebhookOutbox)
            .filter(finished, WebhookOutbox.id < min(keep_ids))
            .delete(synchronize_session=False)
        )
    db.commit()
    return int(removed or 0)


def _worker_loop() -> None:
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            encrypt_legacy_secrets(db)
    except Exception:  # noqa: BLE001
        logger.warning("Could not encrypt legacy webhook secrets", exc_info=True)
    last_purge = 0.0
    while True:
        try:
            with SessionLocal() as db:
                process_due(db)
                if time.monotonic() - last_purge > 3600:
                    purge_history(db)
                    last_purge = time.monotonic()
        except Exception:  # noqa: BLE001 - keep the worker alive
            logger.exception("Webhook worker iteration failed")
        _wake.wait(POLL_SECONDS)
        _wake.clear()


def start_webhook_worker() -> None:
    """Start the delivery worker once per process."""
    global _worker_started
    if os.environ.get("SPANEL_DISABLE_WEBHOOK_WORKER", "").strip().lower() in {"1", "true", "yes"}:
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, name="spanel-webhooks", daemon=True).start()


# ── History, tests and replays (Settings page) ───────────────────────────

def row_to_dict(row: WebhookOutbox) -> dict:
    try:
        payload = json.loads(row.payload or "{}")
    except ValueError:
        payload = {}
    return {
        "id": row.delivery_id,
        "webhook_id": row.endpoint_id,
        "user_id": row.user_id,
        "url": row.url,
        "event": row.event,
        "payload": payload,
        "status": row.status,
        "status_code": row.last_status_code,
        "latency_ms": row.latency_ms,
        "response_preview": row.response_preview,
        "has_signature": bool(row.has_signature),
        "replayed_from": row.replayed_from,
        "attempts": row.attempts,
        "max_attempts": row.max_attempts,
        "next_attempt_at": _iso(row.next_attempt_at),
        "delivered_at": _iso(row.delivered_at),
        "created_at": _iso(row.created_at),
    }


def send_webhook_sync(
    url: str,
    secret: Optional[str],
    payload: dict,
    *,
    webhook_id: Optional[int] = None,
    user_id: Optional[int] = None,
    replayed_from: Optional[str] = None,
    retry: bool = False,
) -> dict:
    """Deliver ``payload`` now and record it in the history.

    Used by the "test" button: the answer is needed in the same request, and
    a test that fails is not retried unless ``retry`` is set.
    """
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        now = _utcnow()
        row = WebhookOutbox(
            delivery_id=_new_delivery_id(),
            endpoint_id=webhook_id,
            user_id=user_id,
            url=url,
            event=str(payload.get("event") or "unknown")[:100],
            payload=json.dumps(payload, ensure_ascii=False),
            status="delivering",
            attempts=0,
            max_attempts=MAX_ATTEMPTS if retry else 1,
            next_attempt_at=now,
            has_signature=bool(secret),
            replayed_from=replayed_from,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        deliver(db, row, secret=secret, use_endpoint_secret=False)
        return row_to_dict(row)


def list_deliveries(user_id: Optional[int] = None, *, is_admin: bool = False, limit: int = 50) -> list[dict]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        query = db.query(WebhookOutbox)
        if not is_admin:
            query = query.filter(WebhookOutbox.user_id == user_id)
        rows = query.order_by(WebhookOutbox.id.desc()).limit(max(1, min(int(limit or 50), 500))).all()
        return [row_to_dict(row) for row in rows]


def clear_deliveries(user_id: Optional[int] = None, *, is_admin: bool = False) -> int:
    """Remove finished deliveries from the history. Pending retries stay."""
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        query = db.query(WebhookOutbox).filter(WebhookOutbox.status.in_(("success", "failed")))
        if not is_admin:
            query = query.filter(WebhookOutbox.user_id == user_id)
        removed = query.delete(synchronize_session=False)
        db.commit()
        return int(removed or 0)


def replay_delivery(
    db: Session,
    delivery_id: str,
    *,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> dict:
    target = db.query(WebhookOutbox).filter(WebhookOutbox.delivery_id == delivery_id).first()
    if not target:
        raise ValueError("Không tìm thấy bản ghi lịch sử Webhook này.")
    if not is_admin and target.user_id != user_id:
        raise ValueError("Không có quyền gửi lại bản ghi Webhook này.")
    try:
        payload: dict[str, Any] = json.loads(target.payload or "{}")
    except ValueError:
        payload = {}
    payload["replayed_at"] = _iso(_utcnow())
    endpoint = None
    if target.endpoint_id is not None:
        endpoint = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == target.endpoint_id).first()
    if endpoint is None:
        raise ValueError("Webhook endpoint của bản ghi này đã bị xóa.")
    row = enqueue(db, endpoint, payload, replayed_from=delivery_id)
    row.status = "delivering"
    db.commit()
    deliver(db, row)
    return row_to_dict(row)
