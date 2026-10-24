"""Background Auto-Discovery & Safe Scheduled Auto-Update Daemon."""

import json
import time
import threading
from pathlib import Path
from app.services import updates, php

AUTO_UPDATE_CONFIG = Path("/opt/spanel/backend/auto_update.json")


def is_auto_update_enabled() -> bool:
    cfg = updates.get_update_source_config()
    if "auto_update_enabled" in cfg:
        return bool(cfg.get("auto_update_enabled"))
    try:
        if AUTO_UPDATE_CONFIG.exists():
            return bool(json.loads(AUTO_UPDATE_CONFIG.read_text(encoding="utf-8")).get("enabled", False))
    except Exception:
        pass
    return False


def set_auto_update(enabled: bool, window: str = "02:00", auto_check: bool = True) -> dict:
    res = updates.save_update_source_config({
        "auto_update_enabled": bool(enabled),
        "auto_update_window": window,
        "auto_check_enabled": bool(auto_check),
    })
    try:
        AUTO_UPDATE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        AUTO_UPDATE_CONFIG.write_text(json.dumps({"enabled": bool(enabled), "window": window}), encoding="utf-8")
    except Exception:
        pass
    return res


def _auto_update_worker() -> None:
    """Release checks and PHP hardening, in the background of the API.

    Applying anything at a set hour is not done here any more: panel
    auto-update, windowed PHP auto-patch and core stack patching run from
    spanel-maintenance.timer (app.services.maintenance_window), which keeps
    its "already ran in this window" marker on disk. This thread only fired
    if a check happened to land inside the window hour, and forgot what it
    had done on every restart.
    """
    # Initial delay so startup is never slowed down
    time.sleep(45)
    last_check_ts = 0.0
    last_php_check_ts = 0.0

    while True:
        try:
            cfg = updates.get_update_source_config()
            interval_sec = max(3600, int(cfg.get("auto_check_interval_hours", 6)) * 3600)
            now = time.time()

            if cfg.get("auto_check_enabled", True) and (now - last_check_ts >= interval_sec):
                updates.panel_release_status(force_refresh=True)
                last_check_ts = now
        except Exception:
            pass

        try:
            php_state = php.check_php_updates(force_refresh=False)
            php_interval_sec = max(3600, int(php_state.get("check_interval_hours", 6)) * 3600)
            now = time.time()

            if now - last_php_check_ts >= php_interval_sec:
                php_state = php.check_php_updates(force_refresh=True)
                last_php_check_ts = now

            if php_state.get("auto_harden_php2shell", True) and int(php_state.get("unprotected_count") or 0) > 0:
                php.apply_php2shell_hardening()
                php_state = php.check_php_updates(force_refresh=False)

            # A set hour belongs to the maintenance window; only "immediate"
            # is applied from here.
            pref_win = str(php_state.get("preferred_window") or "03:00").strip().lower()
            if (php_state.get("auto_patch_enabled", True) and pref_win == "immediate"
                    and int(php_state.get("updates_available_count") or 0) > 0):
                php.upgrade_php_packages(trigger="auto")
        except Exception:
            pass

        time.sleep(600)


def start_auto_update_daemon() -> None:
    thread = threading.Thread(target=_auto_update_worker, name="spanel-auto-updater", daemon=True)
    thread.start()
