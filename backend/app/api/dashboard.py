"""The dashboard's status summary: one request, every part best effort.

The dashboard used to repeat the sidebar as three groups of links. It now
says what the sidebar cannot: how things stand. Each part is read on its own,
a part that fails is left out rather than failing the page, and nothing here
waits on the network - opening the dashboard must stay cheap.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, is_admin_role, normalize_role
from app.models.entities import BackupSchedule, DatabaseAccount, User, Website
from app.services import firewall, maldet, malware_scan, panel_settings, updates, waf
from app.services.shell import shell
from app.services.system import list_services

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)
UNSECURED_SHOWN = 5
# A scan that ran to the end. An interrupted or failed one says nothing about
# whether the files are clean, so the card reports the last one that finished.
FINISHED_SCANS = {"done", "infected"}


def _safe(read: Callable[[], Any], default: Any = None) -> Any:
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - one broken probe must not blank the dashboard
        logger.warning("dashboard: %s failed: %s", getattr(read, "__name__", "probe"), exc)
        return default


def _services() -> dict:
    # The units the Services page lists. PHP-FPM pools run all the time here,
    # unlike OpenLiteSpeed's on-demand lsphp, so a stopped one is news.
    names = list_services()
    result = shell.run(["systemctl", "is-active", *names], check=False)
    states = (result.stdout or "").split()[: len(names)]
    states += ["unknown"] * (len(names) - len(states))
    stopped = [name for name, state in zip(names, states, strict=True) if state != "active"]
    return {"total": len(names), "running": len(names) - len(stopped), "stopped": stopped}


def _waf_engine() -> str:
    output = waf.status().stdout or ""
    if "not-installed" in output:
        return "off"
    return "on" if "installed" in output else "unknown"


def _malware() -> dict:
    jobs = panel_settings.list_malware_scan_jobs(limit=20)
    last = next((job for job in jobs if job.get("status") in FINISHED_SCANS), None)
    from app.services import addons

    return {
        "addon": addons.is_installed(addons.MALWARE),
        "installed": bool(maldet.installed() or malware_scan.clamav_installed()),
        "running": any(job.get("status") in {"queued", "running"} for job in jobs),
        "last_scan": {
            "status": last.get("status"),
            "infected": int(last.get("infected") or 0),
            "finished_at": last.get("finished_at") or last.get("updated_at") or last.get("started_at"),
        } if last else None,
    }


def _backups(db: Session) -> dict:
    schedules = db.query(BackupSchedule).filter(BackupSchedule.is_active.is_(True)).all()
    ran = [s for s in schedules if s.last_run_at]
    latest = max(ran, key=lambda s: s.last_run_at) if ran else None
    failed = [s.id for s in schedules if (s.last_status or "").lower() in {"error", "failed"}]
    return {
        "schedules": len(schedules),
        "last_run_at": latest.last_run_at.isoformat() if latest else None,
        "last_status": latest.last_status if latest else None,
        "failed": len(failed),
    }


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    admin = is_admin_role(current_user.role)
    site_query = db.query(Website)
    db_query = db.query(DatabaseAccount)
    if not admin:
        if normalize_role(current_user.role) == Role.reseller:
            sub_ids = db.query(User.id).filter(User.reseller_id == current_user.id)
            site_query = site_query.filter((Website.owner_id == current_user.id) | (Website.owner_id.in_(sub_ids)))
            db_query = db_query.filter((DatabaseAccount.owner_id == current_user.id) | (DatabaseAccount.owner_id.in_(sub_ids)))
        else:
            site_query = site_query.filter(Website.owner_id == current_user.id)
            db_query = db_query.filter(DatabaseAccount.owner_id == current_user.id)
    sites = site_query.all()
    unsecured = sorted(site.domain for site in sites if not site.ssl_enabled)
    summary: dict[str, Any] = {
        "websites": {
            "total": len(sites),
            "suspended": sum(1 for site in sites if site.status == "suspended"),
            "waf_on": sum(1 for site in sites if site.waf_enabled),
        },
        "ssl": {
            "total": len(sites),
            "secured": len(sites) - len(unsecured),
            "unsecured": unsecured[:UNSECURED_SHOWN],
            "unsecured_count": len(unsecured),
        },
        "databases": {"total": db_query.count()},
    }
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    own_sites = [site for site in sites if site.owner_id == current_user.id]
    own_unsecured = sorted(site.domain for site in own_sites if not site.ssl_enabled)
    summary["own_websites"] = {
        "total": len(own_sites),
        "suspended": sum(1 for site in own_sites if site.status == "suspended"),
        "waf_on": sum(1 for site in own_sites if site.waf_enabled),
    }
    summary["own_ssl"] = {
        "total": len(own_sites),
        "secured": len(own_sites) - len(own_unsecured),
        "unsecured": own_unsecured[:UNSECURED_SHOWN],
        "unsecured_count": len(own_unsecured),
    }

    def _tenants_summary() -> dict[str, Any]:
        from app.services import resource_monitor, storage_quota

        all_dbs = db_query.all()
        locked_dbs = [d.db_name for d in all_dbs if getattr(d, "write_locked", False)]
        own_locked_dbs = [
            d.db_name
            for d in all_dbs
            if d.owner_id == current_user.id and getattr(d, "write_locked", False)
        ]
        if admin:
            managed_users = db.query(User).filter(User.id != current_user.id).all()
        elif normalize_role(current_user.role) == Role.reseller:
            managed_users = db.query(User).filter(User.reseller_id == current_user.id).all()
        else:
            managed_users = []

        suspended_users = sum(1 for u in managed_users if not u.is_active)
        under_attack_users = []
        over_quota_users = []
        for u in managed_users:
            atk = resource_monitor.cached_user_attack_state(u.id)
            if atk.get("under_attack"):
                under_attack_users.append(u.username)
            limit_mb = int(getattr(u, "storage_limit_mb", 0) or 0)
            if limit_mb > 0:
                used_b = int(storage_quota.cached_storage_used_bytes(u.id) or 0)
                if used_b >= limit_mb * 1024 * 1024 * 0.9:
                    over_quota_users.append(u.username)

        own_atk = resource_monitor.cached_user_attack_state(current_user.id)
        reseller_user_limit = 0
        if current_user.reseller_package and getattr(current_user.reseller_package, "user_limit", 0):
            reseller_user_limit = int(current_user.reseller_package.user_limit)
        elif current_user.package and getattr(current_user.package, "reseller_user_limit", 0):
            reseller_user_limit = int(current_user.package.reseller_user_limit)

        return {
            "managed_users_total": len(managed_users),
            "reseller_user_limit": reseller_user_limit,
            "suspended_users": suspended_users,
            "under_attack_users": under_attack_users[:5],
            "under_attack_count": len(under_attack_users),
            "over_quota_users": over_quota_users[:5],
            "over_quota_count": len(over_quota_users),
            "locked_databases": locked_dbs[:5],
            "locked_databases_count": len(locked_dbs),
            "own_locked_databases_count": len(own_locked_dbs),
            "own_under_attack": bool(own_atk.get("under_attack")),
        }

    summary["tenants"] = _safe(_tenants_summary, {})
    if admin:
        summary["firewall"] = {"enabled": _safe(firewall.is_enabled)}
        summary["waf"] = {"engine": _safe(_waf_engine, "unknown")}
        summary["malware"] = _safe(_malware)
        summary["services"] = _safe(_services)
        summary["updates"] = _safe(updates.cached_release_summary)
        summary["backups"] = _safe(lambda: _backups(db))
    return summary
