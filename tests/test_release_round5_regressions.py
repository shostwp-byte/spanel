"""Regressions found by the round-5 review, most of them on a real server.

Each test names the fault it guards against, so a failure says what broke.
"""
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
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
