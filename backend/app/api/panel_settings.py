from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.core.security import hash_password
from app.core.step_up import require_sensitive_action_step_up
from app.models.entities import User
from app.schemas.schemas import (
    AdminAccountUpdate,
    PanelIpv6Toggle,
    PanelSettingsOut,
    PanelSettingsUpdate,
    PanelSslInstall,
    PanelSslUseDomain,
)
from app.services import cron, panel_ipv6, panel_settings, site_users
from app.services.audit import list_audit_logs, log_action, verify_audit_chain


router = APIRouter(prefix="/panel-settings", tags=["panel-settings"])


# The login page needs the panel's name and its artwork. What certificate the
# panel runs on, and which domains live on this server, are for people who have
# signed in - an anonymous visitor asking for the login page is not owed the
# customer list.
PUBLIC_SETTING_FIELDS = ("app_name", "logo_url", "favicon_url", "login_background_url", "ticket_url", "primary_color")


@router.get("/public", response_model=PanelSettingsOut)
def public_panel_settings():
    data = panel_settings._read_raw_lenient()
    app_name = (data.get("app_name") or panel_settings.settings.app_name or "spanel").strip() or "spanel"
    return {
        "app_name": app_name,
        "primary_color": data.get("primary_color") or "#4f46e5",
        "logo_url": panel_settings._asset_url(data.get("logo_filename")),
        "favicon_url": panel_settings._asset_url(data.get("favicon_filename")) or "/favicon.png",
        "login_background_url": panel_settings._asset_url(data.get("login_background_filename")),
        "ticket_url": data.get("ticket_url") or "",
    }


@router.get("", response_model=PanelSettingsOut)
def get_panel_settings(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return panel_settings.current_settings()


@router.patch("", response_model=PanelSettingsOut)
def update_panel_settings(
    payload: PanelSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        result = panel_settings.update_settings(
            primary_color=payload.primary_color,
            app_name=payload.app_name,
            panel_hostname=payload.panel_hostname,
            panel_port=payload.panel_port,
            panel_url=payload.panel_url,
            ticket_url=payload.ticket_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "update_panel_settings", result.get("panel_url") or "panel", request=request)
    return result


@router.patch("/admin-account")
def update_admin_account(
    payload: AdminAccountUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    next_email = str(payload.email) if payload.email is not None else None
    password_changed = bool(payload.password)
    if password_changed:
        require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
    if next_email is not None and next_email != current_user.email:
        if db.query(User).filter(User.email == next_email, User.id != current_user.id).first():
            raise HTTPException(status_code=409, detail="Email already in use")
    if password_changed:
        try:
            minted = site_users.retire_shared_login_password(
                current_user.username,
                already_separate=current_user.sftp_password_set_at is not None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if minted is not None:
            current_user.sftp_password_set_at = datetime.utcnow()
        current_user.hashed_password = hash_password(payload.password or "")
        current_user.token_version = (current_user.token_version or 0) + 1
    if next_email is not None and next_email != current_user.email:
        current_user.email = next_email
    db.commit()
    log_action(db, current_user.id, "update_admin_account", current_user.username, request=request)
    return {"message": "Admin account updated", "password_changed": password_changed}


@router.post("/logo", response_model=PanelSettingsOut)
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        result = await panel_settings.save_asset("logo", file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_panel_logo", "panel", request=request)
    return result


@router.post("/login_background", response_model=PanelSettingsOut)
async def upload_login_background(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        result = await panel_settings.save_asset("login_background", file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_panel_login_background", "panel", request=request)
    return result

@router.post("/favicon", response_model=PanelSettingsOut)
async def upload_favicon(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        result = await panel_settings.save_asset("favicon", file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_panel_favicon", "panel", request=request)
    return result


@router.post("/ssl", response_model=PanelSettingsOut)
def install_panel_ssl(
    payload: PanelSslInstall,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        result = panel_settings.install_panel_ssl(
            str(current_user.email or ""),
            panel_hostname=payload.panel_hostname,
            panel_port=payload.panel_port,
            panel_url=payload.panel_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "install_panel_ssl", result.get("panel_url") or payload.panel_hostname or "panel", request=request)
    return result


@router.post("/ssl/use-domain")
def use_domain_certificate(
    payload: PanelSslUseDomain,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Serve the panel with a certificate one of this server's websites already has.

    Better than asking a certificate authority for a second certificate covering
    a name it has already signed, and it renews with the website.
    """
    ensure_role(current_user.role, Role.admin)
    result = panel_settings.use_domain_certificate(payload.domain, payload.panel_port)
    log_action(db, current_user.id, "panel_ssl_use_domain", payload.domain, request=request)
    return result


@router.post("/ssl/self-signed")
def regenerate_self_signed(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    result = panel_settings.regenerate_self_signed()
    log_action(db, current_user.id, "panel_ssl_self_signed", "panel", request=request)
    return result


@router.post("/ipv6", response_model=PanelSettingsOut)
def toggle_ipv6(
    payload: PanelIpv6Toggle,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Turn IPv6 on or off for every website and for the panel itself.

    Turning it on is refused when the server has no IPv6 address: nginx cannot
    bind an address family that is not there, and it would refuse to start.
    """
    ensure_role(current_user.role, Role.admin)
    result = panel_ipv6.set_enabled(bool(payload.enabled))
    log_action(
        db,
        current_user.id,
        "toggle_ipv6",
        "on" if payload.enabled else "off",
        request=request,
    )
    settings_out = panel_settings.current_settings()
    settings_out["message"] = result.get("message")
    return settings_out


@router.get("/audit-logs")
def get_audit_logs(
    q: str = "",
    action_filter: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    return list_audit_logs(db, q=q, action_filter=action_filter, limit=limit)


@router.post("/audit-logs/verify")
def verify_audit_logs_chain(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    res = verify_audit_chain(db, seal_unhashed=True)
    log_action(
        db,
        current_user.id,
        "verify_audit_chain",
        "audit_logs",
        detail=f"valid={res.get('valid')} total={res.get('total_entries')}",
        request=request,
    )
    return verify_audit_chain(db, seal_unhashed=True)


@router.get("/cron-security")
def get_cron_security(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    return cron.cron_security_status(db)


@router.post("/cron-security/enforce")
def enforce_cron_security(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    res = cron.enforce_all_cron_confinement(db)
    log_action(
        db,
        current_user.id,
        "enforce_cron_confinement",
        "cron",
        detail=f"upgraded={res.get('upgraded_jobs', 0)} total={res.get('total_jobs', 0)}",
        request=request,
    )
    return res



