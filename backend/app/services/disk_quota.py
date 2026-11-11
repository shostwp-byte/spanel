"""Which disk quota is enforced: the kernel's, or only the panel's own.

"app" mode is storage_quota.py: the panel measures each account and refuses
panel uploads past the limit, but PHP and SFTP can still write. "kernel" mode
is user quota on the filesystem holding /home, set per account by
user-limits-apply (disk space and inodes), which nothing on the box can write
past. spanel-helper quota-status reports which applies; quota-enable installs
the tools and adds the fstab options - it never remounts or reboots, so a
server can sit in "remount-required" until its administrator does.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import is_admin_role
from app.models.entities import User
from app.services.shell import _use_helper, shell

logger = logging.getLogger(__name__)

STATUS_KEYS = (
    "mode", "state", "mount", "fstype", "device", "tools", "mounted_options",
    "fstab_options", "quota_on", "hint", "backup",
)
STATES = {
    "active", "needs-quotaon", "remount-required", "kernel-cmdline-required",
    "not-configured", "unsupported", "unknown",
}
# Kernel limits are set per account when its limits are applied. When kernel
# quota first turns up (after the remount or reboot quota-enable asked for),
# every account is applied once so none is left without its limit.
APPLIED_MARKER = Path("/var/lib/spanel/disk-quota-applied")

_enable_lock = threading.Lock()
_state_lock = threading.Lock()
_reconciling = False
_auto_enable_attempted_for: set[str] = set()
_last_auto_enable_error: dict[str, str] = {}
_boot_reconciler_started = False


def _target_key(current: dict) -> str:
    mount = (current.get("mount") or "").strip()
    device = (current.get("device") or "").strip()
    return f"{mount}:{device}" if (mount or device) else ""


def _enrich_runtime_state(parsed: dict) -> dict:
    key = _target_key(parsed)
    with _state_lock:
        parsed["reconciling"] = _reconciling
        if parsed.get("state") == "active":
            if key:
                _last_auto_enable_error.pop(key, None)
            parsed["last_error"] = ""
        else:
            parsed["last_error"] = _last_auto_enable_error.get(key, "") if key else ""
    return parsed


def parse_status(text: str) -> dict:
    """quota-status / quota-enable output (key=value lines) as a dict."""
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in STATUS_KEYS:
            values[key] = value.strip()
    state = values.get("state", "unknown")
    if state not in STATES:
        state = "unknown"
    return {
        "mode": "kernel" if state == "active" else "app",
        "state": state,
        "mount": values.get("mount", ""),
        "fstype": values.get("fstype", ""),
        "device": values.get("device", ""),
        "tools_installed": values.get("tools") == "yes",
        "mounted_options": values.get("mounted_options") == "yes",
        "fstab_options": values.get("fstab_options") == "yes",
        "quota_on": values.get("quota_on") == "yes",
        "hint": values.get("hint", ""),
        "fstab_backup": values.get("backup", ""),
        # The administrator can act on these; the others need a remount,
        # a reboot or a different filesystem.
        "can_enable": state in {"not-configured", "needs-quotaon"},
        "reconciling": False,
        "last_error": "",
    }


def status() -> dict:
    result = shell.privileged(
        "quota-status",
        check=False,
        timeout=30,
        fallback=["bash", "-lc", "echo state=not-configured; echo hint=dry-run"],
    )
    if result.returncode != 0:
        parsed = parse_status("")
        parsed["hint"] = (result.stderr or result.stdout or "quota-status failed").strip()[-500:]
        return _enrich_runtime_state(parsed)
    return _enrich_runtime_state(parse_status(result.stdout))


def enable(*, manual: bool = False) -> dict:
    global _reconciling
    if manual:
        with _state_lock:
            _last_auto_enable_error.clear()
            _auto_enable_attempted_for.clear()
    with _enable_lock:
        with _state_lock:
            _reconciling = True
        try:
            result = shell.privileged(
                "quota-enable",
                check=False,
                timeout=900,
                fallback=["bash", "-lc", "echo state=not-configured; echo hint=dry-run"],
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Could not enable disk quota").strip()[-2000:])
            parsed = parse_status(result.stdout)
        finally:
            with _state_lock:
                _reconciling = False
    return _enrich_runtime_state(parsed)


def should_auto_enable(current: dict) -> bool:
    """True once per (mount, device) when fstab + active mount options are in
    place after a reboot/remount and only quotacheck + quotaon remain."""
    if current.get("state") != "needs-quotaon":
        return False
    if not current.get("mounted_options") or not current.get("fstab_options"):
        return False
    key = _target_key(current)
    if not key:
        return False
    with _state_lock:
        return key not in _auto_enable_attempted_for and not _reconciling


def _run_auto_enable(key: str, db_factory: Callable[[], Session] | None = None) -> dict | None:
    try:
        updated = enable(manual=False)
        if db_factory is not None:
            with db_factory() as db:
                reconcile_account_limits(db, updated)
        logger.info(
            "Kernel disk quota auto-enable finished on %s: state=%s mode=%s",
            key,
            updated.get("state"),
            updated.get("mode"),
        )
        return updated
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc).strip()[-500:]
        with _state_lock:
            _last_auto_enable_error[key] = err_msg
        logger.warning("Automatic kernel disk quota activation failed for %s: %s", key, err_msg)
        return None


def maybe_trigger_auto_enable(
    current: dict,
    *,
    db_factory: Callable[[], Session] | None = None,
    async_run: bool = True,
) -> bool:
    """Kick off one-shot quotacheck + quotaon when options are already mounted."""
    if not should_auto_enable(current):
        return False
    key = _target_key(current)
    with _state_lock:
        if key in _auto_enable_attempted_for or _reconciling:
            return False
        _auto_enable_attempted_for.add(key)

    if db_factory is None:
        from app.core.database import SessionLocal

        db_factory = SessionLocal

    if async_run:
        with _state_lock:
            current["reconciling"] = True
        threading.Thread(
            target=_run_auto_enable,
            args=(key, db_factory),
            name="spanel-quota-auto-enable",
            daemon=True,
        ).start()
    else:
        _run_auto_enable(key, db_factory)
    return True


def reconcile_on_startup(db_factory: Callable[[], Session] | None = None) -> dict:
    """Check disk quota after boot: finish switching on a mounted fstab quota,
    and ensure every tenant account has its kernel limits applied."""
    if db_factory is None:
        from app.core.database import SessionLocal

        db_factory = SessionLocal

    current = status()
    if should_auto_enable(current):
        maybe_trigger_auto_enable(current, db_factory=db_factory, async_run=False)
        current = status()
    elif current.get("mode") == "kernel":
        with db_factory() as db:
            reconcile_account_limits(db, current)
    return current


def start_boot_reconciler() -> None:
    """Background post-boot check so rebooting after enabling fstab quota
    finishes quotacheck + quotaon automatically without a second click."""
    global _boot_reconciler_started
    if settings.command_dry_run or not _use_helper():
        return
    with _state_lock:
        if _boot_reconciler_started:
            return
        _boot_reconciler_started = True

    def _worker() -> None:
        try:
            reconcile_on_startup()
        except Exception:  # noqa: BLE001
            logger.warning("Startup kernel disk quota check failed", exc_info=True)

    threading.Thread(target=_worker, name="spanel-quota-boot-reconcile", daemon=True).start()


def reconcile_account_limits(db: Session, current: dict, marker: Path | None = None) -> int:
    """Apply every account's limits once kernel quota is active.

    Returns how many accounts were queued. Forgets the marker when kernel
    quota goes away, so turning it on again applies them again.
    """
    from app.services import resource_monitor

    marker = marker or APPLIED_MARKER
    wanted = f"{current.get('mount', '')}:{current.get('device', '')}"
    if current.get("mode") != "kernel":
        try:
            marker.unlink()
        except OSError:
            pass
        return 0
    try:
        if marker.read_text(encoding="utf-8").strip() == wanted:
            return 0
    except OSError:
        pass
    queued = 0
    for user in db.query(User).all():
        if is_admin_role(user.role):
            continue
        resource_monitor.apply_user_cgroup_limits_async(user.id)
        queued += 1
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(wanted + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Could not record that kernel disk quota limits were applied", exc_info=True)
    return queued
