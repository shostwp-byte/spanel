"""The DirectAdmin upload directory lives somewhere the panel cannot create.

/home/admin is root-owned, and the API runs as spanel. Both the upload endpoint
and the listing behind the import page used to call DA_BACKUP_DIR.mkdir()
directly, so on a server where that directory did not already exist - any fresh
install - the page and the upload both answered 500 and the uploaded file went
nowhere. Nothing appeared in the log, because nothing caught the exception to
write one.
"""

from pathlib import Path

import pytest

from app.services import da_import
from app.services.shell import CommandResult

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


def test_listing_survives_a_directory_it_cannot_create(monkeypatch, tmp_path):
    """The import page must render, not 500, when the directory is missing."""
    missing = tmp_path / "not-creatable" / "da"

    class Denied(type(missing)):
        pass

    monkeypatch.setattr(da_import, "DA_BACKUP_DIR", missing)
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR_IS_DEFAULT", False)

    def boom(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(missing), "mkdir", boom, raising=False)

    assert da_import.list_da_backups() == []


def test_ensure_backup_dir_never_raises(monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "da"
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR", missing)
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR_IS_DEFAULT", False)
    monkeypatch.setattr(type(missing), "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "nope")),
                        raising=False)

    da_import.ensure_backup_dir()  # must not raise


def test_the_helper_is_asked_when_the_path_is_the_default(monkeypatch, tmp_path):
    """Only root can create it, so the panel has to go through the helper."""
    missing = tmp_path / "default" / "da"
    calls = []

    monkeypatch.setattr(da_import, "DA_BACKUP_DIR", missing)
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR_IS_DEFAULT", True)

    def fake_privileged(verb, **kwargs):
        calls.append(verb)
        missing.mkdir(parents=True, exist_ok=True)
        return CommandResult(command=verb, returncode=0, stdout=str(missing), stderr="")

    monkeypatch.setattr(da_import.shell, "privileged", fake_privileged)
    da_import.ensure_backup_dir()

    assert calls == ["da-backup-dir-ensure"]
    assert missing.is_dir()


def test_an_overridden_path_is_not_handed_to_the_helper(monkeypatch, tmp_path):
    """spanel_DA_BACKUP_DIR points somewhere the helper knows nothing about."""
    custom = tmp_path / "custom" / "da"
    calls = []
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR", custom)
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR_IS_DEFAULT", False)
    monkeypatch.setattr(da_import.shell, "privileged", lambda *a, **k: calls.append(a))

    da_import.ensure_backup_dir()

    assert calls == []
    assert custom.is_dir()


def test_an_existing_writable_directory_costs_nothing(monkeypatch, tmp_path):
    ready = tmp_path / "ready"
    ready.mkdir()
    calls = []
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR", ready)
    monkeypatch.setattr(da_import, "DA_BACKUP_DIR_IS_DEFAULT", True)
    monkeypatch.setattr(da_import.shell, "privileged", lambda *a, **k: calls.append(a))

    da_import.ensure_backup_dir()

    assert calls == []


def test_helper_creates_it_as_root_and_gives_the_group_to_spanel():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "da-backup-dir-ensure)" in helper
    body = helper.split("ensure_da_backup_dir()")[1].split("\n}\n")[0]
    # spanel has to be able to write, and only root can hand it that.
    assert "-o root -g spanel" in body
    assert "/home/admin/spanel_backups" in body
