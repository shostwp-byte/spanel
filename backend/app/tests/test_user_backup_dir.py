"""The schedule that ran every night and saved nothing.

    ok 0 user(s); sieunhim: [Errno 13] Permission denied:
    '/var/backups/spanel/users/sieunhim'

The panel owns /var/backups/spanel and creates account directories one level
below it, in /var/backups/spanel/users. Nothing ever created that middle
directory - not install.sh, not the helper - so it belonged to whichever
process reached it first. Where that was a root one it came out root:root
0755, and from then on the panel could read it and not write in it. Every
scheduled backup for every account failed, and the only trace was an errno in
the schedule's last message.

Three layers, because each covers a case the others cannot:
  install.sh  creates it correctly on new servers
  update.sh   repairs the ones already out there
  the helper  fixes it at the moment a backup needs it, whatever put it back
"""

import os
from pathlib import Path

import pytest

from app.services import backup
from app.services.shell import CommandResult

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def test_a_writable_parent_costs_no_helper_call(monkeypatch, tmp_path):
    """The common path stays a plain mkdir - no sudo on every backup."""
    calls = []
    monkeypatch.setattr(backup.shell, "privileged", lambda *a, **k: calls.append(a))

    created = backup._ensure_user_dir(tmp_path / "users" / "alice")

    assert created.is_dir()
    assert calls == []


def test_a_root_owned_parent_goes_through_the_helper(monkeypatch, tmp_path):
    """What actually happened on .88: users/ was root:root, the leaf was not."""
    target = tmp_path / "users" / "sieunhim"
    calls = []

    real_mkdir = Path.mkdir

    def denied(self, *args, **kwargs):
        if self == target and not target.exists():
            raise PermissionError(13, "Permission denied", str(target))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)

    def fake_privileged(verb, helper_args=None, **kwargs):
        calls.append((verb, helper_args))
        os.makedirs(target, exist_ok=True)
        return CommandResult(command=verb, returncode=0, stdout=str(target), stderr="")

    monkeypatch.setattr(backup.shell, "privileged", fake_privileged)

    assert backup._ensure_user_dir(target) == target
    assert calls == [("user-backup-dir-ensure", ["sieunhim"])]


def test_the_original_error_survives_a_helper_that_cannot_help(monkeypatch, tmp_path):
    """The operator needs the errno, not "the helper returned 1"."""
    target = tmp_path / "users" / "sieunhim"

    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(target))

    monkeypatch.setattr(Path, "mkdir", denied)
    monkeypatch.setattr(backup.shell, "privileged",
                        lambda *a, **k: CommandResult(command="x", returncode=1, stdout="", stderr="no"))

    with pytest.raises(PermissionError):
        backup._ensure_user_dir(target)


def test_only_a_name_crosses_the_sudo_boundary():
    """The helper builds the path; the API process only names the leaf.

    Handing it a full path would make a compromised API process able to point
    a root chown anywhere under any parent it could name.
    """
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "backup.py").read_text(encoding="utf-8")
    body = source.split("def _ensure_user_dir(")[1].split("\ndef ")[0]
    assert "helper_args=[path.name]" in body
    assert "str(path)" not in body.split("fallback=")[0], (
        "the helper argument must be the leaf name, never the path"
    )


def test_every_writer_under_users_goes_through_it():
    """One missed call site is one more schedule that fails at 2am.

    Four places create something under <backup_root>/users on this branch: the
    nightly archive, the directory an application payload is unpacked into
    during a restore, the upload endpoint, and the staging directory a remote
    backup is downloaded into. Each is a separate way to hit the same errno,
    so each is named here rather than counted - a count passes the day someone
    adds a writer and forgets.
    """
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "backup.py").read_text(encoding="utf-8")
    assert "def _ensure_user_dir(" in source
    for writer in ("_ensure_user_dir(_user_backup_dir(user.username))",
                   "_ensure_user_dir(staging_dir)",
                   "_ensure_user_dir(_user_restore_dir())"):
        assert writer in source, f"{writer} is not going through the helper"
    # The restore directory has two writers here: the upload endpoint and
    # stage_remote_backup. On main there is only the first.
    assert source.count("_ensure_user_dir(_user_restore_dir())") == 2

    # Nothing under users/ may still mkdir on its own. The per-domain and
    # per-site directories elsewhere in this file are a different matter: the
    # panel owns <backup_root> itself and can always write there.
    assert "staging_dir.mkdir(" not in source
    assert "backup_dir = _user_backup_dir(user.username)" not in source


def test_the_helper_hands_the_directory_to_the_panel():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "user-backup-dir-ensure)" in helper
    body = helper.split("ensure_user_backup_dir()")[1].split("\n}\n")[0]
    # root:spanel 0750 would not do: the panel has to create files in here.
    assert "-o spanel -g spanel" in body
    assert '"${BACKUP_ROOT}/users"' in body
    # The name is validated against the same shape the API enforces, and the
    # path is built here rather than accepted from the caller.
    assert "^[A-Za-z0-9._-]{3,64}$" in body
    assert '".."' in body


def test_new_installs_get_the_directory_up_front():
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert 'install -d -o spanel -g spanel -m 0750 "$BACKUP_ROOT/users"' in install
    assert 'install -d -o spanel -g spanel -m 0750 "$BACKUP_ROOT/users/restore"' in install


def test_existing_installs_are_repaired_on_update():
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "migrate_user_backup_dir_owner() {" in update
    body = update.split("migrate_user_backup_dir_owner() {")[1].split("\n}\n")[0]
    assert "chown -R spanel:spanel" in body, (
        "archives already written as root would outlive retention otherwise"
    )
    assert "install -d -m 0750 -o spanel -g spanel" in body
    calls = [line.strip() for line in update.splitlines()
             if line.strip() == "migrate_user_backup_dir_owner"]
    assert len(calls) == 1, "the migration is defined but never invoked"


def test_the_backup_root_is_never_taken_away_from_the_panel():
    """`install -d` rewrites the owner of a directory that already exists.

    require_backup_path guarded the app export and import paths by making sure
    the backup root was there - with -o root -g spanel. 0750 with the group
    gives the panel read and traverse, not write, so the first application
    export would have taken the backup root away from the panel and every
    website backup after it would have failed to create its own directory.
    """
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("require_backup_path() {")[1].split("\n}\n")[0]
    assert '-o root -g spanel "$BACKUP_ROOT"' not in body
    assert '-o spanel -g spanel "$BACKUP_ROOT"' in body

    # And the installer has to agree, or an update undoes whichever ran last.
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert 'install -d -o spanel -g spanel -m 0750 "$BACKUP_ROOT"' in install
