"""Remote MySQL / MariaDB hosts (admin). Lives under /multi-server/remote-db,
where the Multi-Server page has always called it; the cluster peer endpoints
stay in app.api.multi_server."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import remote_mysql
from app.services.audit import log_action

router = APIRouter(prefix="/multi-server/remote-db", tags=["remote-mysql"])


class RemoteDbHostIn(BaseModel):
    name: str = Field(..., max_length=80)
    host: str = Field(..., max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    admin_user: str = Field(default="root", max_length=64)
    admin_password: str = Field(default="", max_length=255)
    use_tls: bool = False
    ca_cert: str = Field(default="", max_length=remote_mysql.MAX_CA_CERT_BYTES)
    allowed_client_hosts: str = Field(default="", max_length=512)
    enabled: bool = True


class RemoteDbHostUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    host: Optional[str] = Field(default=None, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    admin_user: Optional[str] = Field(default=None, max_length=64)
    # Empty or absent keeps the stored password.
    admin_password: Optional[str] = Field(default=None, max_length=255)
    use_tls: Optional[bool] = None
    ca_cert: Optional[str] = Field(default=None, max_length=remote_mysql.MAX_CA_CERT_BYTES)
    allowed_client_hosts: Optional[str] = Field(default=None, max_length=512)
    enabled: Optional[bool] = None


def _host_message(host: dict, saved: str) -> str:
    if host.get("status") == "online":
        return f"{saved} Connected to {host.get('name')} ({host.get('latency_ms')}ms, {host.get('server_version')})."
    return f"{saved} The connection test failed: {host.get('last_error') or 'no answer'}"


@router.get("")
def list_remote_db_hosts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"hosts": remote_mysql.list_hosts(db)}


@router.post("")
def create_remote_db_host(
    payload: RemoteDbHostIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        row = remote_mysql.add_host(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = remote_mysql.test_host(db, row.id)
    remote_mysql.sync_phpmyadmin_servers(db)
    log_action(db, current_user.id, "remote_db_host_add", f"{row.host}:{row.port}", request=request)
    host = remote_mysql.serialize_host(db, row)
    return {"host": host, "message": _host_message(host, "Remote database host saved.")}


@router.put("/{host_id}")
def update_remote_db_host(
    host_id: int,
    payload: RemoteDbHostUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        row = remote_mysql.update_host(db, host_id, payload.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = remote_mysql.test_host(db, row.id)
    remote_mysql.sync_phpmyadmin_servers(db)
    log_action(db, current_user.id, "remote_db_host_update", f"{row.host}:{row.port}", request=request)
    host = remote_mysql.serialize_host(db, row)
    return {"host": host, "message": _host_message(host, "Remote database host updated.")}


@router.post("/{host_id}/test")
def test_remote_db_host(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        row = remote_mysql.test_host(db, host_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    host = remote_mysql.serialize_host(db, row)
    if host["status"] != "online":
        raise HTTPException(status_code=502, detail=host["last_error"] or f"No answer from {row.host}:{row.port}")
    return {"host": host, "message": _host_message(host, "Connection test passed.")}


@router.delete("/{host_id}")
def delete_remote_db_host(
    host_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        label = remote_mysql.delete_host(db, host_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    remote_mysql.sync_phpmyadmin_servers(db)
    log_action(db, current_user.id, "remote_db_host_remove", label, request=request)
    return {"ok": True, "message": "Remote database host removed."}
