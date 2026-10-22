from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.provisioning import _get_provisioning_token, _require_scope
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import multi_server
from app.services.audit import log_action
from app.services.provisioning import create_api_token

router = APIRouter(prefix="/multi-server", tags=["multi-server"])


class ClusterSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    node_name: Optional[str] = Field(default=None, max_length=80)
    auto_dns_sync: Optional[bool] = None
    check_domain_conflict: Optional[bool] = None
    check_username_conflict: Optional[bool] = None


class PeerNodeIn(BaseModel):
    name: str = Field(..., max_length=80)
    url: str = Field(..., max_length=255)
    api_token: str = Field(default="", max_length=255)
    role: str = Field(default="full_peer", max_length=32)
    verify_tls: bool = False
    sync_dns: bool = True
    check_domain: bool = True
    enabled: bool = True


class PeerNodeUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    url: Optional[str] = Field(default=None, max_length=255)
    api_token: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, max_length=32)
    verify_tls: Optional[bool] = None
    sync_dns: Optional[bool] = None
    check_domain: Optional[bool] = None
    enabled: Optional[bool] = None


class DnsSyncRequestIn(BaseModel):
    zone: Optional[str] = None


class IncomingDnsSyncIn(BaseModel):
    source_node: str = Field(default="", max_length=120)
    zones: list[dict[str, Any]] = Field(default_factory=list)


class RemoteDbHostIn(BaseModel):
    name: str = Field(..., max_length=80)
    host: str = Field(..., max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    admin_user: str = Field(default="root", max_length=64)
    admin_password: str = Field(default="", max_length=255)
    use_tls: bool = False
    enabled: bool = True


class LocalTokenCreateIn(BaseModel):
    name: str = Field(default="SPanel Cluster Peer", max_length=80)
    allowed_ips: str = Field(default="", max_length=255)


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
    raw_token, token_row = create_api_token(
        db,
        name=(payload.name or "SPanel Cluster Peer").strip(),
        scopes="provisioning:read,provisioning:write",
        allowed_ips=(payload.allowed_ips or "").strip(),
    )
    log_action(db, current_user.id, "create_cluster_token", token_row.name, request=request)
    return {
        "id": token_row.id,
        "name": token_row.name,
        "token": raw_token,
        "message": "Đã khởi tạo khóa Cluster API Token mới. Hãy sao chép và dán vào máy chủ SPanel đối tác.",
    }


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
    result = multi_server.sync_dns_to_peers(db, zone_name=payload.zone, action="upsert")
    log_action(db, current_user.id, "multi_server_dns_sync", payload.zone or "all_zones", request=request)
    return result


# ── Remote Database Hosts (Remote MySQL / MariaDB) ───────────────────────

@router.post("/remote-db")
def create_remote_db_host(
    payload: RemoteDbHostIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        host = multi_server.add_remote_db_host(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_add_remote_db", f"{host.get('host')}:{host.get('port')}", request=request)
    return {
        "host": host,
        "message": (
            f"Đã thêm và kết nối thành công tới Remote Database {host.get('name')} ({host.get('latency_ms')}ms)!"
            if host.get("status") == "online"
            else f"Đã lưu {host.get('name')}, nhưng chưa kết nối được tới cổng {host.get('port')}: {host.get('last_error')}"
        ),
    }


@router.post("/remote-db/{host_id}/test")
def test_remote_db(
    host_id: str,
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        host = multi_server.test_remote_db_host(host_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if host.get("status") != "online":
        raise HTTPException(
            status_code=502,
            detail=host.get("last_error") or f"Không thể kết nối tới {host.get('host')}:{host.get('port')}",
        )
    return {
        "host": host,
        "message": f"Kết nối MySQL/MariaDB tới {host.get('name')} thành công ({host.get('latency_ms')}ms • {host.get('server_version')}).",
    }


@router.delete("/remote-db/{host_id}")
def remove_remote_db(
    host_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        multi_server.delete_remote_db_host(host_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    log_action(db, current_user.id, "multi_server_remove_remote_db", host_id, request=request)
    return {"ok": True, "message": "Đã xóa máy chủ Remote Database."}


# ── Peer-to-Peer Cluster Endpoints (Bearer Token Authenticated) ──────────

@router.get("/peer/ping")
def peer_ping(request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:read")
    return multi_server.local_node_summary(db)


@router.get("/peer/check-conflict")
def peer_check_conflict(
    request: Request,
    domain: str = Query(default="", max_length=253),
    username: str = Query(default="", max_length=64),
    db: Session = Depends(get_db),
):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:read")
    return multi_server.check_local_conflict(db, domain=domain, username=username)


@router.post("/peer/dns-sync")
def peer_receive_dns_sync(
    payload: IncomingDnsSyncIn,
    request: Request,
    db: Session = Depends(get_db),
):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    try:
        return multi_server.apply_incoming_dns_sync(db, payload.zones, source_node=payload.source_node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
