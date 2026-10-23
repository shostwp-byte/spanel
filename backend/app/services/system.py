import datetime as dt
import json
import os
import shutil
import time
from pathlib import Path

from app.core.config import settings
from app.services.shell import shell

SELF_HEALING_FILE = Path("/var/lib/spanel/self-healing.json")
FALLBACK_SELF_HEALING_FILE = Path(__file__).resolve().parent.parent.parent / ".spanel_self_healing.json"

BASE_SERVICES = ("spanel-api", "nginx", "mariadb", "redis-server")
OPTIONAL_SERVICES = ("fail2ban", "clamav-daemon", "exim4", "dovecot", "rspamd", "pdns", "maldet", "docker")
PHP_VERSION_ORDER = ("5.6", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5")
PHP_ETC_DIR = Path("/etc/php")
SUPPORTED_ACTIONS = {"start", "stop", "restart", "reload", "status"}
PROTECTED_SERVICE_ACTIONS = {
    ("spanel-api", "stop"): "Stopping spanel-api from the panel would make the panel unavailable",
    ("redis-server", "stop"): "Stopping redis-server would disable production login rate limiting",
}


def _php_sort_key(service_name: str) -> tuple[int, list[int]]:
    version = service_name.removeprefix("php").removesuffix("-fpm")
    try:
        known_index = PHP_VERSION_ORDER.index(version)
    except ValueError:
        known_index = len(PHP_VERSION_ORDER)
    numeric = []
    for part in version.split("."):
        try:
            numeric.append(int(part))
        except ValueError:
            numeric.append(999)
    return known_index, numeric


def installed_php_services() -> list[str]:
    if not PHP_ETC_DIR.exists():
        return []
    services = []
    for version_dir in PHP_ETC_DIR.iterdir():
        version = version_dir.name
        if (version_dir / "fpm" / "php-fpm.conf").exists():
            services.append(f"php{version}-fpm")
    return sorted(set(services), key=_php_sort_key)


def list_services() -> list[str]:
    extra = []
    if shutil.which("fail2ban-client") or Path("/usr/bin/fail2ban-client").exists():
        extra.append("fail2ban")
    return [*BASE_SERVICES[:2], *installed_php_services(), *BASE_SERVICES[2:], *extra]


def service_action(name: str, action: str):
    allowed_units = set(list_services()) | set(OPTIONAL_SERVICES) | {f"php{v}-fpm" for v in PHP_VERSION_ORDER}
    if name not in allowed_units:
        raise ValueError("Unsupported service")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError("Unsupported action")
    if reason := PROTECTED_SERVICE_ACTIONS.get((name, action)):
        raise ValueError(reason)
    if action == "status":
        # Status is read-only; non-privileged user can call systemctl status fine.
        return shell.run(["systemctl", action, name], check=False)
    return shell.privileged(
        "systemctl",
        helper_args=[name, action],
        check=False,
        fallback=["systemctl", action, name],
    )


def system_info() -> dict:
    os_info = shell.run(["bash", "-lc", "cat /etc/os-release | head -20"], check=False)
    disk = shell.run(["df", "-h", "/"], check=False)
    memory = shell.run(["free", "-m"], check=False)
    return {"os": os_info.stdout, "disk": disk.stdout, "memory": memory.stdout}


def _read_cpu_times() -> dict:
    with open("/proc/stat", encoding="utf-8") as handle:
        fields = handle.readline().split()
    if not fields or fields[0] != "cpu":
        raise RuntimeError("Cannot read CPU counters")
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return {"idle": idle, "total": sum(values)}


def _cpu_percent(start: dict, end: dict) -> float:
    total_delta = end["total"] - start["total"]
    idle_delta = end["idle"] - start["idle"]
    if total_delta <= 0:
        return 0.0
    percent = (1 - (idle_delta / total_delta)) * 100
    return round(max(0.0, min(100.0, percent)), 1)


def _read_network_totals() -> dict:
    totals = {"rx": 0, "tx": 0}
    with open("/proc/net/dev", encoding="utf-8") as handle:
        for line in handle.readlines()[2:]:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = data.split()
            if len(fields) >= 16:
                totals["rx"] += int(fields[0])
                totals["tx"] += int(fields[8])
    return totals


def _memory_usage() -> dict:
    values = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            key, raw_value = line.split(":", 1)
            values[key] = int(raw_value.split()[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - available)
    percent = round((used / total) * 100, 1) if total else 0.0
    return {"total": total, "used": used, "available": available, "percent": percent}


def _disk_usage() -> dict:
    usage = shutil.disk_usage("/")
    percent = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
    return {"mount": "/", "total": usage.total, "used": usage.used, "free": usage.free, "percent": percent}


def resource_usage() -> dict:
    sample_seconds = 0.2
    cpu_start = _read_cpu_times()
    network_start = _read_network_totals()
    time.sleep(sample_seconds)
    cpu_end = _read_cpu_times()
    network_end = _read_network_totals()
    rx_delta = max(0, network_end["rx"] - network_start["rx"])
    tx_delta = max(0, network_end["tx"] - network_start["tx"])
    try:
        load_average = [round(value, 2) for value in os.getloadavg()]
    except OSError:
        load_average = []
    return {
        "cpu": {"percent": _cpu_percent(cpu_start, cpu_end), "load": load_average, "cores": os.cpu_count() or 1},
        "memory": _memory_usage(),
        "disk": _disk_usage(),
        "network": {
            "rx_per_sec": round(rx_delta / sample_seconds),
            "tx_per_sec": round(tx_delta / sample_seconds),
            "rx_total": network_end["rx"],
            "tx_total": network_end["tx"],
        },
        "sample_seconds": sample_seconds,
    }


def install_wordpress_stack():
    raise PermissionError(
        "Installing the system stack from the panel is disabled. "
        "Run installer/install.sh on the server instead."
    )


def _self_healing_path() -> Path:
    try:
        if SELF_HEALING_FILE.parent.exists() and os.access(SELF_HEALING_FILE.parent, os.W_OK):
            return SELF_HEALING_FILE
    except OSError:
        pass
    return FALLBACK_SELF_HEALING_FILE


def _load_self_healing_data() -> dict:
    path = _self_healing_path()
    default_data = {
        "enabled": True,
        "validate_config_before_restart": True,
        "auto_db_backup_enabled": True,
        "max_restarts_per_15m": 3,
        "max_restarts_per_hour": 5,
        "systemd_timer_interval_sec": 120,
        "incidents_table": "watchdog_incidents",
        "last_check_at": None,
        "last_healed_at": None,
        "total_healed_count": 0,
        "service_states": {},
        "restart_history": {},
        "events": [],
    }
    if not path.exists():
        return default_data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            default_data.update(raw)
    except Exception:
        pass
    return default_data


def _record_watchdog_incident_db(event: dict) -> None:
    """Persist a watchdog incident into the SQLite `watchdog_incidents` table."""
    try:
        from app.core.database import SessionLocal
        from app.models.entities import WatchdogIncident

        with SessionLocal() as db:
            row = WatchdogIncident(
                service=str(event.get("service") or "")[:64],
                previous_state=str(event.get("previous_state") or "failed")[:32],
                action=str(event.get("action") or "auto_restart")[:64],
                recovered=bool(event.get("recovered", False)),
                reason=str(event.get("reason") or "background")[:64],
                detail=str(event.get("detail") or ""),
            )
            db.add(row)
            db.commit()
    except Exception:
        pass


def _save_self_healing_data(data: dict) -> None:
    path = _self_healing_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _validate_service_config(name: str) -> tuple[bool, str]:
    if os.name == "nt" or settings.command_dry_run:
        return True, "config syntax ok (dry-run)"
    if name == "nginx" and shutil.which("nginx"):
        res = shell.run(["nginx", "-t"], check=False)
        if res.returncode != 0:
            return False, (res.stderr or res.stdout or "nginx -t failed")[:240]
        return True, "nginx -t syntax ok"
    if name.startswith("php") and name.endswith("-fpm"):
        ver = name.removeprefix("php").removesuffix("-fpm")
        fpm_bin = f"/usr/sbin/php-fpm{ver}"
        if Path(fpm_bin).exists():
            res = shell.run([fpm_bin, "-t"], check=False)
            if res.returncode != 0:
                return False, (res.stderr or res.stdout or f"php-fpm{ver} -t failed")[:240]
            return True, f"php-fpm{ver} -t syntax ok"
    return True, "unit ready"


def get_self_healing_status() -> dict:
    data = _load_self_healing_data()
    services = [s for s in list_services() if s != "spanel-api"]
    if not data.get("service_states"):
        states = {}
        if os.name != "nt" and not settings.command_dry_run and services:
            res = shell.run(["systemctl", "is-active", *services], check=False)
            answers = (res.stdout or "").split()[: len(services)]
            answers += ["unknown"] * (len(services) - len(answers))
            for s, ans in zip(services, answers):
                states[s] = ans
        else:
            for s in services:
                states[s] = "active"
        data["service_states"] = states
    data["monitored_services"] = services
    return data


def update_self_healing_config(payload: dict) -> dict:
    data = _load_self_healing_data()
    if "enabled" in payload:
        data["enabled"] = bool(payload["enabled"])
    if "validate_config_before_restart" in payload:
        data["validate_config_before_restart"] = bool(payload["validate_config_before_restart"])
    if "auto_db_backup_enabled" in payload:
        data["auto_db_backup_enabled"] = bool(payload["auto_db_backup_enabled"])
    if "max_restarts_per_15m" in payload:
        data["max_restarts_per_15m"] = max(1, min(10, int(payload["max_restarts_per_15m"])))
    if "max_restarts_per_hour" in payload:
        data["max_restarts_per_hour"] = max(1, min(20, int(payload["max_restarts_per_hour"])))
    _save_self_healing_data(data)
    return get_self_healing_status()


def clear_self_healing_logs() -> dict:
    data = _load_self_healing_data()
    data["events"] = []
    _save_self_healing_data(data)
    try:
        from app.core.database import SessionLocal
        from app.models.entities import WatchdogIncident

        with SessionLocal() as db:
            db.query(WatchdogIncident).delete()
            db.commit()
    except Exception:
        pass
    return get_self_healing_status()


def run_self_healing_check(reason: str = "manual") -> dict:
    data = _load_self_healing_data()
    now_utc = dt.datetime.now(dt.timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = time.time()

    services = [s for s in list_services() if s != "spanel-api"]
    if os.name != "nt" and not settings.command_dry_run and services:
        res = shell.run(["systemctl", "is-active", *services], check=False)
        answers = (res.stdout or "").split()[: len(services)]
        answers += ["unknown"] * (len(services) - len(answers))
    else:
        answers = ["active"] * len(services)

    states = {}
    healed_now = []
    failed_now = []
    restart_history = dict(data.get("restart_history") or {})
    max_per_15m = int(data.get("max_restarts_per_15m") or 3)
    max_per_hour = int(data.get("max_restarts_per_hour") or 5)
    should_heal = bool(data.get("enabled", True)) or reason == "manual"

    for name, answer in zip(services, answers):
        if answer == "active":
            states[name] = "active"
            continue

        states[name] = answer
        if not should_heal:
            continue

        # Prune restart timestamps older than 1 hour and evaluate 15-min anti-flap window
        recent_ts = [float(t) for t in (restart_history.get(name) or []) if now_ts - float(t) < 3600]
        recent_15m = [t for t in recent_ts if now_ts - t < 900]
        if (len(recent_15m) >= max_per_15m or len(recent_ts) >= max_per_hour) and reason != "manual":
            flap_msg = (
                f"Anti-flap triggered: {len(recent_15m)}/{max_per_15m} restarts in 15m "
                f"({len(recent_ts)}/{max_per_hour} in 1h); manual inspection required."
            )
            event = {
                "timestamp": now_iso,
                "service": name,
                "previous_state": answer,
                "action": "circuit_breaker_tripped",
                "recovered": False,
                "reason": reason,
                "detail": flap_msg,
            }
            data.setdefault("events", []).insert(0, event)
            _record_watchdog_incident_db(event)
            failed_now.append(name)
            continue

        if data.get("validate_config_before_restart", True):
            cfg_ok, cfg_detail = _validate_service_config(name)
            if not cfg_ok:
                event = {
                    "timestamp": now_iso,
                    "service": name,
                    "previous_state": answer,
                    "action": "config_check_failed",
                    "recovered": False,
                    "reason": reason,
                    "detail": f"Pre-restart syntax check failed: {cfg_detail}",
                }
                data.setdefault("events", []).insert(0, event)
                _record_watchdog_incident_db(event)
                failed_now.append(name)
                continue
        else:
            cfg_detail = "syntax check skipped"

        try:
            act_res = service_action(name, "restart")
            recent_ts.append(now_ts)
            restart_history[name] = recent_ts
            verify_res = shell.run(["systemctl", "is-active", name], check=False)
            recovered = (verify_res.stdout or "").strip() == "active"
            states[name] = "active" if recovered else "failed"
            detail_msg = f"Auto-restarted ({cfg_detail}) -> {'ACTIVE' if recovered else 'FAILED'}"
            if not recovered and act_res.stderr:
                detail_msg += f" | {act_res.stderr.strip()[:160]}"
        except Exception as exc:
            recovered = False
            detail_msg = f"Exception during restart: {exc}"

        event = {
            "timestamp": now_iso,
            "service": name,
            "previous_state": answer,
            "action": "auto_restart",
            "recovered": recovered,
            "reason": reason,
            "detail": detail_msg,
        }
        data.setdefault("events", []).insert(0, event)
        _record_watchdog_incident_db(event)
        if recovered:
            healed_now.append(name)
            data["total_healed_count"] = int(data.get("total_healed_count") or 0) + 1
            data["last_healed_at"] = now_iso
        else:
            failed_now.append(name)

    # Periodic SQLite Hot Backup check (every 6 hours)
    if data.get("auto_db_backup_enabled", True):
        try:
            from app.core.database import get_sqlite_health, run_sqlite_hot_backup

            db_health = get_sqlite_health()
            need_backup = False
            if db_health.get("backup_count", 0) == 0:
                need_backup = True
            elif db_health.get("last_backup_at"):
                try:
                    last_dt = dt.datetime.strptime(db_health["last_backup_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                    if (now_utc - last_dt).total_seconds() >= 21600:
                        need_backup = True
                except Exception:
                    pass
            if need_backup:
                run_sqlite_hot_backup(reason="watchdog")
        except Exception:
            pass

    data["last_check_at"] = now_iso
    data["service_states"] = states
    data["restart_history"] = restart_history
    data["events"] = (data.get("events") or [])[:100]
    _save_self_healing_data(data)

    if healed_now or failed_now:
        try:
            from app.services import notifications

            if healed_now:
                notifications.notify(
                    "service_down",
                    {"services": healed_now, "recovered": True, "self_healed": True},
                    admins=True,
                )
            if failed_now:
                notifications.notify(
                    "service_down",
                    {"services": failed_now, "recovered": False, "self_healed": False},
                    admins=True,
                )
        except Exception:
            pass

    result = get_self_healing_status()
    result["healed_now"] = healed_now
    result["failed_now"] = failed_now
    return result
