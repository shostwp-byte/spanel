"""The cluster's background work, in the API process.

One daemon thread, started from main.py like the resource monitor: it
pings every node at the health interval (status, latency, load, an alert
when one goes down), delivers the DNS outbox (at once when kick() is called after a zone change,
otherwise every LOOP_SECONDS for retries and for rows other processes
queued), and reconciles every peer's copy of our zones at the configured
interval. Everything it does is idempotent and driven by rows in the
database, so a restart loses nothing - the next round picks up where the last
one stopped.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("spanel.cluster")

LOOP_SECONDS = 15
_wake = threading.Event()
_started = False
_start_lock = threading.Lock()
_last_run: dict[str, float] = {}


def kick() -> None:
    """Deliver queued zone changes now rather than at the next round."""
    _wake.set()


def _settings() -> dict:
    from app.services import multi_server

    with multi_server._state_lock:
        state = multi_server._read_state()
    return {"settings": state.get("settings") or {}, "has_nodes": bool(state.get("nodes"))}


def _due(name: str, interval: float, now: float) -> bool:
    if now - _last_run.get(name, 0.0) >= interval:
        _last_run[name] = now
        return True
    return False


def run_once(now: float | None = None) -> dict:
    """One round. Returns what was done (for logs and tests)."""
    from app.core.database import SessionLocal
    from app.services import cluster_dns

    now = time.time() if now is None else now
    info = _settings()
    settings = info["settings"]
    done: dict = {}
    if not settings.get("enabled", True) or not info["has_nodes"]:
        return done
    if _due("health", float(settings.get("health_interval_sec") or 60), now):
        from app.services import multi_server

        # Status, latency and load of every node, and an alert when one
        # stops answering (multi_server.apply_health_result).
        done["health"] = len(multi_server.ping_all_peer_nodes(enabled_only=True))
    with SessionLocal() as db:
        done["outbox"] = cluster_dns.process_outbox(db)
        if settings.get("auto_dns_sync", True) and _due("reconcile", float(settings.get("reconcile_interval_sec") or 900), now):
            try:
                if cluster_dns.dns.active():
                    done["reconcile"] = cluster_dns.reconcile(db)
            except Exception as exc:  # noqa: BLE001 - next round
                logger.warning("Cluster DNS reconcile failed: %s", exc)
    return done


def _loop() -> None:
    # Let the panel finish starting before the first round.
    _wake.wait(timeout=30)
    try:
        from app.core.database import SessionLocal
        from app.services import cluster_transfer

        with SessionLocal() as db:
            cluster_transfer.recover_interrupted(db)
    except Exception:  # noqa: BLE001 - nothing to recover is the usual case
        logger.warning("Could not check for interrupted cluster transfers", exc_info=True)
    while True:
        _wake.clear()
        try:
            run_once()
        except Exception:  # noqa: BLE001 - the loop must survive anything
            logger.warning("Cluster worker round failed", exc_info=True)
        _wake.wait(timeout=LOOP_SECONDS)


def start_cluster_daemon() -> None:
    global _started
    if os.environ.get("SPANEL_CLUSTER_WORKER", "1") == "0":
        return
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="spanel-cluster-worker", daemon=True).start()
