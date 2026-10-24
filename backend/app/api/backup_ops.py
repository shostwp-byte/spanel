"""Backup operations that are about the server rather than one account: the
panel's own disaster-recovery backup and scheduled backup verification.
Administrators only."""
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import SftpBackupTarget, User
from app.services import backup_verify, panel_backup
from app.services.audit import log_action

router = APIRouter(prefix="/maintenance", tags=["backup-ops"])

_run_lock = threading.Lock()
_verify_lock = threading.Lock()


def _server_target_ids(db: Session) -> set[int]:
    """Destinations an administrator saved for the server (not a customer's own)."""
    rows = db.query(SftpBackupTarget).filter(SftpBackupTarget.owner_id == None,  # noqa: E711
                                             SftpBackupTarget.is_active == True).all()  # noqa: E712
    return {row.id for row in rows}


class PanelDrSettings(BaseModel):
    enabled: Optional[bool] = None
    window: Optional[str] = Field(default=None, max_length=5)
    weekdays: Optional[list[int]] = None
    target_ids: Optional[list[int]] = None
    keep_remote: Optional[int] = None


class PassphraseInit(BaseModel):
    rotate: bool = False


class PassphraseConfirm(BaseModel):
    passphrase: str = Field(min_length=1, max_length=256)


@router.get("/panel-dr")
def panel_dr_status(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {**panel_backup.public_status(), "key": panel_backup.key_status()}


@router.put("/panel-dr")
def panel_dr_save(payload: PanelDrSettings, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        status = panel_backup.save_settings(payload.model_dump(exclude_unset=True), _server_target_ids(db))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "update_panel_dr", "panel-dr",
               f"enabled={status['enabled']} window={status['window']} targets={status['target_ids']}", request=request)
    return {**status, "key": panel_backup.key_status()}


@router.post("/panel-dr/passphrase")
def panel_dr_passphrase(payload: PassphraseInit, request: Request, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    """Returns the passphrase - the only time it is ever returned."""
    ensure_role(current_user.role, Role.admin)
    try:
        created = panel_backup.init_passphrase(rotate=payload.rotate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "rotate_panel_dr_passphrase" if payload.rotate else "create_panel_dr_passphrase",
               "panel-dr", f"fingerprint={created['fingerprint']}", request=request)
    return created


@router.post("/panel-dr/passphrase/confirm")
def panel_dr_passphrase_confirm(payload: PassphraseConfirm, request: Request, db: Session = Depends(get_db),
                                current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        status = panel_backup.confirm_passphrase(payload.passphrase)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "confirm_panel_dr_passphrase", "panel-dr", request=request)
    return status


def _run_panel_dr_job() -> None:
    db = SessionLocal()
    try:
        panel_backup.run_backup(db, trigger="manual")
    except ValueError:
        pass
    finally:
        db.close()
        _run_lock.release()


@router.post("/panel-dr/run")
def panel_dr_run(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    if not panel_backup.key_status()["confirmed"]:
        raise HTTPException(status_code=400, detail="Create the passphrase and confirm you stored it first")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A panel self-backup is already running")
    threading.Thread(target=_run_panel_dr_job, name="spanel-panel-dr", daemon=True).start()
    log_action(db, current_user.id, "run_panel_dr", "panel-dr", request=request)
    return {"status": "started", "detail": "Running. Refresh to see the result."}


class VerifySettings(BaseModel):
    enabled: Optional[bool] = None
    window: Optional[str] = Field(default=None, max_length=5)
    weekdays: Optional[list[int]] = None
    remote_samples: Optional[int] = None
    test_restore_db: Optional[bool] = None
    max_download_mb: Optional[int] = None


@router.get("/backup-verify")
def backup_verify_status(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return backup_verify.public_status()


@router.put("/backup-verify")
def backup_verify_save(payload: VerifySettings, request: Request, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        status = backup_verify.save_settings(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "update_backup_verify", "backup-verify",
               f"enabled={status['enabled']} window={status['window']} test_restore_db={status['test_restore_db']}",
               request=request)
    return status


def _run_verify_job() -> None:
    db = SessionLocal()
    try:
        backup_verify.run_verification(db, trigger="manual")
    finally:
        db.close()
        _verify_lock.release()


@router.post("/backup-verify/run")
def backup_verify_run(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    if not _verify_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A verification is already running")
    threading.Thread(target=_run_verify_job, name="spanel-backup-verify", daemon=True).start()
    log_action(db, current_user.id, "run_backup_verify", "backup-verify", request=request)
    return {"status": "started", "detail": "Verifying. Refresh to see the result."}
