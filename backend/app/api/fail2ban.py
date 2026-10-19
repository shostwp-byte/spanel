"""Who fail2ban is currently keeping out, and letting one back in."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import addons, fail2ban
from app.services.audit import log_action

router = APIRouter(prefix="/fail2ban", tags=["fail2ban"])


class UnbanRequest(BaseModel):
    ip: str
    jail: Optional[str] = None


class BanRequest(BaseModel):
    ip: str
    jail: str = "sshd"


class ServiceActionRequest(BaseModel):
    action: str


@router.get("/status")
def status(current_user: User = Depends(get_current_user)):
    """Counts and health of Fail2Ban."""
    ensure_role(current_user.role, Role.admin)
    info = fail2ban.status()
    if info.get("installed") and info.get("running") and not addons.is_installed(addons.FAIL2BAN):
        addons.install(addons.FAIL2BAN)
    info["addon_installed"] = addons.is_installed(addons.FAIL2BAN)
    return info


@router.get("/banned")
def banned(
    jail: str = "sshd",
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        return fail2ban.banned(jail=jail, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ban")
def ban_ip(
    payload: BanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        fail2ban.ban(payload.ip, jail=payload.jail or "sshd")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "fail2ban_ban", f"{payload.ip} ({payload.jail})")
    return {"banned_ip": payload.ip, **fail2ban.banned(jail=payload.jail or "sshd")}


@router.post("/unban")
def unban(
    payload: UnbanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Let an address back in."""
    ensure_role(current_user.role, Role.admin)
    try:
        fail2ban.unban(payload.ip, jail=payload.jail)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "fail2ban_unban", payload.ip)
    return {"unbanned": payload.ip, **fail2ban.banned(jail=payload.jail or "sshd")}


@router.post("/service")
def control_service(
    payload: ServiceActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        if payload.action == "install":
            info = fail2ban.install()
            addons.install(addons.FAIL2BAN)
        else:
            info = fail2ban.service_control(payload.action)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "fail2ban_service", payload.action)
    info["addon_installed"] = addons.is_installed(addons.FAIL2BAN)
    return info
