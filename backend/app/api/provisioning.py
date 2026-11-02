from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.users import _enforce_reseller_quotas, _reseller_can_grant_terminal
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.rate_limit import enforce_rate_limit
from app.core.security import hash_password
from app.models.entities import ApiToken, DatabaseAccount, ProvisioningAccount, ResellerPackage, User, UserPackage, Website
from app.schemas.schemas import (
    ApiTokenCreate,
    ApiTokenCreatedResponse,
    ApiTokenOut,
    ApiTokenRotate,
    ApiTokenUpdate,
    ProvisioningAccountCreate,
    ProvisioningAccountList,
    ProvisioningAccountOut,
    ProvisioningPackageChange,
    ProvisioningPasswordChange,
    ProvisioningSuspendRequest,
    ProvisioningUsageOut,
)
from app.services import mariadb, nginx, provisioning_idempotency as idempotency, site_users, wordpress
from app.services.audit import log_action
from app.services.provisioning import (
    account_to_dict,
    account_usage,
    apply_account_limits,
    authenticate_token,
    check_ip_allowed,
    create_api_token,
    discard_failed_account,
    has_scope,
    linux_user_exists,
    normalize_allowed_ips,
    normalize_scopes,
    panel_base_url,
    queue_account_backup,
    rate_limit_for,
    rotate_api_token,
    suspend_account,
    terminate_account,
    undo_failed_create,
    unsuspend_account,
)
from app.services.sso_tokens import create_panel_login_token

router = APIRouter(prefix="/provisioning/v1", tags=["provisioning"])

# Failed authentications allowed per source address per minute before the
# caller gets 429 instead of 401.
_AUTH_FAILURES_PER_MINUTE = 20


def _auth_failure(request: Request, status_code: int, detail: str) -> HTTPException:
    enforce_rate_limit(request, "provisioning-auth-fail", max_requests=_AUTH_FAILURES_PER_MINUTE, window_seconds=60)
    return HTTPException(status_code=status_code, detail=detail)


def _get_provisioning_token(request: Request, db: Session) -> ApiToken:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise _auth_failure(request, 401, "Missing Bearer token")
    raw = auth[7:].strip()
    token = authenticate_token(db, raw)
    if not token:
        raise _auth_failure(request, 401, "Invalid, expired or revoked token")
    client_ip = (request.client.host if request.client else "").strip()
    if not check_ip_allowed(token, client_ip):
        raise _auth_failure(request, 403, "IP not allowed")
    # Per token (and source address): a leaked or misbehaving billing
    # integration cannot queue account creations faster than this.
    enforce_rate_limit(
        request,
        "provisioning-api",
        max_requests=rate_limit_for(token),
        window_seconds=60,
        key_suffix=f"token{token.id}",
    )
    actor: Optional[User] = None
    if token.created_by_id is not None:
        actor = db.query(User).filter(User.id == token.created_by_id).first()
        if actor is None or not actor.is_active or actor.role not in (Role.admin, Role.reseller):
            raise _auth_failure(request, 403, "Token owner account is inactive or unauthorized")
    token._actor = actor  # type: ignore[attr-defined]
    return token


def _token_reseller(token: ApiToken) -> Optional[User]:
    actor = getattr(token, "_actor", None)
    if actor is not None and actor.role == Role.reseller:
        return actor
    return None


def _account_belongs_to_reseller(account: ProvisioningAccount, reseller: User) -> bool:
    if account.user is not None:
        return account.user.reseller_id == reseller.id
    return account.reseller_id == reseller.id


def _require_scope(token: ApiToken, scope: str) -> None:
    if not has_scope(token, scope):
        raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")


def _authorize(request: Request, db: Session, scope: str) -> ApiToken:
    token = _get_provisioning_token(request, db)
    _require_scope(token, scope)
    return token


def _account_by_external_id(
    db: Session,
    external_id: str,
    reseller: Optional[User] = None,
) -> ProvisioningAccount:
    account = db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == external_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if reseller is not None and not _account_belongs_to_reseller(account, reseller):
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _check_reseller_package(db: Session, is_reseller: bool, reseller_package_id) -> None:
    if reseller_package_id is None:
        return
    if not is_reseller:
        raise HTTPException(status_code=400, detail="reseller_package_id is only valid for reseller accounts")
    if not db.query(ResellerPackage).filter(ResellerPackage.id == reseller_package_id).first():
        raise HTTPException(status_code=404, detail="Reseller package not found")


def _apply_cgroup_limits(user_id: int) -> None:
    # The same follow-up the panel's own user form does: CPU, memory and IO
    # limits reach systemd only through this.
    try:
        from app.services import resource_monitor

        resource_monitor.apply_user_cgroup_limits_async(user_id)
    except Exception:  # noqa: BLE001 - limits are re-applied by the monitor
        pass


def _emit(db: Session, event: str, account: ProvisioningAccount, **extra) -> None:
    """Queue an account.* webhook event. Never carries a password."""
    from app.services import webhooks

    data = account_to_dict(account, db)
    data.pop("sftp_password", None)
    data.update(extra)
    webhooks.emit_event(db, event, data, subject=account.user)


def _provisioning_email(payload: ProvisioningAccountCreate) -> str:
    if payload.email:
        return str(payload.email).strip().lower()
    domain = (payload.domain or "").strip().lower()
    if domain:
        return f"admin@{domain}"
    from app.services import panel_settings

    panel_domain = (panel_settings.get_brand().panel_domain or "").strip().lower()
    if panel_domain and panel_settings.DOMAIN_RE.fullmatch(panel_domain):
        return f"admin@{panel_domain}"
    import re

    safe_host = re.sub(r"[^a-z0-9-]", "-", payload.username.lower()).strip("-") or "user"
    return f"admin@{safe_host}.localdomain"

def _provisioning_app_type(payload: ProvisioningAccountCreate) -> str:
    if not payload.domain:
        return "php"
    if payload.install_wordpress:
        return "wordpress"
    return payload.app_type


# ── Plans ────────────────────────────────────────────────────────────────

@router.get("/plans")
def list_plans(request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "plans:read")
    reseller = _token_reseller(token)
    query = db.query(UserPackage)
    if reseller is not None:
        query = query.filter(
            or_(UserPackage.owner_id == None, UserPackage.owner_id == reseller.id),  # noqa: E711
            UserPackage.is_reseller_package == False,  # noqa: E712
        )
    packages = query.order_by(UserPackage.id.asc()).all()
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

def _record_failed_create(
    db: Session,
    payload: ProvisioningAccountCreate,
    message: str,
    reseller_id: Optional[int] = None,
) -> None:
    """Keep a visible "failed" record after everything else was rolled back."""
    db.add(ProvisioningAccount(
        external_id=payload.external_id,
        package_id=payload.package_id,
        reseller_id=reseller_id,
        status="failed",
        last_action="create",
        last_message=message[:2000],
    ))
    db.commit()


def _create_account(
    payload: ProvisioningAccountCreate,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    existing = db.query(ProvisioningAccount).filter(ProvisioningAccount.external_id == payload.external_id).first()
    if existing:
        if reseller is not None and not _account_belongs_to_reseller(existing, reseller):
            raise HTTPException(status_code=409, detail="External account ID already exists")
        if existing.status in ("active", "pending") and existing.user:
            return account_to_dict(existing, db)
        if existing.status == "suspended" and existing.user:
            raise HTTPException(status_code=409, detail="Account already exists and is suspended")
        # failed or terminated: start over. A failed record from before
        # creates were rolled back may still own a panel and Linux user.
        discard_failed_account(db, existing)

    account_email = _provisioning_email(payload)

    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    if payload.domain and (
        db.query(Website).filter(Website.domain == payload.domain).first()
        or nginx.vhost_exists(payload.domain)
    ):
        raise HTTPException(status_code=409, detail="Domain already exists")
    from app.services import multi_server

    cluster_check = multi_server.check_cluster_conflict(domain=payload.domain or "", username=payload.username)
    if cluster_check.get("conflict"):
        raise HTTPException(status_code=multi_server.conflict_http_status(cluster_check),
                            detail=cluster_check.get("message") or "Already exists in the cluster")

    package = db.query(UserPackage).filter(UserPackage.id == payload.package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    if reseller is not None:
        if bool(getattr(payload, "is_reseller", False)) or payload.reseller_package_id is not None:
            raise HTTPException(status_code=403, detail="Reseller API tokens can only create end-user hosting accounts")
        if bool(getattr(package, "is_reseller_package", False)) or package.owner_id not in (None, reseller.id):
            raise HTTPException(status_code=403, detail="Cannot assign a package you do not own")
        eff_website_limit = (
            payload.limits.website_limit
            if (payload.limits and payload.limits.website_limit is not None)
            else package.website_limit
        )
        eff_storage_mb = (
            payload.limits.storage_limit_mb
            if (payload.limits and payload.limits.storage_limit_mb is not None)
            else package.storage_limit_mb
        )
        eff_database_limit = getattr(package, "database_limit", None)
        _enforce_reseller_quotas(
            db,
            reseller,
            target_website_limit=eff_website_limit,
            target_storage_mb=eff_storage_mb,
            target_database_limit=eff_database_limit,
            is_create=True,
        )
    else:
        _check_reseller_package(db, payload.is_reseller, payload.reseller_package_id)

    account = ProvisioningAccount(
        external_id=payload.external_id,
        package_id=payload.package_id,
        reseller_id=reseller.id if reseller is not None else None,
        status="pending",
        last_action="create",
    )
    db.add(account)
    db.flush()

    # Only a Linux account this request creates may be deleted when it fails:
    # panel-user-ensure adopts one that already exists.
    created_linux_user = not linux_user_exists(payload.username)
    db_info = None
    website = None

    def _fail(exc: Exception) -> HTTPException:
        # Nothing of a failed create survives: the panel user, website and
        # database rows go with the rollback, the system side is undone, and
        # only a "failed" record remains so the billing system sees why.
        db.rollback()
        undo_failed_create(
            username=payload.username,
            domain=payload.domain,
            db_info=db_info,
            delete_linux_user=created_linux_user,
        )
        _record_failed_create(
            db,
            payload,
            str(exc),
            reseller_id=reseller.id if reseller is not None else None,
        )
        if isinstance(exc, HTTPException):
            return exc
        return HTTPException(status_code=400, detail=str(exc))

    try:
        # Unified password on creation: Panel login and SFTP share payload.password.
        sftp_password = payload.password
        linux_user = site_users.ensure_panel_user(payload.username, sftp_password)

        user = User(
            username=payload.username,
            email=account_email,
            hashed_password=hash_password(payload.password),
            role=Role.reseller if getattr(payload, "is_reseller", False) else Role.end_user,
            package_id=package.id,
            website_limit=package.website_limit,
            storage_limit_mb=package.storage_limit_mb,
            sftp_password_set_at=None,
            reseller_package_id=payload.reseller_package_id if payload.is_reseller else None,
            reseller_id=reseller.id if reseller is not None else None,
        )
        apply_account_limits(user, package, payload.limits)
        if reseller is not None and user.terminal_enabled and not _reseller_can_grant_terminal(reseller):
            user.terminal_enabled = False
        db.add(user)
        db.flush()
        account.user_id = user.id

        app_type_value = _provisioning_app_type(payload)
        install_wp = payload.install_wordpress and app_type_value == "wordpress"

        if payload.domain:
            from app.core.config import settings as app_settings

            effective_php = payload.php_version
            if not app_settings.command_dry_run:
                from app.services import php as php_service

                installed_phps = php_service.list_installed_php()
                if installed_phps and effective_php not in installed_phps:
                    effective_php = installed_phps[-1]

            root_path = site_users.site_root_for_panel_user(payload.username, payload.domain)
            if install_wp:
                from app.api.databases import enforce_user_database_limit
                enforce_user_database_limit(db, user)
                db_info = mariadb.create_database(payload.domain)
                linux_user = site_users.ensure_site_runtime(payload.domain, root_path, effective_php, linux_user)
                root_path = wordpress.install_wordpress(
                    payload.domain, db_info, payload.domain,
                    "admin", payload.password, account_email,
                    effective_php, linux_user, root_path=root_path,
                )
            else:
                runtime_php = effective_php if app_type_value in {"wordpress", "php"} else None
                linux_user = site_users.ensure_site_runtime(payload.domain, root_path, runtime_php, linux_user)

            from app.api.websites import _ensure_default_waf_file, _write_placeholder_page
            _ensure_default_waf_file(payload.domain)
            if not install_wp:
                if not app_settings.command_dry_run:
                    _write_placeholder_page(payload.domain, root_path, linux_user, effective_php)
                    site_users.fix_site_path(str(site_users.document_root(root_path)), linux_user)

            rewrite_mode = "front_controller" if app_type_value == "wordpress" else "none"
            php_socket = site_users.site_php_fpm_socket(linux_user, root_path, effective_php) if app_type_value in {"wordpress", "php"} else None
            nginx.write_vhost(
                payload.domain, root_path,
                app_type=app_type_value,
                php_version=effective_php,
                php_fpm_socket_override=php_socket,
                document_root="public_html",
                rewrite_mode=rewrite_mode,
            )

            website = Website(
                domain=payload.domain,
                owner_id=user.id,
                root_path=root_path,
                document_root="public_html",
                linux_user=linux_user,
                php_version=effective_php,
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
        db.commit()
    except HTTPException as exc:
        raise _fail(exc)
    except (RuntimeError, ValueError, OSError) as exc:
        raise _fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - roll back whatever went wrong, then let it surface
        _fail(exc)
        raise

    if payload.enable_ssl and payload.domain and website is not None:
        from app.services import ssl as ssl_service
        try:
            result = ssl_service.issue_ssl(payload.domain)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "Could not issue SSL")
            website.ssl_enabled = True
            website.ssl_mode = "letsencrypt"
            website.ssl_updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()

    log_action(db, None, "provisioning_create", payload.external_id, detail=payload.domain or payload.username, request=request)
    _apply_cgroup_limits(user.id)
    if payload.domain:
        # DNS Manager: every domain on the server gets its DNS.
        from app.services import dns

        try:
            dns.zone_for_new_domain(db, payload.domain, user.id)
        except Exception:  # noqa: BLE001 - the account exists; DNS sync retries on start
            db.rollback()
    _emit(db, "account.created", account)
    body = account_to_dict(account, db)
    # Shown once: the billing system is the only thing that can hand the
    # customer their SFTP password.
    body["sftp_password"] = sftp_password
    return body


@router.post("/accounts", response_model=ProvisioningAccountOut)
def create_account(payload: ProvisioningAccountCreate, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, payload, lambda: _create_account(payload, request, db, reseller=reseller))


# Declared before /accounts/{external_id}: Starlette matches routes in order,
# and "usage" is a perfectly good external_id to the route below, so the bulk
# usage call WHMCS's UsageUpdate makes used to be answered "Account not found".
@router.get("/accounts/usage", response_model=List[ProvisioningUsageOut])
def get_all_usage(request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "usage:read")
    reseller = _token_reseller(token)
    query = db.query(ProvisioningAccount).filter(ProvisioningAccount.user_id.isnot(None))
    if reseller is not None:
        query = query.join(User, ProvisioningAccount.user_id == User.id).filter(User.reseller_id == reseller.id)
    accounts = query.all()
    return [account_usage(db, account, use_cache=True) for account in accounts if account.user]


@router.get("/accounts", response_model=ProvisioningAccountList)
def list_accounts(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=500),
    status: str = Query(default="", max_length=32, description="active, suspended, pending, failed, terminated, or all"),
    q: str = Query(default="", max_length=255, description="Substring of external_id or username"),
    db: Session = Depends(get_db),
):
    """Accounts this panel provisioned, for WHMCS ListAccounts / GetUserCount.

    Without ``status`` the list holds the accounts that exist on the server
    (active, suspended, pending); ``status=all`` adds failed and terminated
    records.
    """
    token = _authorize(request, db, "accounts:read")
    reseller = _token_reseller(token)
    query = db.query(ProvisioningAccount)
    wanted = status.strip().lower()
    if wanted and wanted != "all":
        query = query.filter(ProvisioningAccount.status == wanted)
    elif not wanted:
        query = query.filter(ProvisioningAccount.status.in_(("active", "suspended", "pending")))
    term = q.strip()
    if reseller is not None or term:
        query = query.outerjoin(User, ProvisioningAccount.user_id == User.id)
    if reseller is not None:
        query = query.filter(
            or_(
                User.reseller_id == reseller.id,
                and_(ProvisioningAccount.user_id == None, ProvisioningAccount.reseller_id == reseller.id),  # noqa: E711
            )
        )
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(ProvisioningAccount.external_id.ilike(like), User.username.ilike(like))
        )
    total = query.count()
    rows = (
        query.order_by(ProvisioningAccount.id.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "items": [account_to_dict(account, db) for account in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/accounts/{external_id}", response_model=ProvisioningAccountOut)
def get_account(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "accounts:read")
    account = _account_by_external_id(db, external_id, reseller=_token_reseller(token))
    return account_to_dict(account, db)


@router.post("/accounts/{external_id}/login")
def create_login_url(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "sso")
    account = _account_by_external_id(db, external_id, reseller=_token_reseller(token))
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


def _suspend(
    external_id: str,
    payload: ProvisioningSuspendRequest,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
    if account.status == "suspended":
        return {"ok": True, "status": "suspended"}
    if account.status != "active":
        raise HTTPException(status_code=400, detail=f"Cannot suspend account in status: {account.status}")
    suspend_account(db, account, payload.reason)
    log_action(db, None, "provisioning_suspend", external_id, detail=payload.reason, request=request)
    _emit(db, "account.suspended", account, reason=payload.reason)
    return {"ok": True, "status": "suspended"}


@router.post("/accounts/{external_id}/suspend")
def suspend(external_id: str, payload: ProvisioningSuspendRequest, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, payload, lambda: _suspend(external_id, payload, request, db, reseller=reseller))


def _unsuspend(
    external_id: str,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
    if account.status == "active":
        return {"ok": True, "status": "active"}
    if account.status != "suspended":
        raise HTTPException(status_code=400, detail=f"Cannot unsuspend account in status: {account.status}")
    unsuspend_account(db, account)
    log_action(db, None, "provisioning_unsuspend", external_id, request=request)
    _emit(db, "account.unsuspended", account)
    return {"ok": True, "status": "active"}


@router.post("/accounts/{external_id}/unsuspend")
def unsuspend(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, None, lambda: _unsuspend(external_id, request, db, reseller=reseller))


def _terminate(
    external_id: str,
    backup: bool,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
    if account.status == "terminated":
        return {"ok": True, "status": "terminated"}
    # Captured first: termination deletes the user the event describes.
    before = account_to_dict(account, db)
    subject = account.user
    subject_info = None
    if subject is not None:
        from types import SimpleNamespace

        subject_info = SimpleNamespace(id=subject.id, username=subject.username, reseller_id=subject.reseller_id)
        if account.reseller_id is None and subject.reseller_id is not None:
            account.reseller_id = subject.reseller_id
    deleted_domains = terminate_account(db, account, backup=backup)
    log_action(db, None, "provisioning_terminate", external_id, detail=",".join(deleted_domains), request=request)
    body = {"ok": True, "status": "terminated", "deleted_websites": deleted_domains}
    if backup:
        # "backup=<path>" or "backup failed: <reason>", set by terminate_account.
        body["backup"] = account.last_message or ""
    from app.services import webhooks

    before.update(status="terminated", deleted_websites=deleted_domains, backup=body.get("backup", ""))
    webhooks.emit_event(db, "account.terminated", before, subject=subject_info)
    return body


@router.delete("/accounts/{external_id}")
def terminate(external_id: str, request: Request, backup: bool = Query(default=False), db: Session = Depends(get_db)):
    """Delete a provisioned account.

    ``backup`` defaults to off: the backup runs inside this request, and a
    billing module that gives the call a short HTTP timeout would report a
    failure while the server kept working. Pass ``?backup=true`` when the
    caller can wait for a full account archive.
    """
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, None, lambda: _terminate(external_id, backup, request, db, reseller=reseller))


def _change_password(
    external_id: str,
    payload: ProvisioningPasswordChange,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
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
    _emit(db, "account.password_changed", account, sftp_password_rotated=minted is not None)
    body = {"ok": True}
    if minted is not None:
        # This account's SFTP login was the panel password. It has been replaced
        # so the old value stops working; this is the only time it is readable,
        # and the billing system is the only thing that can pass it on.
        body["sftp_password"] = minted
        body["sftp_password_rotated"] = True
    return body


@router.patch("/accounts/{external_id}/password")
def change_password(external_id: str, payload: ProvisioningPasswordChange, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, payload, lambda: _change_password(external_id, payload, request, db, reseller=reseller))


def _change_package(
    external_id: str,
    payload: ProvisioningPackageChange,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
    if not account.user:
        raise HTTPException(status_code=400, detail="Account has no user")
    package = db.query(UserPackage).filter(UserPackage.id == payload.package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    if reseller is not None:
        if payload.reseller_package_id is not None:
            raise HTTPException(status_code=403, detail="Reseller API tokens cannot modify reseller packages")
        if bool(getattr(package, "is_reseller_package", False)) or package.owner_id not in (None, reseller.id):
            raise HTTPException(status_code=403, detail="Cannot assign a package you do not own")
        eff_website_limit = (
            payload.limits.website_limit
            if (payload.limits and payload.limits.website_limit is not None)
            else package.website_limit
        )
        eff_storage_mb = (
            payload.limits.storage_limit_mb
            if (payload.limits and payload.limits.storage_limit_mb is not None)
            else package.storage_limit_mb
        )
        eff_database_limit = getattr(package, "database_limit", None)
        _enforce_reseller_quotas(
            db,
            reseller,
            target_website_limit=eff_website_limit,
            target_storage_mb=eff_storage_mb,
            target_database_limit=eff_database_limit,
            is_create=False,
            exclude_user_id=account.user.id,
        )
    else:
        _check_reseller_package(db, account.user.role == Role.reseller, payload.reseller_package_id)
    account.package_id = package.id
    apply_account_limits(account.user, package, payload.limits)
    if reseller is not None and account.user.terminal_enabled and not _reseller_can_grant_terminal(reseller):
        account.user.terminal_enabled = False
    if reseller is None and payload.reseller_package_id is not None:
        account.user.reseller_package_id = payload.reseller_package_id
    account.last_action = "change_package"
    db.commit()
    log_action(db, None, "provisioning_change_package", external_id, detail=str(package.id), request=request)
    _apply_cgroup_limits(account.user.id)
    _emit(db, "account.package_changed", account, limits=payload.limits.model_dump(exclude_none=True) if payload.limits else {})
    return {"ok": True, "package_id": package.id}


@router.patch("/accounts/{external_id}/package")
def change_package(external_id: str, payload: ProvisioningPackageChange, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, payload, lambda: _change_package(external_id, payload, request, db, reseller=reseller))


@router.get("/accounts/{external_id}/usage", response_model=ProvisioningUsageOut)
def get_usage(external_id: str, request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "usage:read")
    account = _account_by_external_id(db, external_id, reseller=_token_reseller(token))
    if not account.user:
        raise HTTPException(status_code=400, detail="Account has no user")
    return account_usage(db, account)


def _reset_sftp_password(
    external_id: str,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
    if not account.user:
        raise HTTPException(status_code=400, detail="Account has no user")
    password = site_users.rotate_panel_user_password(account.user.username)
    account.user.sftp_password_set_at = datetime.utcnow()
    account.last_action = "reset_sftp_password"
    db.commit()
    log_action(db, None, "provisioning_reset_sftp_password", external_id, request=request)
    _emit(db, "account.sftp_password_reset", account)
    # Shown once; the panel keeps no readable copy.
    return {"ok": True, "sftp_password": password, "sftp_username": account.user.username}


@router.post("/accounts/{external_id}/sftp-password")
def reset_sftp_password(external_id: str, request: Request, db: Session = Depends(get_db)):
    """Give the account's SFTP/SSH login a new random password and return it."""
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, None, lambda: _reset_sftp_password(external_id, request, db, reseller=reseller))


def _backup(
    external_id: str,
    request: Request,
    db: Session,
    reseller: Optional[User] = None,
) -> dict:
    account = _account_by_external_id(db, external_id, reseller=reseller)
    if not account.user or account.status not in ("active", "suspended"):
        raise HTTPException(status_code=400, detail=f"Cannot back up account in status: {account.status}")
    if not queue_account_backup(account.id):
        raise HTTPException(status_code=409, detail="A backup of this account is already running")
    log_action(db, None, "provisioning_backup_queued", external_id, request=request)
    return {"ok": True, "queued": True}


@router.post("/accounts/{external_id}/backup")
def create_backup(external_id: str, request: Request, db: Session = Depends(get_db)):
    """Queue a full backup of the account. It runs in the background; the
    outcome lands on the account (``last_action=backup``) and in the audit log."""
    token = _authorize(request, db, "accounts:write")
    reseller = _token_reseller(token)
    return idempotency.run(db, token, request, None, lambda: _backup(external_id, request, db, reseller=reseller))


# Changing a username is deliberately not offered. The username is the Linux
# account: it names the home directory every website root lives under
# (/home/<user>/<domain>), the PHP-FPM pools and their sockets referenced from
# each vhost, the crontab, the cgroup slice, the backup directory and archive
# names, and SFTP sub-accounts pinned to those paths. Renaming means moving
# all of that live, with no atomic step to fall back on if one part fails.
# Billing systems address accounts by external_id, so they never need it.


# ── Reseller packages ────────────────────────────────────────────────────

@router.get("/reseller-plans")
def list_reseller_plans(request: Request, db: Session = Depends(get_db)):
    token = _authorize(request, db, "plans:read")
    if _token_reseller(token) is not None:
        return []
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "user_limit": p.user_limit,
            "total_storage_mb": p.total_storage_mb,
            "total_website_limit": p.total_website_limit,
            "total_database_limit": p.total_database_limit,
            "overselling_enabled": p.overselling_enabled,
        }
        for p in db.query(ResellerPackage).order_by(ResellerPackage.id.asc()).all()
    ]


# ── Server-level panel single sign-on (Admin or Reseller) ────────────────

@router.post("/admin/login")
def create_admin_login_url(request: Request, db: Session = Depends(get_db)):
    """A one-time login link for the Admin or Reseller who owns this token.

    - Admin tokens require explicit ``sso:admin`` scope and a non-empty IP
      allowlist, signing in as that Administrator.
    - Reseller tokens require explicit ``sso:reseller`` (or ``sso:admin``) scope
      and a non-empty IP allowlist, signing in strictly as that Reseller.
    """
    token = _get_provisioning_token(request, db)
    issuer: Optional[User] = getattr(token, "_actor", None)
    if issuer is not None and issuer.role == Role.reseller:
        if not (has_scope(token, "sso:reseller") or has_scope(token, "sso:admin")):
            raise HTTPException(status_code=403, detail="Missing scope: sso:reseller")
        if not (token.allowed_ips or "").strip():
            raise HTTPException(status_code=403, detail="sso:reseller requires the token to have an IP allowlist")
        if not issuer.is_active:
            raise HTTPException(status_code=403, detail="Token has no active reseller to sign in as")
        login_token = create_panel_login_token(issuer.username)
        log_action(db, None, "provisioning_reseller_login", issuer.username, detail=f"token={token.id}", request=request)
        path = f"/api/auth/sso/{login_token}"
        base = panel_base_url(request)
        absolute = f"{base}{path}" if base else path
        return {
            "login_url": absolute,
            "url": absolute,
            "path": path,
            "expires_in": 300,
            "role": "reseller",
            "username": issuer.username,
        }

    _require_scope(token, "sso:admin")
    if not (token.allowed_ips or "").strip():
        # The link skips the administrator's own 2FA, so it is only handed out
        # to a token pinned to the billing server's addresses.
        raise HTTPException(status_code=403, detail="sso:admin requires the token to have an IP allowlist")
    if issuer is None or issuer.role != Role.admin or not issuer.is_active:
        raise HTTPException(status_code=403, detail="Token has no active administrator to sign in as")
    login_token = create_panel_login_token(issuer.username)
    log_action(db, None, "provisioning_admin_login", issuer.username, detail=f"token={token.id}", request=request)
    path = f"/api/auth/sso/{login_token}"
    base = panel_base_url(request)
    absolute = f"{base}{path}" if base else path
    return {
        "login_url": absolute,
        "url": absolute,
        "path": path,
        "expires_in": 300,
        "role": "admin",
        "username": issuer.username,
    }


# ── Token self-service (Bearer auth) ─────────────────────────────────────

def _serialize_token(token: ApiToken, owners_by_id: dict[int, User]) -> dict:
    out = ApiTokenOut.model_validate(token).model_dump()
    owner = owners_by_id.get(token.created_by_id) if token.created_by_id else None
    if owner is not None:
        out["owner_username"] = owner.username
        out["owner_role"] = owner.role
    return out


@router.post("/token/rotate", response_model=ApiTokenCreatedResponse)
def rotate_own_token(payload: ApiTokenRotate, request: Request, db: Session = Depends(get_db)):
    """Replace the calling token. Needs the ``tokens:rotate`` scope."""
    token = _authorize(request, db, "tokens:rotate")

    def _rotate() -> dict:
        raw, replacement = rotate_api_token(db, token, payload.grace_minutes)
        owners = {}
        if replacement.created_by_id:
            u = db.query(User).filter(User.id == replacement.created_by_id).first()
            if u:
                owners[u.id] = u
        log_action(db, None, "rotate_api_token", replacement.name, detail=f"self old={token.id} new={replacement.id}", request=request)
        return {"token": raw, "info": _serialize_token(replacement, owners)}

    return idempotency.run(db, token, request, payload, _rotate)


# ── API Tokens (Admin-only panel management, cookie auth) ─────────────────

@router.get("/tokens", response_model=List[ApiTokenOut])
def list_tokens(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    rows = db.query(ApiToken).order_by(ApiToken.id.desc()).all()
    owner_ids = {r.created_by_id for r in rows if r.created_by_id}
    owners = (
        {u.id: u for u in db.query(User).filter(User.id.in_(owner_ids)).all()}
        if owner_ids
        else {}
    )
    return [_serialize_token(r, owners) for r in rows]


@router.get("/scopes")
def list_scopes(current_user: User = Depends(get_current_user)):
    """The scope catalogue, for the token form."""
    ensure_role(current_user.role, Role.admin)
    from app.services.provisioning import LEGACY_SCOPES, SCOPES

    return {
        "scopes": [{"name": name, "description": desc} for name, desc in SCOPES.items()],
        "legacy": {name: sorted(expanded) for name, expanded in LEGACY_SCOPES.items()},
    }


def _align_scopes_for_owner_role(scopes: str, owner_role: Optional[str]) -> str:
    items = [s for s in scopes.split(",") if s]
    mapped: list[str] = []
    for s in items:
        if owner_role == Role.reseller and s == "sso:admin":
            s_item = "sso:reseller"
        elif owner_role == Role.admin and s == "sso:reseller":
            s_item = "sso:admin"
        else:
            s_item = s
        if s_item not in mapped:
            mapped.append(s_item)
    return ",".join(sorted(mapped))


@router.post("/tokens", response_model=ApiTokenCreatedResponse)
def create_token(payload: ApiTokenCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    owner_user = current_user
    if payload.owner_user_id is not None:
        target = db.query(User).filter(User.id == payload.owner_user_id).first()
        if not target or not target.is_active or target.role not in (Role.admin, Role.reseller):
            raise HTTPException(status_code=400, detail="Token owner must be an active Admin or Reseller account")
        owner_user = target

    try:
        scopes = normalize_scopes(payload.scopes)
        allowed_ips = normalize_allowed_ips(payload.allowed_ips)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    scopes = _align_scopes_for_owner_role(scopes, owner_user.role)
    scope_set = set(scopes.split(","))
    if ("sso:admin" in scope_set or "sso:reseller" in scope_set) and not allowed_ips.strip():
        raise HTTPException(
            status_code=400,
            detail="sso:admin and sso:reseller require a non-empty IP allowlist (WHMCS server IP)",
        )

    expires_at = None
    if payload.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=payload.expires_in_days)
    raw, token = create_api_token(
        db,
        payload.name,
        scopes,
        allowed_ips,
        expires_at=expires_at,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        created_by_id=owner_user.id,
    )
    log_action(
        db,
        current_user.id,
        "create_api_token",
        payload.name,
        detail=f"owner={owner_user.username}({owner_user.role}) scopes={scopes}",
        request=request,
    )
    return {"token": raw, "info": _serialize_token(token, {owner_user.id: owner_user})}


@router.patch("/tokens/{token_id}", response_model=ApiTokenOut)
def update_token(token_id: int, payload: ApiTokenUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Update an active token's name, scopes, allowed IPs or rate limit in place without changing its secret."""
    ensure_role(current_user.role, Role.admin)
    token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    if not token.is_active or token.revoked_at is not None:
        raise HTTPException(status_code=400, detail="A revoked token cannot be updated")

    owner = db.query(User).filter(User.id == token.created_by_id).first() if token.created_by_id else None
    owner_role = owner.role if owner is not None else None

    next_scopes = token.scopes
    next_ips = token.allowed_ips or ""
    try:
        if payload.scopes is not None:
            next_scopes = normalize_scopes(payload.scopes)
        if payload.allowed_ips is not None:
            next_ips = normalize_allowed_ips(payload.allowed_ips)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    next_scopes = _align_scopes_for_owner_role(next_scopes, owner_role)
    scope_set = set(next_scopes.split(","))
    if ("sso:admin" in scope_set or "sso:reseller" in scope_set) and not next_ips.strip():
        raise HTTPException(
            status_code=400,
            detail="sso:admin and sso:reseller require a non-empty IP allowlist (WHMCS server IP)",
        )

    if payload.name is not None:
        token.name = payload.name.strip()
    token.scopes = next_scopes
    token.allowed_ips = next_ips
    if payload.rate_limit_per_minute is not None:
        token.rate_limit_per_minute = payload.rate_limit_per_minute
    db.commit()
    db.refresh(token)

    owners = {owner.id: owner} if owner is not None else {}
    log_action(
        db,
        current_user.id,
        "update_api_token",
        token.name,
        detail=f"id={token.id} scopes={token.scopes} allowed_ips={token.allowed_ips or 'any'}",
        request=request,
    )
    return _serialize_token(token, owners)


@router.post("/tokens/{token_id}/rotate", response_model=ApiTokenCreatedResponse)
def rotate_token(token_id: int, payload: ApiTokenRotate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    if not token.is_active or token.revoked_at is not None:
        raise HTTPException(status_code=400, detail="A revoked token cannot be rotated")
    raw, replacement = rotate_api_token(db, token, payload.grace_minutes)
    owners = {}
    if replacement.created_by_id:
        u = db.query(User).filter(User.id == replacement.created_by_id).first()
        if u:
            owners[u.id] = u
    log_action(db, current_user.id, "rotate_api_token", token.name, detail=f"old={token.id} new={replacement.id} grace={payload.grace_minutes}m", request=request)
    return {"token": raw, "info": _serialize_token(replacement, owners)}


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
