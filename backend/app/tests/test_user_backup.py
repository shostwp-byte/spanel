"""Full-user backup archives: which ones spanel agrees to restore."""

import json
import tarfile

from app.services import backup


def _manifest_archive(tmp_path, manifest: dict, name: str = "user-x-20260901.tar.gz"):
    archive = tmp_path / name
    path = tmp_path / backup.BACKUP_MANIFEST
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(path, arcname=backup.BACKUP_MANIFEST)
    return archive


def test_describe_accepts_an_opanel_user_backup(tmp_path, monkeypatch):
    archive = _manifest_archive(tmp_path, {
        "kind": "opanel_user",
        "user": {"username": "shop"},
        "websites": [{"domain": "shop.vn"}, {"domain": "blog.vn"}],
    })
    monkeypatch.setattr(backup, "user_backup_path", lambda name: archive)

    described = backup.describe_user_backup(archive.name)

    assert described["valid"] is True
    assert described["source"] == "opanel"
    assert described["websites"] == 2
    assert described["error"] == ""


def test_describe_marks_a_spanel_backup_as_its_own(tmp_path, monkeypatch):
    archive = _manifest_archive(tmp_path, {
        "kind": "spanel_user",
        "user": {"username": "shop"},
        "websites": [],
    })
    monkeypatch.setattr(backup, "user_backup_path", lambda name: archive)

    described = backup.describe_user_backup(archive.name)

    assert described["valid"] is True
    assert described["source"] == "spanel"


def test_describe_rejects_an_unknown_kind(tmp_path, monkeypatch):
    archive = _manifest_archive(tmp_path, {"kind": "cpanel_user"})
    monkeypatch.setattr(backup, "user_backup_path", lambda name: archive)

    described = backup.describe_user_backup(archive.name)

    assert described["valid"] is False
    assert "opanel" in described["error"]


def test_restore_refuses_a_non_user_archive(tmp_path, monkeypatch):
    archive = _manifest_archive(tmp_path, {"kind": "website"})
    monkeypatch.setattr(backup, "user_backup_path", lambda name: archive)

    try:
        backup.restore_user_backup(archive.name, db=None)
    except ValueError as exc:
        assert "opanel" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("restore_user_backup accepted a non-user archive")
