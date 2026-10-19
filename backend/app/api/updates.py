from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import SystemAutoUpdateConfig
from app.services import updates, auto_updater

router = APIRouter(prefix="/updates", tags=["updates"])


class UpdateSourcePayload(BaseModel):
    source_type: Optional[str] = None
    server_url: Optional[str] = None
    manifest_url: Optional[str] = None
    channel: Optional[str] = None
    auth_token: Optional[str] = None
    clear_auth_token: bool = False
    verify_checksum: Optional[bool] = None
    auto_check_enabled: Optional[bool] = None
    auto_check_interval_hours: Optional[int] = None
    auto_update_enabled: Optional[bool] = None
    auto_update_window: Optional[str] = None
    auto_rollback_enabled: Optional[bool] = None


class SnapshotCreatePayload(BaseModel):
    reason: str = "manual"


class SnapshotRestorePayload(BaseModel):
    restore_db: bool = False


class PanelAutoUpdatePayload(BaseModel):
    enabled: bool = False
    mode: str = "all"
    auto_reboot: bool = False
    window: str = "02:00"
    auto_check_enabled: bool = True


@router.get("/status")
def get_update_status(refresh: bool = Query(default=False), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.status(force_refresh=refresh)


@router.post("/source")
def save_update_source(payload: UpdateSourcePayload, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    masked = updates.save_update_source_config(payload.model_dump(exclude_unset=True))
    panel = updates.panel_release_status(force_refresh=True)
    return {"source": masked, "panel": panel}


@router.post("/snapshots/create")
def create_panel_snapshot(payload: SnapshotCreatePayload = SnapshotCreatePayload(), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    snap = updates.create_snapshot(reason=payload.reason)
    return {"snapshot": snap, "snapshots": updates.list_snapshots()}


@router.post("/snapshots/{snapshot_id}/restore")
def restore_panel_snapshot(
    snapshot_id: str,
    payload: SnapshotRestorePayload = SnapshotRestorePayload(),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        res = updates.rollback_to_snapshot(snapshot_id, restore_db=payload.restore_db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**res, "snapshots": updates.list_snapshots()}


@router.delete("/snapshots/{snapshot_id}")
def delete_panel_snapshot(snapshot_id: str, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        res = updates.delete_snapshot(snapshot_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**res, "snapshots": updates.list_snapshots()}


@router.post("/os/run")
def run_os_update(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.run_os_update().__dict__


@router.post("/os/auto")
def configure_os_auto_update(payload: SystemAutoUpdateConfig, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.configure_os_auto_update(payload.enabled, payload.mode, payload.auto_reboot).__dict__


@router.post("/panel/run")
def run_panel_update(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.run_panel_update().__dict__


@router.post("/panel/auto")
def configure_panel_auto_update(payload: PanelAutoUpdatePayload, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    source = auto_updater.set_auto_update(
        enabled=payload.enabled,
        window=payload.window or "02:00",
        auto_check=payload.auto_check_enabled,
    )
    return {"status": "success", "enabled": payload.enabled, "source": source}
