import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.permissions import Role, is_admin_role
from app.models.entities import DatabaseAccount, User, UserPackage, Website
from app.services import mariadb, nginx, site_users, storage_quota
from app.services.shell import shell

logger = logging.getLogger("spanel.resource_monitor")

BYTES_PER_MB = 1024 * 1024
_STATE_FILE = Path("/var/lib/spanel/resource_telemetry.json")
_FALLBACK_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".spanel_resource_telemetry.json"

_lock = threading.Lock()
_daemon_started = False
_mysql_governor_initialized = False

# In-memory zero-wait caches
_db_sizes_bytes: dict[str, int] = {}
_user_mysql_bytes: dict[int, int] = {}
_user_live_metrics: dict[int, dict[str, Any]] = {}
_site_bandwidth_bytes: dict[int, int] = {}
_user_bandwidth_bytes: dict[int, int] = {}
_log_offsets: dict[str, dict[str, int | str]] = {}
_user_high_load_streaks: dict[int, int] = {}
_user_mysql_scrape_streaks: dict[int, int] = {}
_user_sniped_queries_count: dict[int, int] = {}
_user_under_attack_state: dict[int, dict[str, Any]] = {}
_prev_mysql_user_stats: dict[str, dict[str, Any]] = {}
_prev_mysql_stats_ts: float = 0.0
_db_limits_initialized: set[str] = set()
_last_fast_collect_ts: float = 0.0
_last_db_collect_ts: float = 0.0

UNDER_ATTACK_FLOOD_CONFIG = {
    "access_limit_requests": 25,
    "access_limit_window": 10,
    "access_limit_burst": 30,
    "connection_limit": 15,
}


def _state_file_path() -> Path:
    try:
        if _STATE_FILE.parent.exists() and os.access(_STATE_FILE.parent, os.W_OK):
            return _STATE_FILE
    except OSError:
        pass
    return _FALLBACK_STATE_FILE


def _load_persisted_state() -> None:
    global _db_sizes_bytes, _site_bandwidth_bytes, _user_bandwidth_bytes, _log_offsets, _user_under_attack_state
    path = _state_file_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        with _lock:
            for k, v in (raw.get("db_sizes_bytes") or {}).items():
                _db_sizes_bytes[str(k)] = int(v or 0)
            for k, v in (raw.get("site_bandwidth_bytes") or {}).items():
                _site_bandwidth_bytes[int(k)] = int(v or 0)
            for k, v in (raw.get("user_bandwidth_bytes") or {}).items():
                _user_bandwidth_bytes[int(k)] = int(v or 0)
            for k, v in (raw.get("user_under_attack_state") or {}).items():
                if isinstance(v, dict):
                    _user_under_attack_state[int(k)] = v
            _log_offsets = dict(raw.get("log_offsets") or {})
    except Exception:
        pass


def _save_persisted_state() -> None:
    path = _state_file_path()
    try:
        with _lock:
            payload = {
                "updated_at": datetime.utcnow().isoformat(),
                "db_sizes_bytes": _db_sizes_bytes,
                "site_bandwidth_bytes": {str(k): v for k, v in _site_bandwidth_bytes.items()},
                "user_bandwidth_bytes": {str(k): v for k, v in _user_bandwidth_bytes.items()},
                "user_under_attack_state": {str(k): v for k, v in _user_under_attack_state.items()},
                "log_offsets": _log_offsets,
            }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def cached_db_size_bytes(db_name: str) -> int:
    with _lock:
        return int(_db_sizes_bytes.get(db_name, 0))


def cached_user_mysql_bytes(user_id: int) -> int:
    with _lock:
        return int(_user_mysql_bytes.get(int(user_id), 0))


def cached_user_bandwidth_bytes(user_id: int) -> int:
    with _lock:
        return int(_user_bandwidth_bytes.get(int(user_id), 0))


def cached_site_bandwidth_bytes(site_id: int) -> int:
    with _lock:
        return int(_site_bandwidth_bytes.get(int(site_id), 0))


def cached_user_attack_state(user_id: int) -> dict[str, Any]:
    with _lock:
        st = _user_under_attack_state.get(int(user_id))
        streak = int(_user_high_load_streaks.get(int(user_id), 0))
        scrape_streak = int(_user_mysql_scrape_streaks.get(int(user_id), 0))
        if st and st.get("enabled"):
            return {
                "under_attack": True,
                "reason": st.get("reason") or "Active Blast-Radius Isolation Shield",
                "activated_at": st.get("activated_at"),
                "high_load_streak": max(streak, scrape_streak),
            }
    return {
        "under_attack": False,
        "reason": None,
        "activated_at": None,
        "high_load_streak": max(streak, scrape_streak),
    }


def cached_user_live_metrics(user_id: int) -> dict[str, Any]:
    with _lock:
        data = _user_live_metrics.get(int(user_id))
        if data:
            return dict(data)
    return {
        "cpu_percent": 0.0,
        "php_cpu_percent": 0.0,
        "mysql_cpu_percent": 0.0,
        "mysql_qps": 0.0,
        "mysql_rows_read_rate": 0,
        "mysql_sniped_queries": 0,
        "memory_used_bytes": 0,
        "tasks_count": 0,
        "io_read_bps": 0,
        "io_write_bps": 0,
        "processes": [],
    }


def snipe_user_slow_queries(db: Session, user_id: int, max_time_sec: int = 2) -> list[dict]:
    """Manually or automatically kill slow/heavy SELECT queries belonging to a user's database accounts."""
    dbs = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user_id).all()
    db_users = [d.db_user for d in dbs if d.db_user]
    if not db_users:
        return []
    killed = mariadb.snipe_slow_scraping_queries(db_users, max_time_sec=max_time_sec)
    if killed:
        with _lock:
            _user_sniped_queries_count[user_id] = int(_user_sniped_queries_count.get(user_id, 0)) + len(killed)
            if user_id in _user_live_metrics:
                _user_live_metrics[user_id]["mysql_sniped_queries"] = _user_sniped_queries_count[user_id]
    return killed


def apply_user_cgroup_limits(user: User) -> bool:
    """Apply Linux Cgroups v2 & PHP-FPM resource limits for a panel user."""
    if is_admin_role(user.role):
        return True
    try:
        linux_user = site_users.linux_user_for_panel_username(user.username)
    except ValueError:
        return False

    args = [
        linux_user,
        str(max(0, int(getattr(user, "cpu_quota_percent", 100) or 0))),
        str(max(0, int(getattr(user, "memory_high_mb", 1024) or 0))),
        str(max(0, int(getattr(user, "memory_max_mb", 2048) or 0))),
        str(max(0, int(getattr(user, "tasks_max", 512) or 0))),
        str(max(0, int(getattr(user, "io_read_mbps", 0) or 0))),
        str(max(0, int(getattr(user, "io_write_mbps", 0) or 0))),
        str(max(0, int(getattr(user, "iops_read_max", 0) or 0))),
        str(max(0, int(getattr(user, "iops_write_max", 0) or 0))),
        str(max(0, int(getattr(user, "storage_limit_mb", 0) or 0))),
    ]
    try:
        shell.privileged(
            "user-limits-apply",
            helper_args=args,
            check=False,
            timeout=30,
            fallback=["true"],
        )
        return True
    except Exception:
        logger.debug("Could not apply cgroup limits for %s", user.username, exc_info=True)
        return False


def apply_user_cgroup_limits_async(user_id: int) -> None:
    def _runner():
        try:
            with SessionLocal() as db:
                u = db.query(User).filter(User.id == user_id).first()
                if u:
                    apply_user_cgroup_limits(u)
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()


def set_user_under_attack_mode(
    db: Session,
    user_id: int,
    enabled: bool,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Activate or deactivate the 5-Layer Blast-Radius & MySQL Anti-Scraping Isolation Shield for a user.

    When enabled=True:
    1. Enables Nginx per-IP & per-Vhost rate/connection limiting + immediate JS Browser Challenge on scraping URLs.
    2. Tightens MariaDB MAX_USER_CONNECTIONS (8) and MAX_STATEMENT_TIME (3s) + snipes running slow queries.
    3. Re-enforces Cgroups v2 slice limits and binds all PHP-FPM workers of this user into user-<UID>.slice.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("User not found")

    sites = db.query(Website).filter(Website.owner_id == user.id).all()
    dbs = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).all()

    with _lock:
        existing_state = dict(_user_under_attack_state.get(user.id) or {})

    if enabled:
        prev_sites = existing_state.get("prev_sites") or {}
        for s in sites:
            if str(s.id) not in prev_sites:
                prev_sites[str(s.id)] = {
                    "http_flood_enabled": bool(getattr(s, "http_flood_enabled", False)),
                    "http_flood_config": getattr(s, "http_flood_config", "") or "",
                }
            s.http_flood_enabled = True
            s.http_flood_config = json.dumps(UNDER_ATTACK_FLOOD_CONFIG)
        db.commit()

        # Sync Nginx flood & anti-scraping zones and update each site's vhost
        try:
            all_sites = db.query(Website).all()
            nginx.sync_http_flood_zones(all_sites)
            with nginx.deferred_reload():
                for s in sites:
                    try:
                        nginx.update_http_flood_block(s.domain, True, UNDER_ATTACK_FLOOD_CONFIG)
                    except Exception:
                        pass
        except Exception:
            logger.debug("Could not apply Nginx flood shield for %s", user.username, exc_info=True)

        # Tighten MariaDB per-user connection & statement time caps (8 conns, 3s timeout) + snipe active slow queries
        db_users = []
        for d in dbs:
            db_users.append(d.db_user)
            try:
                mariadb.enforce_db_user_resource_limits(d.db_user, max_user_connections=8, max_statement_time=3)
            except Exception:
                pass
        if db_users:
            try:
                mariadb.snipe_slow_scraping_queries(db_users, max_time_sec=2)
            except Exception:
                pass

        apply_user_cgroup_limits(user)

        state_obj = {
            "enabled": True,
            "reason": reason or "MANUAL: Blast-Radius & MySQL Anti-Scraping Shield enabled",
            "activated_at": datetime.utcnow().isoformat(),
            "activated_ts": time.time(),
            "prev_sites": prev_sites,
        }
        with _lock:
            _user_under_attack_state[user.id] = state_obj
        _save_persisted_state()
        return state_obj
    else:
        prev_sites = existing_state.get("prev_sites") or {}
        for s in sites:
            saved = prev_sites.get(str(s.id))
            if saved is not None:
                s.http_flood_enabled = bool(saved.get("http_flood_enabled", False))
                s.http_flood_config = saved.get("http_flood_config", "") or ""
            else:
                s.http_flood_enabled = False
        db.commit()

        try:
            all_sites = db.query(Website).all()
            nginx.sync_http_flood_zones(all_sites)
            with nginx.deferred_reload():
                for s in sites:
                    try:
                        cfg = nginx.http_flood_config_for_website(s)
                        nginx.update_http_flood_block(s.domain, bool(s.http_flood_enabled), cfg)
                    except Exception:
                        pass
        except Exception:
            logger.debug("Could not restore Nginx flood config for %s", user.username, exc_info=True)

        for d in dbs:
            try:
                mariadb.enforce_db_user_resource_limits(d.db_user, max_user_connections=25, max_statement_time=15)
            except Exception:
                pass

        with _lock:
            _user_under_attack_state.pop(user.id, None)
            _user_high_load_streaks[user.id] = 0
            _user_mysql_scrape_streaks[user.id] = 0
        _save_persisted_state()
        return {"enabled": False, "reason": None, "activated_at": None}


def _query_mysql_sizes_map() -> dict[str, int]:
    """Collect physical byte size of every MySQL database in <10ms."""
    sizes: dict[str, int] = {}
    if settings.command_dry_run or os.name == "nt":
        return sizes

    # 1. Try fast directory stat via spanel-helper mysql-sizes-collect
    try:
        res = shell.privileged(
            "mysql-sizes-collect",
            helper_args=[],
            check=False,
            timeout=15,
            fallback=["true"],
        )
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                parts = line.strip().split("\t", 1)
                if len(parts) == 2 and parts[0].isdigit():
                    sizes[parts[1].strip()] = int(parts[0])
            if sizes:
                return sizes
    except Exception:
        pass

    # 2. Fallback: single batch query against information_schema.TABLES
    try:
        sql = (
            "SELECT TABLE_SCHEMA, IFNULL(SUM(DATA_LENGTH + INDEX_LENGTH), 0) "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') "
            "GROUP BY TABLE_SCHEMA;\n"
        )
        res = mariadb._run_sql(sql, check=False)
        for line in (res.stdout or "").splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                sizes[parts[0].strip()] = int(parts[1])
    except Exception:
        pass
    return sizes


def collect_and_enforce_mysql_quotas(db: Session) -> dict[int, int]:
    """Measure all user databases, update size_bytes, enforce per-user connection limits, and auto-enforce Read-Only write locks when over quota."""
    global _last_db_collect_ts
    sizes_map = _query_mysql_sizes_map()
    all_dbs = db.query(DatabaseAccount).all()
    users_by_id = {u.id: u for u in db.query(User).all()}

    user_totals: dict[int, int] = {}
    changed = False

    for db_acc in all_dbs:
        sz = sizes_map.get(db_acc.db_name, int(getattr(db_acc, "size_bytes", 0) or 0))
        if getattr(db_acc, "size_bytes", 0) != sz:
            db_acc.size_bytes = sz
            changed = True
        with _lock:
            _db_sizes_bytes[db_acc.db_name] = sz
            need_conn_limit = db_acc.db_user not in _db_limits_initialized
            if need_conn_limit:
                _db_limits_initialized.add(db_acc.db_user)
        if need_conn_limit:
            try:
                mariadb.enforce_db_user_resource_limits(db_acc.db_user, max_user_connections=25, max_statement_time=15)
            except Exception:
                pass
        user_totals[db_acc.owner_id] = user_totals.get(db_acc.owner_id, 0) + sz

    with _lock:
        _user_mysql_bytes.clear()
        _user_mysql_bytes.update(user_totals)
        _last_db_collect_ts = time.monotonic()

    # Evaluate per-user and per-DB MySQL quota enforcement
    dbs_by_owner: dict[int, list[DatabaseAccount]] = {}
    for db_acc in all_dbs:
        dbs_by_owner.setdefault(db_acc.owner_id, []).append(db_acc)

    for owner_id, owner_dbs in dbs_by_owner.items():
        owner = users_by_id.get(owner_id)
        if not owner or is_admin_role(owner.role):
            continue

        user_mysql_total = user_totals.get(owner_id, 0)
        mysql_limit_mb = int(getattr(owner, "mysql_storage_limit_mb", 0) or 0)
        mysql_limit_bytes = mysql_limit_mb * BYTES_PER_MB if mysql_limit_mb > 0 else 0

        max_single_db_mb = int(getattr(owner, "mysql_max_db_size_mb", 0) or 0)
        max_single_db_bytes = max_single_db_mb * BYTES_PER_MB if max_single_db_mb > 0 else 0

        storage_limit_mb = int(getattr(owner, "storage_limit_mb", 0) or 0)
        storage_limit_bytes = storage_limit_mb * BYTES_PER_MB if storage_limit_mb > 0 else 0

        cached_total_storage = storage_quota.cached_storage_used_bytes(owner.id)
        if cached_total_storage is None:
            cached_total_storage = user_mysql_total

        user_over_mysql_quota = mysql_limit_bytes > 0 and user_mysql_total > mysql_limit_bytes
        user_over_total_storage = storage_limit_bytes > 0 and cached_total_storage > storage_limit_bytes

        for db_acc in owner_dbs:
            db_sz = int(getattr(db_acc, "size_bytes", 0) or 0)
            single_db_over = max_single_db_bytes > 0 and db_sz > max_single_db_bytes

            should_auto_lock = False
            reason = None
            if single_db_over:
                should_auto_lock = True
                reason = f"AUTO: Database size ({db_sz // BYTES_PER_MB} MB) exceeded single DB limit ({max_single_db_mb} MB)"
            elif user_over_mysql_quota:
                should_auto_lock = True
                reason = f"AUTO: Total MySQL usage ({user_mysql_total // BYTES_PER_MB} MB) exceeded MySQL quota ({mysql_limit_mb} MB)"
            elif user_over_total_storage:
                should_auto_lock = True
                reason = f"AUTO: Account storage ({cached_total_storage // BYTES_PER_MB} MB) exceeded package storage limit ({storage_limit_mb} MB)"

            currently_locked = bool(getattr(db_acc, "write_locked", False))
            current_reason = getattr(db_acc, "write_locked_reason", None) or ""

            if should_auto_lock and not currently_locked:
                try:
                    mariadb.set_database_write_lock(db_acc.db_name, db_acc.db_user, True)
                    db_acc.write_locked = True
                    db_acc.write_locked_reason = reason
                    changed = True
                except Exception:
                    logger.warning("Failed to auto write-lock database %s", db_acc.db_name)
            elif not should_auto_lock and currently_locked and current_reason.startswith("AUTO:"):
                # Hysteresis: unlock when usage is back below 95% of limits
                below_single = not max_single_db_bytes or db_sz < int(max_single_db_bytes * 0.95)
                below_mysql = not mysql_limit_bytes or user_mysql_total < int(mysql_limit_bytes * 0.95)
                below_total = not storage_limit_bytes or cached_total_storage < int(storage_limit_bytes * 0.95)
                if below_single and below_mysql and below_total:
                    try:
                        mariadb.set_database_write_lock(db_acc.db_name, db_acc.db_user, False)
                        db_acc.write_locked = False
                        db_acc.write_locked_reason = None
                        changed = True
                    except Exception:
                        logger.warning("Failed to auto unlock database %s", db_acc.db_name)

    if changed:
        try:
            db.commit()
        except Exception:
            db.rollback()

    return user_totals


def collect_live_process_metrics(db: Session) -> None:
    """Collect live PHP/Linux CPU %, MySQL per-user CPU % & Rows Read/s (via USER_STATISTICS),
    classify PHP-FPM workers into Cgroups v2, snipe slow scraping SQL queries,
    and run the Automated Anti-DDoS & Anti-Scraping Circuit Breaker."""
    global _last_fast_collect_ts, _prev_mysql_user_stats, _prev_mysql_stats_ts, _mysql_governor_initialized
    users = db.query(User).all()
    all_dbs = db.query(DatabaseAccount).all()
    db_user_to_owner: dict[str, int] = {}
    owner_to_db_users: dict[int, list[str]] = {}
    for d in all_dbs:
        if d.db_user:
            db_user_to_owner[d.db_user] = d.owner_id
            owner_to_db_users.setdefault(d.owner_id, []).append(d.db_user)

    linux_to_user: dict[str, User] = {}
    for u in users:
        try:
            lu = site_users.linux_user_for_panel_username(u.username)
            linux_to_user[lu] = u
        except ValueError:
            continue

    with _lock:
        sniped_Snapshot = dict(_user_sniped_queries_count)

    agg: dict[int, dict[str, Any]] = {
        u.id: {
            "cpu_percent": 0.0,
            "php_cpu_percent": 0.0,
            "mysql_cpu_percent": 0.0,
            "mysql_qps": 0.0,
            "mysql_rows_read_rate": 0,
            "mysql_sniped_queries": int(sniped_Snapshot.get(u.id, 0)),
            "memory_used_bytes": 0,
            "tasks_count": 0,
            "io_read_bps": 0,
            "io_write_bps": 0,
            "processes": [],
        }
        for u in users
    }

    if os.name != "nt" and not settings.command_dry_run:
        if not _mysql_governor_initialized:
            _mysql_governor_initialized = True
            try:
                mariadb.ensure_mysql_governor_settings()
            except Exception:
                pass

        # Ensure all tenant PHP-FPM workers are bound inside their user-<UID>.slice/spanel.scope
        try:
            shell.privileged(
                "cgroup-classify-workers",
                helper_args=[],
                check=False,
                timeout=10,
                fallback=["true"],
            )
        except Exception:
            pass

        try:
            res = shell.privileged(
                "process-ps",
                helper_args=[],
                check=False,
                timeout=5,
                fallback=["ps", "-eo", "pid,user:32,pcpu,rss,nlwp,comm,args", "--no-headers"],
            )
            if res.returncode != 0 or not (res.stdout or "").strip():
                res = subprocess.run(
                    ["ps", "-eo", "pid,user:32,pcpu,rss,nlwp,comm,args", "--no-headers"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            for line in (res.stdout or "").splitlines():
                parts = line.strip().split(None, 6)
                if len(parts) < 6:
                    continue
                pid_str, uname, pcpu_str, rss_kb_str, nlwp_str, comm = parts[:6]
                args_str = parts[6] if len(parts) > 6 else comm
                u_obj = linux_to_user.get(uname)
                if not u_obj:
                    continue
                entry = agg[u_obj.id]
                try:
                    cpu_val = float(pcpu_str)
                    rss_bytes = int(rss_kb_str) * 1024
                    threads = int(nlwp_str)
                except ValueError:
                    continue
                entry["php_cpu_percent"] = round(entry["php_cpu_percent"] + cpu_val, 1)
                entry["memory_used_bytes"] += rss_bytes
                entry["tasks_count"] += max(1, threads)
                if len(entry["processes"]) < 15:
                    entry["processes"].append({
                        "pid": int(pid_str) if pid_str.isdigit() else pid_str,
                        "cpu_percent": cpu_val,
                        "memory_bytes": rss_bytes,
                        "threads": threads,
                        "command": args_str[:160],
                    })
        except Exception:
            pass

        # Collect MariaDB per-user CPU_TIME, BUSY_TIME, ROWS_READ, and QPS deltas
        try:
            now_mono = time.monotonic()
            curr_mysql_stats = mariadb.collect_user_statistics()
            with _lock:
                prev_stats = dict(_prev_mysql_user_stats)
                prev_ts = float(_prev_mysql_stats_ts)
                _prev_mysql_user_stats = curr_mysql_stats
                _prev_mysql_stats_ts = now_mono

            dt = now_mono - prev_ts if prev_ts > 0 else 0.0
            if dt >= 1.0 and prev_stats:
                for db_u, cur in curr_mysql_stats.items():
                    owner_id = db_user_to_owner.get(db_u)
                    if not owner_id or owner_id not in agg:
                        continue
                    prv = prev_stats.get(db_u)
                    if not prv:
                        continue
                    d_cpu = max(0.0, float(cur.get("cpu_time", 0.0)) - float(prv.get("cpu_time", 0.0)))
                    d_busy = max(0.0, float(cur.get("busy_time", 0.0)) - float(prv.get("busy_time", 0.0)))
                    effective_db_cpu_sec = d_cpu if d_cpu > 0 else (d_busy * 0.6)
                    d_rows = max(0, int(cur.get("rows_read", 0)) - int(prv.get("rows_read", 0)))
                    d_q = max(
                        0,
                        (int(cur.get("select_commands", 0)) + int(cur.get("update_commands", 0)))
                        - (int(prv.get("select_commands", 0)) + int(prv.get("update_commands", 0))),
                    )
                    entry = agg[owner_id]
                    entry["mysql_cpu_percent"] = round(entry["mysql_cpu_percent"] + (effective_db_cpu_sec / dt) * 100.0, 1)
                    entry["mysql_qps"] = round(entry["mysql_qps"] + (d_q / dt), 1)
                    entry["mysql_rows_read_rate"] += int(round(d_rows / dt))
        except Exception:
            pass

    for uid, entry in agg.items():
        entry["cpu_percent"] = round(float(entry["php_cpu_percent"]) + float(entry["mysql_cpu_percent"]), 1)

    with _lock:
        _user_live_metrics.clear()
        _user_live_metrics.update(agg)
        _last_fast_collect_ts = time.monotonic()

    # Evaluate Automated Circuit Breaker ("Under Attack & Anti-Scraping Shield") per tenant user
    now_ts = time.time()
    for u in users:
        if is_admin_role(u.role) or u.role == Role.reseller:
            continue
        live = agg.get(u.id) or {}
        cpu_q = int(getattr(u, "cpu_quota_percent", 100) or 100)
        mem_max = int(getattr(u, "memory_max_mb", 2048) or 0) * BYTES_PER_MB
        tasks_max = int(getattr(u, "tasks_max", 512) or 0)

        total_cpu = float(live.get("cpu_percent") or 0.0)
        mysql_cpu = float(live.get("mysql_cpu_percent") or 0.0)
        mysql_rows_rate = int(live.get("mysql_rows_read_rate") or 0)
        mysql_qps = float(live.get("mysql_qps") or 0.0)

        cpu_util = (total_cpu / cpu_q * 100.0) if cpu_q > 0 else total_cpu
        ram_util = (int(live.get("memory_used_bytes") or 0) / mem_max * 100.0) if mem_max > 0 else 0.0
        tasks_util = (int(live.get("tasks_count") or 0) / tasks_max * 100.0) if tasks_max > 0 else 0.0

        # Detect MySQL Scraping Anomaly even when PHP CPU is low
        is_mysql_scraping = (
            mysql_cpu >= 45.0
            or (cpu_q > 0 and (mysql_cpu / cpu_q * 100.0) >= 65.0)
            or mysql_rows_rate >= 40000
            or mysql_qps >= 75.0
        )
        is_pegged = cpu_util >= 90.0 or ram_util >= 90.0 or tasks_util >= 85.0 or is_mysql_scraping
        is_calm = (
            cpu_util < 45.0
            and ram_util < 70.0
            and tasks_util < 50.0
            and mysql_cpu < 15.0
            and mysql_rows_rate < 10000
        )

        # Active Slow-Query Sniper: immediately kill slow SELECT queries (>3s) when MySQL load spikes
        if is_mysql_scraping or mysql_cpu >= 25.0:
            try:
                snipe_user_slow_queries(db, u.id, max_time_sec=3)
            except Exception:
                pass

        with _lock:
            streak = int(_user_high_load_streaks.get(u.id, 0))
            scrape_streak = int(_user_mysql_scrape_streaks.get(u.id, 0))
            if is_pegged:
                streak += 1
            elif is_calm:
                streak = 0
            if is_mysql_scraping:
                scrape_streak += 1
            elif is_calm:
                scrape_streak = 0
            _user_high_load_streaks[u.id] = streak
            _user_mysql_scrape_streaks[u.id] = scrape_streak
            attack_st = dict(_user_under_attack_state.get(u.id) or {})

        if (scrape_streak >= 2 or streak >= 3) and not attack_st.get("enabled"):
            if scrape_streak >= 2:
                reason_str = (
                    f"AUTO: MySQL Data Scraping detected ({mysql_cpu}% MySQL CPU, "
                    f"{mysql_rows_rate:,} rows/s, {mysql_qps} QPS) — Anti-Scraping Shield & DB Governor engaged"
                )
            else:
                reason_str = (
                    f"AUTO: Sustained high load ({round(cpu_util)}% CPU / {int(live.get('tasks_count') or 0)} tasks) "
                    "— Anti-DDoS Shield engaged"
                )
            try:
                set_user_under_attack_mode(db, u.id, True, reason=reason_str)
            except Exception:
                pass
        elif (
            attack_st.get("enabled")
            and str(attack_st.get("reason") or "").startswith("AUTO:")
            and streak == 0
            and scrape_streak == 0
            and now_ts - float(attack_st.get("activated_ts") or 0) >= 600
        ):
            try:
                set_user_under_attack_mode(db, u.id, False)
            except Exception:
                pass


def collect_bandwidth_metrics(db: Session) -> None:
    """Incrementally track monthly Nginx access log traffic per website & user without re-reading whole files."""
    if os.name == "nt" or settings.command_dry_run:
        return
    month_key = datetime.utcnow().strftime("%Y-%m")
    websites = db.query(Website).all()
    user_bw: dict[int, int] = {}

    for site in websites:
        log_path = Path(f"/var/log/nginx/{site.domain}.access.log")
        site_id = site.id
        with _lock:
            prev_Site_bw = int(_site_bandwidth_bytes.get(site_id, 0))
            offset_info = dict(_log_offsets.get(site.domain) or {})

        if offset_info.get("month") != month_key:
            prev_Site_bw = 0
            offset_info = {"month": month_key, "offset": 0, "inode": 0}

        delta_bytes = 0
        try:
            if log_path.exists():
                st = log_path.stat()
                last_offset = int(offset_info.get("offset") or 0)
                last_inode = int(offset_info.get("inode") or 0)
                if st.st_ino != last_inode or st.st_size < last_offset:
                    last_offset = 0
                if st.st_size > last_offset:
                    read_len = min(st.st_size - last_offset, 4 * 1024 * 1024)
                    with log_path.open("rb") as fh:
                        fh.seek(last_offset)
                        chunk = fh.read(read_len)
                        new_offset = fh.tell()
                    for raw_line in chunk.splitlines():
                        parts = raw_line.split(b'"')
                        if len(parts) >= 3:
                            status_and_bytes = parts[2].strip().split()
                            if len(status_and_bytes) >= 2 and status_and_bytes[1].isdigit():
                                delta_bytes += int(status_and_bytes[1]) + len(raw_line)
                    offset_info["offset"] = new_offset
                    offset_info["inode"] = st.st_ino
        except OSError:
            pass

        total_site_bw = prev_Site_bw + delta_bytes
        with _lock:
            _site_bandwidth_bytes[site_id] = total_site_bw
            _log_offsets[site.domain] = offset_info
        user_bw[site.owner_id] = user_bw.get(site.owner_id, 0) + total_site_bw

    with _lock:
        _user_bandwidth_bytes.clear()
        _user_bandwidth_bytes.update(user_bw)


def inspect_slow_bottlenecks(
    db: Session,
    *,
    allowed_user_ids: set[int] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Inspect MySQL Slow Queries and PHP-FPM Slowlog Stack Traces mapped to tenants."""
    all_users = {u.id: u for u in db.query(User).all()}
    user_by_name = {u.username: u for u in all_users.values()}
    all_dbs = db.query(DatabaseAccount).all()
    db_by_user = {d.db_user: d for d in all_dbs if d.db_user}
    db_by_name = {d.db_name: d for d in all_dbs if d.db_name}
    all_sites = db.query(Website).all()
    sites_by_user: dict[str, list[str]] = {}
    for s in all_sites:
        lu = getattr(s, "linux_user", None) or (all_users.get(s.owner_id).username if s.owner_id in all_users else "")
        if lu:
            sites_by_user.setdefault(lu, []).append(s.domain)

    mysql_slow: list[dict[str, Any]] = []

    # 1. Live active slow queries from information_schema.PROCESSLIST
    if not settings.command_dry_run:
        proc_res = shell.privileged(
            "mysql",
            helper_args=[
                "-N",
                "-B",
                "-e",
                (
                    "SELECT ID, USER, DB, TIME, STATE, LEFT(REPLACE(REPLACE(INFO, '\\n', ' '), '\\t', ' '), 240) "
                    "FROM information_schema.PROCESSLIST "
                    "WHERE COMMAND != 'Sleep' AND INFO IS NOT NULL AND TIME >= 1 "
                    "ORDER BY TIME DESC LIMIT 20;"
                ),
            ],
            check=False,
            fallback=["bash", "-lc", "true"],
        )
        for line in (proc_res.get("stdout") or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            qid, db_user, db_name, qtime, state, sql_text = parts[:6]
            acct = db_by_user.get(db_user) or db_by_name.get(db_name)
            owner_id = acct.owner_id if acct else None
            if allowed_user_ids is not None and owner_id not in allowed_user_ids:
                continue
            owner_user = all_users.get(owner_id) if owner_id else None
            try:
                duration_s = float(qtime)
            except ValueError:
                duration_s = 1.0
            mysql_slow.append({
                "source": "live_processlist",
                "query_id": qid,
                "username": owner_user.username if owner_user else db_user,
                "owner_id": owner_id,
                "db_name": db_name or (acct.db_name if acct else "—"),
                "db_user": db_user,
                "query_time_sec": round(duration_s, 3),
                "lock_time_sec": 0.0,
                "rows_examined": 0,
                "state": state or "executing",
                "sql": sql_text.strip(),
            })

    # 2. Historical slow queries from /var/log/mysql/spanel-slow.log
    slow_log_paths = [
        Path("/var/log/mysql/spanel-slow.log"),
        Path("/var/log/mysql/mysql-slow.log"),
    ]
    for slow_path in slow_log_paths:
        try:
            if not slow_path.is_file():
                continue
            raw_size = slow_path.stat().st_size
            with slow_path.open("rb") as fh:
                if raw_size > 131072:
                    fh.seek(raw_size - 131072)
                tail_text = fh.read().decode("utf-8", errors="replace")
            current_user_db = ""
            current_db_name = ""
            current_qtime = 0.0
            current_lock = 0.0
            current_rows = 0
            sql_lines: list[str] = []
            for ln in tail_text.splitlines():
                if ln.startswith("# User@Host:"):
                    if sql_lines and current_qtime > 0:
                        acct = db_by_user.get(current_user_db) or db_by_name.get(current_db_name)
                        owner_id = acct.owner_id if acct else None
                        if allowed_user_ids is None or owner_id in allowed_user_ids:
                            owner_user = all_users.get(owner_id) if owner_id else None
                            mysql_slow.append({
                                "source": "slow_log",
                                "query_id": "",
                                "username": owner_user.username if owner_user else (current_user_db or "mysql"),
                                "owner_id": owner_id,
                                "db_name": current_db_name or (acct.db_name if acct else "—"),
                                "db_user": current_user_db or "—",
                                "query_time_sec": round(current_qtime, 3),
                                "lock_time_sec": round(current_lock, 3),
                                "rows_examined": current_rows,
                                "state": "logged",
                                "sql": " ".join(sql_lines)[:260],
                            })
                    sql_lines = []
                    m_u = re.search(r"\[\s*([a-zA-Z0-9_]+)\s*\]", ln)
                    current_user_db = m_u.group(1) if m_u else ""
                elif ln.startswith("# Query_time:"):
                    m_q = re.search(
                        r"Query_time:\s*([0-9.]+)\s+Lock_time:\s*([0-9.]+).*?Rows_examined:\s*(\d+)",
                        ln,
                    )
                    if m_q:
                        current_qtime = float(m_q.group(1))
                        current_lock = float(m_q.group(2))
                        current_rows = int(m_q.group(3))
                elif ln.lower().startswith("use ") and ln.endswith(";"):
                    current_db_name = ln[4:-1].strip().strip("`")
                elif not ln.startswith("#") and not ln.startswith("SET timestamp="):
                    if ln.strip():
                        sql_lines.append(ln.strip())
            if sql_lines and current_qtime > 0:
                acct = db_by_user.get(current_user_db) or db_by_name.get(current_db_name)
                owner_id = acct.owner_id if acct else None
                if allowed_user_ids is None or owner_id in allowed_user_ids:
                    owner_user = all_users.get(owner_id) if owner_id else None
                    mysql_slow.append({
                        "source": "slow_log",
                        "query_id": "",
                        "username": owner_user.username if owner_user else (current_user_db or "mysql"),
                        "owner_id": owner_id,
                        "db_name": current_db_name or (acct.db_name if acct else "—"),
                        "db_user": current_user_db or "—",
                        "query_time_sec": round(current_qtime, 3),
                        "lock_time_sec": round(current_lock, 3),
                        "rows_examined": current_rows,
                        "state": "logged",
                        "sql": " ".join(sql_lines)[:260],
                    })
            break
        except OSError:
            continue

    mysql_slow.sort(key=lambda x: x.get("query_time_sec", 0.0), reverse=True)
    mysql_slow = mysql_slow[:limit]

    # 3. PHP-FPM Slowlog Stack Traces
    php_traces: list[dict[str, Any]] = []
    php_log_dir = Path("/var/log/php")
    slow_files: list[Path] = []
    try:
        if php_log_dir.is_dir():
            slow_files.extend(sorted(php_log_dir.glob("spanel-slow-*.log")))
        var_log = Path("/var/log")
        if var_log.is_dir():
            slow_files.extend(sorted(var_log.glob("php*-fpm*slow*.log")))
    except OSError:
        pass

    for sf in slow_files:
        try:
            if not sf.is_file():
                continue
            raw_size = sf.stat().st_size
            with sf.open("rb") as fh:
                if raw_size > 65536:
                    fh.seek(raw_size - 65536)
                content = fh.read().decode("utf-8", errors="replace")
            blocks = re.split(r"\n\s*\n", content.strip())
            for blk in reversed(blocks[-20:]):
                lines = [l.strip() for l in blk.splitlines() if l.strip()]
                if not lines:
                    continue
                header = lines[0]
                m_pool = re.search(r"\[pool\s+([^\]]+)\]", header)
                pool_name = m_pool.group(1) if m_pool else sf.stem
                linux_user = site_users.linux_user_from_pool_name(pool_name)
                owner_user = user_by_name.get(linux_user)
                owner_id = owner_user.id if owner_user else None
                if allowed_user_ids is not None and owner_id not in allowed_user_ids:
                    continue
                script_file = ""
                stack_frames: list[str] = []
                for l in lines[1:]:
                    if l.startswith("script_filename ="):
                        script_file = l.split("=", 1)[1].strip()
                    elif l.startswith("[0x"):
                        parts_f = l.split("]", 1)
                        frame_str = parts_f[1].strip() if len(parts_f) > 1 else l
                        stack_frames.append(frame_str)
                if script_file or stack_frames:
                    domains = sites_by_user.get(linux_user, [])
                    matched_domain = next((d for d in domains if d in script_file), domains[0] if domains else "—")
                    php_traces.append({
                        "pool": pool_name,
                        "username": linux_user or "www-data",
                        "owner_id": owner_id,
                        "domain": matched_domain,
                        "script_filename": script_file or "index.php",
                        "top_frame": stack_frames[0] if stack_frames else "main()",
                        "stack": stack_frames[:6],
                        "header": header[:120],
                    })
                    if len(php_traces) >= limit:
                        break
        except OSError:
            continue

    return {
        "summary": {
            "mysql_slow_count": len(mysql_slow),
            "php_slow_count": len(php_traces),
            "slow_query_log_enabled": True,
            "long_query_time_sec": 2,
            "php_slowlog_timeout_sec": 5,
        },
        "mysql_slow_queries": mysql_slow,
        "php_slow_traces": php_traces,
    }


def collect_domain_traffic_analytics(
    db: Session,
    *,
    allowed_user_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Collect per-domain HTTP status distribution (2xx/3xx/4xx/5xx), bandwidth, and FastCGI cache hit ratio."""
    q = db.query(Website)
    if allowed_user_ids is not None:
        if not allowed_user_ids:
            return {
                "summary": {
                    "total_domains": 0,
                    "total_requests": 0,
                    "total_bandwidth_bytes": 0,
                    "status_2xx": 0,
                    "status_3xx": 0,
                    "status_4xx": 0,
                    "status_5xx": 0,
                },
                "items": [],
            }
        q = q.filter(Website.owner_id.in_(allowed_user_ids))

    sites = q.order_by(Website.id.desc()).all()
    users_map = {u.id: u.username for u in db.query(User).all()}
    items: list[dict[str, Any]] = []

    tot_req = 0
    tot_bw = 0
    tot_2xx = 0
    tot_3xx = 0
    tot_4xx = 0
    tot_5xx = 0

    for site in sites:
        s_2xx = 0
        s_3xx = 0
        s_4xx = 0
        s_5xx = 0
        log_bytes = 0
        log_path = Path(f"/var/log/nginx/{site.domain}.access.log")
        try:
            if log_path.is_file():
                fsize = log_path.stat().st_size
                with log_path.open("rb") as fh:
                    if fsize > 131072:
                        fh.seek(fsize - 131072)
                    chunk = fh.read()
                for raw_line in chunk.splitlines():
                    parts = raw_line.split(b'"')
                    if len(parts) >= 3:
                        sb = parts[2].strip().split()
                        if len(sb) >= 2 and sb[0].isdigit():
                            code = int(sb[0])
                            if 200 <= code < 300:
                                s_2xx += 1
                            elif 300 <= code < 400:
                                s_3xx += 1
                            elif 400 <= code < 500:
                                s_4xx += 1
                            elif code >= 500:
                                s_5xx += 1
                            if sb[1].isdigit():
                                log_bytes += int(sb[1])
        except OSError:
            pass

        req_count = s_2xx + s_3xx + s_4xx + s_5xx
        bw_bytes = max(cached_site_bandwidth_bytes(site.id), log_bytes)
        tot_req += req_count
        tot_bw += bw_bytes
        tot_2xx += s_2xx
        tot_3xx += s_3xx
        tot_4xx += s_4xx
        tot_5xx += s_5xx

        items.append({
            "website_id": site.id,
            "domain": site.domain,
            "owner_id": site.owner_id,
            "owner_username": users_map.get(site.owner_id, f"user#{site.owner_id}"),
            "app_type": site.app_type,
            "php_version": site.php_version,
            "ssl_enabled": bool(site.ssl_enabled),
            "http3_quic_ready": bool(site.ssl_enabled),
            "fastcgi_cache_enabled": site.app_type == "wordpress",
            "requests_total": req_count,
            "status_2xx": s_2xx,
            "status_3xx": s_3xx,
            "status_4xx": s_4xx,
            "status_5xx": s_5xx,
            "bandwidth_bytes": bw_bytes,
        })

    items.sort(key=lambda x: (x["bandwidth_bytes"], x["requests_total"]), reverse=True)
    return {
        "summary": {
            "total_domains": len(items),
            "total_requests": tot_req,
            "total_bandwidth_bytes": tot_bw,
            "status_2xx": tot_2xx,
            "status_3xx": tot_3xx,
            "status_4xx": tot_4xx,
            "status_5xx": tot_5xx,
        },
        "items": items,
    }


def refresh_all_telemetry(db: Session | None = None, *, refresh_disk: bool = False) -> None:
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        collect_live_process_metrics(db)
        collect_and_enforce_mysql_quotas(db)
        collect_bandwidth_metrics(db)
        if refresh_disk:
            for u in db.query(User).all():
                storage_quota.forget_user_storage(u.id)
                storage_quota.user_storage_used_bytes(db, u, use_cache=True)
        _save_persisted_state()
    except Exception:
        logger.debug("Telemetry refresh cycle error", exc_info=True)
    finally:
        if own_session:
            db.close()


def start_resource_monitor_daemon() -> None:
    global _daemon_started
    with _lock:
        if _daemon_started:
            return
        _daemon_started = True

    _load_persisted_state()

    def _loop():
        cycle = 0
        while True:
            try:
                with SessionLocal() as db:
                    collect_live_process_metrics(db)
                    if cycle % 3 == 0:
                        collect_and_enforce_mysql_quotas(db)
                        collect_bandwidth_metrics(db)
                    if cycle % 4 == 0:
                        try:
                            from app.services.system import run_self_healing_check

                            run_self_healing_check(reason="background")
                        except Exception:
                            pass
                    if cycle % 20 == 0:
                        for u in db.query(User).all():
                            if storage_quota.cached_storage_used_bytes(u.id) is None:
                                storage_quota.user_storage_used_bytes(db, u, use_cache=True)
                        _save_persisted_state()
                cycle += 1
            except Exception:
                pass
            time.sleep(15)

    threading.Thread(target=_loop, name="spanel-resource-monitor", daemon=True).start()
