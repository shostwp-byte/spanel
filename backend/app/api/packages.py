from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User, UserPackage
from app.schemas.schemas import UserPackageCreate, UserPackageOut, UserPackageUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/packages", tags=["packages"])


def _package_by_id(db: Session, package_id: int) -> UserPackage:
    package = db.query(UserPackage).filter(UserPackage.id == package_id).first()
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    return package


def _ensure_unique_name(db: Session, name: str, package_id: int | None = None) -> None:
    query = db.query(UserPackage).filter(UserPackage.name == name)
    if package_id is not None:
        query = query.filter(UserPackage.id != package_id)
    if query.first():
        raise HTTPException(status_code=409, detail="Package name already exists")


@router.get("", response_model=List[UserPackageOut])
def list_packages(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    query = db.query(UserPackage)
    if current_user.role == Role.reseller:
        query = query.filter((UserPackage.owner_id == None) | (UserPackage.owner_id == current_user.id))
    return query.order_by(UserPackage.id.asc()).all()


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


@router.post("", response_model=UserPackageOut)
def create_package(
    payload: UserPackageCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    terminal_flag = bool(payload.terminal_enabled or payload.ssh_access)
    if current_user.role == Role.reseller.value and (terminal_flag or payload.ssh_access_users):
        if not _reseller_can_grant_terminal(current_user):
            raise HTTPException(status_code=403, detail="You are not allowed to enable terminal access")
    _ensure_unique_name(db, payload.name)

    # Standardize paired fields so legacy & canonical columns stay in sync
    effective_alias_limit = payload.alias_limit if payload.alias_limit is not None else payload.domain_pointers_limit
    effective_pointers_limit = payload.domain_pointers_limit if payload.domain_pointers_limit is not None else effective_alias_limit
    effective_sftp_limit = payload.sftp_accounts_limit if payload.sftp_accounts_limit is not None else payload.ftp_accounts_limit
    effective_ftp_limit = payload.ftp_accounts_limit if payload.ftp_accounts_limit is not None else effective_sftp_limit

    package = UserPackage(
        name=payload.name,
        slug=payload.slug,
        owner_id=current_user.id if current_user.role == Role.reseller else None,
        website_limit=payload.website_limit,
        storage_limit_mb=payload.storage_limit_mb,
        database_limit=payload.database_limit,
        alias_limit=effective_alias_limit,
        backup_retention_days=payload.backup_retention_days,
        terminal_enabled=terminal_flag,
        waf_enabled=payload.waf_enabled,
        wordpress_enabled=payload.wordpress_enabled,
        node_apps_limit=payload.node_apps_limit,
        sftp_accounts_limit=effective_sftp_limit,
        mail_accounts_limit=payload.mail_accounts_limit,
        node_app_memory_mb=max(64, int(payload.node_app_memory_mb or 512)),

        is_reseller_package=payload.is_reseller_package if current_user.role == Role.admin else False,
        reseller_user_limit=payload.reseller_user_limit if current_user.role == Role.admin else 0,
        reseller_overselling_enabled=payload.reseller_overselling_enabled if current_user.role == Role.admin else False,

        bandwidth_limit_mb=payload.bandwidth_limit_mb,
        inode_limit=payload.inode_limit,
        subdomain_limit=payload.subdomain_limit,
        email_forwarders_limit=payload.email_forwarders_limit,
        mailing_lists_limit=payload.mailing_lists_limit,
        autoresponders_limit=payload.autoresponders_limit,
        domain_pointers_limit=effective_pointers_limit,
        ftp_accounts_limit=effective_ftp_limit,
        ips_limit=payload.ips_limit,
        anonymous_ftp=payload.anonymous_ftp,
        cgi_access=payload.cgi_access,
        git_access=payload.git_access,
        php_access=payload.php_access,
        spamassassin=payload.spamassassin,
        catch_all_email=payload.catch_all_email,
        ssl_access=payload.ssl_access,
        ssh_access=terminal_flag,
        ssh_access_users=payload.ssh_access_users,
        cron_jobs=payload.cron_jobs,
        redis_access=payload.redis_access,
        system_info=payload.system_info,
        login_keys=payload.login_keys,
        dns_control=payload.dns_control,
        personal_dns_mode=payload.personal_dns_mode,
        share_server_ip=payload.share_server_ip,
        cpu_quota_percent=payload.cpu_quota_percent,
        io_read_mbps=payload.io_read_mbps,
        io_write_mbps=payload.io_write_mbps,
        iops_read_max=payload.iops_read_max,
        iops_write_max=payload.iops_write_max,
        memory_high_mb=payload.memory_high_mb,
        memory_max_mb=payload.memory_max_mb,
        tasks_max=payload.tasks_max,
        mysql_storage_limit_mb=payload.mysql_storage_limit_mb,
        mysql_max_db_size_mb=payload.mysql_max_db_size_mb,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    log_action(db, current_user.id, "create_package", package.name, request=request)
    return package


@router.patch("/{package_id}", response_model=UserPackageOut)
def update_package(
    package_id: int,
    payload: UserPackageUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    package = _package_by_id(db, package_id)
    if current_user.role == Role.reseller and package.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot modify a package you do not own")
    if current_user.role == Role.reseller.value and (
        payload.terminal_enabled is True or payload.ssh_access is True or payload.ssh_access_users is True
    ):
        if not _reseller_can_grant_terminal(current_user):
            raise HTTPException(status_code=403, detail="You are not allowed to enable terminal access")

    if payload.name is not None and payload.name != package.name:
        _ensure_unique_name(db, payload.name, package_id=package.id)
        package.name = payload.name
    if payload.slug is not None:
        package.slug = payload.slug
    if payload.website_limit is not None:
        package.website_limit = payload.website_limit
    if payload.storage_limit_mb is not None:
        package.storage_limit_mb = payload.storage_limit_mb
    if payload.database_limit is not None:
        package.database_limit = payload.database_limit
    if payload.alias_limit is not None:
        package.alias_limit = payload.alias_limit
        if payload.domain_pointers_limit is None:
            package.domain_pointers_limit = payload.alias_limit
    if payload.domain_pointers_limit is not None:
        package.domain_pointers_limit = payload.domain_pointers_limit
        if payload.alias_limit is None:
            package.alias_limit = payload.domain_pointers_limit
    if payload.backup_retention_days is not None:
        package.backup_retention_days = payload.backup_retention_days
    if payload.terminal_enabled is not None or payload.ssh_access is not None:
        term_val = bool(
            payload.terminal_enabled
            if payload.terminal_enabled is not None
            else payload.ssh_access
        )
        package.terminal_enabled = term_val
        package.ssh_access = term_val
    if payload.waf_enabled is not None:
        package.waf_enabled = payload.waf_enabled
    if payload.wordpress_enabled is not None:
        package.wordpress_enabled = payload.wordpress_enabled
    if payload.node_apps_limit is not None:
        package.node_apps_limit = payload.node_apps_limit
    if payload.sftp_accounts_limit is not None:
        package.sftp_accounts_limit = payload.sftp_accounts_limit
        if payload.ftp_accounts_limit is None:
            package.ftp_accounts_limit = payload.sftp_accounts_limit
    if payload.ftp_accounts_limit is not None:
        package.ftp_accounts_limit = payload.ftp_accounts_limit
        if payload.sftp_accounts_limit is None:
            package.sftp_accounts_limit = payload.ftp_accounts_limit
    if payload.mail_accounts_limit is not None:
        package.mail_accounts_limit = payload.mail_accounts_limit
    if payload.node_app_memory_mb is not None:
        package.node_app_memory_mb = max(64, int(payload.node_app_memory_mb))
    if current_user.role == Role.admin:
        if payload.is_reseller_package is not None:
            package.is_reseller_package = payload.is_reseller_package
        if payload.reseller_user_limit is not None:
            package.reseller_user_limit = payload.reseller_user_limit
        if payload.reseller_overselling_enabled is not None:
            package.reseller_overselling_enabled = payload.reseller_overselling_enabled

    for field in [
        "bandwidth_limit_mb", "inode_limit", "subdomain_limit", "email_forwarders_limit",
        "mailing_lists_limit", "autoresponders_limit", "ips_limit",
        "anonymous_ftp", "cgi_access", "git_access", "php_access", "spamassassin",
        "catch_all_email", "ssl_access", "ssh_access_users", "cron_jobs",
        "redis_access", "system_info", "login_keys", "dns_control",
        "personal_dns_mode", "share_server_ip", "cpu_quota_percent",
        "io_read_mbps", "io_write_mbps", "iops_read_max", "iops_write_max",
        "memory_high_mb", "memory_max_mb", "tasks_max",
        "mysql_storage_limit_mb", "mysql_max_db_size_mb",
    ]:
        val = getattr(payload, field, None)
        if val is not None:
            setattr(package, field, val)

    affected_users = db.query(User).filter(User.package_id == package.id).all()
    for user in affected_users:
        user.website_limit = package.website_limit
        user.storage_limit_mb = package.storage_limit_mb
        user.database_limit = package.database_limit
        user.bandwidth_limit_mb = package.bandwidth_limit_mb
        user.inode_limit = package.inode_limit
        user.terminal_enabled = package.terminal_enabled
        user.sftp_accounts_limit = package.sftp_accounts_limit
        user.mail_accounts_limit = package.mail_accounts_limit
        user.cpu_quota_percent = package.cpu_quota_percent
        user.io_read_mbps = package.io_read_mbps
        user.io_write_mbps = package.io_write_mbps
        user.iops_read_max = package.iops_read_max
        user.iops_write_max = package.iops_write_max
        user.memory_high_mb = package.memory_high_mb
        user.memory_max_mb = package.memory_max_mb
        user.tasks_max = package.tasks_max
        user.mysql_storage_limit_mb = package.mysql_storage_limit_mb
        user.mysql_max_db_size_mb = package.mysql_max_db_size_mb
    db.commit()
    from app.services import resource_monitor
    for user in affected_users:
        resource_monitor.apply_user_cgroup_limits_async(user.id)
    db.refresh(package)
    log_action(db, current_user.id, "update_package", package.name, request=request)
    return package


@router.delete("/{package_id}")
def delete_package(
    package_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    package = _package_by_id(db, package_id)
    if current_user.role == Role.reseller and package.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot delete a package you do not own")
    if db.query(User).filter(User.package_id == package.id).first():
        raise HTTPException(status_code=400, detail="Package is in use")
    name = package.name
    db.delete(package)
    db.commit()
    log_action(db, current_user.id, "delete_package", name, request=request)
    return {"ok": True}
