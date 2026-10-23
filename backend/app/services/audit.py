import datetime as dt
import hashlib
import logging
from typing import Optional

from fastapi import Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.entities import AuditLog, User
from app.services.webhooks import dispatch_webhook


logger = logging.getLogger("spanel.audit")
GENESIS_HASH = "0" * 64


def _canonical_ts(value: Optional[dt.datetime]) -> str:
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _compute_entry_hash(
    prev_hash: str,
    user_id: Optional[int],
    action: str,
    target: str,
    detail: str,
    created_at: Optional[dt.datetime],
) -> str:
    payload = "|".join([
        prev_hash or GENESIS_HASH,
        str(user_id if user_id is not None else ""),
        action or "",
        target or "",
        detail or "",
        _canonical_ts(created_at),
    ])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def log_action(
    db: Session,
    user_id: Optional[int],
    action: str,
    target: str,
    detail: str = "",
    request: Optional[Request] = None,
) -> None:
    extras = []
    if request is not None:
        # request.client.host is trustworthy because uvicorn is started with
        # --proxy-headers --forwarded-allow-ips 127.0.0.1, so X-Forwarded-For
        # is only honoured when the peer is the trusted local Nginx (matches
        # /usr/local/sbin/spanel-api-start). Direct hits on 0.0.0.0:2222 from
        # the public Internet cannot forge the source IP.
        ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")[:200]
        if ip:
            extras.append(f"ip={ip}")
        if ua:
            extras.append(f"ua={ua}")
    if extras:
        if detail:
            detail = f"{detail} | " + " ".join(extras)
        else:
            detail = " ".join(extras)

    now = dt.datetime.utcnow().replace(microsecond=0)
    prev_hash = GENESIS_HASH
    try:
        last_entry = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        if last_entry is not None:
            if last_entry.entry_hash:
                prev_hash = last_entry.entry_hash
            else:
                prev_hash = _compute_entry_hash(
                    last_entry.prev_hash or GENESIS_HASH,
                    last_entry.user_id,
                    last_entry.action,
                    last_entry.target,
                    last_entry.detail,
                    last_entry.created_at,
                )
    except Exception:
        prev_hash = GENESIS_HASH

    entry_hash = _compute_entry_hash(prev_hash, user_id, action, target, detail, now)
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target=target,
            detail=detail,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            created_at=now,
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to write audit log: action=%s target=%s", action, target)

    try:
        dispatch_webhook(db, user_id, action, target, detail)
    except Exception:
        pass


def verify_audit_chain(db: Session, seal_unhashed: bool = True) -> dict:
    """Verify the SHA-256 hash chain across all AuditLog entries.

    If legacy rows exist without `entry_hash` and `seal_unhashed=True`, they are
    cryptographically sealed into the chain on first verification.
    """
    rows = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    total = len(rows)
    if total == 0:
        return {
            "valid": True,
            "total_entries": 0,
            "verified_entries": 0,
            "sealed_legacy_entries": 0,
            "head_hash": GENESIS_HASH,
            "tampered_entries": [],
            "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    expected_prev = GENESIS_HASH
    sealed_count = 0
    verified_count = 0
    tampered = []

    for row in rows:
        computed = _compute_entry_hash(
            expected_prev,
            row.user_id,
            row.action,
            row.target,
            row.detail,
            row.created_at,
        )
        if not row.entry_hash:
            if seal_unhashed:
                row.prev_hash = expected_prev
                row.entry_hash = computed
                sealed_count += 1
                verified_count += 1
                expected_prev = computed
            else:
                expected_prev = computed
            continue

        if row.prev_hash != expected_prev or row.entry_hash != computed:
            tampered.append({
                "id": row.id,
                "action": row.action,
                "target": row.target,
                "created_at": _canonical_ts(row.created_at),
                "expected_hash": computed[:16],
                "actual_hash": (row.entry_hash or "")[:16],
            })
            expected_prev = row.entry_hash
        else:
            verified_count += 1
            expected_prev = row.entry_hash

    if sealed_count > 0:
        try:
            db.commit()
        except Exception:
            db.rollback()

    return {
        "valid": len(tampered) == 0,
        "total_entries": total,
        "verified_entries": verified_count,
        "sealed_legacy_entries": sealed_count,
        "head_hash": expected_prev,
        "tampered_entries": tampered[:25],
        "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def list_audit_logs(
    db: Session,
    q: str = "",
    action_filter: str = "",
    limit: int = 100,
) -> dict:
    safe_limit = max(1, min(int(limit or 100), 500))
    chain_status = verify_audit_chain(db, seal_unhashed=True)

    query = db.query(AuditLog)
    if action_filter and action_filter.strip():
        query = query.filter(AuditLog.action.ilike(f"%{action_filter.strip()}%"))
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                AuditLog.action.ilike(term),
                AuditLog.target.ilike(term),
                AuditLog.detail.ilike(term),
                AuditLog.entry_hash.ilike(term),
            )
        )

    rows = query.order_by(AuditLog.id.desc()).limit(safe_limit).all()
    user_ids = {r.user_id for r in rows if r.user_id is not None}
    user_map = {}
    if user_ids:
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        user_map = {u.id: u.username for u in users}

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "user_id": r.user_id,
            "username": user_map.get(r.user_id, "system" if r.user_id is None else f"user#{r.user_id}"),
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "prev_hash": r.prev_hash or "",
            "entry_hash": r.entry_hash or "",
            "created_at": _canonical_ts(r.created_at) + "Z" if r.created_at else "",
        })

    return {
        "items": items,
        "chain": chain_status,
    }
