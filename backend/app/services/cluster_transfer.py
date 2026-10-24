"""Moving one account from this SPanel node to another (push).

On the node the account leaves (direction "out"), a job runs these steps:

1. precheck  - the target answers, has the room, and has no account, website
               or zone of the same name (its copy of our own zone is fine).
2. backup    - a full create_user_backup, encrypted with a passphrase made
               for this transfer alone (never SPANEL_BACKUP_PASSPHRASE, never
               stored): secrets.token_urlsafe(32).
3. upload    - the archive is streamed to the target's peer endpoint, signed,
               with its SHA-256; the target checks size and digest.
4. restore   - the target restores it with restore_user_backup in a job of
               its own (direction "in"); this side polls that job. The
               passphrase and the account's password hash travel sealed with
               a key derived from the node token both sides hold (seal()), so
               they are not readable even where TLS is not verified.
5. dns       - optional: the account's zones change master. This node marks
               them replicas of the target, the target takes them over,
               points A records that named this server's addresses at its
               own and pushes them to every peer (this one included).
6. suspend   - optional: the account is suspended here.

The local archive is removed at the end either way: it is encrypted with a
passphrase nobody kept, so it is no use as a backup.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.models.entities import ClusterDnsOutbox, ClusterTransferJob, ClusterZoneOrigin, DnsZone, User, Website, WebsiteAlias

logger = logging.getLogger("spanel.cluster")

UPLOAD_NAME_RE = re.compile(r"^user-[A-Za-z0-9._-]{3,64}-transfer-[0-9a-f]{8}\.tar\.gz$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
POLL_SECONDS = 5
RESTORE_TIMEOUT_SECONDS = 12 * 3600
DISK_MARGIN = 1.15  # room asked for on the target, over the archive size
STEPS = ("precheck", "backup", "upload", "restore", "dns", "suspend", "done")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spanel-cluster-transfer")


class TransferError(RuntimeError):
    pass


# --- sealing secrets between the two nodes -------------------------------------

def _seal_key(token_hash: str) -> bytes:
    digest = hashlib.sha256(f"spanel-cluster-transfer\n{token_hash}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def seal(token_hash: str, plaintext: str) -> str:
    return Fernet(_seal_key(token_hash)).encrypt(plaintext.encode("utf-8")).decode("ascii")


def unseal(token_hash: str, sealed: str) -> str:
    try:
        return Fernet(_seal_key(token_hash)).decrypt((sealed or "").encode("ascii"), ttl=24 * 3600).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise TransferError("Could not unseal the transfer secret (wrong token?)") from exc


# --- jobs ----------------------------------------------------------------------

def job_dict(job: ClusterTransferJob) -> dict[str, Any]:
    try:
        options = json.loads(job.options or "{}")
    except ValueError:
        options = {}
    return {
        "job_uid": job.job_uid, "direction": job.direction, "username": job.username,
        "node_id": job.node_id, "node_name": job.node_name, "status": job.status, "step": job.step,
        "message": job.message, "options": options, "archive_size": job.archive_size,
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
        "updated_at": job.updated_at.isoformat() + "Z" if job.updated_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
    }


def _set(db: Session, job: ClusterTransferJob, *, status: Optional[str] = None, step: Optional[str] = None,
         message: Optional[str] = None) -> None:
    if status:
        job.status = status
        if status in ("done", "failed"):
            job.finished_at = datetime.utcnow()
    if step:
        job.step = step
    if message is not None:
        job.message = message[:4000]
    job.updated_at = datetime.utcnow()
    db.commit()


def list_jobs(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.query(ClusterTransferJob).order_by(ClusterTransferJob.id.desc()).limit(limit).all()
    return [job_dict(r) for r in rows]


def recover_interrupted(db: Session) -> int:
    """Jobs a restart cut short: they cannot resume (the passphrase is gone)."""
    rows = db.query(ClusterTransferJob).filter(ClusterTransferJob.status.in_(("queued", "running"))).all()
    for job in rows:
        _set(db, job, status="failed", message="Interrupted by a panel restart; start the transfer again.")
    return len(rows)


# --- the source side -----------------------------------------------------------

def _node(node_id: str) -> dict[str, Any]:
    from app.services import multi_server

    with multi_server._state_lock:
        state = multi_server._read_state()
    node = next((dict(n) for n in state.get("nodes", []) if n.get("id") == node_id), None)
    if node is None:
        raise ValueError("Không tìm thấy máy chủ Peer này.")
    return node


def _peer(node: dict[str, Any], method: str, path: str, payload: Optional[dict] = None, timeout: float = 30) -> dict:
    from app.services import multi_server

    resp, _latency = multi_server._peer_request(node, method, path, payload, timeout=timeout)
    return resp


def _upload(node: dict[str, Any], archive: Path, size: int, sha256: str, username: str) -> dict:
    from app.services import cluster_peer, multi_server

    with archive.open("rb") as handle:
        resp, _latency = cluster_peer.request(
            node, multi_server.node_token(node), multi_server.local_node_uid(), "PUT",
            "/api/multi-server/peer/transfer/upload", stream=handle, stream_size=size, stream_sha256=sha256,
            extra_headers={"X-SPanel-Transfer-User": username}, timeout=300,
        )
    return resp


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def user_domains(db: Session, user: User) -> list[str]:
    names = {w.domain.lower() for w in db.query(Website).filter(Website.owner_id == user.id)}
    names |= {a.domain.lower() for a in db.query(WebsiteAlias).join(Website).filter(Website.owner_id == user.id)}
    return sorted(names)


def user_master_zones(db: Session, user: User) -> list[str]:
    replicas = {r.zone for r in db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.origin_uid.isnot(None))}
    return sorted(r.name for r in db.query(DnsZone).filter(DnsZone.owner_id == user.id) if r.name not in replicas)


def start_transfer(db: Session, *, user_id: int, node_id: str, switch_dns: bool = False,
                   suspend_source: bool = False, created_by: Optional[int] = None) -> dict[str, Any]:
    from app.core.permissions import is_admin_role

    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    if is_admin_role(user.role):
        raise ValueError("Administrator accounts are not moved between nodes.")
    node = _node(node_id)
    if not node.get("enabled", True):
        raise ValueError("The target node is disabled.")
    if node.get("role") in ("dns_only", "db_node"):
        raise ValueError("The target node's role does not host accounts (choose a Full Cluster Peer).")
    busy = db.query(ClusterTransferJob).filter(ClusterTransferJob.username == user.username,
                                               ClusterTransferJob.status.in_(("queued", "running"))).first()
    if busy:
        raise ValueError(f"A transfer of {user.username} is already running.")
    job = ClusterTransferJob(
        job_uid=secrets.token_hex(8), direction="out", username=user.username, node_id=node_id,
        node_name=str(node.get("name") or ""), status="queued", step="queued",
        options=json.dumps({"switch_dns": bool(switch_dns), "suspend_source": bool(suspend_source)}),
        created_by=created_by, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    _executor.submit(_run_outgoing_safely, job.job_uid)
    return job_dict(job)


def _run_outgoing_safely(job_uid: str) -> None:
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            run_outgoing(db, job_uid)
    except Exception:  # noqa: BLE001 - recorded on the job by run_outgoing
        logger.exception("Cluster transfer %s crashed", job_uid)


def run_outgoing(db: Session, job_uid: str) -> dict[str, Any]:
    from app.services import backup

    job = db.query(ClusterTransferJob).filter(ClusterTransferJob.job_uid == job_uid).one()
    options = json.loads(job.options or "{}")
    archive: Optional[Path] = None
    try:
        user = db.query(User).filter(User.username == job.username).first()
        if user is None:
            raise TransferError("The account no longer exists here.")
        node = _node(job.node_id)

        _set(db, job, status="running", step="precheck", message="Checking the target node")
        info = _peer(node, "GET", "/api/multi-server/peer/ping", timeout=10)
        target_uid = str(info.get("node_uid") or "")
        if not target_uid:
            raise TransferError("The target node runs an SPanel version without account transfer; update it first.")
        check = _peer(node, "POST", "/api/multi-server/peer/transfer/precheck",
                      {"username": user.username, "domains": user_domains(db, user)})
        if check.get("conflicts"):
            raise TransferError("The target already has: " + "; ".join(str(c) for c in check["conflicts"]))

        _set(db, job, step="backup", message="Creating an encrypted full backup")
        passphrase = secrets.token_urlsafe(32)
        archive = Path(backup.create_user_backup(user, db, backup_mode="full", passphrase=passphrase))
        size = archive.stat().st_size
        digest = backup._compute_sha256(archive)
        job.archive_name, job.archive_size, job.archive_sha256 = archive.name, size, digest
        free = check.get("free_bytes")
        if isinstance(free, (int, float)) and free and size * DISK_MARGIN > free:
            raise TransferError(f"The target has {int(free)} bytes free for a {size} byte archive.")

        _set(db, job, step="upload", message=f"Uploading {size} bytes")
        uploaded = _upload(node, archive, size, digest, user.username)
        upload_id = str(uploaded.get("upload_id") or "")
        if not upload_id:
            raise TransferError("The target did not accept the upload.")

        _set(db, job, step="restore", message="Restoring on the target node")
        from app.services import multi_server

        token_hash = _token_hash(node)
        with multi_server._state_lock:
            source_name = (multi_server._read_state().get("settings") or {}).get("node_name") or ""
        started = _peer(node, "POST", "/api/multi-server/peer/transfer/restore", {
            "upload_id": upload_id, "username": user.username, "source_job": job.job_uid,
            "source_name": source_name,
            "passphrase_sealed": seal(token_hash, passphrase),
            "password_hash_sealed": seal(token_hash, user.hashed_password or ""),
        })
        job.remote_job_uid = str(started.get("job_uid") or "")
        db.commit()
        deadline = time.monotonic() + RESTORE_TIMEOUT_SECONDS
        while True:
            remote = _peer(node, "GET", f"/api/multi-server/peer/transfer/jobs/{job.remote_job_uid}", timeout=15)
            if remote.get("status") == "done":
                break
            if remote.get("status") == "failed":
                raise TransferError(f"Restore on the target failed: {remote.get('message')}")
            if time.monotonic() > deadline:
                raise TransferError("The restore on the target did not finish in time.")
            _sleep(POLL_SECONDS)

        notes = [f"Restored on {job.node_name}."]
        if options.get("switch_dns"):
            _set(db, job, step="dns", message="Moving the account's DNS zones to the target")
            notes.append(switch_dns(db, user, node, target_uid))
        if options.get("suspend_source"):
            _set(db, job, step="suspend", message="Suspending the account here")
            suspend_source_account(db, user, job.created_by)
            notes.append("Suspended on this node.")
        _set(db, job, status="done", step="done", message=" ".join(notes))
    except Exception as exc:  # noqa: BLE001 - every failure ends on the job
        db.rollback()
        logger.warning("Cluster transfer %s of %s failed at %s: %s", job.job_uid, job.username, job.step, exc)
        _set(db, job, status="failed", message=f"{job.step}: {exc}")
    finally:
        if archive is not None:
            backup.discard_local_copy(str(archive))
    from app.services import multi_server

    multi_server.append_sync_log("account_transfer", f"{job.username} → {job.node_name}",
                                 "success" if job.status == "done" else "warn", job.message)
    return job_dict(job)


def _token_hash(node: dict[str, Any]) -> str:
    from app.services import cluster_peer, multi_server

    return cluster_peer.signing_key_from_token(multi_server.node_token(node))


def switch_dns(db: Session, user: User, node: dict[str, Any], target_uid: str) -> str:
    """Hand the account's zones to the target as their new master."""
    from app.services import dns, server_network

    zones = user_master_zones(db, user)
    if not zones:
        return "No DNS zones to move."
    # From now on this node only takes these zones from the target.
    previous = {}
    for zone in zones:
        row = db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.zone == zone).first()
        previous[zone] = row.origin_uid if row else None
        if row is None:
            row = ClusterZoneOrigin(zone=zone)
            db.add(row)
        row.origin_uid = target_uid
        row.origin_name = str(node.get("name") or "")[:120]
        row.origin_owner = user.username
        row.source_serial = 0
        row.content_hash = ""
        row.updated_at = datetime.utcnow()
    db.query(ClusterDnsOutbox).filter(ClusterDnsOutbox.zone.in_(zones)).delete(synchronize_session=False)
    db.commit()
    # Other peers holding our replicas take the zones from the target from
    # now on. Best effort: a peer missed here converges at the next reconcile
    # (we stop listing the zones as ours, it drops them, the target re-pushes).
    from app.services import cluster_dns

    handover = [{"zone": zone, "action": "handover", "new_origin": target_uid,
                 "new_origin_name": str(node.get("name") or "")} for zone in zones]
    for peer in cluster_dns._sync_peers():
        if peer.get("id") == node.get("id"):
            continue
        try:
            cluster_dns._send_batch(peer, handover)
        except Exception as exc:  # noqa: BLE001 - see above
            logger.warning("Zone handover to %s failed: %s", peer.get("name"), exc)
    old_ips = list(server_network.ipv4_addresses() or [])
    try:
        zone_ip = dns.settings().get("zone_ip")
        if zone_ip:
            old_ips.append(zone_ip)
    except Exception:  # noqa: BLE001 - the addresses we know are enough
        pass
    try:
        result = _peer(node, "POST", "/api/multi-server/peer/transfer/claim-zones",
                       {"zones": zones, "old_ips": sorted(set(old_ips))})
    except Exception:
        # The target did not take them: this node stays their master.
        for zone in zones:
            row = db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.zone == zone).first()
            if row is not None:
                row.origin_uid = previous[zone]
        db.commit()
        raise
    claimed = list(result.get("claimed") or [])
    skipped = list(result.get("skipped") or [])
    for zone in skipped:
        row = db.query(ClusterZoneOrigin).filter(ClusterZoneOrigin.zone == zone).first()
        if row is not None:
            row.origin_uid = previous.get(zone)
    db.commit()
    text = f"DNS master moved for {len(claimed)} zone(s)."
    if skipped:
        text += f" Kept here: {', '.join(skipped)}."
    return text


def suspend_source_account(db: Session, user: User, actor_id: Optional[int]) -> None:
    from app.api import users as users_api

    actor = SimpleNamespace(id=actor_id or 0, role="admin")
    users_api.suspend_user(user.id, request=None, db=db, current_user=actor)


# --- the target side -----------------------------------------------------------

def precheck(db: Session, username: str, domains: list[str], asking_node: str) -> dict[str, Any]:
    from app.core.config import settings
    from app.services import cluster_dns

    conflicts = []
    if not USERNAME_RE.fullmatch(username or ""):
        conflicts.append("an invalid account name")
    elif db.query(User.id).filter(User.username == username).first():
        conflicts.append(f"an account named {username}")
    for domain in domains or []:
        name = str(domain).strip().lower()
        if not name:
            continue
        if db.query(Website.id).filter(Website.domain == name).first() or \
                db.query(WebsiteAlias.id).filter(WebsiteAlias.domain == name).first():
            conflicts.append(f"a website {name}")
            continue
        if db.query(DnsZone.id).filter(DnsZone.name == name).first():
            row = cluster_dns.origin_row(db, name)
            if not (row is not None and row.origin_uid == asking_node):
                conflicts.append(f"a DNS zone {name}")
    try:
        free = shutil.disk_usage(settings.backup_root).free
    except OSError:
        free = None
    return {"ok": not conflicts, "conflicts": conflicts, "free_bytes": free}


def new_upload_name(username: str) -> str:
    if not USERNAME_RE.fullmatch(username or ""):
        raise ValueError("Invalid account name")
    return f"user-{username}-transfer-{secrets.token_hex(4)}.tar.gz"


def upload_path(name: str) -> Path:
    from app.services import backup

    if not UPLOAD_NAME_RE.fullmatch(name or ""):
        raise ValueError("Invalid upload id")
    return Path(backup.user_restore_dir()) / name


def start_incoming_restore(db: Session, *, upload_id: str, username: str, passphrase: str, password_hash: str,
                           source_uid: str, source_name: str, source_job: str) -> dict[str, Any]:
    path = upload_path(upload_id)
    if not path.is_file():
        raise ValueError("The uploaded archive is not here (expired or never finished).")
    if not upload_id.startswith(f"user-{username}-transfer-"):
        raise ValueError("The upload does not belong to this account.")
    if db.query(User.id).filter(User.username == username).first():
        raise ValueError(f"An account named {username} already exists on this node.")
    job = ClusterTransferJob(
        job_uid=secrets.token_hex(8), direction="in", username=username, node_id=source_uid,
        node_name=(source_name or "")[:120], status="queued", step="restore",
        options=json.dumps({"source_job": source_job}), archive_name=upload_id,
        archive_size=path.stat().st_size, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    _executor.submit(_run_incoming_safely, job.job_uid, passphrase, password_hash)
    return job_dict(job)


def _run_incoming_safely(job_uid: str, passphrase: str, password_hash: str) -> None:
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            run_incoming(db, job_uid, passphrase, password_hash)
    except Exception:  # noqa: BLE001 - recorded on the job by run_incoming
        logger.exception("Incoming cluster transfer %s crashed", job_uid)


def run_incoming(db: Session, job_uid: str, passphrase: str, password_hash: str) -> dict[str, Any]:
    from app.services import backup

    job = db.query(ClusterTransferJob).filter(ClusterTransferJob.job_uid == job_uid).one()
    path = upload_path(job.archive_name)
    try:
        _set(db, job, status="running", step="restore", message="Restoring the account")
        # The name is checked against the manifest before anything is
        # written: a peer must not be able to restore over a local account.
        result = backup.restore_user_backup(
            str(path), db, restore_mode="full", passphrase=passphrase,
            expected_username=job.username, require_new_user=True,
        )
        user = db.query(User).filter(User.username == job.username).first()
        if user is not None and password_hash.startswith("$2"):
            # The panel password comes along; the backup alone would have
            # given the account a new random one.
            user.hashed_password = password_hash
            user.token_version = (user.token_version or 0) + 1
            db.commit()
        _set(db, job, status="done", step="done",
             message=f"Restored {len(result.get('websites') or [])} website(s) from {job.node_name}.")
    except Exception as exc:  # noqa: BLE001 - every failure ends on the job
        db.rollback()
        _set(db, job, status="failed", message=str(exc))
    finally:
        try:
            backup.delete_user_restore_backup(str(path))
        except (FileNotFoundError, OSError):
            pass
    from app.services import multi_server

    multi_server.append_sync_log("account_transfer_in", f"{job.username} ← {job.node_name}",
                                 "success" if job.status == "done" else "warn", job.message)
    return job_dict(job)


def incoming_job(db: Session, job_uid: str, source_uid: str) -> dict[str, Any]:
    job = db.query(ClusterTransferJob).filter(ClusterTransferJob.job_uid == job_uid,
                                              ClusterTransferJob.direction == "in",
                                              ClusterTransferJob.node_id == source_uid).first()
    if job is None:
        raise ValueError("No such transfer")
    return {"job_uid": job.job_uid, "status": job.status, "step": job.step, "message": job.message}


def claim_zones(db: Session, zones: list[str], old_ips: list[str], source_uid: str) -> dict[str, Any]:
    """The source hands these zones over: this node becomes their master,
    records that pointed at the source's addresses point here, and the
    result is pushed to every peer (the source included)."""
    from app.services import cluster_dns, dns

    claimed, skipped = [], []
    new_ip = ""
    try:
        new_ip = dns.settings().get("zone_ip") or ""
    except Exception:  # noqa: BLE001 - zones still change master
        pass
    old = {str(ip).strip() for ip in old_ips or [] if str(ip).strip()}
    for raw in zones or []:
        try:
            zone = dns.normalize_zone(str(raw))
        except ValueError:
            skipped.append(str(raw))
            continue
        row = cluster_dns.origin_row(db, zone)
        if row is not None and row.origin_uid not in (None, source_uid):
            skipped.append(zone)  # a third node's zone: not the source's to give
            continue
        if not (dns.active() and db.query(DnsZone.id).filter(DnsZone.name == zone).first()):
            skipped.append(zone)
            continue
        if row is None:
            row = ClusterZoneOrigin(zone=zone)
            db.add(row)
        row.origin_uid = None
        row.origin_name = ""
        row.updated_at = datetime.utcnow()
        db.commit()
        changes: dict[tuple[str, str], tuple[int, list[str]]] = {}
        if new_ip and old:
            for rrset in dns._zone_data(zone).get("rrsets", []):
                if rrset.get("type") != "A":
                    continue
                contents = [r["content"] for r in rrset.get("records", [])]
                if any(c in old for c in contents):
                    replaced = []
                    for c in contents:
                        value = new_ip if c in old else c
                        if value not in replaced:
                            replaced.append(value)
                    changes[(rrset["name"], "A")] = (int(rrset.get("ttl") or dns.DEFAULT_TTL), replaced)
        if changes:
            dns._patch(zone, changes)  # pushes to the peers through the usual hook
        else:
            cluster_dns.note_local_change(db, zone, "upsert")
        claimed.append(zone)
    from app.services import cluster_worker

    cluster_worker.kick()
    return {"claimed": claimed, "skipped": skipped}
