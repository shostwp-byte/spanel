"""The maintenance window: jobs that must run once a day, at a set hour.

Panel auto-update and PHP auto-patch used to live in a thread inside the API
that woke every ten minutes and fired only if a release check (every six hours
or more) happened to land inside the window hour. Most nights it did not, so
"auto update at 02:00" meant "some night, eventually". A restart of the API
reset the in-memory "already ran today" marker as well, so the same job could
also run twice in one night.

This runs from spanel-maintenance.timer every five minutes instead, as the
panel account. A job is due when "now" falls inside its window
[start, start + window_minutes) and the window it falls in has not been
claimed yet. The claim (the date the window started on) is written to disk
BEFORE the job runs: a crash or a restart half-way through never makes the
same window run the job a second time. That is at-most-once on purpose -
an update that died half-way wants a person, not a retry at 02:05.

A server that is off for a whole window skips that night rather than
patching at nine in the morning; the next window catches up.
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger("spanel.maintenance")

DATA_DIR = Path(os.environ.get("SPANEL_DATA_DIR") or os.environ.get("spanel_DATA_DIR") or "/var/lib/spanel")
STATE_FILE = Path(os.environ.get("SPANEL_MAINTENANCE_FILE") or str(DATA_DIR / "maintenance-window.json"))
LOCK_FILE = STATE_FILE.with_suffix(".lock")

WINDOW_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DEFAULT_WINDOW_MINUTES = 120
MIN_WINDOW_MINUTES = 15
MAX_WINDOW_MINUTES = 360

# The panel-managed core stack. Same list as updates.CORE_STACK_SERVICES and as
# the helper's allowlist in upgrade_core_stack_packages / render_core_blacklist.
CORE_STACK_PACKAGES = (
    "nginx", "mariadb-server", "redis-server", "pdns-server", "exim4-daemon-heavy",
    "dovecot-core", "rspamd", "openssl", "fail2ban",
)

DEFAULT_CONFIG = {
    "window_minutes": DEFAULT_WINDOW_MINUTES,
    "core_stack": {
        "enabled": False,
        "window": "03:30",
        "weekdays": [],          # empty = every day; 0 = Monday ... 6 = Sunday
        "packages": list(CORE_STACK_PACKAGES),
    },
}


# --- pure window logic --------------------------------------------------------

def parse_window(value: str) -> Optional[tuple[int, int]]:
    text = (value or "").strip()
    if not WINDOW_RE.match(text):
        return None
    hour, minute = text.split(":")
    return int(hour), int(minute)


def clamp_window_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_MINUTES
    return max(MIN_WINDOW_MINUTES, min(MAX_WINDOW_MINUTES, minutes))


def clean_weekdays(values: Iterable) -> list[int]:
    out = set()
    for value in values or []:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            out.add(day)
    return sorted(out)


def window_occurrence(now: datetime, window: str, duration_minutes: int,
                      weekdays: Iterable[int] = ()) -> Optional[str]:
    """The window `now` is inside, named by the date it started on, or None.

    A window may cross midnight (23:30 for two hours), so yesterday's start is
    considered as well as today's. `weekdays` restricts which start days count.
    """
    parsed = parse_window(window)
    if parsed is None:
        return None
    duration = timedelta(minutes=clamp_window_minutes(duration_minutes))
    days = set(clean_weekdays(weekdays))
    for offset in (0, -1):
        start_day: date = now.date() + timedelta(days=offset)
        start = datetime.combine(start_day, dtime(parsed[0], parsed[1]))
        if start <= now < start + duration:
            if days and start.weekday() not in days:
                return None
            return start_day.isoformat()
    return None


def due_occurrence(job_state: dict, now: datetime, window: str, duration_minutes: int,
                   weekdays: Iterable[int] = ()) -> Optional[str]:
    """The window to claim for this job now, or None if it is not due."""
    occurrence = window_occurrence(now, window, duration_minutes, weekdays)
    if occurrence and (job_state or {}).get("last_occurrence") != occurrence:
        return occurrence
    return None


def next_window_start(now: datetime, window: str, weekdays: Iterable[int] = ()) -> Optional[datetime]:
    parsed = parse_window(window)
    if parsed is None:
        return None
    days = set(clean_weekdays(weekdays))
    for offset in range(0, 8):
        day = now.date() + timedelta(days=offset)
        start = datetime.combine(day, dtime(parsed[0], parsed[1]))
        if start <= now:
            continue
        if days and start.weekday() not in days:
            continue
        return start
    return None


# --- state on disk ---------------------------------------------------------------

def _read() -> dict:
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o640)
    except OSError:
        pass
    tmp.replace(STATE_FILE)


def load_config() -> dict:
    stored = _read().get("config") or {}
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["window_minutes"] = clamp_window_minutes(stored.get("window_minutes", DEFAULT_WINDOW_MINUTES))
    core = stored.get("core_stack") if isinstance(stored.get("core_stack"), dict) else {}
    config["core_stack"]["enabled"] = bool(core.get("enabled", False))
    if parse_window(core.get("window") or ""):
        config["core_stack"]["window"] = core["window"]
    config["core_stack"]["weekdays"] = clean_weekdays(core.get("weekdays") or [])
    if isinstance(core.get("packages"), list):
        config["core_stack"]["packages"] = [p for p in CORE_STACK_PACKAGES if p in core["packages"]]
    return config


def save_config(changes: dict) -> dict:
    data = _read()
    config = load_config()
    if changes.get("window_minutes") is not None:
        config["window_minutes"] = clamp_window_minutes(changes["window_minutes"])
    core = changes.get("core_stack") or {}
    if core.get("enabled") is not None:
        config["core_stack"]["enabled"] = bool(core["enabled"])
    if core.get("window") is not None:
        if not parse_window(str(core["window"])):
            raise ValueError("The window must be HH:MM (24-hour)")
        config["core_stack"]["window"] = str(core["window"]).strip()
    if core.get("weekdays") is not None:
        config["core_stack"]["weekdays"] = clean_weekdays(core["weekdays"])
    if core.get("packages") is not None:
        unknown = [p for p in core["packages"] if p not in CORE_STACK_PACKAGES]
        if unknown:
            raise ValueError(f"Not a core stack package: {', '.join(map(str, unknown))}")
        config["core_stack"]["packages"] = [p for p in CORE_STACK_PACKAGES if p in core["packages"]]
    if config["core_stack"]["enabled"] and not config["core_stack"]["packages"]:
        raise ValueError("Pick at least one package to patch")
    data["config"] = config
    _write(data)
    return config


def job_states() -> dict:
    jobs = _read().get("jobs")
    return jobs if isinstance(jobs, dict) else {}


def _update_job(name: str, **fields) -> dict:
    data = _read()
    jobs = data.setdefault("jobs", {})
    entry = jobs.setdefault(name, {})
    entry.update(fields)
    _write(data)
    return entry


@contextmanager
def _run_lock():
    """One scheduler run at a time: a slow apt run must not overlap the next tick."""
    try:
        import fcntl  # noqa: PLC0415 - not on Windows dev machines
    except ImportError:
        yield True
        return
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# --- jobs -----------------------------------------------------------------------------

@dataclass
class Schedule:
    window: str
    weekdays: list[int] = field(default_factory=list)


@dataclass
class Job:
    name: str
    label: str
    schedule: Callable[[], Optional[Schedule]]
    run: Callable[[], tuple[str, str]]


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _panel_update_schedule() -> Optional[Schedule]:
    from app.services import updates

    cfg = updates.get_update_source_config()
    if not cfg.get("auto_update_enabled"):
        return None
    return Schedule(window=cfg.get("auto_update_window") or "02:00")


def _panel_update_run() -> tuple[str, str]:
    from app.services import updates

    release = updates.panel_release_status(force_refresh=True)
    if release.get("check_error"):
        return "failed", f"Release check failed: {release['check_error']}"
    if not release.get("update_available"):
        return "skipped", f"Already on the latest version ({release.get('current_version')})"
    result = updates.run_panel_update()
    if result.returncode != 0:
        return "failed", updates.sanitize_update_text((result.stderr or result.stdout or "").strip())[:500]
    return "ok", f"Update to {release.get('latest_version')} started"


def _core_stack_schedule() -> Optional[Schedule]:
    core = load_config()["core_stack"]
    if not core["enabled"] or not core["packages"]:
        return None
    return Schedule(window=core["window"], weekdays=core["weekdays"])


def _core_stack_run() -> tuple[str, str]:
    from app.services import updates

    packages = load_config()["core_stack"]["packages"]
    # Re-assert the unattended-upgrades blacklist every run: it is cheap, and
    # it repairs a file removed by hand or rendered by an older helper.
    updates.set_core_stack_blacklist(True, packages)
    # No pre-check against apt-cache here: the panel account reads package
    # lists that may be a day old. The helper refreshes them and upgrades
    # only what has a newer candidate, then says what actually changed.
    result = updates.upgrade_core_stack_packages(packages=packages, trigger="scheduled")
    upgraded = result.get("upgraded") or []
    if result.get("status") != "completed":
        return "failed", result.get("summary") or "Core stack patching failed"
    if not upgraded:
        return "skipped", "No newer packages for the selected core stack"
    return "ok", f"Patched: {', '.join(upgraded)}"


def _php_patch_schedule() -> Optional[Schedule]:
    from app.services import php

    state = php.check_php_updates(force_refresh=False)
    if not state.get("auto_patch_enabled", True):
        return None
    window = str(state.get("preferred_window") or "03:00").strip().lower()
    if window == "immediate" or not parse_window(window):
        return None  # "immediate" stays with the API's background checker
    return Schedule(window=window)


def _php_patch_run() -> tuple[str, str]:
    from app.services import php

    state = php.check_php_updates(force_refresh=True)
    if int(state.get("updates_available_count") or 0) <= 0:
        return "skipped", "No PHP updates pending"
    php.upgrade_php_packages(trigger="auto")
    return "ok", "PHP packages patched"


def default_jobs() -> list[Job]:
    jobs = [
        Job("panel_update", "Panel auto-update", _panel_update_schedule, _panel_update_run),
        Job("core_stack", "Core stack security patching", _core_stack_schedule, _core_stack_run),
        Job("php_patch", "PHP auto-patch", _php_patch_schedule, _php_patch_run),
    ]
    # Jobs owned by other modules register here so there is one scheduler.
    try:
        from app.services import panel_backup

        jobs.append(Job("panel_dr", "Panel self-backup", panel_backup.scheduled_window, panel_backup.scheduled_run))
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.services import backup_verify

        jobs.append(Job("backup_verify", "Backup verification", backup_verify.scheduled_window, backup_verify.scheduled_run))
    except ImportError:  # pragma: no cover
        pass
    return jobs


def _notify_failure(job: Job, message: str) -> None:
    try:
        from app.services import alert_webhooks, notifications

        notifications.notify("maintenance_job_failed",
                             {"job": job.label, "detail": message, "when": datetime.now().strftime("%d/%m/%Y %H:%M")},
                             admins=True, dedupe_key=f"maintenance:{job.name}:{date.today().isoformat()}")
        alert_webhooks.alert("maintenance_job_failed", f"{job.label} failed", message, {"job": job.name})
    except Exception as exc:  # noqa: BLE001 - the job's own record says it failed
        logger.warning("maintenance failure notice not sent: %s", exc)


def run_due(now: Optional[datetime] = None, jobs: Optional[list[Job]] = None) -> list[dict]:
    """Run every job whose window is open and not yet claimed. Returns what ran."""
    now = now or datetime.now()
    ran: list[dict] = []
    with _run_lock() as acquired:
        if not acquired:
            return ran
        window_minutes = load_config()["window_minutes"]
        states = job_states()
        for job in jobs if jobs is not None else default_jobs():
            try:
                schedule = job.schedule()
            except Exception as exc:  # noqa: BLE001 - a broken config must not stop the others
                logger.warning("maintenance job %s: schedule unreadable: %s", job.name, exc)
                continue
            if schedule is None:
                continue
            occurrence = due_occurrence(states.get(job.name) or {}, now, schedule.window,
                                        window_minutes, schedule.weekdays)
            if not occurrence:
                continue
            # Claim the window first: at most once per window, even across a crash.
            _update_job(job.name, last_occurrence=occurrence, last_started_at=_utc_now(),
                        last_status="running", last_message="")
            try:
                status, message = job.run()
            except Exception as exc:  # noqa: BLE001
                status, message = "failed", f"{type(exc).__name__}: {exc}"[:500]
            _update_job(job.name, last_finished_at=_utc_now(), last_status=status, last_message=message[:1000])
            if status == "failed":
                _notify_failure(job, message)
            ran.append({"job": job.name, "occurrence": occurrence, "status": status, "message": message})
    return ran


def overview(now: Optional[datetime] = None) -> dict:
    """What the page shows: settings, each job's window, last run and next run."""
    now = now or datetime.now()
    config = load_config()
    states = job_states()
    rows = []
    for job in default_jobs():
        try:
            schedule = job.schedule()
        except Exception:  # noqa: BLE001
            schedule = None
        nxt = next_window_start(now, schedule.window, schedule.weekdays) if schedule else None
        rows.append({
            "name": job.name,
            "label": job.label,
            "enabled": schedule is not None,
            "window": schedule.window if schedule else "",
            "weekdays": schedule.weekdays if schedule else [],
            "next_run_at": nxt.isoformat(timespec="minutes") if nxt else "",
            **{k: (states.get(job.name) or {}).get(k, "") for k in
               ("last_occurrence", "last_started_at", "last_finished_at", "last_status", "last_message")},
        })
    return {"config": config, "jobs": rows, "core_stack_packages": list(CORE_STACK_PACKAGES),
            "server_time": now.isoformat(timespec="minutes")}


if __name__ == "__main__":
    for item in run_due():
        print(f"{item['job']} ({item['occurrence']}): {item['status']} {item['message']}")
