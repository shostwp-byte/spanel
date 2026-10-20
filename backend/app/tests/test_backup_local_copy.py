"""A backup sent off-server is not kept on this disk too.

Operator, 2026-09-27: "Lịch tôi backup chỉ có đẩy lên S3 là file media, tại
sao local lại có 1 bản tương tự. Như vậy tôi đâu cần backup s3 làm gì." On
.88 the schedule to S3 had left 32 archives, 6 GB, under
/var/backups/spanel/users as well. OPanel fixed the same thing in 1720888.
"""
import inspect
import tarfile
from types import SimpleNamespace

import pytest

from app.services import backup, backup_scheduler


class _DB:
    def commit(self):
        pass


def _schedule(target_id):
    return SimpleNamespace(id=1, target_id=target_id, retention=7, last_run_at=None,
                           last_status="", last_message="", name_suffix=None)


@pytest.fixture
def run(tmp_path, monkeypatch):
    """run_one for one account, with the archive written for real and the
    upload replaced by `upload`."""
    root = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_root", str(root))
    monkeypatch.setattr(backup.settings, "command_dry_run", False)
    monkeypatch.setattr(backup_scheduler, "_schedule_users", lambda db, s: [SimpleNamespace(username="acme")])
    monkeypatch.setattr(backup_scheduler, "_notify_failure", lambda *a, **k: None)

    def create(user, db):
        path = root / "users" / user.username / f"user-{user.username}-20260927020000.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"archive")
        return str(path)

    monkeypatch.setattr(backup, "create_user_backup", create)

    def go(schedule, upload):
        monkeypatch.setattr(backup_scheduler, "_upload_if_configured", upload)
        ok = backup_scheduler.run_one(_DB(), schedule)
        return ok, sorted(p.name for p in (root / "users" / "acme").iterdir())

    return go


def test_an_uploaded_archive_is_not_kept_here(run):
    ok, left = run(_schedule(1), lambda db, s, archive: "file media:acme/user-acme-Sat.tar.gz")
    assert ok
    assert left == []


def test_a_failed_upload_keeps_the_only_copy(run):
    def fail(db, s, archive):
        raise RuntimeError("S3 unreachable")

    ok, left = run(_schedule(1), fail)
    assert not ok
    assert left == ["user-acme-20260927020000.tar.gz"]


def test_a_schedule_without_a_destination_keeps_its_archives(run):
    ok, left = run(_schedule(None), lambda db, s, archive: archive)
    assert ok
    assert left == ["user-acme-20260927020000.tar.gz"]


def test_discard_never_reaches_outside_the_backup_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path / "backups"))
    outside = tmp_path / "elsewhere.tar.gz"
    outside.write_bytes(b"x")
    backup.discard_local_copy(str(outside))
    assert outside.exists()


def test_manual_uploads_discard_the_local_copy_too():
    from app.api import maintenance

    for job in (maintenance._run_user_backup_job, maintenance._run_sftp_backup_job):
        source = inspect.getsource(job)
        assert source.index("upload_archive_to_target(") < source.index("backup.discard_local_copy(archive)"), job.__name__
    user_job = inspect.getsource(maintenance._run_user_backup_job)
    assert "if remote_file:\n            # Sent off-server" in user_job, "a local-only backup stays"


def test_a_site_backup_leaves_no_loose_sql_dump(tmp_path, monkeypatch):
    site_root = tmp_path / "site"
    (site_root / "public_html").mkdir(parents=True)
    (site_root / "public_html" / "index.php").write_text("<?php", encoding="utf-8")
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(backup.mariadb, "export_database",
                        lambda name, out: open(out, "w", encoding="utf-8").write("-- dump\n"))

    archive = backup.create_backup(SimpleNamespace(domain="acme.test", root_path=str(site_root)), "acme_wp")

    with tarfile.open(archive) as tar:
        assert any(name.startswith("database/") and name.endswith(".sql") for name in tar.getnames())
    assert list((tmp_path / "backups" / "acme.test").glob("*.sql")) == []
