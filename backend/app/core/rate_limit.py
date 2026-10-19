"""Thread-safe in-memory sliding-window rate limiter for sensitive API endpoints."""

from __future__ import annotations

import threading
import time
from collections import deque
from fastapi import HTTPException, Request, status

_LOCK = threading.Lock()
_BUCKETS: dict[str, deque[float]] = {}
_MAX_KEYS = 4096


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host.strip()
    return "unknown"


def enforce_rate_limit(
    request: Request,
    bucket: str,
    max_requests: int = 10,
    window_seconds: int = 60,
    key_suffix: str = "",
) -> None:
    now = time.monotonic()
    cutoff = now - window_seconds
    ip = client_ip(request)
    key = f"{bucket}:{ip}:{key_suffix}" if key_suffix else f"{bucket}:{ip}"

    with _LOCK:
        if len(_BUCKETS) > _MAX_KEYS and key not in _BUCKETS:
            stale_keys = [
                k for k, q in _BUCKETS.items() if not q or q[-1] <= cutoff
            ]
            for k in stale_keys:
                _BUCKETS.pop(k, None)

        q = _BUCKETS.get(key)
        if q is None:
            q = deque()
            _BUCKETS[key] = q

        while q and q[0] <= cutoff:
            q.popleft()

        if len(q) >= max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait a moment and try again.",
            )
        q.append(now)
