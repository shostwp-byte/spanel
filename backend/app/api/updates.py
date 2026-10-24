from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import SystemAutoUpdateConfig
from app.services import updates, auto_updater, maintenance_window, php
from app.services.audit import log_action

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


class PhpSmartConfigPayload(BaseModel):
    auto_patch_enabled: Optional[bool] = None
    auto_harden_php2shell: Optional[bool] = None
    harden_mode: Optional[str] = None
    check_interval_hours: Optional[int] = None
    preferred_window: Optional[str] = None


class PhpUpgradeRunPayload(BaseModel):
    versions: Optional[list[str]] = None


class PhpHardenPayload(BaseModel):
    versions: Optional[list[str]] = None
    mode: Optional[str] = None


class PhpAcknowledgePayload(BaseModel):
    update_id: Optional[str] = None


@router.get("/php/status")
def get_php_smart_status(refresh: bool = Query(default=False), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return php.check_php_updates(force_refresh=refresh)


@router.post("/php/config")
def update_php_smart_config(payload: PhpSmartConfigPayload, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    state = php.save_php_smart_config(payload.model_dump(exclude_unset=True))
    return {"status": "success", "php_smart_guard": state}


@router.post("/php/run")
def run_php_smart_upgrade(
    payload: PhpUpgradeRunPayload = PhpUpgradeRunPayload(),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        return php.upgrade_php_packages(versions=payload.versions, trigger="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/php/harden")
def run_php_security_hardening(
    payload: PhpHardenPayload = PhpHardenPayload(),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        return php.apply_php2shell_hardening(versions=payload.versions, mode=payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/php/acknowledge")
def acknowledge_php_update_notifications(
    payload: PhpAcknowledgePayload = PhpAcknowledgePayload(),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    state = php.acknowledge_php_updates(update_id=payload.update_id)
    return {"status": "ok", "php_smart_guard": state}


class CoreStackUpgradePayload(BaseModel):
    packages: Optional[list[str]] = None


class CoreStackSchedulePayload(BaseModel):
    enabled: Optional[bool] = None
    window: Optional[str] = None
    weekdays: Optional[list[int]] = None
    packages: Optional[list[str]] = None


class MaintenanceWindowPayload(BaseModel):
    window_minutes: Optional[int] = None
    core_stack: Optional[CoreStackSchedulePayload] = None


@router.get("/maintenance-window")
def get_maintenance_window(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return maintenance_window.overview()


@router.put("/maintenance-window")
def save_maintenance_window(
    payload: MaintenanceWindowPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    changes = payload.model_dump(exclude_unset=True)
    try:
        config = maintenance_window.save_config(changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The blacklist follows the schedule: while the panel patches these
    # packages, unattended-upgrades must leave them alone, and when it stops,
    # they go back to the OS.
    core = config["core_stack"]
    result = updates.set_core_stack_blacklist(core["enabled"], core["packages"])
    log_action(db, current_user.id, "update_maintenance_window", "core_stack",
               f"enabled={core['enabled']} window={core['window']} packages={','.join(core['packages'])}",
               request=request)
    overview = maintenance_window.overview()
    overview["blacklist_applied"] = result.returncode == 0
    return overview


@router.get("/core-stack/status")
def get_core_stack_status(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.check_core_stack_updates()


@router.post("/core-stack/run")
def run_core_stack_upgrade(
    payload: CoreStackUpgradePayload = CoreStackUpgradePayload(),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        return updates.upgrade_core_stack_packages(packages=payload.packages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


