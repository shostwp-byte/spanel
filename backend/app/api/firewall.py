from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import FirewallBlocklistUrl, FirewallIpRule, FirewallPortRule
from app.services import firewall

router = APIRouter(prefix="/firewall", tags=["firewall"])


def _require_admin(current_user: User) -> None:
    ensure_role(current_user.role, Role.admin)


def _result(result):
    return result.__dict__


def _status_result(result):
    data = _result(result)
    data["rules"] = firewall.rules()
    return data


@router.get("/status")
def get_status(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return _status_result(firewall.status())


@router.post("/enable")
def enable_firewall(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return _result(firewall.enable())


@router.post("/disable")
def disable_firewall(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return _result(firewall.disable())


@router.post("/reload")
def reload_firewall(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return _result(firewall.reload())


@router.post("/repair")
def repair_firewall(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return _result(firewall.repair())


@router.post("/allow-port")
def allow_port(payload: FirewallPortRule, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        result = firewall.allow_port(payload.port, payload.protocol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _result(result)


@router.post("/allow-ip")
def allow_ip(payload: FirewallIpRule, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        result = firewall.allow_ip(payload.ip, payload.port, payload.protocol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _result(result)


@router.post("/block-ip")
def block_ip(payload: FirewallIpRule, request: Request, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        result = firewall.block_ip(
            payload.ip,
            payload.port,
            payload.protocol,
            requester=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _result(result)


@router.delete("/rules/{number}")
def delete_rule(number: int, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        result = firewall.delete_rule(number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _result(result)


@router.get("/blocklists")
def get_blocklists(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return _result(firewall.blocklists())


@router.post("/blocklists")
def add_blocklist(payload: FirewallBlocklistUrl, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    result = firewall.add_blocklist_url(payload.url)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not add URL").strip())
    return _result(result)


@router.post("/blocklists/delete")
def delete_blocklist(payload: FirewallBlocklistUrl, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    result = firewall.delete_blocklist_url(payload.url)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not delete URL").strip())
    return _result(result)


@router.post("/blocklists/update")
def update_blocklists(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    result = firewall.update_blocklists()
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not update blocklists").strip())
    return _result(result)
