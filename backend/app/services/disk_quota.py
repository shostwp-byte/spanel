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
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role
from app.models.entities import User
from app.services.shell import shell

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
        return parsed
    return parse_status(result.stdout)


def enable() -> dict:
    result = shell.privileged(
        "quota-enable",
        check=False,
        timeout=900,
        fallback=["bash", "-lc", "echo state=not-configured; echo hint=dry-run"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not enable disk quota").strip()[-2000:])
    return parse_status(result.stdout)


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
