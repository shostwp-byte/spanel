import json
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.rate_limit import enforce_rate_limit
from app.core.security import hash_password
from app.models.entities import ApiToken, DatabaseAccount, ProvisioningAccount, User, UserPackage, Website
from app.schemas.schemas import (
    ApiTokenCreate,
    ApiTokenCreatedResponse,
    ApiTokenOut,
    ProvisioningAccountCreate,
    ProvisioningAccountOut,
    ProvisioningPackageChange,
    ProvisioningPasswordChange,
    ProvisioningSuspendRequest,
    ProvisioningUsageOut,
)
from app.services import mariadb, nginx, site_users, storage_quota, wordpress
from app.services.audit import log_action
from app.services.provisioning import (
    account_to_dict,
    authenticate_token,
    check_ip_allowed,
    create_api_token,
    panel_base_url,
    suspend_account,
    terminate_account,
    unsuspend_account,
)
from app.services.sso_tokens import create_panel_login_token

router = APIRouter(prefix="/provisioning/v1", tags=["provisioning"])


def _get_provisioning_token(request: Request, db: Session) -> ApiToken:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    raw = auth[7:].strip()
    token = authenticate_token(db, raw)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    client_ip = (request.client.host if request.client else "").strip()
    if not check_ip_allowed(token, client_ip):
        raise HTTPException(status_code=403, detail="IP not allowed")
    return token


def _require_scope(token: ApiToken, scope: str) -> None:
    scopes = {s.strip() for s in (token.scopes or "").split(",") if s.strip()}
    if scope not in scopes:
        raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")


def _account_by_external_id(db: Session, external_id: str) -> ProvisioningAccount:
    account = db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == external_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _provisioning_email(username: str) -> str:
    return f"{username}@users.spanel.dev"

def _provisioning_app_type(payload: ProvisioningAccountCreate) -> str:
    if not payload.domain:
        return "php"
    if payload.install_wordpress:
        return "wordpress"
    return payload.app_type


# ── Plans ────────────────────────────────────────────────────────────────

@router.get("/plans")
def list_plans(request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:read")
    packages = db.query(UserPackage).order_by(UserPackage.id.asc()).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "website_limit": p.website_limit,
            "storage_limit_mb": p.storage_limit_mb,
            "database_limit": p.database_limit,
            "alias_limit": p.alias_limit,
            "backup_retention_days": p.backup_retention_days,
            "terminal_enabled": p.terminal_enabled,
            "waf_enabled": p.waf_enabled,
            "wordpress_enabled": p.wordpress_enabled,
        }
        for p in packages
    ]


# ── Accounts ─────────────────────────────────────────────────────────────

@router.post("/accounts", response_model=ProvisioningAccountOut)
def create_account(payload: ProvisioningAccountCreate, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")

    existing = db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == payload.external_id).first()
    if existing:
        if existing.status in ("active", "pending") and existing.user:
            return account_to_dict(existing, db)
        db.delete(existing)
        db.commit()

    account_email = _provisioning_email(payload.username)

    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if payload.domain and (
        db.query(Website).filter(Website.domain == payload.domain).first()
        or nginx.vhost_exists(payload.domain)
    ):
        raise HTTPException(status_code=409, detail="Domain already exists")

    package = db.query(UserPackage).filter(UserPackage.id == payload.package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")

    account = ProvisioningAccount(
        external_id=payload.external_id,
        package_id=payload.package_id,
        status="pending",
        last_action="create",
    )
    db.add(account)
    db.flush()

    # Its own secret from the start: the panel password is not an SFTP password.
    sftp_password = site_users.generate_login_password()
    try:
        linux_user = site_users.ensure_panel_user(payload.username, sftp_password)
    except (ValueError, RuntimeError) as exc:
        account.status = "failed"
        account.last_message = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = User(
        username=payload.username,
        email=account_email,
        hashed_password=hash_password(payload.password),
        role=Role.reseller if getattr(payload, "is_reseller", False) else Role.end_user,
        package_id=package.id,
        website_limit=package.website_limit,
        storage_limit_mb=package.storage_limit_mb,
        sftp_password_set_at=datetime.utcnow(),
    )
    db.add(user)
    db.flush()
    account.user_id = user.id

    app_type_value = _provisioning_app_type(payload)
    install_wp = payload.install_wordpress and app_type_value == "wordpress"
    db_info = None

    if payload.domain:
        root_path = site_users.site_root_for_panel_user(payload.username, payload.domain)
        try:
            if install_wp:
                db_info = mariadb.create_database(payload.domain)
                linux_user = site_users.ensure_site_runtime(payload.domain, root_path, payload.php_version, linux_user)
                root_path = wordpress.install_wordpress(
                    payload.domain, db_info, payload.domain,
                    "admin", payload.password, account_email,
                    payload.php_version, linux_user, root_path=root_path,
                )
            else:
                runtime_php = payload.php_version if app_type_value in {"wordpress", "php"} else None
                linux_user = site_users.ensure_site_runtime(payload.domain, root_path, runtime_php, linux_user)

            from app.api.websites import _ensure_default_waf_file, _write_placeholder_page
            _ensure_default_waf_file(payload.domain)
            if not install_wp:
                from app.core.config import settings as app_settings
                if not app_settings.command_dry_run:
                    _write_placeholder_page(payload.domain, root_path, linux_user, payload.php_version)
                    site_users.fix_site_path(str(site_users.document_root(root_path)), linux_user)

            rewrite_mode = "front_controller" if app_type_value == "wordpress" else "none"
            php_socket = site_users.site_php_fpm_socket(linux_user, root_path, payload.php_version) if app_type_value in {"wordpress", "php"} else None
            nginx.write_vhost(
                payload.domain, root_path,
                app_type=app_type_value,
                php_version=payload.php_version,
                php_fpm_socket_override=php_socket,
                document_root="public_html",
                rewrite_mode=rewrite_mode,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            if db_info:
                mariadb.drop_database(db_info["db_name"], db_info["db_user"])
            account.status = "failed"
            account.last_message = str(exc)
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        website = Website(
            domain=payload.domain,
            owner_id=user.id,
            root_path=root_path,
            document_root="public_html",
            linux_user=linux_user,
            php_version=payload.php_version,
            app_type=app_type_value,
            nginx_rewrite_mode=rewrite_mode,
            status="active",
        )
        db.add(website)
        db.flush()

        if db_info:
            from app.core.secrets import encrypt
            db.add(DatabaseAccount(
                owner_id=user.id,
                website_id=website.id,
                db_name=db_info["db_name"],
                db_user=db_info["db_user"],
                db_password=encrypt(db_info["db_password"]),
            ))

        account.primary_website_id = website.id
    account.status = "active"
    account.last_message = ""

    if payload.enable_ssl and payload.domain:
        from app.services import ssl as ssl_service
        try:
            result = ssl_service.issue_ssl(payload.domain)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "Could not issue SSL")
            website.ssl_enabled = True
            website.ssl_mode = "letsencrypt"
            website.ssl_updated_at = datetime.now(timezone.utc)
        except Exception:
            pass

    db.commit()
    log_action(db, None, "provisioning_create", payload.external_id, detail=payload.domain or payload.username, request=request)
    if payload.domain:
        # DNS Manager: every domain on the server gets its DNS.
        from app.services import dns

        dns.zone_for_new_domain(db, payload.domain, user.id)
    return account_to_dict(account, db)


@router.get("/accounts/{external_id}", response_model=ProvisioningAccountOut)
def get_account(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:read")
    account = _account_by_external_id(db, external_id)
    return account_to_dict(account, db)


@router.post("/accounts/{external_id}/login")
def create_login_url(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    account = _account_by_external_id(db, external_id)
    if not account.user or not account.user.is_active:
        raise HTTPException(status_code=404, detail="Account user not found or inactive")
    login_token = create_panel_login_token(account.user.username)
    log_action(db, None, "provisioning_login", external_id, detail=account.user.username, request=request)
    path = f"/api/auth/sso/{login_token}"
    base = panel_base_url(request)
    absolute = f"{base}{path}" if base else path
    # `login_url` is the documented field; `url` is kept for billing modules
    # built against the original response shape.
    return {"login_url": absolute, "url": absolute, "path": path, "expires_in": 300}


@router.post("/accounts/{external_id}/suspend")
def suspend(external_id: str, payload: ProvisioningSuspendRequest, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    account = _account_by_external_id(db, external_id)
    if account.status == "suspended":
        return {"ok": True, "status": "suspended"}
    if account.status != "active":
        raise HTTPException(status_code=400, detail=f"Cannot suspend account in status: {account.status}")
    suspend_account(db, account, payload.reason)
    log_action(db, None, "provisioning_suspend", external_id, detail=payload.reason, request=request)
    return {"ok": True, "status": "suspended"}


@router.post("/accounts/{external_id}/unsuspend")
def unsuspend(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    account = _account_by_external_id(db, external_id)
    if account.status == "active":
        return {"ok": True, "status": "active"}
    if account.status != "suspended":
        raise HTTPException(status_code=400, detail=f"Cannot unsuspend account in status: {account.status}")
    unsuspend_account(db, account)
    log_action(db, None, "provisioning_unsuspend", external_id, request=request)
    return {"ok": True, "status": "active"}


@router.delete("/accounts/{external_id}")
def terminate(external_id: str, request: Request, backup: bool = Query(default=False), db: Session = Depends(get_db)):
    """Delete a provisioned account.

    ``backup`` defaults to off: the backup runs inside this request, and a
    billing module that gives the call a short HTTP timeout would report a
    failure while the server kept working. Pass ``?backup=true`` when the
    caller can wait for a full account archive.
    """
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    account = _account_by_external_id(db, external_id)
    if account.status == "terminated":
        return {"ok": True, "status": "terminated"}
    deleted_domains = terminate_account(db, account, backup=backup)
    log_action(db, None, "provisioning_terminate", external_id, detail=",".join(deleted_domains), request=request)
    return {"ok": True, "status": "terminated", "deleted_websites": deleted_domains}


@router.patch("/accounts/{external_id}/password")
def change_password(external_id: str, payload: ProvisioningPasswordChange, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    account = _account_by_external_id(db, external_id)
    if not account.user:
        raise HTTPException(status_code=400, detail="Account has no user")
    account.user.hashed_password = hash_password(payload.password)
    account.user.token_version = (account.user.token_version or 0) + 1
    # The panel password stops here. It used to be written to the Linux account
    # as well, which put it on port 22 behind sshd's password authentication.
    minted = site_users.retire_shared_login_password(
        account.user.username,
        already_separate=account.user.sftp_password_set_at is not None,
    )
    if minted is not None:
        account.user.sftp_password_set_at = datetime.utcnow()
    account.last_action = "change_password"
    db.commit()
    log_action(db, None, "provisioning_change_password", external_id, request=request)
    body = {"ok": True}
    if minted is not None:
        # This account's SFTP login was the panel password. It has been replaced
        # so the old value stops working; this is the only time it is readable,
        # and the billing system is the only thing that can pass it on.
        body["sftp_password"] = minted
        body["sftp_password_rotated"] = True
    return body


@router.patch("/accounts/{external_id}/package")
def change_package(external_id: str, payload: ProvisioningPackageChange, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:write")
    account = _account_by_external_id(db, external_id)
    if not account.user:
        raise HTTPException(status_code=400, detail="Account has no user")
    package = db.query(UserPackage).filter(UserPackage.id == payload.package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    account.package_id = package.id
    account.user.package_id = package.id
    account.user.website_limit = package.website_limit
    account.user.storage_limit_mb = package.storage_limit_mb
    account.last_action = "change_package"
    db.commit()
    log_action(db, None, "provisioning_change_package", external_id, detail=str(package.id), request=request)
    return {"ok": True, "package_id": package.id}



@router.get("/accounts/usage", response_model=List[ProvisioningUsageOut])
def get_all_usage(request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:read")
    
    results = []
    accounts = db.query(ProvisioningAccount).all()
    for account in accounts:
        if not account.user:
            continue
        user = account.user
        summary = storage_quota.storage_usage_summary(db, user, use_cache=True)
        website_count = db.query(Website).filter(Website.owner_id == user.id).count()
        database_count = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).count()
        
        # NOTE: bandwidth tracking not yet implemented in backend models, returning 0
        results.append({
            "external_id": account.external_id,
            "storage_used_bytes": summary["storage_used_bytes"],
            "storage_limit_bytes": summary["storage_limit_bytes"],
            "storage_percent": summary["storage_percent"],
            "website_count": website_count,
            "database_count": database_count,
            "bandwidth_used_bytes": 0,
            "bandwidth_limit_bytes": 0,
        })
    return results


@router.get("/accounts/{external_id}/usage", response_model=ProvisioningUsageOut)
def get_usage(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _get_provisioning_token(request, db)
    _require_scope(token, "provisioning:read")
    account = _account_by_external_id(db, external_id)
    if not account.user:
        raise HTTPException(status_code=400, detail="Account has no user")
    user = account.user
    summary = storage_quota.storage_usage_summary(db, user)
    website_count = db.query(Website).filter(Website.owner_id == user.id).count()
    database_count = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).count()
    return {
        "external_id": account.external_id,
        "storage_used_bytes": summary["storage_used_bytes"],
        "storage_limit_bytes": summary["storage_limit_bytes"],
        "storage_percent": summary["storage_percent"],
        "website_count": website_count,
        "database_count": database_count,
        "bandwidth_used_bytes": 0,
        "bandwidth_limit_bytes": 0,
    }


# ── API Tokens (admin-only, cookie auth) ─────────────────────────────────

@router.get("/tokens", response_model=List[ApiTokenOut])
def list_tokens(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return db.query(ApiToken).order_by(ApiToken.id.desc()).all()


@router.post("/tokens", response_model=ApiTokenCreatedResponse)
def create_token(payload: ApiTokenCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    raw, token = create_api_token(db, payload.name, payload.scopes, payload.allowed_ips)
    log_action(db, current_user.id, "create_api_token", payload.name, request=request)
    return {"token": raw, "info": token}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    token.is_active = False
    token.revoked_at = datetime.now(timezone.utc)
    db.commit()
    log_action(db, current_user.id, "revoke_api_token", token.name, request=request)
    return {"ok": True}
