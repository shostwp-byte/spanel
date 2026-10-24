import datetime as dt
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import AuditLog, User
from app.services.webhooks import dispatch_webhook


logger = logging.getLogger("spanel.audit")
GENESIS_HASH = "0" * 64
AUDIT_HMAC_KEY_FILE = Path("/etc/spanel/audit-hmac.key")


def _audit_anchor_path() -> Path:
    custom = os.environ.get("SPANEL_AUDIT_ANCHOR_PATH", "").strip()
    if custom:
        return Path(custom)
    return Path("/var/lib/spanel/audit-chain-anchor.json")


def _audit_hmac_key() -> bytes:
    env_key = os.environ.get("SPANEL_AUDIT_HMAC_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")
    try:
        if AUDIT_HMAC_KEY_FILE.is_file():
            raw = AUDIT_HMAC_KEY_FILE.read_text(encoding="utf-8").strip()
            if raw:
                return raw.encode("utf-8")
    except OSError:
        pass
    # No hard-coded fallback: a key anyone can read in this repository would let
    # them recompute the whole chain. config.py already refuses a missing or
    # default SECRET_KEY in production; this covers an empty one elsewhere.
    secret = settings.secret_key
    if not secret:
        raise RuntimeError("SECRET_KEY is empty; cannot derive the audit chain key")
    return hmac.new(secret.encode("utf-8"), b"spanel-audit-hmac-v1", hashlib.sha256).digest()


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
    return hmac.new(
        _audit_hmac_key(),
        payload.encode("utf-8", errors="replace"),
        hashlib.sha256,
    ).hexdigest()


def _read_audit_anchor() -> Optional[dict]:
    path = _audit_anchor_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def _write_audit_anchor(
    last_id: Optional[int],
    head_hash: str,
    total_entries: int,
    action: str,
    target: str,
) -> None:
    anchor = {
        "last_id": int(last_id or 0),
        "head_hash": head_hash,
        "total_entries": int(total_entries),
        "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    logger.info(
        "AUDIT_ANCHOR id=%s total=%s head=%s action=%s target=%s",
        anchor["last_id"],
        anchor["total_entries"],
        head_hash,
        action,
        target,
    )
    path = _audit_anchor_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(anchor, indent=2), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(path)
    except OSError:
        pass


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
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target=target,
        detail=detail,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        created_at=now,
    )
    db.add(entry)
    try:
        db.commit()
        total_count = db.query(AuditLog).count()
        _write_audit_anchor(entry.id, entry_hash, total_count, action, target)
    except Exception:
        db.rollback()
        logger.exception("Failed to write audit log: action=%s target=%s", action, target)

    try:
        dispatch_webhook(db, user_id, action, target, detail)
    except Exception:
        pass


def verify_audit_chain(db: Session, seal_unhashed: bool = False) -> dict:
    """Verify the HMAC-SHA256 hash chain and external anchor across all AuditLog entries.

    By default `seal_unhashed=False` so rows with blanked `entry_hash` are flagged
    as tampered rather than silently re-sealed. Even when `seal_unhashed=True` (used
    only for one-time migration of pre-chain rows), any unhashed row appearing after
    an already-sealed row is treated as tampering.
    """
    rows = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    total = len(rows)
    anchor = _read_audit_anchor()
    if total == 0:
        tampered_empty = []
        if anchor and int(anchor.get("total_entries") or 0) > 0:
            tampered_empty.append({
                "id": int(anchor.get("last_id") or 0),
                "action": "chain_truncated",
                "target": "audit_logs",
                "created_at": anchor.get("updated_at", ""),
                "expected_hash": (anchor.get("head_hash") or "")[:16],
                "actual_hash": "EMPTY_TABLE",
            })
        return {
            "valid": len(tampered_empty) == 0,
            "cipher": "HMAC-SHA256",
            "total_entries": 0,
            "verified_entries": 0,
            "sealed_legacy_entries": 0,
            "head_hash": GENESIS_HASH,
            "tampered_entries": tampered_empty,
            "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    expected_prev = GENESIS_HASH
    sealed_count = 0
    verified_count = 0
    tampered = []
    seen_sealed_row = False

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
            if seal_unhashed and not seen_sealed_row and not row.prev_hash:
                row.prev_hash = expected_prev
                row.entry_hash = computed
                sealed_count += 1
                verified_count += 1
                expected_prev = computed
            else:
                tampered.append({
                    "id": row.id,
                    "action": row.action,
                    "target": row.target,
                    "created_at": _canonical_ts(row.created_at),
                    "expected_hash": computed[:16],
                    "actual_hash": "MISSING_HASH",
                })
                expected_prev = computed
            continue

        seen_sealed_row = True
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

    if anchor:
        anchor_total = int(anchor.get("total_entries") or 0)
        anchor_last_id = int(anchor.get("last_id") or 0)
        anchor_head = str(anchor.get("head_hash") or "")
        last_row_id = int(rows[-1].id or 0)
        if total < anchor_total or (anchor_last_id and last_row_id < anchor_last_id):
            tampered.append({
                "id": anchor_last_id or last_row_id,
                "action": "chain_tail_truncated",
                "target": f"expected_total={anchor_total},actual_total={total}",
                "created_at": anchor.get("updated_at", ""),
                "expected_hash": anchor_head[:16],
                "actual_hash": expected_prev[:16],
            })
        elif total == anchor_total and anchor_head and anchor_head != expected_prev and not tampered:
            tampered.append({
                "id": last_row_id,
                "action": "chain_anchor_mismatch",
                "target": "audit_logs",
                "created_at": anchor.get("updated_at", ""),
                "expected_hash": anchor_head[:16],
                "actual_hash": expected_prev[:16],
            })

    return {
        "valid": len(tampered) == 0,
        "cipher": "HMAC-SHA256",
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
    chain_status = verify_audit_chain(db, seal_unhashed=False)

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
