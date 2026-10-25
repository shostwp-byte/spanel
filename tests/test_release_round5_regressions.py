"""Regressions found by the round-5 review, most of them on a real server.

Each test names the fault it guards against, so a failure says what broke.
"""
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
UPDATE_SH = REPO_ROOT / "installer" / "update.sh"
BASH = shutil.which("bash")
RSYNC = shutil.which("rsync")
PYTHON3 = shutil.which("python3")


def _update_function(name: str) -> str:
    text = UPDATE_SH.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
    assert match, f"function {name} not found in update.sh"
    return match.group(0)


def _backend_rsync_filters() -> list[str]:
    text = UPDATE_SH.read_text(encoding="utf-8")
    block = text.split('rsync -a --delete \\', 1)[1].split('"$SOURCE_DIR/backend/"', 1)[0]
    return [f"--filter={f}" for f in re.findall(r"--filter='([^']+)'", block)]


def _run(tmp_path: Path, script: str, env: dict) -> subprocess.CompletedProcess:
    import os

    script_file = tmp_path / "run.sh"
    script_file.write_bytes(script.encode("utf-8"))
    run_env = dict(os.environ)
    run_env.update(env)
    return subprocess.run([BASH, script_file.as_posix()], capture_output=True, text=True, env=run_env)


def _wal_database(path: Path) -> sqlite3.Connection:
    """A database whose newest row exists only in its WAL. The connection is
    returned open: closing the last connection would checkpoint it."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE websites (domain TEXT)")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("INSERT INTO websites VALUES ('only-in-wal.example')")
    conn.commit()
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    return conn


# --- the update must not delete the SQLite WAL ------------------------------

def test_backend_sync_protects_every_sqlite_file():
    filters = _backend_rsync_filters()
    for name in ("spanel.db", "spanel.db-wal", "spanel.db-shm", "spanel.db-journal"):
        assert f"--filter=protect /{name}" in filters, name


@pytest.mark.skipif(not RSYNC, reason="needs rsync")
def test_backend_sync_keeps_the_wal_of_a_running_panel(tmp_path):
    """rsync --delete protected spanel.db but not spanel.db-wal: every row
    written since the last checkpoint vanished on update (a whole website on
    the test server)."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "app").mkdir(parents=True)
    (src / "app" / "main.py").write_text("new\n")
    dst.mkdir()
    for name in ("spanel.db", "spanel.db-wal", "spanel.db-shm", "spanel.db-journal", "stale.py"):
        (dst / name).write_text(name)

    subprocess.run([RSYNC, "-a", "--delete", *_backend_rsync_filters(), f"{src}/", f"{dst}/"], check=True)

    for name in ("spanel.db", "spanel.db-wal", "spanel.db-shm", "spanel.db-journal"):
        assert (dst / name).read_text() == name
    assert not (dst / "stale.py").exists()
    assert (dst / "app" / "main.py").read_text() == "new\n"


@pytest.mark.skipif(not (BASH and PYTHON3), reason="needs bash and python3")
def test_pre_update_snapshot_contains_rows_still_in_the_wal(tmp_path):
    """backup_db fell back to cp when the sqlite3 CLI was missing (stock
    Ubuntu), and a copy of spanel.db alone has none of the WAL's rows."""
    app_dir = tmp_path / "app"
    (app_dir / "backend").mkdir(parents=True)
    db = app_dir / "backend" / "spanel.db"
    writer = _wal_database(db)
    try:
        # No spanel account here, even on a box that has one: the scratch
        # tree belongs to whoever runs the tests.
        script = "id() { return 1; }\n" + _update_function("backup_db") + "backup_db\n"
        proc = _run(tmp_path, script, {"APP_DIR": str(app_dir), "BACKUP_ROOT": str(tmp_path / "backups")})
        assert proc.returncode == 0, proc.stderr
    finally:
        writer.close()

    snaps = list((tmp_path / "backups" / "db-snapshots").glob("spanel-*.db"))
    assert len(snaps) == 1
    rows = sqlite3.connect(snaps[0]).execute("SELECT domain FROM websites").fetchall()
    assert rows == [("only-in-wal.example",)]


def test_pre_update_snapshot_never_falls_back_to_cp():
    body = _update_function("backup_db")
    assert "cp -a" not in body
    assert "xargs -r rm" not in body.replace("xargs -0 -r rm", "")


# --- the cron migration must open the panel's real database -----------------

def test_cron_migration_runs_the_panel_code_from_backend():
    """Stage 2 ran after `cd frontend`. The app reads .env (and DATABASE_URL)
    relative to the working directory, so it opened an empty
    frontend/spanel.db and migrated nothing, on every update."""
    from app.core.config import Settings

    assert Settings.Config.env_file == ".env"  # relative: the cwd decides
    body = _update_function("migrate_site_cron_php_binary")
    stage2 = body.split("mig_out=", 1)[1].split("<<'PY_MIGRATE'", 1)[0]
    assert stage2.lstrip().startswith('"$(cd "$APP_DIR/backend" && sudo -u spanel')
    # Nothing in the app reads it; it only looked like the path was passed.
    assert "SPANEL_DB_PATH" not in UPDATE_SH.read_text(encoding="utf-8")


def test_update_removes_the_empty_database_left_in_frontend():
    text = UPDATE_SH.read_text(encoding="utf-8")
    after = text.split("\nmigrate_site_cron_php_binary\n", 1)[1].split("nginx -t", 1)[0]
    assert 'rm -f -- "$APP_DIR/frontend/spanel.db"' in after


# --- root never acts by name inside the panel account's directories --------

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
SYS_PYTHON = Path("/usr/bin/python3")
needs_linux_tools = pytest.mark.skipif(
    not (BASH and SYS_PYTHON.exists()), reason="needs bash and /usr/bin/python3"
)


def _helper_function(name: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", text, re.M | re.S)
    assert match, f"function {name} not found in spanel-helper.sh"
    return match.group(0)


# Stand-ins for what a test box lacks: no spanel account, no systemd, no
# root. runuser runs the command as the caller; install drops -o/-g.
SWAP_STUBS = r'''
set -euo pipefail
deny() { echo "spanel-helper: $*" >&2; exit 1; }
systemctl() { [[ "${1:-}" == "is-active" ]] && return 1; return 0; }
runuser() { shift 3; "$@"; }
install() {
  local -a args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
  done
  command install "${args[@]}"
}
mktemp() { command mktemp -d "$TEST_TMP/stage.XXXXXX"; }
panel_db_path() { echo "$TEST_DB"; }
SPANEL_DB_TIMERS=(a.timer)
SPANEL_DB_ONESHOTS=(a.service)
'''


def _swap_script(src: Path, pre_dir: Path) -> str:
    return (
        SWAP_STUBS
        + _helper_function("sqlite_online_copy")
        + _helper_function("sqlite_integrity_ok")
        + _helper_function("start_panel_db_units")
        + _helper_function("swap_panel_db")
        + f'PANEL_DB_PRE_RESTORE_DIR="{pre_dir}"\nswap_panel_db "{src}"\n'
    )


def _one_row_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


@needs_linux_tools
def test_db_swap_keeps_root_out_of_the_panel_accounts_directory(tmp_path):
    """swap_panel_db wrote, chowned and pruned spanel.db.pre-restore-* by name
    in backend/ (spanel's), and pruned with `ls | xargs rm`: a planted link or
    name pointed root's write, chown or rm at any file."""
    backend = tmp_path / "backend"
    backend.mkdir()
    db = backend / "spanel.db"
    _one_row_db(db, "current")
    replacement = tmp_path / "replacement.db"
    _one_row_db(replacement, "restored")
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    # What the account could leave behind in its own directory.
    (backend / "spanel.db.pre-restore-20990101T000000Z").symlink_to(victim)
    (backend / "spanel.db.pre-restore-x y").write_text("odd name")
    pre_dir = tmp_path / "root-only" / "db-pre-restore"

    proc = _run(tmp_path, _swap_script(replacement, pre_dir), {"TEST_TMP": str(tmp_path), "TEST_DB": str(db)})
    assert proc.returncode == 0, proc.stderr

    assert sqlite3.connect(db).execute("SELECT v FROM t").fetchall() == [("restored",)]
    previous = list(pre_dir.glob("spanel.db.pre-restore-*"))
    assert len(previous) == 1
    assert sqlite3.connect(previous[0]).execute("SELECT v FROM t").fetchall() == [("current",)]
    assert victim.read_text() == "untouched"
    assert (backend / "spanel.db.pre-restore-20990101T000000Z").is_symlink()
    assert (backend / "spanel.db.pre-restore-x y").read_text() == "odd name"


@needs_linux_tools
def test_db_swap_refuses_a_symlinked_source(tmp_path):
    real = tmp_path / "real.db"
    _one_row_db(real, "elsewhere")
    link = tmp_path / "spanel-20990101T000000Z-manual.db"
    link.symlink_to(real)
    db = tmp_path / "spanel.db"
    proc = _run(tmp_path, _swap_script(link, tmp_path / "pre"), {"TEST_TMP": str(tmp_path), "TEST_DB": str(db)})
    assert proc.returncode != 0
    assert "database copy not found" in proc.stderr
    assert not db.exists()


def test_panel_snapshots_live_outside_the_accounts_backup_root():
    """/var/backups/spanel is spanel's: it could swap panel-snapshots/ for its
    own, and panel-snapshot-restore installed the spanel-helper it found."""
    helper = HELPER.read_text(encoding="utf-8")
    assert 'PANEL_SNAPSHOT_DIR="/var/backups/spanel-panel-snapshots"' in helper
    assert 'PANEL_DB_PRE_RESTORE_DIR="/var/backups/spanel-panel-snapshots/db-pre-restore"' in helper
    update = (REPO_ROOT / "installer" / "files" / "spanel-update").read_text(encoding="utf-8")
    assert 'SNAPSHOT_DIR="/var/backups/spanel-panel-snapshots"' in update
    from app.services import updates

    assert updates.SNAPSHOT_DIR.as_posix() == "/var/backups/spanel-panel-snapshots"

    assert 'panel_snapshot_trusted "$tar_path"' in _helper_function("panel_snapshot_restore")
    trusted = _helper_function("panel_snapshot_trusted")
    assert "! -L" in trusted and "%u" in trusted
    ensure = _helper_function("panel_snapshot_dir_ensure")
    assert "-user root" in ensure and "-type f" in ensure


@needs_linux_tools
def test_legacy_snapshot_links_are_not_carried_over(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    outside = tmp_path / "outside.tar.gz"
    outside.write_text("not a snapshot")
    (legacy / "snapshot-v9-evil.tar.gz").symlink_to(outside)
    new_dir = tmp_path / "new"
    script = (
        SWAP_STUBS
        + _helper_function("panel_snapshot_dir_ensure")
        + f'PANEL_SNAPSHOT_DIR="{new_dir}"\nLEGACY_PANEL_SNAPSHOT_DIR="{legacy}"\npanel_snapshot_dir_ensure\n'
    )
    proc = _run(tmp_path, script, {"TEST_TMP": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    moved = new_dir / "snapshot-v9-evil.tar.gz"
    assert not moved.exists() and not moved.is_symlink()


def test_code_tree_restore_is_written_by_the_account():
    restore = _helper_function("panel_snapshot_restore")
    assert 'cp -a "$work/backend/app"' not in restore
    assert 'panel_snapshot_restore_tree "$work/backend" app' in restore
    assert "runuser -u spanel -- tar" in _helper_function("panel_snapshot_restore_tree")


@needs_linux_tools
def test_update_state_file_is_never_written_through_a_link(tmp_path):
    """write_update_state wrote through, then chowned by name, a file in
    /var/lib/spanel: a link there handed any file on the box to spanel."""
    import json

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text('{"secret": true}\n')
    state_file = state_dir / "update-status.json"
    state_file.symlink_to(victim)
    script = (
        "set -euo pipefail\ncurrent_panel_version() { echo 1.0.7; }\n"
        + _update_function("write_update_state")
        + _update_function("update_progress")
        + f'UPDATE_STATE_FILE="{state_file}"\n'
        + 'write_update_state updating v1.0.8 "hello"\nupdate_progress 40 backend "half way"\n'
    )
    proc = _run(tmp_path, script, {})
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text() == '{"secret": true}\n'
    assert not state_file.is_symlink()
    state = json.loads(state_file.read_text())
    assert state["last_update_status"] == "updating"
    assert state["progress_percent"] == 40
    assert "secret" not in state
    assert state_file.stat().st_mode & 0o777 == 0o640


def test_update_never_lets_root_follow_the_accounts_names():
    update = UPDATE_SH.read_text(encoding="utf-8")
    install = (REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    # Python run as root never imports from the cwd (backend/ is spanel's).
    assert update.index("export PYTHONSAFEPATH=1") < update.index("\nlog()")
    assert 'chown spanel:spanel "$UPDATE_STATE_FILE"' not in update
    for text in (update, install):
        for line in text.splitlines():
            if "-exec chown" in line:
                assert "-exec chown -h" in line, line
    assert 'release_requirements="$SOURCE_DIR/backend/requirements.txt"' in update
    assert 'pip install -r "$release_requirements"' in update
