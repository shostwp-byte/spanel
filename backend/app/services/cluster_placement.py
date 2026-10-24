"""Where a new or moved account should go: a suggestion, never a routing.

suggest_node() scores every node that can host accounts from the figures
the health checks keep (multi_server.apply_health_result): free disk, free
memory, CPU and load per core. A node is left out - with the reason shown -
when it is disabled, offline, has not answered for STALE_SECONDS, hosts no
accounts by role (dns_only, db_node), is nearly full, or has less free disk
than the account needs with a margin. Among the rest the highest score wins,
fewer websites breaking a tie. This node can be a candidate too when the
question is where to create an account; never when it is where to move one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

STALE_SECONDS = 15 * 60
DISK_FULL_PERCENT = 90.0
RAM_FULL_PERCENT = 95.0
SPACE_MARGIN = 1.2
WEIGHTS = {"disk": 0.4, "ram": 0.3, "cpu": 0.2, "load": 0.1}
HOSTING_ROLES = ("full_peer", "local")


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _age_seconds(iso: Optional[str], now: datetime) -> Optional[float]:
    if not iso:
        return None
    try:
        seen = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (now - seen).total_seconds()


def score_node(metrics: dict[str, Any], *, role: str, status: str, enabled: bool, last_seen_at: Optional[str],
               required_bytes: int = 0, now: Optional[datetime] = None) -> tuple[Optional[float], list[str]]:
    """(score 0-100 or None when not eligible, reasons)."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if not enabled:
        return None, ["disabled"]
    if role not in HOSTING_ROLES:
        return None, [f"role {role} does not host accounts"]
    if status != "online":
        return None, ["offline"]
    age = _age_seconds(last_seen_at, now)
    if role != "local" and (age is None or age > STALE_SECONDS):
        return None, ["no recent health data"]

    disk_pct = _num(metrics.get("disk_percent"))
    ram_pct = _num(metrics.get("ram_percent"))
    cpu_pct = _num(metrics.get("cpu_percent"))
    load = _num(metrics.get("load_1"))
    cores = _num(metrics.get("cpu_cores")) or 1.0
    free = _num(metrics.get("backup_free_bytes")) or _num(metrics.get("disk_free_bytes"))

    if disk_pct is not None and disk_pct >= DISK_FULL_PERCENT:
        return None, [f"disk {disk_pct:.0f}% full"]
    if ram_pct is not None and ram_pct >= RAM_FULL_PERCENT:
        return None, [f"memory {ram_pct:.0f}% used"]
    if required_bytes and free is not None and free < required_bytes * SPACE_MARGIN:
        return None, [f"{int(free)} bytes free, {int(required_bytes * SPACE_MARGIN)} needed"]

    parts = {
        "disk": 100.0 - disk_pct if disk_pct is not None else 50.0,
        "ram": 100.0 - ram_pct if ram_pct is not None else 50.0,
        "cpu": 100.0 - cpu_pct if cpu_pct is not None else 50.0,
        "load": max(0.0, 1.0 - load / cores) * 100.0 if load is not None else 50.0,
    }
    if disk_pct is None or ram_pct is None:
        reasons.append("incomplete metrics")
    score = round(sum(WEIGHTS[k] * max(0.0, min(100.0, v)) for k, v in parts.items()), 1)
    reasons.append(f"disk {parts['disk']:.0f}% free, RAM {parts['ram']:.0f}% free, CPU {parts['cpu']:.0f}% idle")
    return score, reasons


def required_bytes_for(db: Session, user_id: Optional[int]) -> int:
    if not user_id:
        return 0
    from app.models.entities import User
    from app.services import storage_quota

    user = db.get(User, user_id)
    if user is None:
        return 0
    cached = storage_quota.cached_storage_used_bytes(user.id)
    if cached is not None:
        return int(cached)
    try:
        return int(storage_quota.user_storage_used_bytes(db, user, use_cache=True))
    except Exception:  # noqa: BLE001 - unknown size: rank without it
        return 0


def rank(candidates: list[dict[str, Any]], *, required_bytes: int = 0, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    ranked = []
    for node in candidates:
        metrics = node.get("metrics") or {}
        score, reasons = score_node(metrics, role=node.get("role") or "full_peer", status=node.get("status") or "unknown",
                                    enabled=bool(node.get("enabled", True)), last_seen_at=node.get("last_seen_at"),
                                    required_bytes=required_bytes, now=now)
        ranked.append({"id": node.get("id"), "name": node.get("name"), "role": node.get("role"),
                       "eligible": score is not None, "score": score, "reasons": reasons,
                       "websites_count": metrics.get("websites_count")})
    ranked.sort(key=lambda c: (not c["eligible"], -(c["score"] or 0), c["websites_count"] or 0))
    return ranked


def suggest_node(db: Session, *, purpose: str = "account", user_id: Optional[int] = None) -> dict[str, Any]:
    """Candidates best first, and the suggestion (or None)."""
    from app.services import multi_server

    purpose = "transfer" if purpose == "transfer" else "account"
    with multi_server._state_lock:
        state = multi_server._read_state()
    candidates = [{
        "id": n.get("id"), "name": n.get("name"), "role": n.get("role") or "full_peer",
        "status": n.get("status"), "enabled": n.get("enabled", True), "last_seen_at": n.get("last_seen_at"),
        "metrics": n.get("remote_info") or {},
    } for n in state.get("nodes", [])]
    if purpose == "account":
        local = multi_server.local_node_summary(db)
        candidates.append({"id": "local", "name": local.get("node_name") or "local", "role": "local",
                           "status": "online", "enabled": True,
                           "last_seen_at": datetime.now(timezone.utc).isoformat(), "metrics": local})
    required = required_bytes_for(db, user_id) if purpose == "transfer" else 0
    ranked = rank(candidates, required_bytes=required)
    suggested = next((c for c in ranked if c["eligible"]), None)
    return {"purpose": purpose, "required_bytes": required, "suggested": suggested, "candidates": ranked}
