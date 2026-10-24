"""Maintenance window scheduler, scheduled core stack patching and the
unattended-upgrades blacklist that keeps the two from both upgrading nginx."""
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"

from app.services import maintenance_window as mw  # noqa: E402
from app.services import updates  # noqa: E402


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "maintenance-window.json"
    monkeypatch.setattr(mw, "STATE_FILE", path)
    monkeypatch.setattr(mw, "LOCK_FILE", tmp_path / "maintenance-window.lock")
    return path


# --- pure window logic ---------------------------------------------------------

@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 10, 24, 1, 59), None),
    (datetime(2026, 10, 24, 2, 0), "2026-10-24"),
    (datetime(2026, 10, 24, 3, 59), "2026-10-24"),
    (datetime(2026, 10, 24, 4, 0), None),          # window is [start, start+120)
])
def test_window_occurrence_bounds(now, expected):
    assert mw.window_occurrence(now, "02:00", 120) == expected


def test_window_crossing_midnight_belongs_to_the_day_it_started():
    assert mw.window_occurrence(datetime(2026, 10, 25, 0, 30), "23:30", 120) == "2026-10-24"
    assert mw.window_occurrence(datetime(2026, 10, 25, 1, 30), "23:30", 120) is None
    assert mw.window_occurrence(datetime(2026, 10, 24, 23, 45), "23:30", 120) == "2026-10-24"


def test_weekday_restriction_uses_the_start_day():
    # 2026-10-24 is a Saturday (weekday 5).
    assert mw.window_occurrence(datetime(2026, 10, 24, 3, 0), "02:00", 120, [5]) == "2026-10-24"
    assert mw.window_occurrence(datetime(2026, 10, 24, 3, 0), "02:00", 120, [0]) is None
    # Saturday 23:30 window seen on Sunday 00:30 still counts as Saturday's.
    assert mw.window_occurrence(datetime(2026, 10, 25, 0, 30), "23:30", 120, [5]) == "2026-10-24"


def test_invalid_window_never_runs():
    assert mw.window_occurrence(datetime(2026, 10, 24, 2, 0), "2am", 120) is None
    assert mw.window_occurrence(datetime(2026, 10, 24, 2, 0), "24:00", 120) is None


def test_due_occurrence_is_none_once_claimed():
    now = datetime(2026, 10, 24, 2, 10)
    assert mw.due_occurrence({}, now, "02:00", 60) == "2026-10-24"
    assert mw.due_occurrence({"last_occurrence": "2026-10-24"}, now, "02:00", 60) is None
    assert mw.due_occurrence({"last_occurrence": "2026-10-23"}, now, "02:00", 60) == "2026-10-24"


def test_next_window_start():
    assert mw.next_window_start(datetime(2026, 10, 24, 2, 30), "02:00") == datetime(2026, 10, 25, 2, 0)
    assert mw.next_window_start(datetime(2026, 10, 24, 1, 0), "02:00") == datetime(2026, 10, 24, 2, 0)
    assert mw.next_window_start(datetime(2026, 10, 24, 1, 0), "02:00", [0]) == datetime(2026, 10, 26, 2, 0)


# --- the runner, with time and jobs mocked --------------------------------------

def _job(name, calls, result=("ok", "done"), window="02:00"):
    def run():
        calls.append(name)
        if isinstance(result, Exception):
            raise result
        return result
    return mw.Job(name, name, lambda: mw.Schedule(window=window), run)


def test_runs_once_per_window_and_survives_a_restart(state_file):
    calls = []
    jobs = [_job("panel_update", calls)]
    # The old thread missed the window unless a check landed inside the hour;
    # here any tick inside the window runs it.
    assert mw.run_due(datetime(2026, 10, 24, 1, 55), jobs) == []
    ran = mw.run_due(datetime(2026, 10, 24, 3, 40), jobs)
    assert [r["job"] for r in ran] == ["panel_update"]
    # More ticks in the same window, including after a "restart" (state is
    # only on disk), do nothing.
    assert mw.run_due(datetime(2026, 10, 24, 3, 45), jobs) == []
    assert mw.run_due(datetime(2026, 10, 24, 3, 50), [_job("panel_update", calls)]) == []
    assert calls == ["panel_update"]
    # Tomorrow's window runs again.
    assert [r["job"] for r in mw.run_due(datetime(2026, 10, 25, 2, 0), jobs)] == ["panel_update"]
    assert calls == ["panel_update", "panel_update"]
    state = mw.job_states()["panel_update"]
    assert state["last_occurrence"] == "2026-10-25" and state["last_status"] == "ok"


def test_window_is_claimed_before_the_job_runs(state_file):
    seen = {}

    def run():
        seen["claimed"] = mw.job_states()["core_stack"]["last_occurrence"]
        raise KeyboardInterrupt  # the process dies half-way

    job = mw.Job("core_stack", "core", lambda: mw.Schedule(window="03:30"), run)
    with pytest.raises(KeyboardInterrupt):
        mw.run_due(datetime(2026, 10, 24, 3, 35), [job])
    assert seen["claimed"] == "2026-10-24"
    # Restarted later in the same window: not run a second time.
    calls = []
    assert mw.run_due(datetime(2026, 10, 24, 3, 40), [_job("core_stack", calls, window="03:30")]) == []
    assert calls == []


def test_a_failing_job_is_recorded_and_notified(state_file, monkeypatch):
    notified = []
    monkeypatch.setattr(mw, "_notify_failure", lambda job, message: notified.append((job.name, message)))
    calls = []
    ran = mw.run_due(datetime(2026, 10, 24, 2, 5), [_job("php_patch", calls, RuntimeError("apt broke"))])
    assert ran[0]["status"] == "failed"
    assert "apt broke" in mw.job_states()["php_patch"]["last_message"]
    assert notified and notified[0][0] == "php_patch"


def test_disabled_and_broken_schedules_are_skipped(state_file):
    calls = []
    off = mw.Job("off", "off", lambda: None, lambda: calls.append("off") or ("ok", ""))

    def broken_schedule():
        raise ValueError("bad config")
    broken = mw.Job("broken", "broken", broken_schedule, lambda: calls.append("broken") or ("ok", ""))
    good = _job("good", calls)
    ran = mw.run_due(datetime(2026, 10, 24, 2, 1), [off, broken, good])
    assert [r["job"] for r in ran] == ["good"] and calls == ["good"]


def test_config_validation(state_file):
    with pytest.raises(ValueError):
        mw.save_config({"core_stack": {"window": "25:00"}})
    with pytest.raises(ValueError):
        mw.save_config({"core_stack": {"packages": ["nginx", "bash"]}})
    with pytest.raises(ValueError):
        mw.save_config({"core_stack": {"enabled": True, "packages": []}})
    saved = mw.save_config({"window_minutes": 5, "core_stack": {"enabled": True, "window": "04:15",
                                                               "weekdays": [6, 6, 9], "packages": ["openssl", "nginx"]}})
    assert saved["window_minutes"] == mw.MIN_WINDOW_MINUTES
    assert saved["core_stack"] == {"enabled": True, "window": "04:15", "weekdays": [6], "packages": ["nginx", "openssl"]}
    assert mw.load_config() == saved


def test_core_stack_job_follows_its_config(state_file, monkeypatch):
    assert mw._core_stack_schedule() is None
    mw.save_config({"core_stack": {"enabled": True, "window": "04:00", "packages": ["nginx"]}})
    sched = mw._core_stack_schedule()
    assert sched.window == "04:00"

    calls = {}
    monkeypatch.setattr(updates, "set_core_stack_blacklist", lambda enabled, pkgs: calls.setdefault("bl", (enabled, pkgs)))
    monkeypatch.setattr(updates, "upgrade_core_stack_packages",
                        lambda packages, trigger: calls.setdefault("up", (packages, trigger)) and
                        {"status": "completed", "upgraded": ["nginx"], "summary": "x"})
    assert mw._core_stack_run() == ("ok", "Patched: nginx")
    assert calls["bl"] == (True, ["nginx"]) and calls["up"] == (["nginx"], "scheduled")


# --- core stack patch result parsing ---------------------------------------------

def test_parse_core_stack_output():
    out = "Reading package lists...\nupgraded=nginx,openssl\nreloaded=nginx\npost_check=ok\n"
    parsed = updates.parse_core_stack_output(out)
    assert parsed == {"upgraded": ["nginx", "openssl"], "reloaded": ["nginx"], "post_check": "ok"}
    assert updates.parse_core_stack_output("upgraded=\npost_check=ok")["upgraded"] == []


def test_upgrade_core_stack_uses_the_helper_without_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "UPDATE_STATE_FILE", tmp_path / "update-status.json")
    monkeypatch.setattr(updates, "check_core_stack_updates", lambda: {"services": []})
    seen = {}

    def privileged(cmd, helper_args=None, **kwargs):
        seen["cmd"], seen["args"] = cmd, helper_args
        return SimpleNamespace(returncode=0, stdout="upgraded=nginx\nreloaded=nginx\npost_check=ok\n", stderr="")
    monkeypatch.setattr(updates.shell, "privileged", privileged)
    res = updates.upgrade_core_stack_packages(["nginx", "evil;rm"], trigger="scheduled")
    assert seen == {"cmd": "updates-core-stack-patch", "args": ["nginx"]}
    assert res["status"] == "completed" and res["upgraded"] == ["nginx"]
    history = updates._read_update_state()["core_stack_history"][0]
    assert history["trigger"] == "scheduled" and "apt_hold_enforced" not in history

    def failing(cmd, helper_args=None, **kwargs):
        return SimpleNamespace(returncode=1, stdout="upgraded=nginx\npost_check=failed:nginx\n",
                               stderr="spanel-helper: nginx -t post-upgrade configuration test failed")
    monkeypatch.setattr(updates.shell, "privileged", failing)
    res = updates.upgrade_core_stack_packages(["nginx"])
    assert res["status"] == "failed" and "failed:nginx" in res["summary"]


def test_blacklist_toggle_arguments(monkeypatch):
    seen = []
    monkeypatch.setattr(updates.shell, "privileged",
                        lambda cmd, helper_args=None, **kw: seen.append((cmd, helper_args)) or SimpleNamespace(returncode=0))
    updates.set_core_stack_blacklist(True, ["nginx", "bogus", "openssl"])
    updates.set_core_stack_blacklist(True, [])
    updates.set_core_stack_blacklist(False, ["nginx"])
    assert seen == [
        ("updates-core-blacklist", ["on", "nginx", "openssl"]),
        ("updates-core-blacklist", ["off"]),
        ("updates-core-blacklist", ["off"]),
    ]


# --- the helper side -------------------------------------------------------------

def _helper_function(name: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_helper_renders_the_unattended_upgrades_blacklist(tmp_path):
    script = (_helper_function("core_stack_blacklist_patterns") + _helper_function("render_core_blacklist")
              + 'render_core_blacklist "$@"\n')
    script_file = tmp_path / "render.sh"
    script_file.write_bytes(script.encode("utf-8"))
    out = subprocess.run([shutil.which("bash"), script_file.as_posix(), "nginx", "openssl"],
                         capture_output=True, check=True).stdout.decode("utf-8")
    assert "Unattended-Upgrade::Package-Blacklist {" in out and out.rstrip().endswith("};")
    patterns = re.findall(r'^    "([^"]+)";$', out, re.M)
    assert patterns == ["^nginx$", "^nginx-(common|core|full|light|extras)$", "^libnginx-mod-", "^openssl$", "^libssl3"]

    # unattended-upgrades applies each entry with re.match against the name.
    def blocked(name):
        return any(re.match(p, name) for p in patterns)
    for name in ("nginx", "nginx-common", "libnginx-mod-http-geoip2", "openssl", "libssl3t64"):
        assert blocked(name), name
    for name in ("php8.3-fpm", "nginx-foo", "libc6", "mariadb-server"):
        assert not blocked(name), name


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_every_core_stack_package_has_blacklist_patterns(tmp_path):
    script = _helper_function("core_stack_blacklist_patterns") + 'core_stack_blacklist_patterns "$1"\n'
    script_file = tmp_path / "patterns.sh"
    script_file.write_bytes(script.encode("utf-8"))
    for pkg in mw.CORE_STACK_PACKAGES:
        res = subprocess.run([shutil.which("bash"), script_file.as_posix(), pkg], capture_output=True)
        assert res.returncode == 0 and res.stdout.strip(), pkg
        # The package the panel upgrades is itself among what is blacklisted.
        assert any(re.match(line, pkg) for line in res.stdout.decode().split()), pkg
    assert subprocess.run([shutil.which("bash"), script_file.as_posix(), "bash"], capture_output=True).returncode != 0


def test_helper_no_longer_holds_core_packages():
    body = _helper_function("upgrade_core_stack_packages")
    assert "--hold" not in body
    assert "apt-mark hold" not in body
    assert "upgraded=" in body and "post_check=" in body
    assert {s["pkg"] for s in updates.CORE_STACK_SERVICES} == set(mw.CORE_STACK_PACKAGES)
