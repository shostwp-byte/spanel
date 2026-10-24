"""Update rollback: the database snapshot taken before migrations, the
integrity check before a swap, auto_rollback_enabled, snapshot retention."""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
SPANEL_UPDATE = REPO_ROOT / "installer" / "files" / "spanel-update"

from app.services import updates  # noqa: E402

BASH = shutil.which("bash")


def _helper_function(name: str, source: Path = HELPER) -> str:
    text = source.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    # The function's closing brace, not the "}" that ends a JSON heredoc.
    end = re.compile(r"\n}\n(?!EOF)").search(text, start).end()
    return text[start:end]


def _embedded_python(function: str) -> str:
    """The stdlib-only Python a helper function feeds /usr/bin/python3 -I."""
    body = _helper_function(function)
    return body.split("<<'PY'", 1)[1].split("\n", 1)[1].split("\nPY\n", 1)[0]


def _run_snippet(function: str, *args, tmp_path: Path) -> subprocess.CompletedProcess:
    script = tmp_path / f"{function}.py"
    script.write_text(_embedded_python(function), encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(script), *map(str, args)], capture_output=True, text=True)


def _wal_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0045_enterprise_audit_watchdog')")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    return conn


def test_snapshot_copy_includes_rows_still_in_the_wal(tmp_path):
    src = tmp_path / "spanel.db"
    conn = _wal_db(src)
    conn.execute("INSERT INTO users (name) VALUES ('only-in-wal')")
    conn.commit()  # committed, but not checkpointed into the main file
    assert (tmp_path / "spanel.db-wal").stat().st_size > 0
    dst = tmp_path / "copy.db"
    res = _run_snippet("sqlite_online_copy", src, dst, tmp_path=tmp_path)
    assert res.returncode == 0, res.stderr
    copy = sqlite3.connect(dst)
    assert copy.execute("SELECT name FROM users").fetchall() == [("only-in-wal",)]
    copy.close()
    conn.close()

    rev = _run_snippet("sqlite_alembic_revision", dst, tmp_path=tmp_path)
    assert rev.stdout.strip() == "0045_enterprise_audit_watchdog"


def test_integrity_check_gate(tmp_path):
    good = tmp_path / "good.db"
    _wal_db(good).close()
    assert _run_snippet("sqlite_integrity_ok", good, tmp_path=tmp_path).returncode == 0

    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert _run_snippet("sqlite_integrity_ok", empty, tmp_path=tmp_path).returncode == 1

    broken = tmp_path / "broken.db"
    data = bytearray(good.read_bytes())
    data[100:4096] = b"\xff" * (4096 - 100)
    broken.write_bytes(bytes(data))
    assert _run_snippet("sqlite_integrity_ok", broken, tmp_path=tmp_path).returncode == 1

    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database at all" * 100)
    assert _run_snippet("sqlite_integrity_ok", garbage, tmp_path=tmp_path).returncode == 1


def test_swap_checks_integrity_before_stopping_anything_and_keeps_the_old_db():
    body = _helper_function("swap_panel_db")
    check = body.index("sqlite_integrity_ok")
    assert check < body.index("systemctl stop")
    assert body.index("pre-restore-") < body.index('mv -f "$tmp" "$db"')
    # The WAL and shared memory of the old file must not be applied to the new one.
    assert body.index('rm -f "${db}-wal" "${db}-shm"') < body.index('mv -f "$tmp" "$db"')
    assert "spanel-api" in body and "SPANEL_DB_TIMERS" in body


def test_restore_state_writer_does_not_follow_links(tmp_path):
    state = tmp_path / "update-status.json"
    state.write_text('{"last_update_status": "rolling_back", "latest_version": "1.0.8"}', encoding="utf-8")
    res = _run_snippet("panel_update_state_set", state, "rolled_back", "Restored", "snapshot-v1-x", tmp_path=tmp_path)
    assert res.returncode == 0, res.stderr
    import json
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["last_update_status"] == "rolled_back" and data["latest_version"] == "1.0.8"

    target = tmp_path / "secret.txt"
    target.write_text("root only", encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    res = _run_snippet("panel_update_state_set", link, "rolled_back", "x", "s", tmp_path=tmp_path)
    assert res.returncode == 0
    assert target.read_text(encoding="utf-8") == "root only"   # the link was replaced, not followed
    assert not link.is_symlink()


def test_snapshot_keeps_system_files_and_package_set():
    body = _helper_function("panel_snapshot_create")
    assert "system.tar" in body and "pip-freeze.txt" in body and "sqlite_online_copy" in body
    assert 'panel_snapshot_prune "$keep"' in body
    text = HELPER.read_text(encoding="utf-8")
    for glob in ("etc/systemd/system/spanel-*.service", "etc/nginx/conf.d/00-spanel-*.conf", "etc/sudoers.d/spanel"):
        assert f'"{glob}"' in text
    restore = _helper_function("panel_snapshot_restore_system")
    assert "nginx -t" in restore and "system-current.tar" in restore


# --- spanel-update: auto_rollback_enabled and the apply gate -------------------------

def _rollback_harness(tmp_path, *, apply_started: int, auto_rollback: str, helper_ok: bool = True) -> str:
    fake_helper = tmp_path / "spanel-helper"
    fake_helper.write_text(
        "#!/usr/bin/env bash\n"
        "# panel-snapshot-restore <snapshot-id> [--restore-db] [--now]\n"
        f"echo \"$@\" >> '{(tmp_path / 'calls.txt').as_posix()}'\n"
        f"exit {0 if helper_ok else 1}\n", encoding="utf-8")
    fake_helper.chmod(0o755)
    script = "\n".join([
        "set -euo pipefail",
        f"HELPER_BIN='{fake_helper.as_posix()}'",
        "ROLLBACK_SNAPSHOT_ID='snapshot-v1.0.7-20261024T020000Z'",
        f"APPLY_STARTED={apply_started}",
        f"AUTO_ROLLBACK={auto_rollback}",
        "update_state() { echo \"STATE $1 :: $4\"; }",
        "log_step() { :; }",
        "sanitize_msg() { printf '%s' \"$1\"; }",
        "check_panel_health() { return 0; }",
        _helper_function("snapshot_restore_args", SPANEL_UPDATE),
        _helper_function("trigger_auto_rollback", SPANEL_UPDATE),
        "trigger_auto_rollback 'update.sh exited 1'",
    ])
    path = tmp_path / "harness.sh"
    path.write_bytes(script.encode("utf-8"))
    res = subprocess.run([BASH, path.as_posix()], capture_output=True)
    assert res.returncode == 1
    return res.stdout.decode("utf-8")


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_failure_before_apply_rolls_nothing_back(tmp_path):
    out = _rollback_harness(tmp_path, apply_started=0, auto_rollback="true")
    assert "STATE failed" in out
    assert not (tmp_path / "calls.txt").exists()


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_auto_rollback_disabled_is_honoured(tmp_path):
    out = _rollback_harness(tmp_path, apply_started=1, auto_rollback="false")
    assert "auto-rollback disabled" in out
    assert not (tmp_path / "calls.txt").exists()


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_auto_rollback_restores_code_and_database(tmp_path):
    out = _rollback_harness(tmp_path, apply_started=1, auto_rollback="true")
    assert "STATE rolled_back" in out
    calls = (tmp_path / "calls.txt").read_text(encoding="utf-8").split()
    assert calls == ["panel-snapshot-restore", "snapshot-v1.0.7-20261024T020000Z", "--restore-db", "--now"]


def test_spanel_update_reads_rollback_settings_and_gates_the_apply_step():
    text = SPANEL_UPDATE.read_text(encoding="utf-8")
    assert 'src.get("auto_rollback_enabled", True)' in text
    assert "snapshot_keep" in text
    main = text.split("main() {", 1)[1]
    assert main.index("APPLY_STARTED=1") > main.index("bash -n \"$script\"")
    assert main.index("APPLY_STARTED=1") < main.index('/bin/bash "$script"')


# --- the API side ------------------------------------------------------------------

@pytest.fixture()
def update_files(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "UPDATE_STATE_FILE", tmp_path / "update-status.json")
    monkeypatch.setattr(updates, "UPDATE_SOURCE_FILE", tmp_path / "update-source.json")
    return tmp_path


def test_snapshot_keep_setting(update_files, monkeypatch):
    assert updates.get_masked_source_info()["snapshot_keep"] == 5
    updates.save_update_source_config({"snapshot_keep": 99})
    assert updates.get_update_source_config()["snapshot_keep"] == 20
    updates.save_update_source_config({"snapshot_keep": 3, "auto_rollback_enabled": False})
    cfg = updates.get_update_source_config()
    assert cfg["snapshot_keep"] == 3 and cfg["auto_rollback_enabled"] is False

    seen = {}
    monkeypatch.setattr(updates.shell, "privileged",
                        lambda cmd, helper_args=None, **kw: seen.setdefault(cmd, helper_args) and
                        SimpleNamespace(returncode=0, stdout="snapshot-v1-x\n", stderr=""))
    updates.create_snapshot("manual")
    assert seen["panel-snapshot-create"] == ["manual", "3"]


def test_rollback_runs_detached_and_passes_the_database_flag(update_files, monkeypatch):
    seen = {}

    def privileged(cmd, helper_args=None, **kw):
        seen[cmd] = helper_args
        return SimpleNamespace(returncode=0, stdout="scheduled=snapshot-v1.0.7-x\n", stderr="")
    monkeypatch.setattr(updates.shell, "privileged", privileged)
    res = updates.rollback_to_snapshot("snapshot-v1.0.7-x", restore_db=True)
    assert seen["panel-snapshot-restore"] == ["snapshot-v1.0.7-x", "--restore-db"]
    assert res["status"] == "rolling_back"
    assert updates._read_update_state()["last_update_status"] == "rolling_back"

    monkeypatch.setattr(updates.shell, "privileged",
                        lambda cmd, helper_args=None, **kw: SimpleNamespace(returncode=1, stdout="", stderr="integrity_check failed"))
    with pytest.raises(RuntimeError):
        updates.rollback_to_snapshot("snapshot-v1.0.7-x", restore_db=True)
    assert updates._read_update_state()["last_update_status"] == "failed"

    with pytest.raises(ValueError):
        updates.rollback_to_snapshot("../../etc", restore_db=True)


def test_helper_restore_usage_matches_what_spanel_update_greps_for():
    text = HELPER.read_text(encoding="utf-8")
    probe = re.search(r"grep -q '([^']+)'", SPANEL_UPDATE.read_text(encoding="utf-8")).group(1)
    assert re.search(probe, text)
