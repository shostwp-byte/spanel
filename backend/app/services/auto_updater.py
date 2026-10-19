"""Background Auto-Discovery & Safe Scheduled Auto-Update Daemon."""

import json
import time
import threading
from pathlib import Path
from app.services import updates

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
    # Initial delay so startup is never slowed down
    time.sleep(45)
    last_check_ts = 0.0
    last_auto_update_day = ""

    while True:
        try:
            cfg = updates.get_update_source_config()
            interval_sec = max(3600, int(cfg.get("auto_check_interval_hours", 6)) * 3600)
            now = time.time()

            if cfg.get("auto_check_enabled", True) and (now - last_check_ts >= interval_sec):
                rel = updates.panel_release_status(force_refresh=True)
                last_check_ts = now

                if cfg.get("auto_update_enabled", False) and rel.get("update_available"):
                    current_hhmm = time.strftime("%H:%M", time.localtime())
                    today_str = time.strftime("%Y-%m-%d", time.localtime())
                    target_win = cfg.get("auto_update_window") or "02:00"
                    # Trigger within the maintenance hour or once per day
                    if today_str != last_auto_update_day and current_hhmm[:2] == target_win[:2]:
                        last_auto_update_day = today_str
                        updates.run_panel_update()
        except Exception:
            pass

        time.sleep(600)


def start_auto_update_daemon() -> None:
    thread = threading.Thread(target=_auto_update_worker, name="spanel-auto-updater", daemon=True)
    thread.start()
