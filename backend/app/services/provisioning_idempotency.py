"""Idempotency-Key support for the provisioning API's write calls.

A billing system that times out waiting for "create account" cannot tell
whether the account was made. With an ``Idempotency-Key`` header it can send
the same request again and get the first answer back instead of a second
account, a second backup or a second password rotation.

Semantics (the same as the common payment-API convention):

- The key is scoped to the API token and kept for 24 hours.
- Only successful (2xx) responses are stored. A request that fails is
  forgotten, so the caller may retry it with the same key.
- Reusing a key with a different method, path or body is refused (422).
- A retry that arrives while the first request is still running gets 409.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.secrets import decrypt, encrypt
from app.models.entities import ApiToken, ProvisioningIdempotencyKey

HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
TTL = timedelta(hours=24)
# A first request that has not finished after this long is assumed dead (the
# process restarted) and a retry may take the key over.
IN_FLIGHT_STALE_AFTER = timedelta(minutes=15)
KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,255}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def request_fingerprint(method: str, path: str, query: str, payload: Any) -> str:
    body = json.dumps(jsonable_encoder(payload), sort_keys=True, separators=(",", ":")) if payload is not None else ""
    raw = "\n".join([method.upper(), path, query or "", body])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def purge_expired(db: Session, now: Optional[datetime] = None) -> int:
    cutoff = (now or _utcnow()) - TTL
    removed = (
        db.query(ProvisioningIdempotencyKey)
        .filter(ProvisioningIdempotencyKey.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(removed or 0)


def _replay(row: ProvisioningIdempotencyKey) -> JSONResponse:
    try:
        content = json.loads(decrypt(row.response_body) or "null")
    except (RuntimeError, ValueError):
        # SECRET_KEY rotated since: the stored answer is unreadable, and
        # pretending otherwise would hand back something wrong.
        raise HTTPException(status_code=409, detail="Stored response for this Idempotency-Key is no longer readable")
    return JSONResponse(content=content, status_code=row.status_code, headers={REPLAY_HEADER: "true"})


def run(
    db: Session,
    token: ApiToken,
    request: Request,
    payload: Any,
    handler: Callable[[], Any],
) -> Any:
    """Run ``handler`` at most once per (token, Idempotency-Key)."""
    key = (request.headers.get(HEADER) or "").strip()
    if not key:
        return handler()
    if not KEY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 1-255 characters of A-Z a-z 0-9 . _ : -")

    now = _utcnow()
    purge_expired(db, now)
    fingerprint = request_fingerprint(request.method, request.url.path, request.url.query, payload)

    row = ProvisioningIdempotencyKey(
        token_id=token.id,
        idem_key=key,
        method=request.method.upper(),
        path=request.url.path[:500],
        request_hash=fingerprint,
        status_code=0,
        response_body="",
        created_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(ProvisioningIdempotencyKey)
            .filter(ProvisioningIdempotencyKey.token_id == token.id, ProvisioningIdempotencyKey.idem_key == key)
            .first()
        )
        if existing is None:  # purged between the insert and the read
            raise HTTPException(status_code=409, detail="Idempotency-Key conflict, retry the request")
        if existing.request_hash != fingerprint:
            raise HTTPException(status_code=422, detail="Idempotency-Key was already used for a different request")
        if existing.status_code:
            return _replay(existing)
        if existing.created_at and existing.created_at > now - IN_FLIGHT_STALE_AFTER:
            raise HTTPException(status_code=409, detail="A request with this Idempotency-Key is still in progress")
        existing.created_at = now
        db.commit()
        row = existing
    row_id = row.id

    try:
        result = handler()
    except BaseException:
        # Forget the key: the request did not succeed, so a retry with the
        # same key must run it again rather than replay a failure.
        db.rollback()
        db.query(ProvisioningIdempotencyKey).filter(ProvisioningIdempotencyKey.id == row_id).delete(synchronize_session=False)
        db.commit()
        raise

    status_code = 200
    content = result
    if isinstance(result, JSONResponse):
        status_code = result.status_code
        content = json.loads(bytes(result.body).decode("utf-8") or "null")
    stored = db.query(ProvisioningIdempotencyKey).filter(ProvisioningIdempotencyKey.id == row_id).first()
    if stored is not None and 200 <= status_code < 300:
        stored.status_code = status_code
        stored.response_body = encrypt(json.dumps(jsonable_encoder(content)))
        db.commit()
    elif stored is not None:
        db.delete(stored)
        db.commit()
    return result
