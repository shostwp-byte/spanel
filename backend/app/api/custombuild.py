import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.core.version import APP_VERSION
from app.models.entities import User, Website
from app.services import addons as addons_service
from app.services import php as php_service
from app.services import site_apps as site_apps_service
from app.services import site_users
from app.services import system as system_service
from app.services import updates as updates_service

router = APIRouter(prefix="/custombuild", tags=["custombuild"])

_BUILD_LOGS: List[Dict[str, str]] = []


def _append_log(title: str, output: str, status: str = "ok") -> Dict[str, str]:
    entry = {
        "id": f"cb-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "status": status,
        "output": (output or "").strip() or "(completed with no output)",
    }
    _BUILD_LOGS.insert(0, entry)
    del _BUILD_LOGS[30:]
    return entry


def _cmd_version(cmd: List[str], pattern: Optional[str] = None) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
        raw = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
        if not raw:
            return ""
        if pattern:
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                return m.group(1)
        return raw.splitlines()[0].strip()[:60]
    except Exception:
        return ""


def _service_running(unit: str) -> bool:
    try:
        res = system_service.service_action(unit, "status")
        text = f"{res.stdout or ''} {res.stderr or ''}".lower()
        return "active (running)" in text
    except Exception:
        return False


class CustomBuildActionPayload(BaseModel):
    action: str
    target: str
    extra: Optional[str] = None


@router.get("/status")
def get_custombuild_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)

    installed_php = php_service.list_installed_php()
    supported_php = list(php_service.SUPPORTED_PHP_VERSIONS)
    php_ext_data = php_service.list_extensions()

    websites = db.query(Website).all()
    php_usage: Dict[str, int] = {}
    for site in websites:
        ver = (site.php_version or "8.4").strip()
        php_usage[ver] = php_usage.get(ver, 0) + 1

    try:
        runtimes = site_apps_service.runtime_status()
    except Exception:
        runtimes = {"node": {"installed": [], "supported": [18, 20, 22]}, "docker": {"installed": False, "version": ""}}

    try:
        addons_state = addons_service.state(can_manage=True)
        addon_items = addons_state.get("items", [])
    except Exception:
        addon_items = []

    nginx_ver = _cmd_version(["nginx", "-v"], r"nginx/([0-9.]+)") or "1.24+"
    mariadb_ver = _cmd_version(["mariadb", "--version"], r"([0-9]+\.[0-9]+\.[0-9]+)-MariaDB") or "10.11+"
    redis_ver = _cmd_version(["redis-server", "--version"], r"v=([0-9.]+)") or "7.0+"
    ufw_ver = _cmd_version(["ufw", "--version"], r"ufw\s+([0-9.]+)") or "0.36+"
    fail2ban_ver = _cmd_version(["fail2ban-client", "--version"], r"Fail2Ban\s+v?([0-9.]+)") or "1.0+"
    clamav_ver = _cmd_version(["clamscan", "--version"], r"ClamAV\s+([0-9.]+)") or "1.0+"

    core_packages = [
        {
            "id": "nginx",
            "name": "Nginx Web Server",
            "category": "Web Server",
            "version": nginx_ver,
            "installed": True,
            "running": _service_running("nginx"),
            "service_unit": "nginx",
            "description": "High-performance HTTP/2 & HTTP/3 reverse proxy and web server with FastCGI cache.",
            "can_restart": True,
            "can_rebuild_conf": True,
        },
        {
            "id": "mariadb",
            "name": "MariaDB Server",
            "category": "Database",
            "version": mariadb_ver,
            "installed": True,
            "running": _service_running("mariadb"),
            "service_unit": "mariadb",
            "description": "Relational database server powering customer MySQL databases.",
            "can_restart": True,
            "can_rebuild_conf": False,
        },
        {
            "id": "redis",
            "name": "Redis Object Cache",
            "category": "Cache & Queue",
            "version": redis_ver,
            "installed": True,
            "running": _service_running("redis-server"),
            "service_unit": "redis-server",
            "description": "In-memory key-value store for WordPress object caching and session storage.",
            "can_restart": True,
            "can_rebuild_conf": False,
        },
        {
            "id": "fail2ban",
            "name": "Fail2Ban Intrusion Prevention",
            "category": "Security",
            "version": fail2ban_ver,
            "installed": True,
            "running": _service_running("fail2ban"),
            "service_unit": "fail2ban",
            "description": "Scans SSH, Nginx, Dovecot, and Postfix logs to ban brute-force attackers.",
            "can_restart": True,
            "can_rebuild_conf": False,
        },
        {
            "id": "ufw",
            "name": "UFW / iptables Firewall",
            "category": "Security",
            "version": ufw_ver,
            "installed": True,
            "running": True,
            "service_unit": "",
            "description": "Host packet filtering firewall managed by SPanel Security & Firewall.",
            "can_restart": False,
            "can_rebuild_conf": False,
        },
        {
            "id": "clamav",
            "name": "ClamAV Malware Engine",
            "category": "Security",
            "version": clamav_ver,
            "installed": any(a.get("slug") == "malware" and a.get("installed") for a in addon_items),
            "running": _service_running("clamav-daemon"),
            "service_unit": "clamav-daemon",
            "description": "Antivirus and malware signature scanner for website files and uploads.",
            "can_restart": False,
            "can_rebuild_conf": False,
            "addon_slug": "malware",
        },
    ]

    try:
        php_smart = php_service.check_php_updates(force_refresh=False)
    except Exception:
        php_smart = {}
    smart_by_branch = {r.get("branch"): r for r in (php_smart.get("versions") or []) if r.get("branch")}

    account_masters = site_users.installed_user_php_fpm_units()
    php_matrix = []
    for ver in supported_php:
        is_inst = ver in installed_php
        unit = f"php{ver}-fpm"
        s_row = smart_by_branch.get(ver) or {}
        php_matrix.append({
            "version": ver,
            "installed": is_inst,
            "running": _service_running(unit) if is_inst else False,
            "service_unit": unit,
            # Sites run on their account's own master (spanel-php<ver>-fpm@<user>);
            # the distro unit above serves phpMyAdmin and the panel tools.
            "account_masters": sum(1 for u in account_masters if u.startswith(f"spanel-php{ver}-fpm@")),
            "sites_count": php_usage.get(ver, 0),
            "is_default": ver == "8.4",
            "current_full_version": s_row.get("current_version") or (ver if is_inst else ""),
            "latest_full_version": s_row.get("latest_version") or "",
            "update_available": bool(s_row.get("update_available", False)),
            "upgradable_count": int(s_row.get("upgradable_count") or 0),
            "hardening": s_row.get("hardening") or {"protected": False, "mode": "off"},
        })

    return {
        "panel_version": APP_VERSION,
        "core_packages": core_packages,
        "php_versions": php_matrix,
        "php_smart_guard": php_smart,
        "php_extensions": php_ext_data,
        "runtimes": runtimes,
        "addons": addon_items,
        "logs": _BUILD_LOGS,
    }


@router.post("/action")
def run_custombuild_action(
    payload: CustomBuildActionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    action = (payload.action or "").strip().lower()
    target = (payload.target or "").strip()

    try:
        if action == "install_php":
            res = php_service.install_php(target)
            out = res.get("output") or f"PHP {target} installed."
            entry = _append_log(f"Install PHP {target}", out, "ok")
            return {"message": f"PHP {target} installed successfully.", "log": entry}

        if action == "uninstall_php":
            if target == "8.4":
                raise ValueError("Cannot uninstall the default PHP version (8.4).")
            sites_using = db.query(Website).filter(Website.php_version == target).count()
            if sites_using > 0:
                raise ValueError(f"Cannot uninstall PHP {target}: {sites_using} website(s) are currently assigned to it.")
            res = php_service.uninstall_php(target)
            out = res.get("output") or f"PHP {target} uninstalled."
            entry = _append_log(f"Uninstall PHP {target}", out, "ok")
            return {"message": f"PHP {target} uninstalled successfully.", "log": entry}

        if action == "install_php_ext":
            if not payload.extra:
                raise ValueError("Extension name is required in 'extra'")
            out = php_service.install_extension(target, payload.extra)
            entry = _append_log(f"Install php{target}-{payload.extra}", out, "ok")
            return {"message": f"Installed php{target}-{payload.extra}.", "log": entry}

        if action == "retune_php_pools":
            from app.services import php_tune
            res = php_tune.retune_pools()
            out = f"Retuned {res.get('pools_written', 0)} PHP-FPM pool(s)."
            entry = _append_log("Rebuild & Retune PHP-FPM Pools", out, "ok")
            return {"message": out, "log": entry}

        if action == "php_safe_upgrade":
            versions = [target] if target and target != "all" else None
            res = php_service.upgrade_php_packages(versions=versions, trigger="manual")
            out = res.get("summary") or "PHP security packages upgraded safely."
            entry = _append_log(f"Smart PHP Patch ({target or 'All'})", f"{out}\n{res.get('output', '')}".strip(), "ok")
            return {"message": out, "log": entry, "php_smart_guard": res.get("php_smart_guard")}

        if action == "php_security_harden":
            versions = [target] if target and target != "all" else None
            mode = (payload.extra or "strict").strip().lower()
            res = php_service.apply_php2shell_hardening(versions=versions, mode=mode)
            out = f"Applied Anti-PHP2SHELL hardening ({mode}) to {', '.join(versions or php_service.list_installed_php())}."
            entry = _append_log(f"Anti-PHP2SHELL Hardening ({mode})", out, "ok")
            return {"message": out, "log": entry, "php_smart_guard": res.get("php_smart_guard")}

        if action == "service_restart":
            res = system_service.service_action(target, "restart")
            out = f"{res.stdout or ''}\n{res.stderr or ''}".strip() or f"Service {target} restarted."
            status_str = "ok" if res.returncode == 0 else "error"
            entry = _append_log(f"Restart {target}", out, status_str)
            if res.returncode != 0:
                raise RuntimeError(out)
            return {"message": f"Service {target} restarted.", "log": entry}

        if action == "rebuild_nginx":
            from app.services import nginx as nginx_service
            websites = db.query(Website).all()
            rebuilt = 0
            for site in websites:
                try:
                    nginx_service.rewrite_vhost(site)
                    rebuilt += 1
                except Exception:
                    pass
            res = system_service.service_action("nginx", "restart")
            out = f"Verified/rebuilt {rebuilt} vhost configuration(s) and reloaded Nginx."
            entry = _append_log("Rebuild Nginx Vhosts", out, "ok" if res.returncode == 0 else "error")
            return {"message": out, "log": entry}

        if action == "install_addon":
            res = addons_service.install(target)
            out = res.get("output") or res.get("next_step") or f"Addon {target} installed."
            entry = _append_log(f"Install Addon: {target}", out, "ok")
            return {"message": f"Addon '{target}' installed.", "log": entry}

        if action == "uninstall_addon":
            res = addons_service.uninstall(target)
            out = res.get("output") or res.get("kept") or f"Addon {target} removed."
            entry = _append_log(f"Remove Addon: {target}", out, "ok")
            return {"message": f"Addon '{target}' removed.", "log": entry}

        if action == "install_docker":
            res = site_apps_service.install_docker()
            out = res.get("output") or "Docker runtime installed."
            entry = _append_log("Install Docker Engine", out, "ok")
            return {"message": "Docker runtime installed.", "log": entry}

        if action == "install_node":
            res = site_apps_service.install_node(int(target))
            out = res.get("output") or f"Node.js {target} LTS installed."
            entry = _append_log(f"Install Node.js {target}", out, "ok")
            return {"message": f"Node.js {target} installed.", "log": entry}

        if action == "os_update":
            res = updates_service.run_os_updates()
            out = f"{res.stdout or ''}\n{res.stderr or ''}".strip()
            entry = _append_log("Update System Packages (apt)", out, "ok" if res.returncode == 0 else "error")
            return {"message": "System package update finished.", "log": entry}

        raise ValueError(f"Unsupported CustomBuild action: {action}")
    except ValueError as exc:
        _append_log(f"Failed: {action} ({target})", str(exc), "error")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _append_log(f"Error: {action} ({target})", str(exc), "error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
