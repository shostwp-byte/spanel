"""Scheduled backup verification: local newest archives, a remote sample,
the panel self-backup's structure, and test-restoring SQL dumps."""
import io
import json
import os
import random
import shutil
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services import backup, backup_remote, backup_verify  # noqa: E402

PASSPHRASE = "verify-test-passphrase-123"


def _make_archive(path: Path, *, manifest=True, sql=b"CREATE TABLE t (id INT);\n", kind="spanel_user") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if manifest:
            add("manifest.json", json.dumps({"kind": kind, "user": {"username": "x"}}).encode())
        if sql is not None:
            add("databases/example.com.sql", sql)
        add("sites/example.com/site/index.php", b"<?php echo 1;")
    backup._write_sha256_sidecar(path)
    return path


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_verify, "CONFIG_FILE", tmp_path / "backup-verify.json")
    monkeypatch.setattr(backup_verify.settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(backup, "get_server_hostname", lambda: "srv1")
    monkeypatch.setattr(backup_verify, "_server_targets", lambda db: [])
    monkeypatch.setattr(backup_verify, "_accounts", lambda db: [])
    reported = []
    monkeypatch.setattr(backup_verify, "_report", lambda failed, started: reported.append(failed))
    monkeypatch.delenv("SPANEL_BACKUP_PASSPHRASE", raising=False)
    users = tmp_path / "backups" / "users" / "srv1"
    return SimpleNamespace(users=users, tmp=tmp_path, reported=reported)


def test_newest_local_archive_of_each_account_is_checked(roots):
    old = _make_archive(roots.users / "alice" / "user-alice-20261020020000.tar.gz")
    new = _make_archive(roots.users / "alice" / "user-alice-20261024020000.tar.gz")
    os.utime(old, (1, 1))
    _make_archive(roots.users / "bob" / "user-bob-20261024020000.tar.gz")
    _make_archive(roots.tmp / "backups" / "users" / "restore" / "user-zed-20261024020000.tar.gz", manifest=False)
    assert [p.name for p in backup_verify.latest_local_archives()] == [new.name, "user-bob-20261024020000.tar.gz"]

    run = backup_verify.run_verification(db=None)
    assert run["status"] == "ok" and run["checked"] == 2
    check = run["checks"][0]
    assert check["manifest"] and check["databases"] == ["databases/example.com.sql"] and check["sidecar_match"]
    assert roots.reported == []
    assert backup_verify.load_config()["last_run"]["status"] == "ok"


def test_damaged_archives_are_reported(roots):
    bad = _make_archive(roots.users / "alice" / "user-alice-20261024020000.tar.gz")
    data = bytearray(bad.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad.write_bytes(bytes(data))          # the sidecar still has the old digest
    _make_archive(roots.users / "bob" / "user-bob-20261024020000.tar.gz", manifest=False)
    run = backup_verify.run_verification(db=None)
    details = {c["name"]: c["detail"] for c in run["checks"]}
    assert "SHA-256 does not match" in details["user-alice-20261024020000.tar.gz"]
    assert "No manifest.json" in details["user-bob-20261024020000.tar.gz"]
    assert run["status"] == "failed" and len(roots.reported[0]) == 2


def test_encrypted_archive_needs_the_scheduler_passphrase(roots, monkeypatch):
    path = _make_archive(roots.users / "carol" / "user-carol-20261024020000.tar.gz")
    backup.encrypt_backup_archive(path, PASSPHRASE)
    run = backup_verify.run_verification(db=None)
    assert run["checks"][0]["ok"] is False and "SPANEL_BACKUP_PASSPHRASE" in run["checks"][0]["detail"]

    monkeypatch.setenv("SPANEL_BACKUP_PASSPHRASE", PASSPHRASE)
    run = backup_verify.run_verification(db=None)
    assert run["checks"][0]["ok"] is True and run["checks"][0]["encrypted"] is True
    # No decrypted copy is left behind.
    assert not list(Path(backup_verify.settings.backup_root).rglob("verify-*.tar.gz"))


def test_remote_sample_is_downloaded_and_checked(roots, monkeypatch):
    good = _make_archive(roots.tmp / "remote-store" / "user-alice-2026-10-24.tar.gz")
    dr = roots.tmp / "remote-store" / "panel-dr-srv1-20261024T013000Z.spdr"
    dr.write_bytes(b"Salted__" + b"\x01" * 200 + b"SPNLMAC1" + b"\x02" * 32)
    target = SimpleNamespace(name="s3-main")
    monkeypatch.setattr(backup_verify, "_server_targets", lambda db: [target])
    monkeypatch.setattr(backup_verify, "_accounts", lambda db: ["alice", "bob"])
    listed = []

    def list_files(t, subpath, suffixes=(".tar.gz",)):
        listed.append(subpath)
        if subpath == "srv1/alice":
            return [{"name": good.name, "key": "srv1/alice/" + good.name, "size": good.stat().st_size, "modified": ""}]
        if subpath == "srv1/_panel-dr":
            return [{"name": dr.name, "key": "srv1/_panel-dr/" + dr.name, "size": dr.stat().st_size, "modified": ""}]
        return []

    def download(t, key, destination):
        shutil.copyfile(good if key.endswith(".tar.gz") else dr, destination)
        return destination
    monkeypatch.setattr(backup_remote, "list_files", list_files)
    monkeypatch.setattr(backup_remote, "download", download)
    backup_verify.save_settings({"remote_samples": 2})
    run = backup_verify.run_verification(db=None, rng=random.Random(1))
    remote = [c for c in run["checks"] if c["scope"] == "remote"]
    assert remote and remote[0]["ok"] and remote[0]["name"] == good.name and remote[0]["target"] == "s3-main"
    dr_check = [c for c in run["checks"] if c["scope"] == "remote-dr"][0]
    assert dr_check["ok"] and dr_check["name"] == dr.name
    assert {"srv1/alice", "srv1/bob", "srv1/_panel-dr"} <= set(listed)
    # Scratch copies are gone.
    assert not [p for p in (Path(backup_verify.settings.backup_root) / ".verify-work").rglob("*") if p.is_file()]


def test_remote_download_failures_and_short_files_fail(roots, monkeypatch):
    good = _make_archive(roots.tmp / "remote-store" / "user-alice-2026-10-24.tar.gz")
    monkeypatch.setattr(backup_verify, "_server_targets", lambda db: [SimpleNamespace(name="ftp-nas")])
    monkeypatch.setattr(backup_verify, "_accounts", lambda db: ["alice"])
    monkeypatch.setattr(backup_remote, "list_files", lambda t, subpath, suffixes=(".tar.gz",): (
        [{"name": good.name, "key": "k", "size": good.stat().st_size + 10, "modified": ""}] if subpath.endswith("alice") else []))
    monkeypatch.setattr(backup_remote, "download", lambda t, key, destination: shutil.copyfile(good, destination))
    run = backup_verify.run_verification(db=None)
    assert run["checks"][0]["ok"] is False and "the listing said" in run["checks"][0]["detail"]

    def broken(t, key, destination):
        raise backup_remote.RemoteError("connection reset")
    monkeypatch.setattr(backup_remote, "download", broken)
    run = backup_verify.run_verification(db=None)
    assert "download failed" in run["checks"][0]["detail"]


def test_dr_structure_check(tmp_path):
    good = tmp_path / "a.spdr"
    good.write_bytes(b"Salted__" + b"x" * 100 + b"SPNLMAC1" + b"y" * 32)
    assert backup_verify.check_dr_archive_structure(good)["ok"]
    truncated = tmp_path / "b.spdr"
    truncated.write_bytes(b"Salted__" + b"x" * 100)
    assert not backup_verify.check_dr_archive_structure(truncated)["ok"]


def test_sql_dumps_are_test_restored(roots, monkeypatch):
    _make_archive(roots.users / "alice" / "user-alice-20261024020000.tar.gz")
    _make_archive(roots.users / "bob" / "user-bob-20261024020000.tar.gz", sql=b"THIS IS NOT SQL;\n")
    seen = []

    def fake_restore(sql_path):
        text = Path(sql_path).read_bytes()
        seen.append(text)
        return {"ok": b"CREATE TABLE" in text, "tables": 1 if b"CREATE TABLE" in text else 0,
                "detail": "1 table(s) loaded" if b"CREATE TABLE" in text else "ERROR 1064 (42000)"}
    monkeypatch.setattr(backup_verify, "trial_restore_sql", fake_restore)
    backup_verify.save_settings({"test_restore_db": True})
    run = backup_verify.run_verification(db=None)
    by_name = {c["name"]: c for c in run["checks"]}
    assert by_name["user-alice-20261024020000.tar.gz"]["ok"] and by_name["user-alice-20261024020000.tar.gz"]["db_test"][0]["tables"] == 1
    assert not by_name["user-bob-20261024020000.tar.gz"]["ok"]
    assert "did not load" in by_name["user-bob-20261024020000.tar.gz"]["detail"]
    assert len(seen) == 2


def test_throwaway_database_is_always_dropped(monkeypatch, tmp_path):
    from app.services import mariadb

    statements = []
    monkeypatch.setattr(mariadb, "_run_sql", lambda sql, check=True: statements.append(sql) or SimpleNamespace(stdout=""))

    def failing_import(name, path):
        raise RuntimeError("Database import failed: ERROR 1064")
    monkeypatch.setattr(mariadb, "import_database_as_panel", failing_import)
    sql = tmp_path / "d.sql"
    sql.write_text("garbage", encoding="utf-8")
    result = backup_verify.trial_restore_sql(sql)
    assert result["ok"] is False and "1064" in result["detail"]
    assert statements[0].startswith("CREATE DATABASE `spanel_verify_")
    assert statements[-1].startswith("DROP DATABASE IF EXISTS `spanel_verify_")
    assert statements[0].split("`")[1] == statements[-1].split("`")[1]

    statements.clear()
    monkeypatch.setattr(mariadb, "import_database_as_panel", lambda name, path: None)
    monkeypatch.setattr(mariadb.shell, "run", lambda *a, **k: SimpleNamespace(stdout="3\n", stderr="", returncode=0))
    assert backup_verify.trial_restore_sql(sql) == {"ok": True, "tables": 3, "detail": "3 table(s) loaded"}
    assert statements[-1].startswith("DROP DATABASE")


def test_settings_and_schedule(roots):
    with pytest.raises(ValueError):
        backup_verify.save_settings({"window": "25:61"})
    assert backup_verify.scheduled_window() is None
    backup_verify.save_settings({"enabled": True, "window": "05:15", "weekdays": [0, 3]})
    sched = backup_verify.scheduled_window()
    assert sched.window == "05:15" and sched.weekdays == [0, 3]
    from app.services import maintenance_window

    assert "backup_verify" in [job.name for job in maintenance_window.default_jobs()]


def test_failure_notice_renders():
    from app.services import notify_messages

    title, body = notify_messages.render("backup_verify_failed", {"errors": ["s3: a.tar.gz: bad"]}, "en")
    assert "1 problem" in title and "s3: a.tar.gz: bad" in body
