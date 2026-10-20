"""The Restore tab, DirectAdmin-style (operator, 2026-09-27): "chọn nguồn >
điền thông tin (nếu cần) > click chọn user cần restore > Bấm nút".

What must hold: every backup on this server is offered (the page before only
looked in users/restore, so the scheduled backups in users/<account>/ could
not be restored from it); nothing outside the backup folder can be named; an
SFTP server's key is checked before the password is sent; one failed account
does not stop the others; a downloaded copy goes once it has been restored;
another server's password never reaches the job the page reads.
"""
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api import maintenance
from app.schemas.schemas import RestoreRunRequest, RestoreSource
from app.services import backup, restore_sources

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


@pytest.fixture
def backups(monkeypatch, tmp_path):
    monkeypatch.setattr(restore_sources.settings, "backup_root", str(tmp_path))
    monkeypatch.setattr(restore_sources.settings, "command_dry_run", False)
    users = tmp_path / "users"

    def make(relative: str, size: int = 10, when: str = "2026-09-26 19:00") -> Path:
        path = users / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        stamp = datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()
        os.utime(path, (stamp, stamp))
        return path

    return SimpleNamespace(root=tmp_path, users=users, make=make)


# --- this server -------------------------------------------------------------------

def test_every_backup_on_this_server_is_offered(backups):
    backups.make("sieunhim/user-sieunhim-20260926191023.tar.gz", when="2026-09-26 19:10")
    backups.make("sieunhim/user-sieunhim-20260925191023.tar.gz", when="2026-09-25 19:10")
    backups.make("restore/user-bob-Mon.tar.gz", when="2026-09-20 08:00")
    backups.make("uploads/something.tar.gz", when="2026-09-01 08:00")
    backups.make("sieunhim/notes.txt")
    rows = restore_sources.list_local()
    assert [(row["key"], row["username"]) for row in rows] == [
        ("sieunhim/user-sieunhim-20260926191023.tar.gz", "sieunhim"),
        ("sieunhim/user-sieunhim-20260925191023.tar.gz", "sieunhim"),
        ("restore/user-bob-Mon.tar.gz", "bob"),
        ("uploads/something.tar.gz", ""),
    ], "newest first; the account from its folder, or from the name in a shared folder"


def test_a_local_key_cannot_leave_the_backup_folder(backups):
    good = backups.make("alice/user-alice-1.tar.gz")
    assert restore_sources.local_path("alice/user-alice-1.tar.gz") == str(good.resolve())
    (backups.root / "outside.tar.gz").write_bytes(b"x")
    for bad in ("../outside.tar.gz", "alice/../../outside.tar.gz", "/etc/passwd", "alice/user-alice-1.txt",
                "alice", "a/b/c.tar.gz", "alice/missing.tar.gz", "alice/x\n.tar.gz"):
        with pytest.raises(restore_sources.RestoreSourceError):
            restore_sources.local_path(bad)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_is_neither_listed_nor_restored(backups):
    backups.make("alice/user-alice-1.tar.gz")
    (backups.users / "alice" / "user-alice-2.tar.gz").symlink_to(backups.users / "alice" / "user-alice-1.tar.gz")
    assert [row["name"] for row in restore_sources.list_local()] == ["user-alice-1.tar.gz"]
    with pytest.raises(restore_sources.RestoreSourceError):
        restore_sources.local_path("alice/user-alice-2.tar.gz")


# --- another server --------------------------------------------------------------------

def _remote(**changes):
    source = {"kind": "remote", "protocol": "sftp", "host": "backup.example.test", "port": None,
              "username": "u", "password": "secret-pass", "path": "/backups", "host_key": "", "target_id": None}
    source.update(changes)
    return source


def test_a_remote_key_must_be_in_the_listed_folder():
    assert restore_sources.check_key(_remote(), "/backups/user-a-1.tar.gz") == "user-a-1.tar.gz"
    for bad in ("/etc/user-a-1.tar.gz", "/backups/sub/user-a-1.tar.gz", "/backups/user-a-1.zip",
                "/backups/user-a\r\nDELE x.tar.gz"):
        with pytest.raises(restore_sources.RestoreSourceError):
            restore_sources.check_key(_remote(), bad)


class _Key:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"the-server-key"


class _Transport:
    sent = []

    def __init__(self, sock):
        self.banner_timeout = None

    def start_client(self, timeout=None):
        pass

    def get_remote_server_key(self):
        return _Key()

    def auth_password(self, username, password):
        _Transport.sent.append((username, password))

    def close(self):
        pass


def test_an_sftp_server_with_another_key_never_sees_the_password(monkeypatch):
    _Transport.sent = []
    monkeypatch.setattr(restore_sources.socket, "create_connection", lambda address, timeout=None: object())
    monkeypatch.setattr(restore_sources.paramiko, "Transport", _Transport)
    with pytest.raises(backup.SftpHostKeyMismatch):
        with restore_sources._sftp("h", 22, "u", password="secret-pass", pinned="SHA256:somethingelse"):
            pass
    assert _Transport.sent == [], "the password was sent to a server with the wrong key"


def test_a_login_that_does_not_start_sftp_says_so_instead_of_a_500(monkeypatch):
    """Seen on .88: an account whose shell is nologin signs in, then prints a
    line into the SFTP stream - paramiko's "Garbage packet received" came out
    of the API as a bare 500."""
    monkeypatch.setattr(restore_sources.socket, "create_connection", lambda address, timeout=None: object())
    monkeypatch.setattr(restore_sources.paramiko, "Transport", _Transport)

    def garbage(transport):
        raise restore_sources.paramiko.SFTPError("Garbage packet received")

    monkeypatch.setattr(restore_sources.paramiko.SFTPClient, "from_transport", garbage)
    with pytest.raises(restore_sources.RestoreSourceError, match="did not start SFTP"):
        with restore_sources._sftp("h", 22, "u", password="p"):
            pass


def test_a_download_from_another_sftp_server_needs_the_key_seen_when_listing(backups):
    with pytest.raises(restore_sources.RestoreSourceError):
        restore_sources.fetch(_remote(host_key=""), "/backups/user-a-1.tar.gz", 10, db=None)


class _Ftp:
    def __init__(self, mlsd_error=None):
        self.mlsd_error = mlsd_error

    def mlsd(self, folder, facts=None):
        if self.mlsd_error:
            raise restore_sources.ftplib.error_perm(self.mlsd_error)
        return iter([("user-a-20260926190000.tar.gz", {"type": "file", "size": "123", "modify": "20260926190000"}),
                     ("old.zip", {"type": "file", "size": "1"}),
                     ("dir.tar.gz", {"type": "dir"})])

    def nlst(self, folder):
        return [f"{folder}/user-b-Mon.tar.gz", f"{folder}/readme.txt"]

    def size(self, path):
        return 456

    def sendcmd(self, command):
        return "213 20260920080000"


def test_ftp_lists_with_mlsd_and_falls_back_on_an_old_server():
    rows = restore_sources._ftp_list(_Ftp(), "/backups")
    assert [(row["key"], row["size"], row["username"]) for row in rows] == [("/backups/user-a-20260926190000.tar.gz", 123, "a")]
    assert rows[0]["modified"].startswith("2026-09-26T19:00:00")
    rows = restore_sources._ftp_list(_Ftp(mlsd_error="500 Unknown command"), "/backups")
    assert [(row["key"], row["size"], row["username"]) for row in rows] == [("/backups/user-b-Mon.tar.gz", 456, "b")]
    with pytest.raises(restore_sources.RestoreSourceError):
        restore_sources._ftp_list(_Ftp(mlsd_error="550 No such directory"), "/backups")


def test_the_form_refuses_what_is_not_a_server():
    RestoreSource(kind="remote", host="203.0.113.5", username="u", password="p")
    for host in ("bad host", "x;rm -rf /", ""):
        with pytest.raises(ValidationError):
            RestoreSource(kind="remote", host=host, username="u", password="p")
    with pytest.raises(ValidationError):
        RestoreSource(kind="remote", host="h.test", username="u", password="")
    with pytest.raises(ValidationError):
        RestoreSource(kind="target")


# --- downloads and the job ---------------------------------------------------------------

def test_a_download_cut_short_leaves_nothing_behind(backups):
    def broken(destination):
        Path(destination).write_bytes(b"half")
        raise OSError("connection reset")

    with pytest.raises(OSError):
        backup.stage_remote_backup(broken, "user-a-1.tar.gz")
    assert list((backups.users / "restore").glob("*")) == []


def test_each_account_is_reported_and_a_downloaded_copy_goes_once_restored(backups, monkeypatch):
    staged = backups.make("restore/user-a-1-abc123.tar.gz")
    fetched = {"/backups/user-a-1.tar.gz": (str(staged), True),
               "/backups/user-b-1.tar.gz": (str(backups.make("restore/user-b-1-def456.tar.gz")), True)}
    monkeypatch.setattr(restore_sources, "fetch", lambda source, key, size, db: fetched[key])

    def restore(path, db):
        if "user-b" in path:
            raise ValueError("This is not a spanel or opanel user backup")
        return {"username": "a", "message": "Restored a"}

    monkeypatch.setattr(maintenance.backup, "restore_user_backup", restore)
    monkeypatch.setattr(maintenance, "SessionLocal", lambda: SimpleNamespace(rollback=lambda: None, close=lambda: None,
                                                                             get=lambda *a: None))
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    job = maintenance._queue_backup_job(SimpleNamespace(id=1, role="admin"), "user_restore", "Restore queued",
                                        results=[{"name": "user-a-1.tar.gz", "username": "a", "status": "queued", "detail": ""},
                                                 {"name": "user-b-1.tar.gz", "username": "b", "status": "queued", "detail": ""}])
    maintenance._run_restore_job(job["job_id"], 1, _remote(host_key="SHA256:k"),
                                 [{"key": "/backups/user-a-1.tar.gz", "size": 10, "username": "a"},
                                  {"key": "/backups/user-b-1.tar.gz", "size": 10, "username": "b"}])
    result = maintenance._public_backup_job(maintenance._get_backup_job(job["job_id"]))
    assert [(row["username"], row["status"]) for row in result["results"]] == [("a", "done"), ("b", "failed")]
    assert "not a spanel" in result["results"][1]["detail"]
    assert result["status"] == "error" and result["message"] == "Restored 1 of 2"
    assert not staged.exists(), "the copy of a restored account is removed"
    assert Path(fetched["/backups/user-b-1.tar.gz"][0]).exists(), "a failed one stays, to retry without downloading"
    assert "secret-pass" not in repr(maintenance._get_backup_job(job["job_id"]))


def test_the_routes_are_admin_only_and_the_password_stays_out_of_the_job():
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "maintenance.py").read_text(encoding="utf-8")
    for route in ('@router.post("/restore/list")', '@router.post("/restore/run")'):
        body = source.split(route)[1].split("\n@router")[0]
        assert "ensure_role(current_user.role, Role.admin)" in body, route
    run = source.split('@router.post("/restore/run")')[1].split("\n@router")[0]
    assert '_queue_backup_job(current_user, "user_restore", "Restore queued", results=results)' in run
    RestoreRunRequest(source=_remote(host_key="k"), items=[{"key": "/backups/user-a-1.tar.gz"}])
    with pytest.raises(ValidationError):
        RestoreRunRequest(source=_remote(), items=[])


def test_the_restore_tab_asks_source_details_users_then_the_button():
    tab = APP.split("activeBackupTab === 'restore' &&")[1].split("activeBackupTab === 'destination' &&")[0]
    steps = ["Source", "restoreStepTwo", "Accounts to restore", "restore-step-no\">4</span>{t('Restore')}"]
    positions = [tab.index(step) for step in steps]
    assert positions == sorted(positions), "the four steps, in the operator's order"
    assert "{ local: 'Backups on this server', target: 'Backup Destination', remote: 'Connection' }" in APP
    for kind in ("local", "target", "remote"):
        block = tab.split(f"{{restoreSource === '{kind}' && ")[1][:4000]
        assert "{restoreTools}" in block, f"step 2 of {kind} has Upload backup + Refresh"
    assert "'/maintenance/restore/list'" in APP and "'/maintenance/restore/run'" in APP
    assert "restore-catalogue" not in APP and "restore-bulk" not in APP


# --- Upload backup, into the source picked in step 1 ---------------------------------------

def _account_backup(tmp_path, name="user-acme-20260927020000.tar.gz"):
    import io
    import json
    import tarfile

    from starlette.datastructures import UploadFile

    manifest = json.dumps({"kind": "spanel_user", "user": {"username": "acme"}}).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    return UploadFile(file=io.BytesIO(buffer.getvalue()), filename=name)


def _upload(monkeypatch, tmp_path, source, pushed=None, fail=None):
    from fastapi import HTTPException

    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)

    def push(spec, local_file, db):
        if fail:
            raise restore_sources.RestoreSourceError(fail)
        pushed.append((spec["kind"], Path(local_file).name, Path(local_file).exists()))
        return f"/backups/{Path(local_file).name}"

    monkeypatch.setattr(restore_sources, "push", push)
    import json
    try:
        return maintenance.restore_upload(request=None, files=[_account_backup(tmp_path)], source=json.dumps(source),
                                          db=None, current_user=SimpleNamespace(id=1, role="admin"))
    except HTTPException as exc:
        return exc


def test_an_upload_to_this_server_stays_in_its_restore_folder(backups, monkeypatch):
    result = _upload(monkeypatch, backups.root, {"kind": "local"}, pushed=[])
    assert result == {"uploaded": ["user-acme-20260927020000.tar.gz"]}
    assert [row["key"] for row in restore_sources.list_local()] == ["restore/user-acme-20260927020000.tar.gz"]


def test_an_upload_to_a_destination_is_sent_there_and_not_kept_here(backups, monkeypatch):
    pushed = []
    result = _upload(monkeypatch, backups.root, {"kind": "target", "target_id": 3}, pushed=pushed)
    assert pushed == [("target", "user-acme-20260927020000.tar.gz", True)], "checked, then sent"
    assert result == {"uploaded": ["/backups/user-acme-20260927020000.tar.gz"]}
    assert restore_sources.list_local() == [], "the copy passing through is gone"


def test_a_failed_send_reports_it_and_leaves_nothing_here(backups, monkeypatch):
    result = _upload(monkeypatch, backups.root, _remote(), fail="The server refused the username or password")
    assert result.status_code == 400 and "refused" in result.detail
    assert restore_sources.list_local() == []


def test_only_an_account_backup_is_accepted(backups, monkeypatch):
    import io

    from fastapi import HTTPException
    from starlette.datastructures import UploadFile

    with pytest.raises(HTTPException):
        maintenance._save_user_restore_upload(UploadFile(file=io.BytesIO(b"not a tar"), filename="user-x-1.tar.gz"))
    assert restore_sources.list_local() == []
