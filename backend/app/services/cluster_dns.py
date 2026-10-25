"""Multi-server DNS: zones pushed to peer nodes, safely.

Sending
-------
A local zone change (dns.py calls multi_server.trigger_background_zone_sync)
puts one row per peer in cluster_dns_outbox. The row names the zone and the
action, not its content: the zone is read from PowerDNS when the row is sent,
so several edits in a row fold into one delivery of the latest state. Rows
survive a restart (they are in the database), are retried with exponential
backoff when a peer is unreachable, and are given up on after MAX_ATTEMPTS -
the periodic reconcile picks such zones up again. reconcile() asks every peer
which of our zones it holds and at which SOA serial, and queues whatever
differs or is missing, and deletes for zones we no longer have.

Receiving (apply_incoming)
--------------------------
Every zone on a node has an origin (cluster_zone_origins):

- a zone made on this node is ours. A peer can never overwrite or delete it;
  the attempt is answered "conflict" and logged on both sides.
- a zone pushed by a peer is a replica of that peer, and only that peer may
  change or delete it.
- a zone from before origins were tracked (no row) is adopted by the first
  peer that pushes it only if nothing local hangs on it - no owner account,
  no website or alias of that name. Otherwise it is marked ours.

A replica is updated in place (PATCH), never dropped and recreated, and its
SOA serial only moves forward: max(serial at the origin, our serial + 1). A
snapshot older than the one already applied (a retry overtaken by a later
change) is ignored as stale. The receiver never changes who owns the
dns_zones row, and keeps the origin's owner name for display.

DNSSEC
------
DNSSEC-signed zones are not replicated. Each node would sign with its own
keys while the parent zone publishes the DS of only one of them, so a replica
would answer with signatures validating resolvers reject (SERVFAIL). Copying
private keys between servers is the alternative, and not one to take
silently. So: an origin whose zone becomes signed withdraws its replicas
(they stop answering for it rather than answer wrongly); a receiver refuses a
signed zone and refuses to overwrite a replica someone signed locally. The
sync log and the outbox say "DNSSEC zones are not replicated" for those.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import ClusterDnsOutbox, ClusterZoneOrigin, DnsZone, User, Website, WebsiteAlias
from app.services import dns

logger = logging.getLogger("spanel.cluster")

MAX_ATTEMPTS = 12
BACKOFF_BASE = 30
BACKOFF_CAP = 3600
BATCH_SIZE = 25
DNSSEC_REASON = "DNSSEC-signed zones are not replicated (each node would sign with different keys)."

_process_lock = threading.Lock()


class ZoneConflict(Exception):
    pass


def backoff_seconds(attempts: int) -> int:
    """30s, 60s, 2m, 4m ... capped at an hour."""
    return min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(0, attempts - 1)))


def next_serial(incoming: int, current: int) -> int:
    """The SOA serial a replica gets: never backwards, never reset to 1."""
    incoming = max(0, int(incoming or 0))
    current = max(0, int(current or 0))
    if not current:
        return max(incoming, 1)
    return max(incoming, current + 1)


def content_hash(records: list[dict], nameservers: list[str], ttl: int) -> str:
    canonical = sorted(
        json.dumps({k: rec.get(k) for k in ("name", "type", "ttl", "content", "priority")}, sort_keys=True)
        for rec in records if isinstance(rec, dict)
    )
    blob = json.dumps({"records": canonical, "nameservers": [str(n).lower().rstrip(".") for n in nameservers],
                       "ttl": int(ttl or 0)}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def soa_serial(data: dict) -> int:
    for rrset in data.get("rrsets", []) or []:
        if rrset.get("type") == "SOA":
            for record in rrset.get("records", []) or []:
                parts = str(record.get("content") or "").split()
                if len(parts) >= 3 and parts[2].isdigit():
                    return int(parts[2])
    try:
        return int(data.get("serial") or 0)
    except (TypeError, ValueError):
        return 0


# --- origins -------------------------------------------------------------------

def origin_row(db: Session, zone: str) -> Optional[ClusterZoneOrigin]:
    return db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.zone == zone).first()


def is_replica(row: Optional[ClusterZoneOrigin]) -> bool:
    return row is not None and bool(row.origin_uid)


def ownership_decision(row_origin: Optional[str], has_row: bool, exists_local: bool, adoptable: bool,
                       source_uid: str) -> str:
    """'accept', 'adopt' or 'conflict:<why>' for an upsert from source_uid."""
    if has_row:
        if not row_origin:
            return "conflict:the zone is owned by this server"
        if row_origin != source_uid:
            return "conflict:the zone is a replica of another node"
        return "accept"
    if not exists_local:
        return "accept"
    if adoptable:
        return "adopt"
    return "conflict:the zone is owned by this server"


def legacy_adoptable(db: Session, zone: str) -> bool:
    """A zone with no recorded origin that a peer may take over: nothing on
    this server depends on it (no owner, no website or alias of that name)."""
    row = db.query(DnsZone).filter(DnsZone.name == zone).first()
    if row is not None and row.owner_id is not None:
        return False
    names = [zone, f"www.{zone}"]
    if db.query(Website.id).filter(Website.domain.in_(names)).first():
        return False
    if db.query(WebsiteAlias.id).filter(WebsiteAlias.domain.in_(names)).first():
        return False
    return True


# --- sending -------------------------------------------------------------------

def _sync_peers(node_ids: Optional[set[str]] = None) -> list[dict[str, Any]]:
    from app.services import multi_server

    with multi_server._state_lock:
        state = multi_server._read_state()
    if not (state.get("settings") or {}).get("enabled", True):
        return []
    return [
        dict(n) for n in state.get("nodes", [])
        if n.get("enabled", True) and n.get("sync_dns", True) and n.get("role") != "db_node"
        and (node_ids is None or n.get("id") in node_ids)
    ]


def _enqueue_for_node(db: Session, node_id: str, zone: str, action: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.utcnow()
    for _ in range(2):
        row = db.query(ClusterDnsOutbox).filter(ClusterDnsOutbox.node_id == node_id,
                                                ClusterDnsOutbox.zone == zone).first()
        if row is None:
            row = ClusterDnsOutbox(node_id=node_id, zone=zone, created_at=now)
            db.add(row)
        row.action = action
        row.status = "pending"
        row.attempts = 0
        row.next_attempt_at = now
        row.last_error = ""
        row.updated_at = now
        try:
            db.commit()
            return
        except IntegrityError:
            # Another process queued the same (peer, zone) a moment ago.
            db.rollback()
    raise RuntimeError(f"Could not queue the cluster sync of {zone}")


def note_local_change(db: Session, zone: str, action: str = "upsert", *, push: bool = True) -> int:
    """A zone changed on this server: record that it is ours and queue it for
    every peer. Returns how many peers it was queued for."""
    zone = dns.normalize_zone(zone)
    row = origin_row(db, zone)
    if is_replica(row):
        if action == "delete":
            # The local copy of a peer's zone was removed here; forget the
            # origin so the peer can push it again.
            db.delete(row)
            db.commit()
        else:
            logger.info("Not pushing %s: it is a replica of %s; change it there.", zone, row.origin_name or row.origin_uid)
        return 0
    if action == "delete":
        if row is not None:
            db.delete(row)
            db.commit()
    elif row is None:
        db.add(ClusterZoneOrigin(zone=zone, origin_uid=None, origin_name="", updated_at=datetime.utcnow()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    if not push:
        return 0
    peers = _sync_peers()
    for peer in peers:
        _enqueue_for_node(db, peer["id"], zone, action)
    return len(peers)


def local_master_zones(db: Session) -> list[str]:
    """Zones this server is the master of: its dns_zones rows that are not replicas."""
    replicas = {r.zone for r in db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.origin_uid.isnot(None))}
    return [row.name for row in db.query(DnsZone).order_by(DnsZone.name) if row.name not in replicas]


def zone_snapshot(db: Session, zone: str) -> dict[str, Any]:
    """What is sent for one zone: its records as they are now."""
    try:
        data = dns._zone_data(zone)
    except dns.DnsError as exc:
        if exc.status in (404, 422):
            return {"zone": zone, "action": "delete"}
        raise
    if data.get("dnssec"):
        return {"zone": zone, "action": "withdraw", "reason": "dnssec"}
    cfg = dns.settings()
    nameservers = cfg.get("nameservers") or []
    ttl = int(cfg.get("ttl") or dns.DEFAULT_TTL)
    records = dns.records(zone)
    owner = ""
    row = db.query(DnsZone).filter(DnsZone.name == zone).first()
    if row is not None and row.owner_id:
        user = db.get(User, row.owner_id)
        owner = user.username if user else ""
    return {
        "zone": zone,
        "action": "upsert",
        "serial": soa_serial(data),
        "hash": content_hash(records, nameservers, ttl),
        "nameservers": nameservers,
        "ttl": ttl,
        "records": records,
        "owner": owner,
        "dnssec": False,
    }


def _send_batch(node: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services import multi_server

    with multi_server._state_lock:
        settings = multi_server._read_state().get("settings") or {}
    resp, _latency = multi_server._peer_request(
        node, "POST", "/api/multi-server/peer/dns-sync",
        payload={"source_node": settings.get("node_name") or "", "zones": items}, timeout=30,
    )
    return resp


def _retry(row: ClusterDnsOutbox, error: str, now: datetime) -> None:
    row.attempts = (row.attempts or 0) + 1
    row.last_error = error[:2000]
    row.updated_at = now
    if row.attempts >= MAX_ATTEMPTS:
        row.status = "failed"
    else:
        row.next_attempt_at = now + timedelta(seconds=backoff_seconds(row.attempts))


def process_outbox(db: Session, *, now: Optional[datetime] = None,
                   send: Optional[Callable[[dict, list], dict]] = None,
                   node_ids: Optional[set[str]] = None, limit: int = 500) -> dict[str, Any]:
    """Deliver what is due. Returns counts and per-node details."""
    if not _process_lock.acquire(blocking=False):
        return {"delivered": 0, "retrying": 0, "conflicts": 0, "failed": 0, "details": [], "busy": True}
    try:
        return _process_outbox(db, now=now or datetime.utcnow(), send=send or _send_batch,
                               node_ids=node_ids, limit=limit)
    finally:
        _process_lock.release()


def _process_outbox(db, *, now, send, node_ids, limit) -> dict[str, Any]:
    from app.services import multi_server

    query = db.query(ClusterDnsOutbox).filter(ClusterDnsOutbox.status == "pending",
                                              ClusterDnsOutbox.next_attempt_at <= now)
    if node_ids is not None:
        query = query.filter(ClusterDnsOutbox.node_id.in_(list(node_ids)))
    due = query.order_by(ClusterDnsOutbox.id).limit(limit).all()
    summary: dict[str, Any] = {"delivered": 0, "retrying": 0, "conflicts": 0, "failed": 0,
                               "dnssec_skipped": [], "details": []}
    if not due:
        return summary

    with multi_server._state_lock:
        state = multi_server._read_state()
    nodes = {n.get("id"): n for n in state.get("nodes", [])}

    by_node: dict[str, list[ClusterDnsOutbox]] = {}
    for row in due:
        by_node.setdefault(row.node_id, []).append(row)

    for node_id, rows in by_node.items():
        node = nodes.get(node_id)
        if node is None:
            # The peer was removed from the cluster: nothing left to deliver to.
            for row in rows:
                db.delete(row)
            db.commit()
            continue
        if not (node.get("enabled", True) and node.get("sync_dns", True)):
            continue
        detail = {"node_id": node_id, "node_name": node.get("name"), "delivered": 0, "errors": []}
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start:start + BATCH_SIZE]
            items: list[dict[str, Any]] = []
            sendable: list[ClusterDnsOutbox] = []
            for row in chunk:
                if row.action == "delete":
                    items.append({"zone": row.zone, "action": "delete"})
                    sendable.append(row)
                    continue
                try:
                    item = zone_snapshot(db, row.zone)
                except Exception as exc:  # noqa: BLE001 - local PowerDNS trouble: try later
                    _retry(row, f"Could not read the zone locally: {exc}", now)
                    summary["retrying"] += 1
                    continue
                if item["action"] == "withdraw":
                    summary["dnssec_skipped"].append(row.zone)
                items.append(item)
                sendable.append(row)
            if not items:
                db.commit()
                continue
            try:
                resp = send(node, items)
            except Exception as exc:  # noqa: BLE001 - the peer is down or refused the batch
                for row in sendable:
                    _retry(row, str(exc), now)
                    summary["failed" if row.status == "failed" else "retrying"] += 1
                detail["errors"].append(str(exc))
                multi_server.record_node_sync(node_id, ok=False, error=str(exc))
                db.commit()
                continue
            results = {str(r.get("zone")): r for r in (resp.get("results") or []) if isinstance(r, dict)}
            for row in sendable:
                result = results.get(row.zone)
                status = str((result or {}).get("status") or "applied")
                if status == "conflict":
                    row.status = "conflict"
                    row.last_error = str(result.get("detail") or "conflict")[:2000]
                    row.updated_at = now
                    summary["conflicts"] += 1
                    detail["errors"].append(f"{row.zone}: {row.last_error}")
                elif status == "error":
                    _retry(row, str(result.get("detail") or "error"), now)
                    summary["failed" if row.status == "failed" else "retrying"] += 1
                    detail["errors"].append(f"{row.zone}: {row.last_error}")
                else:
                    db.delete(row)
                    summary["delivered"] += 1
                    detail["delivered"] += 1
            multi_server.record_node_sync(node_id, ok=True)
            db.commit()
        summary["details"].append(detail)

    if summary["delivered"] or summary["conflicts"] or summary["retrying"] or summary["failed"]:
        status = "success" if not (summary["conflicts"] or summary["retrying"] or summary["failed"]) else "warn"
        text = (f"Đẩy DNS: {summary['delivered']} zone thành công, {summary['retrying']} chờ thử lại, "
                f"{summary['conflicts']} xung đột, {summary['failed']} thất bại.")
        if summary["dnssec_skipped"]:
            text += f" DNSSEC không đồng bộ: {', '.join(sorted(set(summary['dnssec_skipped'])))}."
        multi_server.append_sync_log("dns_push", f"{len(due)} zone", status, text)
    return summary


def queue_all(db: Session, zone: Optional[str] = None, node_ids: Optional[set[str]] = None) -> int:
    """Queue one zone, or every zone this server is master of, for the peers."""
    zones = [dns.normalize_zone(zone)] if zone else local_master_zones(db)
    peers = _sync_peers(node_ids)
    for name in zones:
        if zone:
            row = origin_row(db, name)
            if is_replica(row):
                raise ValueError(f"{name} is a replica of {row.origin_name or row.origin_uid}; sync it from there.")
        for peer in peers:
            _enqueue_for_node(db, peer["id"], name, "upsert")
    return len(zones) * len(peers)


# --- reconcile -----------------------------------------------------------------

def _fetch_inventory(node: dict[str, Any]) -> dict[str, int]:
    from app.services import multi_server

    resp, _latency = multi_server._peer_request(node, "GET", "/api/multi-server/peer/dns-inventory", timeout=20)
    return {str(item.get("zone")): int(item.get("source_serial") or 0)
            for item in resp.get("zones") or [] if isinstance(item, dict) and item.get("zone")}


def _local_serials() -> dict[str, tuple[int, bool]]:
    out: dict[str, tuple[int, bool]] = {}
    for item in dns._request("GET", "/zones") or []:
        name = str(item.get("name") or "").rstrip(".")
        if name:
            out[name] = (int(item.get("serial") or 0), bool(item.get("dnssec")))
    return out


def reconcile(db: Session, *, fetch_inventory: Optional[Callable[[dict], dict[str, int]]] = None,
              local_serials: Optional[dict[str, tuple[int, bool]]] = None) -> dict[str, Any]:
    """Compare what each peer holds of our zones with what we have; queue the difference."""
    fetch_inventory = fetch_inventory or _fetch_inventory
    peers = _sync_peers()
    if not peers:
        return {"queued": 0, "nodes": []}
    if local_serials is None:
        local_serials = _local_serials()
    ours = set(local_master_zones(db))
    queued = 0
    report = []
    for peer in peers:
        try:
            held = fetch_inventory(peer)
        except Exception as exc:  # noqa: BLE001 - try this peer at the next round
            report.append({"node_id": peer["id"], "error": str(exc)})
            continue
        count = 0
        for zone in sorted(ours):
            if zone not in local_serials:
                continue  # a dns_zones row PowerDNS does not serve
            serial, signed = local_serials[zone]
            if signed:
                if zone in held:
                    _enqueue_for_node(db, peer["id"], zone, "upsert")  # becomes a withdraw
                    count += 1
                continue
            if held.get(zone) != serial:
                _enqueue_for_node(db, peer["id"], zone, "upsert")
                count += 1
        # Held by the peer as ours, but no longer ours here (deleted, or moved
        # to another node): the peer drops its replica.
        for zone in sorted(set(held) - ours):
            _enqueue_for_node(db, peer["id"], zone, "delete")
            count += 1
        queued += count
        report.append({"node_id": peer["id"], "queued": count, "held": len(held)})
    return {"queued": queued, "nodes": report}


# --- receiving -----------------------------------------------------------------

def _pdns_zone(zone: str) -> Optional[dict]:
    try:
        return dns._zone_data(zone)
    except dns.DnsError as exc:
        if exc.status in (404, 422):
            return None
        raise


def _desired_rrsets(zone: str, item: dict[str, Any]) -> tuple[dict[tuple[str, str], tuple[int, list[str]]], list[str], int]:
    local_cfg = dns.settings()
    nameservers = [str(ns).strip().lower().rstrip(".") for ns in (item.get("nameservers") or []) if str(ns).strip()]
    if len(nameservers) < 2:
        nameservers = local_cfg.get("nameservers") or [f"ns1.{zone}", f"ns2.{zone}"]
    ttl = int(item.get("ttl") or local_cfg.get("ttl") or dns.DEFAULT_TTL)
    apex = dns._absolute(zone)
    grouped: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for rec in item.get("records") or []:
        if not isinstance(rec, dict):
            continue
        name, rtype, rec_ttl, content = dns.to_pdns(zone, rec)
        _, contents = grouped.get((name, rtype), (rec_ttl, []))
        if content not in contents:
            contents.append(content)
        grouped[(name, rtype)] = (rec_ttl, contents)
    if (apex, "NS") not in grouped:
        grouped[(apex, "NS")] = (ttl, [dns._absolute(ns) for ns in nameservers])
    return grouped, nameservers, ttl


def write_replica(zone: str, item: dict[str, Any], current: Optional[dict]) -> int:
    """Create or update the replica in PowerDNS. Returns the serial it got."""
    grouped, nameservers, ttl = _desired_rrsets(zone, item)
    apex = dns._absolute(zone)
    incoming = int(item.get("serial") or 0)

    def soa(serial: int) -> dict:
        return {"name": apex, "type": "SOA", "ttl": ttl, "records": [{
            "content": f"{dns._absolute(nameservers[0])} hostmaster.{apex} {serial} 10800 3600 604800 3600",
            "disabled": False,
        }]}

    if current is None:
        serial = next_serial(incoming, 0)
        rrsets = [soa(serial)] + [
            {"name": name, "type": rtype, "ttl": rttl, "records": [{"content": c, "disabled": False} for c in contents]}
            for (name, rtype), (rttl, contents) in grouped.items()
        ]
        dns._request("POST", "/zones", {"name": apex, "kind": "Native", "soa_edit_api": dns.SOA_EDIT_API,
                                        "nameservers": [], "rrsets": rrsets})
        return serial

    if current.get("dnssec"):
        raise ZoneConflict("the replica here was DNSSEC-signed locally; it is not overwritten")
    serial = next_serial(incoming, soa_serial(current))
    changes = [dict(soa(serial), changetype="REPLACE")]
    for (name, rtype), (rttl, contents) in grouped.items():
        changes.append({"name": name, "type": rtype, "ttl": rttl, "changetype": "REPLACE",
                        "records": [{"content": c, "disabled": False} for c in contents]})
    for rrset in current.get("rrsets", []) or []:
        key = (rrset.get("name"), rrset.get("type"))
        if key[1] != "SOA" and key not in grouped:
            changes.append({"name": key[0], "type": key[1], "changetype": "DELETE"})
    dns._request("PATCH", f"/zones/{apex}", {"rrsets": changes})
    return serial


def _apply_one(db: Session, item: dict[str, Any], source_uid: str, source_name: str) -> dict[str, Any]:
    zone = dns.normalize_zone(str(item.get("zone") or ""))
    action = str(item.get("action") or "upsert").strip().lower()
    row = origin_row(db, zone)
    current = _pdns_zone(zone)
    zone_row = db.query(DnsZone).filter(DnsZone.name == zone).first()
    exists_local = current is not None or zone_row is not None

    if action in ("delete", "withdraw"):
        if row is not None and row.origin_uid == source_uid:
            if zone_row is not None and zone_row.owner_id is not None:
                return {"zone": zone, "status": "conflict", "detail": "an account on this server owns the zone"}
            dns.delete_zone(db, zone, sync_cluster=False)
            db.delete(row)
            db.commit()
            return {"zone": zone, "status": "withdrawn" if action == "withdraw" else "deleted",
                    "detail": DNSSEC_REASON if action == "withdraw" else ""}
        if row is None and not exists_local:
            return {"zone": zone, "status": "absent"}
        return {"zone": zone, "status": "conflict", "detail": "the zone here does not belong to the sending node"}

    if action == "handover":
        # The origin moved the zone to another node (account transfer): that
        # node is the one to take it from now on. Only the origin can say so.
        new_origin = str(item.get("new_origin") or "")
        if row is None:
            return {"zone": zone, "status": "absent"}
        if row.origin_uid != source_uid or not re.fullmatch(r"[a-z0-9]{8,40}", new_origin):
            return {"zone": zone, "status": "conflict", "detail": "only the zone's origin can hand it over"}
        row.origin_uid = new_origin
        row.origin_name = str(item.get("new_origin_name") or "")[:120]
        row.source_serial = 0
        row.content_hash = ""
        row.updated_at = datetime.utcnow()
        db.commit()
        return {"zone": zone, "status": "handed_over"}

    if action != "upsert":
        return {"zone": zone, "status": "error", "detail": f"unknown action {action}"}
    if item.get("dnssec"):
        return {"zone": zone, "status": "conflict", "detail": DNSSEC_REASON}

    decision = ownership_decision(row.origin_uid if row else None, row is not None, exists_local,
                                  row is None and exists_local and legacy_adoptable(db, zone), source_uid)
    if decision.startswith("conflict:"):
        if row is None:
            # Settle it: from now on this zone is known to be ours.
            db.add(ClusterZoneOrigin(zone=zone, origin_uid=None, updated_at=datetime.utcnow()))
            db.commit()
        return {"zone": zone, "status": "conflict", "detail": decision.split(":", 1)[1]}

    incoming_serial = int(item.get("serial") or 0)
    incoming_hash = str(item.get("hash") or "")
    if row is not None and current is not None:
        if incoming_serial and row.source_serial and incoming_serial < row.source_serial:
            return {"zone": zone, "status": "stale", "detail": "an older snapshot than the one applied"}
        if incoming_hash and incoming_hash == row.content_hash:
            if incoming_serial > (row.source_serial or 0):
                row.source_serial = incoming_serial
                db.commit()
            return {"zone": zone, "status": "unchanged"}

    try:
        local_serial = write_replica(zone, item, current)
    except ZoneConflict as exc:
        return {"zone": zone, "status": "conflict", "detail": str(exc)}

    if zone_row is None:
        # Nobody on this server owns a replica; the origin's owner is kept on
        # the origin row for display.
        db.add(DnsZone(name=zone, owner_id=None))
    if row is None:
        row = ClusterZoneOrigin(zone=zone)
        db.add(row)
    row.origin_uid = source_uid
    row.origin_name = (source_name or "")[:120]
    row.origin_owner = str(item.get("owner") or "")[:64]
    row.source_serial = incoming_serial
    row.content_hash = incoming_hash
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"zone": zone, "status": "applied", "serial": local_serial,
            "detail": "adopted a zone with no recorded origin" if decision == "adopt" else ""}


def apply_incoming(db: Session, items: list[dict[str, Any]], *, source_uid: str, source_name: str = "") -> dict[str, Any]:
    from app.services import multi_server

    if not dns.active():
        raise ValueError("Add-on DNS Manager (PowerDNS) chưa được cài đặt hoặc bật trên máy chủ Peer này.")
    if not source_uid:
        raise ValueError("The sending node did not identify itself; update it to the same SPanel version.")
    if source_uid == multi_server.local_node_uid():
        raise ValueError("A node cannot sync DNS to itself.")

    results = []
    for item in items or []:
        if not isinstance(item, dict) or not str(item.get("zone") or "").strip():
            continue
        try:
            results.append(_apply_one(db, item, source_uid, source_name))
        except Exception as exc:  # noqa: BLE001 - one bad zone must not stop the batch
            db.rollback()
            results.append({"zone": str(item.get("zone")), "status": "error", "detail": str(exc)[:500]})

    applied = sum(1 for r in results if r["status"] in ("applied", "unchanged"))
    deleted = sum(1 for r in results if r["status"] in ("deleted", "withdrawn"))
    conflicts = [r for r in results if r["status"] == "conflict"]
    errors = [f"{r['zone']}: {r.get('detail', '')}" for r in results if r["status"] in ("error", "conflict")]
    for conflict in conflicts:
        logger.warning("Cluster DNS sync from %s refused for %s: %s", source_name or source_uid,
                       conflict["zone"], conflict.get("detail"))
    multi_server.append_sync_log(
        "receive_dns_sync", source_name or source_uid,
        "success" if not errors else "warn",
        f"Nhận đồng bộ từ {source_name or 'Peer'}: cập nhật {applied} zone, xóa {deleted} zone, "
        f"{len(conflicts)} xung đột.",
    )
    return {"applied": applied, "deleted": deleted, "errors": errors, "results": results}


def inventory_for(db: Session, source_uid: str) -> list[dict[str, Any]]:
    rows = db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.origin_uid == source_uid).order_by(ClusterZoneOrigin.zone)
    return [{"zone": r.zone, "source_serial": r.source_serial, "hash": r.content_hash} for r in rows]


def replica_zones_of(db: Session, source_uid: str) -> set[str]:
    return {r.zone for r in db.query(ClusterZoneOrigin.zone).filter(ClusterZoneOrigin.origin_uid == source_uid)}


# --- status for the page -------------------------------------------------------

def status(db: Session) -> dict[str, Any]:
    from app.services import multi_server

    with multi_server._state_lock:
        state = multi_server._read_state()
    names = {n.get("id"): n.get("name") for n in state.get("nodes", [])}
    rows = db.query(ClusterDnsOutbox).order_by(ClusterDnsOutbox.status, ClusterDnsOutbox.next_attempt_at).limit(200).all()
    counts: dict[str, int] = {}
    for status_name, in db.query(ClusterDnsOutbox.status):
        counts[status_name] = counts.get(status_name, 0) + 1
    replicas = db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.origin_uid.isnot(None)).order_by(ClusterZoneOrigin.zone).limit(500).all()
    return {
        "counts": counts,
        "outbox": [{
            "id": r.id, "node_id": r.node_id, "node_name": names.get(r.node_id, r.node_id), "zone": r.zone,
            "action": r.action, "status": r.status, "attempts": r.attempts,
            "next_attempt_at": r.next_attempt_at.isoformat() + "Z" if r.next_attempt_at else None,
            "last_error": r.last_error,
        } for r in rows],
        "replicas": [{"zone": r.zone, "origin_name": r.origin_name, "origin_uid": r.origin_uid,
                      "origin_owner": r.origin_owner, "source_serial": r.source_serial,
                      "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None} for r in replicas],
        "dnssec_policy": DNSSEC_REASON,
    }


def retry_row(db: Session, row_id: int) -> None:
    row = db.get(ClusterDnsOutbox, row_id)
    if row is None:
        raise ValueError("Not found")
    row.status = "pending"
    row.attempts = 0
    row.next_attempt_at = datetime.utcnow()
    row.last_error = ""
    db.commit()


def discard_row(db: Session, row_id: int) -> None:
    row = db.get(ClusterDnsOutbox, row_id)
    if row is None:
        raise ValueError("Not found")
    db.delete(row)
    db.commit()
