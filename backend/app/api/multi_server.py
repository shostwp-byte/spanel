import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.api.provisioning import _get_provisioning_token
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import ApiToken, User
from app.services import backup, cluster_dns, cluster_peer, cluster_placement, cluster_transfer, cluster_worker, multi_server
from app.services.audit import log_action
from app.services.provisioning import create_api_token, normalize_allowed_ips

router = APIRouter(prefix="/multi-server", tags=["multi-server"])
logger = logging.getLogger("spanel.cluster")


class ClusterSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    node_name: Optional[str] = Field(default=None, max_length=80)
    auto_dns_sync: Optional[bool] = None
    check_domain_conflict: Optional[bool] = None
    check_username_conflict: Optional[bool] = None
    conflict_fail_closed: Optional[bool] = None
    health_interval_sec: Optional[int] = Field(default=None, ge=30, le=3600)
    reconcile_interval_sec: Optional[int] = Field(default=None, ge=300, le=86400)


class PeerNodeIn(BaseModel):
    name: str = Field(..., max_length=80)
    url: str = Field(..., max_length=255)
    api_token: str = Field(default="", max_length=255)
    role: str = Field(default="full_peer", max_length=32)
    verify_tls: bool = True
    tls_fingerprint: str = Field(default="", max_length=128)
    ca_pem: str = Field(default="", max_length=20000)
    sync_dns: bool = True
    check_domain: bool = True
    enabled: bool = True


class PeerNodeUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    url: Optional[str] = Field(default=None, max_length=255)
    api_token: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, max_length=32)
    verify_tls: Optional[bool] = None
    tls_fingerprint: Optional[str] = Field(default=None, max_length=128)
    ca_pem: Optional[str] = Field(default=None, max_length=20000)
    sync_dns: Optional[bool] = None
    check_domain: Optional[bool] = None
    enabled: Optional[bool] = None


class TlsProbeIn(BaseModel):
    url: str = Field(..., max_length=255)


class TransferIn(BaseModel):
    user_id: int = Field(..., ge=1)
    node_id: str = Field(..., max_length=40)
    switch_dns: bool = False
    suspend_source: bool = False


class PeerTransferPrecheckIn(BaseModel):
    username: str = Field(..., max_length=64)
    domains: list[str] = Field(default_factory=list, max_length=2000)


class PeerTransferRestoreIn(BaseModel):
    upload_id: str = Field(..., max_length=120)
    username: str = Field(..., max_length=64)
    source_job: str = Field(default="", max_length=32)
    source_name: str = Field(default="", max_length=120)
    passphrase_sealed: str = Field(..., max_length=4096)
    password_hash_sealed: str = Field(default="", max_length=4096)


class PeerClaimZonesIn(BaseModel):
    zones: list[str] = Field(default_factory=list, max_length=2000)
    old_ips: list[str] = Field(default_factory=list, max_length=64)


# ── Peer authentication ──────────────────────────────────────────────────

def _signed_path(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def authenticate_peer(request: Request, db: Session, scope: str, body_sha256: str) -> tuple[ApiToken, str]:
    """Token, scope and signature of a request from a peer node.

    Returns the token and the sender's node id. A provisioning token is still
    accepted for the read/write peer scopes it used to cover, with a warning
    in the log; it is never accepted for moving accounts.
    """
    token = _get_provisioning_token(request, db)
    decision = cluster_peer.scope_decision(token.scopes, scope)
    if decision == "denied":
        raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")
    if decision == "legacy":
        logger.warning(
            "Cluster peer used provisioning token '%s' (id %s) for %s; this is deprecated - "
            "create a node token on the Multi Server page and paste it into the peer.",
            token.name, token.id, scope,
        )
    try:
        node_uid = cluster_peer.verify_signature(
            token.token_hash, request.method, _signed_path(request), request.headers, body_sha256,
        )
    except cluster_peer.SignatureError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return token, node_uid


async def _body_sha256(request: Request) -> str:
    return hashlib.sha256(await request.body()).hexdigest()


def peer_auth(scope: str):
    """Dependency for a peer endpoint whose body is small enough to hash whole."""
    def dependency(request: Request, body_sha256: str = Depends(_body_sha256), db: Session = Depends(get_db)) -> str:
        _token, node_uid = authenticate_peer(request, db, scope, body_sha256)
        return node_uid

    return dependency


def peer_auth_token(scope: str):
    """As peer_auth, also handing over the token (its hash unseals transfer secrets)."""
    def dependency(request: Request, body_sha256: str = Depends(_body_sha256),
                   db: Session = Depends(get_db)) -> tuple[ApiToken, str]:
        return authenticate_peer(request, db, scope, body_sha256)

    return dependency


def _streamed_upload_auth(request: Request, db: Session = Depends(get_db)) -> str:
    """A streamed upload is signed over its declared SHA-256; the endpoint
    compares the stream with it once read."""
    declared = (request.headers.get(cluster_peer.HDR_BODY_SHA256) or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", declared):
        raise HTTPException(status_code=400, detail="Missing content SHA-256")
    _token, node_uid = authenticate_peer(request, db, cluster_peer.SCOPE_TRANSFER, declared)
    return node_uid


class DnsSyncRequestIn(BaseModel):
    zone: Optional[str] = None


class IncomingDnsSyncIn(BaseModel):
    source_node: str = Field(default="", max_length=120)
    zones: list[dict[str, Any]] = Field(default_factory=list)


class LocalTokenCreateIn(BaseModel):
    name: str = Field(default="SPanel Cluster Peer", max_length=80)
    allowed_ips: str = Field(default="", max_length=255)
    # cluster:transfer lets the peer push whole accounts onto this server.
    allow_transfer: bool = True


# ── Admin Management Endpoints ───────────────────────────────────────────

@router.get("/status")
def get_cluster_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    return multi_server.get_cluster_overview(db)


@router.put("/settings")
def save_cluster_settings(
    payload: ClusterSettingsIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    saved = multi_server.update_cluster_settings(payload.model_dump(exclude_none=True))
    log_action(db, current_user.id, "multi_server_settings", saved.get("node_name") or "cluster", request=request)
    return {"settings": saved, "message": "Đã lưu cấu hình Multi-Server Cluster."}


@router.post("/local-token")
def generate_local_cluster_token(
    payload: LocalTokenCreateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a ready-to-use Cluster Peer API Token on this server."""
    ensure_role(current_user.role, Role.admin)
    try:
        allowed_ips = normalize_allowed_ips(payload.allowed_ips or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Cluster scopes only: the token a peer holds can sync DNS, check for
    # conflicts and (if allowed) move accounts here, but it is not a billing
    # token and cannot create or terminate accounts through provisioning.
    raw_token, token_row = create_api_token(
        db,
        name=(payload.name or "SPanel Cluster Peer").strip(),
        scopes=cluster_peer.node_token_scopes(payload.allow_transfer),
        allowed_ips=allowed_ips,
        created_by_id=current_user.id,
    )
    log_action(db, current_user.id, "create_cluster_token", token_row.name, request=request)
    return {
        "id": token_row.id,
        "name": token_row.name,
        "token": raw_token,
        "scopes": token_row.scopes,
        "message": "Đã khởi tạo khóa Cluster API Token mới. Hãy sao chép và dán vào máy chủ SPanel đối tác.",
    }


@router.post("/tls-probe")
def probe_peer_tls(payload: TlsProbeIn, current_user: User = Depends(get_current_user)):
    """The certificate a peer presents, so the administrator can pin it."""
    ensure_role(current_user.role, Role.admin)
    try:
        url = multi_server.normalize_peer_url(payload.url)
        return cluster_peer.probe_tls(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except cluster_peer.PeerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/nodes")
def create_peer_node(
    payload: PeerNodeIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        node = multi_server.add_peer_node(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_add_node", f"{node.get('name')} ({node.get('url')})", request=request)
    return {
        "node": node,
        "message": (
            f"Đã kết nối thành công tới {node.get('name')} ({node.get('latency_ms')}ms)!"
            if node.get("status") == "online"
            else f"Đã thêm {node.get('name')}, nhưng kiểm tra kết nối ban đầu chưa phản hồi: {node.get('last_error')}"
        ),
    }


@router.put("/nodes/{node_id}")
def update_peer_node(
    node_id: str,
    payload: PeerNodeUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        node = multi_server.update_peer_node(node_id, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_update_node", node.get("name") or node_id, request=request)
    return {"node": node, "message": f"Đã cập nhật cấu hình máy chủ {node.get('name')}."}


@router.delete("/nodes/{node_id}")
def remove_peer_node(
    node_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        multi_server.delete_peer_node(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_remove_node", node_id, request=request)
    return {"ok": True, "message": "Đã gỡ bỏ máy chủ Peer khỏi cụm."}


@router.post("/nodes/{node_id}/test")
def test_peer_node(
    node_id: str,
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        node = multi_server.ping_peer_node(node_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if node.get("status") != "online":
        raise HTTPException(
            status_code=502,
            detail=node.get("last_error") or f"Máy chủ {node.get('name')} không phản hồi.",
        )
    return {
        "node": node,
        "message": f"Kết nối tới {node.get('name')} ổn định ({node.get('latency_ms')}ms • SPanel v{node.get('remote_info', {}).get('version', '—')}).",
    }


@router.post("/ping-all")
def ping_all_nodes(
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    nodes = multi_server.ping_all_peer_nodes()
    online = sum(1 for n in nodes if n.get("status") == "online")
    return {
        "nodes": nodes,
        "message": f"Đã kiểm tra toàn bộ cụm: {online}/{len(nodes)} máy chủ Peer đang trực tuyến (Online).",
    }


@router.post("/sync-dns")
def sync_cluster_dns(
    payload: DnsSyncRequestIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        result = multi_server.sync_dns_to_peers(db, zone_name=payload.zone, action="upsert")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_dns_sync", payload.zone or "all_zones", request=request)
    return result


@router.get("/dns-sync/status")
def dns_sync_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The DNS outbox (what is waiting, retrying or refused) and the replicas held here."""
    ensure_role(current_user.role, Role.admin)
    return cluster_dns.status(db)


@router.post("/dns-sync/reconcile")
def dns_sync_reconcile(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    result = cluster_dns.reconcile(db)
    cluster_worker.kick()
    log_action(db, current_user.id, "multi_server_dns_reconcile", f"{result.get('queued', 0)} queued", request=request)
    return result


@router.post("/dns-sync/outbox/{row_id}/retry")
def dns_sync_retry(row_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        cluster_dns.retry_row(db, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    cluster_worker.kick()
    return {"ok": True}


@router.get("/placement")
def placement_suggestion(
    purpose: str = Query(default="account", pattern="^(account|transfer)$"),
    user_id: Optional[int] = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Which node a new account (or the account being moved) is best placed on."""
    ensure_role(current_user.role, Role.admin)
    return cluster_placement.suggest_node(db, purpose=purpose, user_id=user_id)


@router.get("/transfers")
def list_transfers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"jobs": cluster_transfer.list_jobs(db)}


@router.post("/transfers")
def start_transfer(
    payload: TransferIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move an account to another node: backup, upload, restore there, and
    optionally hand its DNS over and suspend it here."""
    ensure_role(current_user.role, Role.admin)
    try:
        job = cluster_transfer.start_transfer(
            db, user_id=payload.user_id, node_id=payload.node_id, switch_dns=payload.switch_dns,
            suspend_source=payload.suspend_source, created_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_transfer", f"{job['username']} -> {job['node_name']}",
               f"dns={payload.switch_dns} suspend={payload.suspend_source}", request=request)
    return {"job": job, "message": f"Đã bắt đầu chuyển tài khoản {job['username']} sang {job['node_name']}."}


@router.delete("/dns-sync/outbox/{row_id}")
def dns_sync_discard(row_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        cluster_dns.discard_row(db, row_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


# Remote database hosts: app.api.remote_mysql (same /multi-server/remote-db URLs).


# ── Peer-to-Peer Cluster Endpoints (Bearer Token Authenticated) ──────────

@router.get("/peer/ping")
def peer_ping(
    db: Session = Depends(get_db),
    _node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_READ)),
):
    return multi_server.local_node_summary(db)


@router.get("/peer/check-conflict")
def peer_check_conflict(
    domain: str = Query(default="", max_length=253),
    username: str = Query(default="", max_length=64),
    db: Session = Depends(get_db),
    node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_READ)),
):
    return multi_server.check_local_conflict(db, domain=domain, username=username, asking_node=node_uid)


@router.post("/peer/dns-sync")
def peer_receive_dns_sync(
    payload: IncomingDnsSyncIn,
    db: Session = Depends(get_db),
    node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_WRITE)),
):
    try:
        return multi_server.apply_incoming_dns_sync(
            db, payload.zones, source_node=payload.source_node, source_uid=node_uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/peer/dns-inventory")
def peer_dns_inventory(
    db: Session = Depends(get_db),
    node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_READ)),
):
    """The asking node's zones held here, with the serial last applied (for its reconcile)."""
    return {"zones": cluster_dns.inventory_for(db, node_uid)}


# ── Peer-to-peer account transfer (cluster:transfer) ─────────────────────

@router.post("/peer/transfer/precheck")
def peer_transfer_precheck(
    payload: PeerTransferPrecheckIn,
    db: Session = Depends(get_db),
    node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_TRANSFER)),
):
    return cluster_transfer.precheck(db, payload.username, payload.domains, node_uid)


@router.put("/peer/transfer/upload")
async def peer_transfer_upload(request: Request, node_uid: str = Depends(_streamed_upload_auth)):
    """Receive an account archive as a stream, within the size limit, and
    keep it only if its SHA-256 is the one the signature covered."""
    declared = (request.headers.get(cluster_peer.HDR_BODY_SHA256) or "").strip().lower()
    username = (request.headers.get("X-SPanel-Transfer-User") or "").strip()
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    try:
        name = cluster_transfer.new_upload_name(username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    directory = await run_in_threadpool(backup._ensure_user_dir, Path(backup.user_restore_dir()))
    free = (await run_in_threadpool(shutil.disk_usage, directory)).free
    limit = min(backup.MAX_UPLOAD_BYTES, int(free / cluster_transfer.DISK_MARGIN))
    if length <= 0:
        raise HTTPException(status_code=411, detail="Content-Length is required")
    if length > limit:
        raise HTTPException(status_code=413, detail=f"The archive is larger than this node accepts ({limit} bytes).")
    path = directory / name
    digest = hashlib.sha256()
    written = 0
    handle = await run_in_threadpool(path.open, "wb")
    try:
        async for chunk in request.stream():
            written += len(chunk)
            if written > length:
                raise HTTPException(status_code=400, detail="More data than Content-Length")
            digest.update(chunk)
            await run_in_threadpool(handle.write, chunk)
    except BaseException:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    handle.close()
    if written != length or digest.hexdigest() != declared:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The upload is incomplete or its SHA-256 does not match.")
    multi_server.append_sync_log("transfer_upload", username, "info", f"Nhận bản sao lưu {written} bytes từ node {node_uid}.")
    return {"upload_id": name, "size": written}


@router.post("/peer/transfer/restore")
def peer_transfer_restore(
    payload: PeerTransferRestoreIn,
    db: Session = Depends(get_db),
    auth: tuple = Depends(peer_auth_token(cluster_peer.SCOPE_TRANSFER)),
):
    token, node_uid = auth
    try:
        passphrase = cluster_transfer.unseal(token.token_hash, payload.passphrase_sealed)
        password_hash = (cluster_transfer.unseal(token.token_hash, payload.password_hash_sealed)
                         if payload.password_hash_sealed else "")
        job = cluster_transfer.start_incoming_restore(
            db, upload_id=payload.upload_id, username=payload.username, passphrase=passphrase,
            password_hash=password_hash, source_uid=node_uid, source_name=payload.source_name,
            source_job=payload.source_job,
        )
    except (ValueError, cluster_transfer.TransferError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_uid": job["job_uid"], "status": job["status"]}


@router.get("/peer/transfer/jobs/{job_uid}")
def peer_transfer_job(
    job_uid: str,
    db: Session = Depends(get_db),
    node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_TRANSFER)),
):
    try:
        return cluster_transfer.incoming_job(db, job_uid, node_uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/peer/transfer/claim-zones")
def peer_transfer_claim_zones(
    payload: PeerClaimZonesIn,
    db: Session = Depends(get_db),
    node_uid: str = Depends(peer_auth(cluster_peer.SCOPE_TRANSFER)),
):
    return cluster_transfer.claim_zones(db, payload.zones, payload.old_ips, node_uid)
