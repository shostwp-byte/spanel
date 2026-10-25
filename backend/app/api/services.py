from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import check_sqlite_backup, get_db, get_sqlite_health, run_sqlite_hot_backup
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import ServiceAction
from app.services import alert_webhooks
from app.services.audit import log_action
from app.services.shell import shell
from app.services.system import (
    clear_self_healing_logs,
    get_self_healing_status,
    install_wordpress_stack,
    list_services,
    resource_usage,
    run_self_healing_check,
    service_action,
    system_info,
    update_self_healing_config,
)

router = APIRouter(prefix="/services", tags=["services"])

# Everything here describes the machine, not a customer's slice of it: the
# kernel and hostname, the whole box's CPU and memory, and which system units
# are running. An end user has no business with any of it - it is the
# operator's server, and knowing what is installed and how loaded it is helps
# nobody but someone sizing up the host.
#
# The reads were open to end users and the status action with them. Hiding the
# page in the frontend is not access control; these are the checks that are.


@router.get("/system-info")
def get_system_info(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return system_info()


@router.get("/resource-usage")
def get_resource_usage(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return resource_usage()


@router.get("/list")
def get_services(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"services": list_services()}


@router.post("/action")
def run_service_action(payload: ServiceAction, current_user: User = Depends(get_current_user)):
    # Reading a unit's status says as much about the host as listing them does.
    ensure_role(current_user.role, Role.admin)
    try:
        result = service_action(payload.name, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.__dict__


@router.post("/install-wordpress-stack")
def install_stack(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    result = install_wordpress_stack()
    return result.__dict__


@router.get("/self-healing")
def read_self_healing(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return get_self_healing_status()


@router.put("/self-healing")
def save_self_healing(payload: dict, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return update_self_healing_config(payload or {})


@router.post("/self-healing/run")
def trigger_self_healing(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return run_self_healing_check(reason="manual")


@router.delete("/self-healing/logs")
def delete_self_healing_logs(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return clear_self_healing_logs()


@router.get("/db-health")
def read_db_health(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return get_sqlite_health()


@router.post("/db-backup")
def create_db_hot_backup(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    res = run_sqlite_hot_backup(reason="manual")
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=res.get("detail") or "Database backup failed")
    return res


@router.post("/db-backups/{filename}/restore")
def restore_db_hot_backup(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Put a hot backup back as the panel database.

    Checked here first (read-only integrity_check) so a bad file is refused
    with a reason before anything stops. The helper checks again as root,
    then - in a unit of its own, since it has to stop this API - stops the
    API and the timers that open the database, keeps the current file as
    spanel.db.pre-restore-<time>, swaps, and starts everything again.
    """
    ensure_role(current_user.role, Role.admin)
    try:
        check = check_sqlite_backup(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not check["ok"]:
        raise HTTPException(status_code=400, detail=f"The backup failed its integrity check: {check['integrity']}")
    # Audit before the swap: the entry would otherwise be written to the
    # database that is about to be replaced.
    log_action(db, current_user.id, "restore_panel_database", filename,
               f"revision={check['revision']}", request=request)
    result = shell.privileged("panel-db-restore", helper_args=[filename], check=False,
                              fallback=["bash", "-lc", f"echo scheduled={filename}"])
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=(result.stderr or result.stdout or "Restore failed").strip()[-400:])
    alert_webhooks.alert("panel_db_restored", f"Panel database restored from {filename}",
                         f"Requested by {current_user.username}. The API restarts during the swap.",
                         {"backup": filename, "revision": check["revision"]}, db=db)
    return {"status": "scheduled", "backup": filename, "revision": check["revision"],
            "detail": "The panel restarts in a few seconds with the restored database."}


class AlertWebhookPayload(BaseModel):
    enabled: Optional[bool] = None
    url: Optional[str] = Field(default=None, max_length=500)
    clear_url: bool = False
    format: Optional[str] = None
    endpoints_enabled: Optional[bool] = None
    events: Optional[list[str]] = None
    cooldown_minutes: Optional[int] = None


@router.get("/alert-webhook")
def read_alert_webhook(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return alert_webhooks.public_config()


@router.put("/alert-webhook")
def save_alert_webhook(payload: AlertWebhookPayload, request: Request, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        config = alert_webhooks.save_config(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "update_alert_webhook", config["url_host"] or "-",
               f"enabled={config['enabled']} format={config['format']}", request=request)
    return config


@router.post("/alert-webhook/test")
def test_alert_webhook(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        return alert_webhooks.send_test()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - say what the far end said, never the URL
        raise HTTPException(status_code=502, detail=f"The webhook did not accept the test ({type(exc).__name__})") from exc
