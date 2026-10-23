import logging
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.entities import AuditLog
from app.services.webhooks import dispatch_webhook


logger = logging.getLogger("spanel.audit")


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
    db.add(AuditLog(user_id=user_id, action=action, target=target, detail=detail))
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to write audit log: action=%s target=%s", action, target)
        
    try:
        dispatch_webhook(db, user_id, action, target, detail)
    except Exception:
        pass
