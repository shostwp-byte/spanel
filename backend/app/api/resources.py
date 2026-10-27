from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import DatabaseAccount, User, UserPackage, Website
from app.services import db_backends, disk_quota, resource_monitor, storage_quota
from app.services.remote_mysql import RemoteDatabaseError
from app.services.audit import log_action

router = APIRouter(prefix="/resources", tags=["resources"])


class UserResourceLimitsUpdate(BaseModel):
    sync_from_package: bool = False
    package_id: Optional[int] = None
    storage_limit_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    bandwidth_limit_mb: Optional[int] = Field(default=None, ge=0, le=104857600)
    inode_limit: Optional[int] = Field(default=None, ge=0, le=100000000)
    database_limit: Optional[int] = Field(default=None, ge=0, le=10000)
    cpu_quota_percent: Optional[int] = Field(default=None, ge=0, le=6400)
    io_read_mbps: Optional[int] = Field(default=None, ge=0, le=100000)
    io_write_mbps: Optional[int] = Field(default=None, ge=0, le=100000)
    iops_read_max: Optional[int] = Field(default=None, ge=0, le=10000000)
    iops_write_max: Optional[int] = Field(default=None, ge=0, le=10000000)
    memory_high_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    memory_max_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    tasks_max: Optional[int] = Field(default=None, ge=0, le=1000000)
    mysql_storage_limit_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    mysql_max_db_size_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    mysql_max_queries_per_hour: Optional[int] = Field(default=None, ge=0, le=1000000000)
    mysql_max_updates_per_hour: Optional[int] = Field(default=None, ge=0, le=1000000000)
    mysql_max_connections_per_hour: Optional[int] = Field(default=None, ge=0, le=1000000000)


class DatabaseWriteLockToggle(BaseModel):
    locked: bool
    reason: Optional[str] = None


class UserAttackModeToggle(BaseModel):
    enabled: bool
    reason: Optional[str] = None


def _build_user_resource_row(
    user: User,
    *,
    dbs: list[DatabaseAccount],
    sites: list[Website],
    managers: dict[int, User],
    primary_admin: User | None,
) -> dict[str, Any]:
    live = resource_monitor.cached_user_live_metrics(user.id)
    attack_info = resource_monitor.cached_user_attack_state(user.id)
    mysql_bytes = sum(
        max(0, int(getattr(d, "size_bytes", 0) or resource_monitor.cached_db_size_bytes(d.db_name)))
        for d in dbs
    )
    locked_dbs_count = sum(1 for d in dbs if getattr(d, "write_locked", False))
    max_single_db_bytes = max(
        [int(getattr(d, "size_bytes", 0) or resource_monitor.cached_db_size_bytes(d.db_name)) for d in dbs],
        default=0,
    )

    cached_total = storage_quota.cached_storage_used_bytes(user.id)
    if cached_total is None or cached_total < mysql_bytes:
        total_storage_bytes = max(cached_total or 0, mysql_bytes)
    else:
        total_storage_bytes = cached_total

    web_and_mail_bytes = max(0, total_storage_bytes - mysql_bytes)
    bandwidth_bytes = resource_monitor.cached_user_bandwidth_bytes(user.id)

    storage_limit_mb = int(getattr(user, "storage_limit_mb", 0) or 0)
    storage_limit_bytes = storage_limit_mb * storage_quota.BYTES_PER_MB if storage_limit_mb > 0 else 0

    mysql_limit_mb = int(getattr(user, "mysql_storage_limit_mb", 0) or 0)
    effective_mysql_limit_mb = mysql_limit_mb if mysql_limit_mb > 0 else storage_limit_mb
    effective_mysql_limit_bytes = effective_mysql_limit_mb * storage_quota.BYTES_PER_MB if effective_mysql_limit_mb > 0 else 0

    bandwidth_limit_mb = int(getattr(user, "bandwidth_limit_mb", 0) or (user.package.bandwidth_limit_mb if user.package else 0) or 0)
    bandwidth_limit_bytes = bandwidth_limit_mb * storage_quota.BYTES_PER_MB if bandwidth_limit_mb > 0 else 0

    cpu_quota_percent = int(getattr(user, "cpu_quota_percent", 100) or 0)
    memory_high_mb = int(getattr(user, "memory_high_mb", 1024) or 0)
    memory_max_mb = int(getattr(user, "memory_max_mb", 2048) or 0)
    tasks_max = int(getattr(user, "tasks_max", 512) or 0)

    owner = managers.get(user.reseller_id) if user.reseller_id else primary_admin

    storage_pct = round((total_storage_bytes / storage_limit_bytes) * 100, 1) if storage_limit_bytes > 0 else 0.0
    mysql_pct = round((mysql_bytes / effective_mysql_limit_bytes) * 100, 1) if effective_mysql_limit_bytes > 0 else 0.0
    ram_pct = round((live["memory_used_bytes"] / (memory_max_mb * storage_quota.BYTES_PER_MB)) * 100, 1) if memory_max_mb > 0 else 0.0
    cpu_pct = round((live["cpu_percent"] / cpu_quota_percent) * 100, 1) if cpu_quota_percent > 0 else live["cpu_percent"]
    bw_pct = round((bandwidth_bytes / bandwidth_limit_bytes) * 100, 1) if bandwidth_limit_bytes > 0 else 0.0

    status = "normal"
    if attack_info["under_attack"] or locked_dbs_count > 0 or storage_pct >= 100 or mysql_pct >= 100 or ram_pct >= 100 or bw_pct >= 100:
        status = "critical"
    elif storage_pct >= 80 or mysql_pct >= 80 or ram_pct >= 80 or cpu_pct >= 85 or bw_pct >= 80:
        status = "warning"

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "package_id": user.package_id,
        "package_name": user.package.name if user.package else "Custom",
        "reseller_id": owner.id if owner else user.reseller_id,
        "owner_username": owner.username if owner else "admin",
        "owner_role": owner.role if owner else "admin",
        "status": status,
        "under_attack": attack_info["under_attack"],
        "under_attack_reason": attack_info["reason"],
        "under_attack_since": attack_info["activated_at"],
        "high_load_streak": attack_info["high_load_streak"],
        "websites_count": len(sites),
        "website_limit": int(user.website_limit or 0),
        "databases_count": len(dbs),
        "database_limit": int(
            getattr(user.package, "database_limit", getattr(user, "database_limit", 5))
            if getattr(user, "package", None) is not None
            else (getattr(user, "database_limit", 5) or 0)
        ),
        "locked_databases_count": locked_dbs_count,
        "cpu_percent": live["cpu_percent"],
        "php_cpu_percent": float(live.get("php_cpu_percent") or 0.0),
        "mysql_cpu_percent": float(live.get("mysql_cpu_percent") or 0.0),
        "mysql_qps": float(live.get("mysql_qps") or 0.0),
        "mysql_rows_read_rate": int(live.get("mysql_rows_read_rate") or 0),
        "mysql_sniped_queries": int(live.get("mysql_sniped_queries") or 0),
        "cpu_quota_percent": cpu_quota_percent,
        "cpu_utilization_pct": min(999.0, cpu_pct),
        "memory_used_bytes": live["memory_used_bytes"],
        "memory_high_mb": memory_high_mb,
        "memory_max_mb": memory_max_mb,
        "memory_percent": min(999.0, ram_pct),
        "tasks_count": live["tasks_count"],
        "tasks_max": tasks_max,
        "io_read_mbps": int(getattr(user, "io_read_mbps", 0) or 0),
        "io_write_mbps": int(getattr(user, "io_write_mbps", 0) or 0),
        "iops_read_max": int(getattr(user, "iops_read_max", 0) or 0),
        "iops_write_max": int(getattr(user, "iops_write_max", 0) or 0),
        "mysql_used_bytes": mysql_bytes,
        "mysql_storage_limit_mb": mysql_limit_mb,
        "effective_mysql_limit_bytes": effective_mysql_limit_bytes,
        "mysql_percent": min(999.0, mysql_pct),
        "mysql_max_db_size_mb": int(getattr(user, "mysql_max_db_size_mb", 0) or 0),
        "mysql_max_queries_per_hour": int(getattr(user, "mysql_max_queries_per_hour", 0) or 0),
        "mysql_max_updates_per_hour": int(getattr(user, "mysql_max_updates_per_hour", 0) or 0),
        "mysql_max_connections_per_hour": int(getattr(user, "mysql_max_connections_per_hour", 0) or 0),
        "largest_db_bytes": max_single_db_bytes,
        "web_used_bytes": web_and_mail_bytes,
        "storage_used_bytes": total_storage_bytes,
        "storage_limit_mb": storage_limit_mb,
        "storage_limit_bytes": storage_limit_bytes,
        "storage_percent": min(999.0, storage_pct),
        "inode_limit": int(getattr(user, "inode_limit", 0) or 0),
        "bandwidth_used_bytes": bandwidth_bytes,
        "bandwidth_limit_mb": bandwidth_limit_mb,
        "bandwidth_limit_bytes": bandwidth_limit_bytes,
        "bandwidth_percent": min(999.0, bw_pct),
    }


@router.get("/users")
def list_user_resources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)

    managers = {
        u.id: u
        for u in db.query(User).filter(User.role.in_([Role.admin, Role.reseller])).all()
    }
    primary_admin = next(
        (u for u in sorted(managers.values(), key=lambda x: x.id) if u.role == Role.admin),
        None,
    )

    user_query = db.query(User).options(selectinload(User.package)).filter(
        User.role.in_([Role.end_user, "user"])
    )
    if current_user.role == Role.reseller:
        user_query = user_query.filter(User.reseller_id == current_user.id)

    end_users = user_query.order_by(User.id.desc()).all()
    user_ids = [u.id for u in end_users]

    all_dbs = (
        db.query(DatabaseAccount).filter(DatabaseAccount.owner_id.in_(user_ids)).all()
        if user_ids
        else []
    )
    all_sites = (
        db.query(Website).filter(Website.owner_id.in_(user_ids)).all()
        if user_ids
        else []
    )

    dbs_by_user: dict[int, list[DatabaseAccount]] = {}
    for d in all_dbs:
        dbs_by_user.setdefault(d.owner_id, []).append(d)

    sites_by_user: dict[int, list[Website]] = {}
    for s in all_sites:
        sites_by_user.setdefault(s.owner_id, []).append(s)

    items = [
        _build_user_resource_row(
            u,
            dbs=dbs_by_user.get(u.id, []),
            sites=sites_by_user.get(u.id, []),
            managers=managers,
            primary_admin=primary_admin,
        )
        for u in end_users
    ]

    summary = {
        "total_users": len(items),
        "warning_or_critical_users": sum(1 for i in items if i["status"] in ("warning", "critical")),
        "under_attack_users": sum(1 for i in items if i.get("under_attack")),
        "total_cpu_percent": round(sum(i["cpu_percent"] for i in items), 1),
        "total_mysql_cpu_percent": round(sum(i.get("mysql_cpu_percent", 0.0) for i in items), 1),
        "total_mysql_qps": round(sum(i.get("mysql_qps", 0.0) for i in items), 1),
        "total_sniped_queries": sum(i.get("mysql_sniped_queries", 0) for i in items),
        "total_memory_used_bytes": sum(i["memory_used_bytes"] for i in items),
        "total_mysql_used_bytes": sum(i["mysql_used_bytes"] for i in items),
        "total_locked_databases": sum(i["locked_databases_count"] for i in items),
        "total_databases": sum(i["databases_count"] for i in items),
        "total_storage_used_bytes": sum(i["storage_used_bytes"] for i in items),
        "total_bandwidth_used_bytes": sum(i["bandwidth_used_bytes"] for i in items),
    }

    return {"summary": summary, "items": items}


@router.get("/me")
def get_my_resources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    dbs = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == current_user.id).all()
    sites = db.query(Website).filter(Website.owner_id == current_user.id).all()
    row = _build_user_resource_row(
        current_user,
        dbs=dbs,
        sites=sites,
        managers={},
        primary_admin=None,
    )
    return row


@router.get("/users/{user_id}/detail")
def get_user_resource_detail(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    user = db.query(User).options(selectinload(User.package)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role == Role.reseller and user.reseller_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    breakdown = storage_quota.user_storage_breakdown(db, user, use_cache=True)
    live = resource_monitor.cached_user_live_metrics(user.id)
    attack_info = resource_monitor.cached_user_attack_state(user.id)
    dbs = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).order_by(DatabaseAccount.id.desc()).all()
    sites = db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.desc()).all()

    db_items = []
    for d in dbs:
        sz = max(0, int(getattr(d, "size_bytes", 0) or resource_monitor.cached_db_size_bytes(d.db_name)))
        db_items.append({
            "id": d.id,
            "db_name": d.db_name,
            "db_user": d.db_user,
            "website_id": d.website_id,
            "size_bytes": sz,
            "write_locked": bool(getattr(d, "write_locked", False)),
            "write_locked_reason": getattr(d, "write_locked_reason", None),
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })

    site_items = []
    for s in sites:
        site_items.append({
            "id": s.id,
            "domain": s.domain,
            "php_version": s.php_version,
            "app_type": s.app_type,
            "http_flood_enabled": bool(getattr(s, "http_flood_enabled", False)),
            "storage_bytes": storage_quota.website_storage_used_bytes(s),
            "bandwidth_bytes": resource_monitor.cached_site_bandwidth_bytes(s.id),
        })

    return {
        "user_id": user.id,
        "username": user.username,
        "package_id": user.package_id,
        "package_name": user.package.name if user.package else "Custom",
        "under_attack": attack_info["under_attack"],
        "under_attack_reason": attack_info["reason"],
        "under_attack_since": attack_info["activated_at"],
        "breakdown": breakdown,
        "live": live,
        "databases": db_items,
        "websites": site_items,
    }


@router.post("/users/{user_id}/attack-mode")
def toggle_user_attack_mode(
    user_id: int,
    payload: UserAttackModeToggle,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role == Role.reseller and user.reseller_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    reason = payload.reason or f"MANUAL: Blast-Radius Isolation Shield enabled by {current_user.username}"
    state = resource_monitor.set_user_under_attack_mode(db, user.id, payload.enabled, reason=reason)
    log_action(
        db,
        current_user.id,
        "enable_attack_shield" if payload.enabled else "disable_attack_shield",
        user.username,
        request=request,
    )
    return {
        "ok": True,
        "user_id": user.id,
        "username": user.username,
        "under_attack": bool(state.get("enabled")),
        "reason": state.get("reason"),
    }


@router.post("/users/{user_id}/kill-slow-queries")
def kill_user_slow_queries_endpoint(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role == Role.reseller and user.reseller_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    killed = resource_monitor.snipe_user_slow_queries(db, user.id, max_time_sec=1)
    log_action(db, current_user.id, "kill_user_slow_queries", user.username, request=request)
    return {
        "ok": True,
        "user_id": user.id,
        "username": user.username,
        "killed_count": len(killed),
        "killed_queries": killed,
    }


@router.post("/users/{user_id}/limits")
def update_user_resource_limits(
    user_id: int,
    payload: UserResourceLimitsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    user = db.query(User).options(selectinload(User.package)).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role == Role.reseller and user.reseller_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if payload.package_id is not None:
        pkg = db.query(UserPackage).filter(UserPackage.id == payload.package_id).first()
        if not pkg:
            raise HTTPException(status_code=404, detail="Package not found")
        user.package_id = pkg.id
        user.package = pkg

    if payload.sync_from_package and user.package:
        pkg = user.package
        user.website_limit = pkg.website_limit
        user.storage_limit_mb = pkg.storage_limit_mb
        user.database_limit = getattr(pkg, "database_limit", 5)
        user.bandwidth_limit_mb = getattr(pkg, "bandwidth_limit_mb", 0)
        user.inode_limit = getattr(pkg, "inode_limit", 0)
        user.cpu_quota_percent = getattr(pkg, "cpu_quota_percent", 100)
        user.io_read_mbps = getattr(pkg, "io_read_mbps", 0)
        user.io_write_mbps = getattr(pkg, "io_write_mbps", 0)
        user.iops_read_max = getattr(pkg, "iops_read_max", 0)
        user.iops_write_max = getattr(pkg, "iops_write_max", 0)
        user.memory_high_mb = getattr(pkg, "memory_high_mb", 1024)
        user.memory_max_mb = getattr(pkg, "memory_max_mb", 2048)
        user.tasks_max = getattr(pkg, "tasks_max", 512)
        user.mysql_storage_limit_mb = getattr(pkg, "mysql_storage_limit_mb", 0)
        user.mysql_max_db_size_mb = getattr(pkg, "mysql_max_db_size_mb", 0)
        user.mysql_max_queries_per_hour = getattr(pkg, "mysql_max_queries_per_hour", 0)
        user.mysql_max_updates_per_hour = getattr(pkg, "mysql_max_updates_per_hour", 0)
        user.mysql_max_connections_per_hour = getattr(pkg, "mysql_max_connections_per_hour", 0)
    else:
        for field in [
            "storage_limit_mb",
            "bandwidth_limit_mb",
            "inode_limit",
            "database_limit",
            "cpu_quota_percent",
            "io_read_mbps",
            "io_write_mbps",
            "iops_read_max",
            "iops_write_max",
            "memory_high_mb",
            "memory_max_mb",
            "tasks_max",
            "mysql_storage_limit_mb",
            "mysql_max_db_size_mb",
            "mysql_max_queries_per_hour",
            "mysql_max_updates_per_hour",
            "mysql_max_connections_per_hour",
        ]:
            val = getattr(payload, field, None)
            if val is not None:
                setattr(user, field, val)

    db.commit()
    db.refresh(user)
    resource_monitor.apply_user_cgroup_limits_async(user.id)
    resource_monitor.collect_and_enforce_mysql_quotas(db)
    log_action(db, current_user.id, "update_user_resource_limits", user.username, request=request)
    return {"ok": True, "message": f"Updated resource limits for {user.username}"}


@router.post("/databases/{database_id}/write-lock")
def toggle_database_write_lock(
    database_id: int,
    payload: DatabaseWriteLockToggle,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    owner = db.query(User).filter(User.id == item.owner_id).first()
    if current_user.role == Role.reseller and (not owner or owner.reseller_id != current_user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        db_backends.backend_for(item).set_write_lock(item.db_name, item.db_user, payload.locked)
    except RemoteDatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    item.write_locked = payload.locked
    item.write_locked_reason = (
        (payload.reason or f"MANUAL: Locked by {current_user.username}")
        if payload.locked
        else None
    )
    db.commit()
    log_action(
        db,
        current_user.id,
        "db_write_lock" if payload.locked else "db_write_unlock",
        item.db_name,
        request=request,
    )
    return {
        "ok": True,
        "id": item.id,
        "db_name": item.db_name,
        "write_locked": item.write_locked,
        "write_locked_reason": item.write_locked_reason,
    }


@router.get("/databases/{database_id}/tables")
def inspect_database_tables(
    database_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    if item.owner_id != current_user.id:
        ensure_role(current_user.role, Role.reseller)
        owner = db.query(User).filter(User.id == item.owner_id).first()
        if current_user.role == Role.reseller and (not owner or owner.reseller_id != current_user.id):
            raise HTTPException(status_code=403, detail="Access denied")

    try:
        tables = db_backends.backend_for(item).tables_breakdown(item.db_name)
    except RemoteDatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "id": item.id,
        "db_name": item.db_name,
        "size_bytes": max(0, int(getattr(item, "size_bytes", 0) or resource_monitor.cached_db_size_bytes(item.db_name))),
        "write_locked": bool(getattr(item, "write_locked", False)),
        "write_locked_reason": getattr(item, "write_locked_reason", None),
        "tables": tables,
    }


@router.post("/databases/{database_id}/optimize")
def optimize_database_endpoint(
    database_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    if item.owner_id != current_user.id:
        ensure_role(current_user.role, Role.reseller)
        owner = db.query(User).filter(User.id == item.owner_id).first()
        if current_user.role == Role.reseller and (not owner or owner.reseller_id != current_user.id):
            raise HTTPException(status_code=403, detail="Access denied")

    try:
        result = db_backends.backend_for(item).optimize(item.db_name)
    except RemoteDatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    resource_monitor.collect_and_enforce_mysql_quotas(db)
    storage_quota.forget_user_storage(item.owner_id)
    log_action(db, current_user.id, "optimize_database", item.db_name, request=request)
    return {
        "ok": True,
        "db_name": item.db_name,
        "optimized_tables": result.get("optimized_tables", 0),
        "size_bytes": resource_monitor.cached_db_size_bytes(item.db_name),
    }


@router.post("/refresh")
def force_refresh_telemetry(
    refresh_disk: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    resource_monitor.refresh_all_telemetry(db, refresh_disk=refresh_disk)
    return {"ok": True}


@router.get("/bottlenecks")
def get_slow_bottlenecks(
    limit: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    allowed_ids: set[int] | None = None
    if current_user.role == Role.reseller:
        managed = db.query(User.id).filter(User.reseller_id == current_user.id).all()
        allowed_ids = {r[0] for r in managed} | {current_user.id}
    return resource_monitor.inspect_slow_bottlenecks(
        db,
        allowed_user_ids=allowed_ids,
        limit=max(5, min(100, int(limit or 30))),
    )


@router.get("/disk-quota")
def get_disk_quota_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    current = disk_quota.status()
    # Kernel quota turns up after the remount/reboot that quota-enable asked
    # for; that is when every account's limit has to be set once.
    current["accounts_queued"] = disk_quota.reconcile_account_limits(db, current)
    return current


@router.post("/disk-quota/enable")
def enable_disk_quota(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        current = disk_quota.enable()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current["accounts_queued"] = disk_quota.reconcile_account_limits(db, current)
    log_action(db, current_user.id, "enable_kernel_disk_quota", current.get("mount") or "/home",
               detail=f"state={current.get('state')}", request=request)
    return current


@router.get("/domain-traffic")
def get_domain_traffic_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    allowed_ids: set[int] | None = None
    if current_user.role == Role.reseller:
        managed = db.query(User.id).filter(User.reseller_id == current_user.id).all()
        allowed_ids = {r[0] for r in managed} | {current_user.id}
    elif current_user.role != Role.admin:
        allowed_ids = {current_user.id}
    return resource_monitor.collect_domain_traffic_analytics(
        db,
        allowed_user_ids=allowed_ids,
    )

