import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload
from typing import List, Optional

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, normalize_role
from app.core.rate_limit import enforce_rate_limit
from app.core.security import hash_password
from app.core.step_up import require_sensitive_action_step_up
from app.models.entities import AuditLog, BackupSchedule, DatabaseAccount, McpToken, User, UserPackage, Website, MessageTemplate
from app.schemas import schemas
from app.schemas.schemas import (
    AuditLogOut,
    UserCreate,
    UserOut,
    SftpPasswordUpdate,
    UserPasswordUpdate,
    UserUpdate,
)
from app.services.audit import log_action
from app.services import db_backends, demo_mode, mail, nginx, site_users, ssl, storage_quota, teardown, waf, wordpress, notifications, panel_settings

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User, db: Session, *, cached_usage: bool = False) -> dict:
    data = UserOut.model_validate(user).model_dump()
    data["package_name"] = user.package.name if user.package else None
    data["subdomain_limit"] = int(getattr(user.package, "subdomain_limit", 0) or 0) if user.package else 0
    owner = None
    if user.reseller_id:
        owner = db.query(User).filter(User.id == user.reseller_id).first()
    elif user.role in (Role.end_user, "user"):
        owner = db.query(User).filter(User.role == Role.admin).order_by(User.id.asc()).first()
    if owner:
        data["reseller_id"] = owner.id
        data["owner_username"] = owner.username
        data["owner_role"] = owner.role
    data.update(storage_quota.storage_usage_summary(db, user, use_cache=cached_usage))
    return data


def _package_for_payload(
    db: Session, package_id: int | None, current_user: User | None = None
) -> UserPackage | None:
    if package_id is None:
        return None
    package = db.query(UserPackage).filter(UserPackage.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    if current_user is not None and current_user.role == Role.reseller.value:
        if package.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot assign a package you do not own")
    return package


def _reseller_can_grant_terminal(reseller: User) -> bool:
    if bool(getattr(reseller, "terminal_enabled", False)):
        return True
    if reseller.reseller_package is not None and bool(
        getattr(reseller.reseller_package, "ssh_access_users", False)
    ):
        return True
    if reseller.package is not None and bool(
        getattr(reseller.package, "ssh_access_users", False)
    ):
        return True
    return False


def _enforce_reseller_quotas(
    db: Session,
    reseller: User,
    *,
    target_website_limit: int,
    target_storage_mb: int,
    target_database_limit: int | None = None,
    is_create: bool = False,
    exclude_user_id: int | None = None,
) -> None:
    rp = reseller.reseller_package
    up = reseller.package
    if rp is not None:
        user_limit = int(rp.user_limit or 0)
        total_storage_mb = int(rp.total_storage_mb or 0)
        total_website_limit = int(rp.total_website_limit or 0)
        total_database_limit = int(rp.total_database_limit or 0)
        overselling = bool(rp.overselling_enabled)
    elif up is not None and up.is_reseller_package:
        user_limit = int(up.reseller_user_limit or 0)
        total_storage_mb = int(up.storage_limit_mb or 0)
        total_website_limit = int(up.website_limit or 0)
        total_database_limit = int(up.database_limit or 0)
        overselling = bool(up.reseller_overselling_enabled)
    else:
        return

    all_sub_users = (
        db.query(User)
        .options(selectinload(User.package))
        .filter(User.reseller_id == reseller.id)
        .all()
    )
    if is_create and user_limit > 0 and len(all_sub_users) >= user_limit:
        raise HTTPException(status_code=403, detail="Reseller user limit reached")

    other_users = [u for u in all_sub_users if u.id != exclude_user_id]

    if not overselling:
        if total_storage_mb > 0:
            allocated_storage = sum(int(u.storage_limit_mb or 0) for u in other_users)
            if target_storage_mb <= 0 or allocated_storage + target_storage_mb > total_storage_mb:
                raise HTTPException(status_code=403, detail="Reseller storage quota exceeded")
        if total_website_limit > 0:
            allocated_websites = sum(int(u.website_limit or 0) for u in other_users)
            if target_website_limit <= 0 or allocated_websites + target_website_limit > total_website_limit:
                raise HTTPException(status_code=403, detail="Reseller website quota exceeded")
        if total_database_limit > 0:
            allocated_databases = sum(
                int(u.package.database_limit or 0) if u.package else 0 for u in other_users
            )
            if target_database_limit is not None:
                if target_database_limit <= 0 or allocated_databases + target_database_limit > total_database_limit:
                    raise HTTPException(status_code=403, detail="Reseller database quota exceeded")
            actual_databases = (
                db.query(DatabaseAccount)
                .join(User, DatabaseAccount.owner_id == User.id)
                .filter(User.reseller_id == reseller.id)
                .count()
            )
            if actual_databases > total_database_limit:
                raise HTTPException(status_code=403, detail="Reseller database quota exceeded")
    else:
        if total_storage_mb > 0:
            if target_storage_mb <= 0 or target_storage_mb > total_storage_mb:
                raise HTTPException(status_code=403, detail="Reseller storage quota exceeded")
            used_bytes = sum(
                storage_quota.user_storage_used_bytes(db, u, use_cache=True) for u in all_sub_users
            )
            if used_bytes >= total_storage_mb * storage_quota.BYTES_PER_MB:
                raise HTTPException(status_code=403, detail="Reseller storage quota exceeded")
        if total_website_limit > 0:
            if target_website_limit <= 0 or target_website_limit > total_website_limit:
                raise HTTPException(status_code=403, detail="Reseller website quota exceeded")
            actual_websites = (
                db.query(Website)
                .join(User, Website.owner_id == User.id)
                .filter(User.reseller_id == reseller.id)
                .count()
            )
            if actual_websites > total_website_limit:
                raise HTTPException(status_code=403, detail="Reseller website quota exceeded")
        if total_database_limit > 0:
            if target_database_limit is not None and (
                target_database_limit <= 0 or target_database_limit > total_database_limit
            ):
                raise HTTPException(status_code=403, detail="Reseller database quota exceeded")
            actual_databases = (
                db.query(DatabaseAccount)
                .join(User, DatabaseAccount.owner_id == User.id)
                .filter(User.reseller_id == reseller.id)
                .count()
            )
            if actual_databases > total_database_limit:
                raise HTTPException(status_code=403, detail="Reseller database quota exceeded")


def _apply_package_limits(user: User, package: UserPackage | None) -> None:
    user.package_id = package.id if package else None
    if package:
        user.website_limit = package.website_limit
        user.storage_limit_mb = package.storage_limit_mb
        user.database_limit = getattr(package, "database_limit", 5)
        user.bandwidth_limit_mb = getattr(package, "bandwidth_limit_mb", 0)
        user.inode_limit = getattr(package, "inode_limit", 0)
        user.terminal_enabled = package.terminal_enabled
        user.sftp_accounts_limit = package.sftp_accounts_limit
        user.mail_accounts_limit = package.mail_accounts_limit
        user.cpu_quota_percent = getattr(package, "cpu_quota_percent", 100)
        user.io_read_mbps = getattr(package, "io_read_mbps", 0)
        user.io_write_mbps = getattr(package, "io_write_mbps", 0)
        user.iops_read_max = getattr(package, "iops_read_max", 0)
        user.iops_write_max = getattr(package, "iops_write_max", 0)
        user.memory_high_mb = getattr(package, "memory_high_mb", 1024)
        user.memory_max_mb = getattr(package, "memory_max_mb", 2048)
        user.tasks_max = getattr(package, "tasks_max", 512)
        user.mysql_storage_limit_mb = getattr(package, "mysql_storage_limit_mb", 0)
        user.mysql_max_db_size_mb = getattr(package, "mysql_max_db_size_mb", 0)
        user.mysql_max_queries_per_hour = getattr(package, "mysql_max_queries_per_hour", 0)
        user.mysql_max_updates_per_hour = getattr(package, "mysql_max_updates_per_hour", 0)
        user.mysql_max_connections_per_hour = getattr(package, "mysql_max_connections_per_hour", 0)


def _decode_schedule_user_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [item for item in raw.split(",") if item]
    if isinstance(value, int):
        value = [value]
    return [int(item) for item in value if int(item) > 0]


def _remove_user_from_backup_schedules(db: Session, user_id: int) -> None:
    for schedule in db.query(BackupSchedule).all():
        changed = False
        if schedule.user_id == user_id:
            schedule.user_id = None
            changed = True
        user_ids = _decode_schedule_user_ids(schedule.user_ids)
        if user_id in user_ids:
            user_ids = [item for item in user_ids if item != user_id]
            schedule.user_ids = json.dumps(user_ids)
            changed = True
        if changed and not schedule.all_users and schedule.user_id is None and not user_ids:
            db.delete(schedule)


def _delete_owned_website(db: Session, website: Website) -> None:
    db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
    if db_item:
        db_backends.backend_for(db_item).drop_database(db_item.db_name, db_item.db_user)
    nginx.delete_wordpress_vhost(website.domain)
    # Deleting the owner deletes the site, so its certificate goes too - left
    # behind it would keep certbot renewing a name this server no longer serves.
    ssl.release_site_certificates(db, website.domain, exclude_website_id=website.id)
    waf.remove_site_rules(website.domain)
    wordpress.delete_wordpress(website.root_path)
    if db_item:
        db.delete(db_item)
    db.delete(website)


@router.post("", response_model=UserOut)
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    if current_user.role == Role.reseller and payload.role != Role.end_user:
        raise HTTPException(status_code=403, detail="Resellers can only create end_user accounts")
    # Only the username has to be unique — several panel users may share one
    # contact email (a reseller managing many accounts, for example).
    if db.query(User.id).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    # The same account name on another node of the cluster (when the
    # cluster's username guard is on): a later transfer between the two
    # would collide.
    from app.services import multi_server

    cluster_check = multi_server.check_cluster_conflict(username=payload.username)
    if cluster_check.get("conflict"):
        raise HTTPException(status_code=multi_server.conflict_http_status(cluster_check),
                            detail=cluster_check.get("message") or "Username already exists in the cluster")
    package = _package_for_payload(db, payload.package_id, current_user)
    effective_website_limit = package.website_limit if package else payload.website_limit
    effective_storage_mb = package.storage_limit_mb if package else payload.storage_limit_mb
    effective_database_limit = package.database_limit if package else None
    effective_terminal = (
        bool(payload.terminal_enabled)
        if payload.terminal_enabled is not None
        else (bool(package.terminal_enabled) if package else False)
    )
    if current_user.role == Role.reseller.value:
        if effective_terminal and not _reseller_can_grant_terminal(current_user):
            raise HTTPException(status_code=403, detail="You are not allowed to enable terminal access")
        _enforce_reseller_quotas(
            db,
            current_user,
            target_website_limit=effective_website_limit,
            target_storage_mb=effective_storage_mb,
            target_database_limit=effective_database_limit,
            is_create=True,
        )
    # The Linux account gets its own secret from the start. It used to be given
    # the panel password, which put that password on port 22 behind sshd.
    sftp_password = site_users.generate_login_password()
    try:
        site_users.ensure_panel_user(payload.username, sftp_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        package_id=package.id if package else None,
        reseller_package_id=payload.reseller_package_id if current_user.role == Role.admin.value else None,
        reseller_id=current_user.id if payload.role == Role.end_user else None,
        website_limit=payload.website_limit,
        storage_limit_mb=payload.storage_limit_mb,
        sftp_accounts_limit=payload.sftp_accounts_limit,
        mail_accounts_limit=payload.mail_accounts_limit,
        terminal_enabled=bool(payload.terminal_enabled) if payload.terminal_enabled is not None else False,
        sftp_password_set_at=datetime.utcnow(),
    )
    # After the explicit value, so a package still wins when one is assigned.
    _apply_package_limits(user, package)
    if payload.terminal_enabled is not None:
        user.terminal_enabled = payload.terminal_enabled
    db.add(user)
    db.commit()
    db.refresh(user)
    from app.services import resource_monitor
    resource_monitor.apply_user_cgroup_limits_async(user.id)

    # Send welcome email if configured
    if user.email:
        config = notifications.load_config()
        if notifications.smtp_ready(config):
            template_type = "user_welcome" if user.role == Role.end_user else "reseller_welcome"
            reseller_id = current_user.id
            if user.role == Role.admin:
                reseller_id = current_user.id
            
            template = db.query(MessageTemplate).filter(
                MessageTemplate.reseller_id == reseller_id,
                MessageTemplate.template_type == template_type
            ).first()

            subject = "Welcome to your new account"
            body = "Hello {username},\n\nYour account has been created!\n\nUsername: {username}\nPassword: {password}\n\nLogin at: {login_url}"
            if template:
                subject = template.subject
                body = template.body

            brand = panel_settings.get_brand(db, reseller_id)
            login_url = f"https://{brand.panel_domain}" if brand and brand.panel_domain else "https://your-server-ip:2222"

            subject = subject.replace("{username}", user.username)
            body = body.replace("{username}", user.username).replace("{password}", payload.password).replace("{login_url}", login_url)
            
            try:
                notifications.send_email(config, user.email, subject, body)
            except Exception as e:
                import logging
                logging.getLogger("spanel").error(f"Failed to send welcome email to {user.email}: {e}")

    log_action(db, current_user.id, "create_user", user.username, request=request)
    body = _user_out(user, db)
    # Returned once, to whoever created the account. Nothing stores it.
    body["sftp_password"] = sftp_password
    return body


@router.get("", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Who the accounts are. Disk usage only if it is already known.

    Measuring an account's disk usage means walking its home directory, and on
    a cold cache that is the whole list waiting on the slowest account: 3.8
    seconds for fifteen users before a single row could be drawn. The list is
    what the page is for, so it no longer waits. Rows whose figure is not
    cached report -1, and the page asks for those separately - see
    /users/storage-usage.
    """
    ensure_role(current_user.role, Role.reseller)
    rows = []

    primary_admin = (
        db.query(User)
        .filter(User.role == Role.admin)
        .order_by(User.id.asc())
        .first()
    )
    if primary_admin is not None:
        orphan_end_users = (
            db.query(User)
            .filter(User.role.in_([Role.end_user, "user"]), User.reseller_id.is_(None))
            .all()
        )
        if orphan_end_users:
            for u in orphan_end_users:
                u.reseller_id = primary_admin.id
            db.commit()

    managers = {
        u.id: u
        for u in db.query(User).filter(User.role.in_([Role.admin, Role.reseller])).all()
    }

    query = db.query(User).options(selectinload(User.package))
    if current_user.role == Role.reseller:
        query = query.filter(User.reseller_id == current_user.id)

    for user in query.order_by(User.id.desc()).all():
        data = UserOut.model_validate(user).model_dump()
        data["package_name"] = user.package.name if user.package else None
        owner = managers.get(user.reseller_id) if user.reseller_id else (
            primary_admin if user.role in (Role.end_user, "user") else None
        )
        if owner:
            data["reseller_id"] = owner.id
            data["owner_username"] = owner.username
            data["owner_role"] = owner.role
        known = storage_quota.cached_storage_used_bytes(user.id)
        limit_bytes = storage_quota.user_storage_limit_bytes(user)
        from app.services import resource_monitor
        data["mysql_used_bytes"] = resource_monitor.cached_user_mysql_bytes(user.id)
        data["bandwidth_used_bytes"] = resource_monitor.cached_user_bandwidth_bytes(user.id)
        data["storage_limit_bytes"] = limit_bytes
        if known is None:
            # -1, not 0: nobody has "used nothing", and a zero would draw an
            # empty bar that looks like an answer.
            data["storage_used_bytes"] = -1
            data["storage_percent"] = 0.0
        else:
            data["storage_used_bytes"] = known
            data["storage_percent"] = (
                min(999.0, round((known / limit_bytes) * 100, 2)) if limit_bytes else 0.0
            )
        rows.append(data)
    return rows


@router.get("/storage-usage")
def storage_usage(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The figures the list deliberately did not wait for.

    Walks what it has to and fills the cache, so the next list is instant. The
    page calls this once after the rows are on screen.
    """
    ensure_role(current_user.role, Role.reseller)
    
    query = db.query(User)
    if current_user.role == Role.reseller:
        query = query.filter(User.reseller_id == current_user.id)
        
    return {
        str(user.id): storage_quota.storage_usage_summary(db, user)
        for user in query.all()
    }


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _user_out(current_user, db)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    
    query = db.query(User).filter(User.id == user_id)
    if current_user.role == Role.reseller:
        query = query.filter(User.reseller_id == current_user.id)
        
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if current_user.role == Role.reseller and payload.role and payload.role != Role.end_user:
        raise HTTPException(status_code=403, detail="Resellers cannot change role to admin or reseller")
        
    role_changed = False
    if payload.role is not None and payload.role != user.role:
        if user_id == current_user.id:
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        user.role = payload.role
        role_changed = True
    if payload.email is not None and payload.email != user.email:
        user.email = payload.email  # emails need not be unique across panel users
    active_changed = False
    if payload.is_active is not None:
        if user_id == current_user.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        if user.is_active != payload.is_active:
            user.is_active = payload.is_active
            user.token_version = (user.token_version or 0) + 1
            active_changed = True
    package = None
    if "package_id" in payload.model_fields_set:
        package = _package_for_payload(db, payload.package_id, current_user)
        _apply_package_limits(user, package)
    if payload.website_limit is not None:
        user.website_limit = payload.website_limit
    if payload.storage_limit_mb is not None:
        user.storage_limit_mb = payload.storage_limit_mb
    if payload.sftp_accounts_limit is not None:
        user.sftp_accounts_limit = payload.sftp_accounts_limit
    if payload.mail_accounts_limit is not None:
        user.mail_accounts_limit = payload.mail_accounts_limit
    if package:
        _apply_package_limits(user, package)
    if payload.terminal_enabled is not None:
        user.terminal_enabled = payload.terminal_enabled

    if current_user.role == Role.reseller.value:
        if (
            user.terminal_enabled
            or payload.terminal_enabled is True
            or payload.ssh_access_users is True
        ) and not _reseller_can_grant_terminal(current_user):
            raise HTTPException(status_code=403, detail="You are not allowed to enable terminal access")
        effective_pkg = package if "package_id" in payload.model_fields_set else user.package
        _enforce_reseller_quotas(
            db,
            current_user,
            target_website_limit=user.website_limit,
            target_storage_mb=user.storage_limit_mb,
            target_database_limit=effective_pkg.database_limit if effective_pkg else None,
            is_create=False,
            exclude_user_id=user.id,
        )

    if role_changed:
        # New role -> existing tokens with old role claim should be invalidated.
        user.token_version = (user.token_version or 0) + 1

    db.commit()
    db.refresh(user)
    if active_changed:
        # A suspended account's mailboxes keep receiving but cannot sign in.
        mail.sync_quietly(db)
    log_action(db, current_user.id, "update_user", user.username, request=request)
    return _user_out(user, db)


@router.delete("/{user_id}")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_rate_limit(request, "users_delete", max_requests=10, window_seconds=60, key_suffix=str(current_user.id))
    ensure_role(current_user.role, Role.reseller)
    
    query = db.query(User).filter(User.id == user_id)
    if normalize_role(current_user.role) == Role.reseller:
        query = query.filter(User.reseller_id == current_user.id)
        
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if normalize_role(current_user.role) == Role.reseller and normalize_role(user.role) != Role.end_user:
        raise HTTPException(status_code=403, detail="Resellers can only delete end_user accounts")
    if demo_mode.is_demo_user(user):
        raise HTTPException(status_code=400, detail="Cannot delete an active demo account. Remove it from Demo Mode first.")
    websites = db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.asc()).all()
    deleted_domains = []
    panel_linux_user = site_users.linux_user_for_panel_username(user.username)
    try:
        for website in websites:
            if website.linux_user and website.linux_user != panel_linux_user:
                raise ValueError(f"Website {website.domain} is not owned by Linux user {panel_linux_user}")
        for website in websites:
            _delete_owned_website(db, website)
            deleted_domains.append(website.domain)
        _remove_user_from_backup_schedules(db, user.id)
        # Databases and applications are keyed on owner_id, not website_id, so
        # the website loop above cannot reach a database that belongs to no
        # website - which is the only kind POST /api/databases creates - nor any
        # application at all. Migrations 0014/0015 and 0025 re-parented both
        # tables onto users; nothing here followed, and the panel database does
        # not enforce the cleanup it declares (see services/teardown.py).
        purged = teardown.purge_owned_resources(db, user.id)
        # The FK declares ON DELETE CASCADE, but SQLite only honours that with
        # PRAGMA foreign_keys on, and this database does not enforce the
        # cleanup it declares - the same reason purge_owned_resources exists.
        # A leftover token row would be an orphan, not a way in (authenticate
        # refuses a token whose owner is gone), but it would still be a row
        # nobody can see or revoke.
        db.query(McpToken).filter(McpToken.user_id == user.id).delete(synchronize_session=False)
        # Their mail is in the home that goes next; the rows go with it.
        mail.delete_for_owner(db, user)
        site_users.delete_panel_user(user.username)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    username = user.username
    db.delete(user)
    db.commit()
    mail.sync_quietly(db)
    detail = ",".join(deleted_domains)
    if purged["databases"] or purged["applications"]:
        # Worth recording separately: these are resources the website loop never
        # touched, so before this sweep existed they simply stayed behind.
        detail = f"{detail} +db:{len(purged['databases'])} +app:{len(purged['applications'])}"
    log_action(db, current_user.id, "delete_user", username, detail, request=request)
    return {
        "ok": True,
        "deleted_websites": deleted_domains,
        "deleted_databases": purged["databases"],
        "deleted_applications": purged["applications"],
    }


@router.post("/{user_id}/password")
def update_user_password(user_id: int, payload: UserPasswordUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_rate_limit(request, "users_password", max_requests=10, window_seconds=60, key_suffix=str(current_user.id))
    if user_id != current_user.id:
        ensure_role(current_user.role, Role.reseller)
        if payload.current_password or payload.code:
            require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
    else:
        require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
        
    query = db.query(User).filter(User.id == user_id)
    if current_user.role == Role.reseller and user_id != current_user.id:
        query = query.filter(User.reseller_id == current_user.id)
        
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # The panel password no longer reaches the Linux account. It used to, which
    # meant sshd offered the panel password to the internet on port 22.
    try:
        minted = site_users.retire_shared_login_password(
            user.username, already_separate=user.sftp_password_set_at is not None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if minted is not None:
        user.sftp_password_set_at = datetime.utcnow()
    user.hashed_password = hash_password(payload.password)
    # Force re-login on all other sessions of this user.
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    log_action(db, current_user.id, "update_user_password", user.username, request=request)
    from app.services import notifications

    notifications.notify_in_background("security_change", {
        "kind": "password", "username": user.username, "when": notifications.now_text(),
    }, user_ids=[user.id])
    body = {"message": f"Changed password for user {user.username}"}
    if minted is not None:
        # This account's SFTP login was the panel password until a moment ago.
        # It has been replaced so the old value stops working, and this is the
        # only time the new one is readable.
        body["sftp_password"] = minted
        body["sftp_password_rotated"] = True
        body["message"] += (
            ". Its SFTP password was the same secret and has been replaced - "
            "copy the new one now, it is not shown again."
        )
    return body


@router.post("/{user_id}/sftp-password")
def set_sftp_password(
    user_id: int,
    payload: SftpPasswordUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set this account's SFTP password, independently of the panel password.

    Before 0033 there was no such thing: the Linux account carried whatever the
    panel password was, so sshd offered the panel password to anyone on port 22,
    and an SFTP brute force was a panel compromise - which through the sudo
    helper is root.

    Sending no password means "generate one", which is the better default: it
    is the one case where the value is guaranteed not to be a password the user
    has used somewhere else.
    """
    enforce_rate_limit(request, "users_sftp_password", max_requests=10, window_seconds=60, key_suffix=str(current_user.id))
    if user_id != current_user.id:
        ensure_role(current_user.role, Role.reseller)
    else:
        require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
        
    query = db.query(User).filter(User.id == user_id)
    if normalize_role(current_user.role) == Role.reseller and user_id != current_user.id:
        query = query.filter(User.reseller_id == current_user.id)
        
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Named for what it is. This is the one endpoint where a value the user
    # typed may legitimately reach chpasswd, and calling it `password` would
    # make it indistinguishable from a panel password at a glance.
    sftp_password = payload.password or site_users.generate_login_password()
    generated = payload.password is None
    try:
        site_users.set_panel_user_password(user.username, sftp_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    was_shared = user.sftp_password_set_at is None
    user.sftp_password_set_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user.id, "set_sftp_password", user.username, request=request)
    return {
        "message": "SFTP password updated",
        # Returned once, and only when the panel invented it. A password the
        # user chose is never echoed back.
        "password": sftp_password if generated else None,
        # True when this call is what finally separated the two secrets.
        "separated_from_panel_password": was_shared,
    }


@router.post("/{user_id}/2fa/reset")
def reset_user_two_factor(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Use the Security page to disable your own 2FA")
    user.totp_enabled = False
    user.totp_secret = None
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    log_action(db, current_user.id, "reset_user_2fa", user.username, request=request)
    from app.services import notifications

    notifications.notify_in_background("security_change", {
        "kind": "2fa_reset", "username": user.username, "when": notifications.now_text(),
    }, user_ids=[user.id])
    return {"message": f"Reset 2FA for user {user.username}"}

@router.post("/{user_id}/suspend")
def suspend_user(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Full suspend: block login, rewrite nginx, lock SFTP, kill sessions."""
    ensure_role(current_user.role, Role.admin)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot suspend yourself")

    user.is_active = False
    user.token_version = (user.token_version or 0) + 1

    websites = db.query(Website).filter(Website.owner_id == user.id).all()
    for website in websites:
        website.status = "suspended"
        nginx.delete_wordpress_vhost(website.domain)
        nginx.write_vhost(
            website.domain,
            website.root_path,
            app_type="static",
            php_version=website.php_version,
            document_root=website.document_root or "public_html",
            custom_directives="# SUSPENDED",
            rewrite_mode="none",
            preserve_existing_ssl=False,
        )
        if website.linux_user:
            try:
                site_users.lock_linux_user(website.linux_user)
            except Exception:
                pass

    # Sub-accounts share the site user's uid but not its name, so locking the
    # site user above does not touch them. A suspended customer with a working
    # SFTP sub-account still has write access to the sites just disabled.
    teardown.set_owner_sftp_accounts_locked(db, user.id, True)
    teardown.set_owner_databases_locked(db, user.id, True)

    db.commit()
    log_action(db, current_user.id, "suspend_user", user.username, request=request)
    return {"message": f"Suspended user {user.username}", "affected_websites": len(websites)}

@router.post("/{user_id}/unsuspend")
def unsuspend_user(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Full unsuspend: restore login, nginx config, unlock SFTP."""
    ensure_role(current_user.role, Role.admin)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = True

    websites = db.query(Website).filter(Website.owner_id == user.id).all()
    for website in websites:
        website.status = "active"
        rewrite_mode = "front_controller" if website.app_type == "wordpress" else (website.nginx_rewrite_mode or "none")
        php_socket = site_users.site_php_fpm_socket(website.linux_user, website.root_path, website.php_version) if website.app_type in {"wordpress", "php"} else None
        nginx.rewrite_vhost(
            website.domain,
            website.root_path,
            app_type=website.app_type,
            php_version=website.php_version,
            php_fpm_socket_override=php_socket,
            custom_directives=website.nginx_custom or "",
            document_root=website.document_root or "public_html",
            rewrite_mode=rewrite_mode,
            waf_enabled=website.waf_enabled,
            http_flood_enabled=website.http_flood_enabled,
            http_flood_config=website.http_flood_config or "",
            aliases=[a.domain for a in (website.aliases or []) if a.mode == "alias"],
            redirects=[a.domain for a in (website.aliases or []) if a.mode == "redirect"],
        )
        if website.linux_user:
            try:
                site_users.unlock_linux_user(website.linux_user)
            except Exception:
                pass

    teardown.set_owner_sftp_accounts_locked(db, user.id, False)
    teardown.set_owner_databases_locked(db, user.id, False)

    db.commit()
    log_action(db, current_user.id, "unsuspend_user", user.username, request=request)
    return {"message": f"Unsuspended user {user.username}", "affected_websites": len(websites)}


@router.get("/audit/log", response_model=List[AuditLogOut])
def list_audit(
    request: Request,
    user_id: Optional[int] = Query(default=None),
    action: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    query = db.query(AuditLog).order_by(AuditLog.id.desc())
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    rows = query.offset(offset).limit(limit).all()
    entries = [AuditLogOut.from_row(row) for row in rows]
    if demo_mode.is_demo_session(current_user, getattr(request.state, "jwt_payload", None)):
        # Every visitor is the same demo account; none of them should see where
        # the others came from.
        for entry in entries:
            entry.detail = demo_mode.mask_ips(entry.detail)
            entry.target = demo_mode.mask_ips(entry.target)
    return entries


@router.post("/{user_id}/impersonate")
def impersonate_user(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_rate_limit(request, "users_impersonate", max_requests=15, window_seconds=60, key_suffix=str(current_user.id))
    ensure_role(current_user.role, Role.reseller)
    query = db.query(User).filter(User.id == user_id)
    if normalize_role(current_user.role) == Role.reseller:
        query = query.filter(User.reseller_id == current_user.id)
    
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot impersonate yourself")
        
    if normalize_role(current_user.role) == Role.reseller and normalize_role(user.role) != Role.end_user:
        raise HTTPException(status_code=403, detail="Resellers can only impersonate end users")
        
    from app.services.sso_tokens import create_panel_login_token
    token = create_panel_login_token(user.username)
    log_action(db, current_user.id, "impersonate_user", user.username, request=request)
    
    return {"login_url": f"/api/auth/sso/{token}"}

@router.post("/move")
def move_users(payload: schemas.UsersMove, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    
    target_reseller = db.query(User).filter(User.id == payload.target_reseller_id, User.role.in_([Role.admin, Role.reseller])).first()
    if not target_reseller:
        raise HTTPException(status_code=404, detail="Target reseller or admin not found")
        
    users = db.query(User).filter(User.id.in_(payload.user_ids), User.role == Role.end_user).all()
    if not users:
        raise HTTPException(status_code=404, detail="No end users found to move")
        
    moved_count = 0
    for user in users:
        if user.reseller_id != target_reseller.id:
            user.reseller_id = target_reseller.id
            moved_count += 1
            
    db.commit()
    return {"message": f"Successfully moved {moved_count} users to {target_reseller.username}"}
