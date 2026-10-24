"""Retention on backup destinations, with incremental chains, and FTP/FTPS
as a destination."""
import ftplib
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services import backup_remote, backup_retention as br  # noqa: E402


def _entry(name, when: datetime | None = None):
    return {"name": name, "key": f"base/host/alice/{name}", "modified": when.isoformat() if when else ""}


def _names(archives):
    return sorted(a.name for a in archives)


@pytest.mark.parametrize("name,expected", [
    ("user-alice-2026-10-24.tar.gz", ("full", datetime(2026, 10, 24))),
    ("user-alice-2026-10-24_021500-inc.tar.gz", ("inc", datetime(2026, 10, 24, 2, 15))),
    ("user-alice-20261024021500.tar.gz", ("full", datetime(2026, 10, 24, 2, 15))),
    ("user-alice-20261024021500-inc.tar.gz", ("inc", datetime(2026, 10, 24, 2, 15))),
    ("user-alice.tar.gz", None),             # "none": one file, overwritten
    ("user-alice-Mon.tar.gz", None),         # day_of_week rotation
    ("user-alice-W3.tar.gz", None),          # week_of_month rotation
    ("user-alice-bob-2026-10-24.tar.gz", None),  # another account's file
    ("panel-dr-x.spdr", None),
    ("user-alice-2026-13-45.tar.gz", None),
])
def test_classify(name, expected):
    assert br.classify(name, "alice") == expected


def test_full_only_keeps_the_newest():
    start = datetime(2026, 10, 1, 2, 0)
    entries = [_entry(f"user-alice-{(start + timedelta(days=i)):%Y-%m-%d}.tar.gz", start + timedelta(days=i)) for i in range(10)]
    plan = br.plan_retention(entries, 3, "alice")
    assert _names(plan.keep) == ["user-alice-2026-10-08.tar.gz", "user-alice-2026-10-09.tar.gz", "user-alice-2026-10-10.tar.gz"]
    assert len(plan.delete) == 7 and not plan.protected


def _chain_entries():
    """F1 i1 i2 i3 F2 i4 i5, one a day, the way an incremental schedule with
    full_every_n=4 names them on the destination."""
    day = datetime(2026, 10, 1, 2, 0)
    kinds = ["F", "i", "i", "i", "F", "i", "i"]
    entries = []
    for n, kind in enumerate(kinds):
        when = day + timedelta(days=n)
        name = f"user-alice-{when:%Y-%m-%d}.tar.gz" if kind == "F" else f"user-alice-{when:%Y-%m-%d_%H%M%S}-inc.tar.gz"
        entries.append(_entry(name, when + timedelta(minutes=5)))
    return entries


def test_a_kept_incremental_keeps_its_full_and_the_layers_between():
    entries = _chain_entries()
    names = [e["name"] for e in entries]
    plan = br.plan_retention(entries, 2, "alice")
    assert _names(plan.keep) == sorted(names[5:7])
    assert _names(plan.protected) == [names[4]]                     # F2
    assert _names(plan.delete) == sorted(names[0:4])                # F1 and its incrementals

    # Keeping 4 reaches back into the first chain (i3): all of it stays.
    plan = br.plan_retention(entries, 4, "alice")
    assert plan.delete == []
    assert _names(plan.protected) == sorted(names[0:3])


def test_never_deletes_a_full_that_a_kept_incremental_needs_in_any_order():
    entries = list(reversed(_chain_entries()))   # listing order must not matter
    for keep in range(1, 8):
        plan = br.plan_retention(entries, keep, "alice")
        survivors = {a.name for a in plan.keep + plan.protected}
        ordered = [e["name"] for e in _chain_entries()]
        for archive in plan.keep:
            if archive.kind == "inc":
                idx = ordered.index(archive.name)
                base = max(i for i in range(idx) if not ordered[i].endswith("-inc.tar.gz"))
                assert set(ordered[base:idx]) <= survivors, keep


def test_upload_time_orders_a_full_made_later_on_the_same_day():
    """A full_date name has no time. A chain's last incremental made at 01:00
    and a new full made at 14:00 the same day: the incremental belongs to
    the OLD full, which the upload times show and the names cannot."""
    day = datetime(2026, 10, 5)
    entries = [
        _entry("user-alice-2026-10-01.tar.gz", datetime(2026, 10, 1, 14, 5)),
        _entry("user-alice-2026-10-05_010000-inc.tar.gz", day.replace(hour=1, minute=5)),
        _entry("user-alice-2026-10-05.tar.gz", day.replace(hour=14, minute=5)),
    ]
    plan = br.plan_retention(entries, 2, "alice")
    assert _names(plan.keep) == ["user-alice-2026-10-05.tar.gz", "user-alice-2026-10-05_010000-inc.tar.gz"]
    assert _names(plan.protected) == ["user-alice-2026-10-01.tar.gz"]
    assert plan.delete == []


def test_rotating_and_foreign_names_are_left_alone():
    entries = [_entry("user-alice-Mon.tar.gz"), _entry("user-alice.tar.gz"), _entry("notes.tar.gz"),
               _entry("user-alice-2026-10-01.tar.gz", datetime(2026, 10, 1)),
               _entry("user-alice-2026-10-02.tar.gz", datetime(2026, 10, 2))]
    plan = br.plan_retention(entries, 1, "alice")
    assert _names(plan.delete) == ["user-alice-2026-10-01.tar.gz"]
    assert sorted(plan.unmanaged) == ["notes.tar.gz", "user-alice-Mon.tar.gz", "user-alice.tar.gz"]


def test_keep_is_at_least_one():
    entries = [_entry("user-alice-2026-10-01.tar.gz", datetime(2026, 10, 1))]
    assert br.plan_retention(entries, 0, "alice").delete == []


def test_prune_remote_deletes_through_the_destination(monkeypatch):
    target = SimpleNamespace(kind="s3")
    listed, deleted = {}, []

    def list_files(t, subpath, **kw):
        listed["subpath"] = subpath
        return _chain_entries()

    def delete(t, key):
        if key.endswith("2026-10-02_020000-inc.tar.gz"):
            raise backup_remote.RemoteError("permission denied")
        deleted.append(key)
    monkeypatch.setattr(backup_remote, "list_files", list_files)
    monkeypatch.setattr(backup_remote, "delete", delete)
    result = br.prune_remote(target, "alice", 2, hostname="srv1")
    assert listed["subpath"] == "srv1/alice"
    assert len(deleted) == 3 and len(result["errors"]) == 1
    assert all("2026-10-06" not in key and "2026-10-07" not in key and "2026-10-05" not in key for key in deleted)


def test_scheduled_run_prunes_the_destination(monkeypatch):
    from app.services import backup, backup_scheduler

    user = SimpleNamespace(id=7, username="alice")
    schedule = SimpleNamespace(id=1, user_id=7, target_id=3, retention=2, backup_mode="incremental",
                               full_every_n=4, name_suffix="full_date", last_run_at=None, last_status=None,
                               last_message=None)
    target = SimpleNamespace(id=3, kind="s3")

    class Query:
        def filter(self, *a):
            return self

        def first(self):
            return target
    db = SimpleNamespace(commit=lambda: None, query=lambda model: Query())
    pruned = []
    monkeypatch.setattr(backup_scheduler.backup_retention, "prune_remote",
                        lambda t, account, keep: pruned.append((t, account, keep)) or
                        {"deleted": ["a", "b"], "errors": [], "kept": [], "protected": []})
    with patch.object(backup, "create_user_backup", return_value="/tmp/a.tar.gz"), \
            patch.object(backup, "prune_user_backups"), patch.object(backup, "discard_local_copy"), \
            patch.object(backup_scheduler, "_schedule_users", return_value=[user]), \
            patch.object(backup_scheduler, "_upload_if_configured", return_value="dest:key"):
        assert backup_scheduler.run_one(db, schedule) is True
    assert pruned == [(target, "alice", 2)]
    assert "pruned 2 old" in schedule.last_message


# --- FTP / FTPS as a destination -------------------------------------------------------

class FakeFTP:
    def __init__(self):
        self.files: dict[str, bytes] = {"/backups/srv1/alice/user-alice-2026-10-01.tar.gz": b"old"}
        self.dirs = {"/backups", "/backups/srv1", "/backups/srv1/alice"}
        self.closed = False

    def mkd(self, path):
        if path in self.dirs:
            raise ftplib.error_perm("550 exists")
        self.dirs.add(path)

    def storbinary(self, cmd, handle, blocksize=None):
        assert cmd.startswith("STOR ")
        self.files[cmd[5:]] = handle.read()

    def rename(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def delete(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 no such file")
        del self.files[path]

    def retrbinary(self, cmd, write, blocksize=None):
        write(self.files[cmd[5:]])

    def mlsd(self, folder, facts=None):
        prefix = folder.rstrip("/") + "/"
        for path, data in self.files.items():
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                yield path[len(prefix):], {"type": "file", "size": str(len(data)), "modify": "20261001020000"}

    def quit(self):
        self.closed = True


def _ftp_target(kind="ftps"):
    from app.core.secrets import encrypt

    return SimpleNamespace(kind=kind, name="nas", host="nas.example.com", port=21, username="bk",
                           password=encrypt("pw"), remote_path="/backups", private_key=None)


def test_ftp_destination_upload_list_download_delete(tmp_path, monkeypatch):
    fake = FakeFTP()
    opened = []
    monkeypatch.setattr(backup_remote.restore_sources, "_ftp_open", lambda source: opened.append(source) or fake)
    archive = tmp_path / "user-alice-20261024020000.tar.gz"
    archive.write_bytes(b"new archive")
    where = backup_remote.upload(_ftp_target(), str(archive), "user-alice-2026-10-24.tar.gz", "srv1/alice")
    assert where == "nas:/backups/srv1/alice/user-alice-2026-10-24.tar.gz"
    assert fake.files["/backups/srv1/alice/user-alice-2026-10-24.tar.gz"] == b"new archive"
    assert not any(name.endswith(".part") for name in fake.files)
    assert opened[0]["protocol"] == "ftps" and opened[0]["password"] == "pw"

    rows = backup_remote.list_files(_ftp_target(), "srv1/alice")
    assert {r["name"] for r in rows} == {"user-alice-2026-10-24.tar.gz", "user-alice-2026-10-01.tar.gz"}

    out = backup_remote.download(_ftp_target(), "/backups/srv1/alice/user-alice-2026-10-24.tar.gz", str(tmp_path / "d.tar.gz"))
    assert Path(out).read_bytes() == b"new archive"

    backup_remote.delete(_ftp_target(), "/backups/srv1/alice/user-alice-2026-10-01.tar.gz")
    backup_remote.delete(_ftp_target(), "/backups/srv1/alice/missing.tar.gz")   # already gone is fine
    assert "/backups/srv1/alice/user-alice-2026-10-01.tar.gz" not in fake.files
    with pytest.raises(backup_remote.RemoteError):
        backup_remote.delete(_ftp_target(), "/backups/../etc/passwd")


def test_ftp_target_schema():
    from pydantic import ValidationError

    from app.schemas.schemas import SftpBackupTargetCreate

    target = SftpBackupTargetCreate(name="nas", kind="ftps", host="nas.example.com", username="bk", password="pw")
    assert target.port == 21
    with pytest.raises(ValidationError):
        SftpBackupTargetCreate(name="nas", kind="ftp", host="nas.example.com", username="bk")
    with pytest.raises(ValidationError):
        SftpBackupTargetCreate(name="nas", kind="ftp", host="h", username="bk", password="pw", private_key="KEY")
    with pytest.raises(ValidationError):
        SftpBackupTargetCreate(name="nas", kind="webdav", host="h", username="bk", password="pw")


def test_restore_lists_an_ftp_destination(monkeypatch):
    from app.services import restore_sources

    fake = FakeFTP()
    fake.dirs_listing = True

    def mlsd(folder, facts=None):
        if folder == "/backups":
            yield "srv1", {"type": "dir"}
        elif folder == "/backups/srv1":
            yield "alice", {"type": "dir"}
        else:
            yield from FakeFTP.mlsd(fake, folder, facts)
    fake.mlsd = mlsd
    monkeypatch.setattr(restore_sources, "_ftp_open", lambda source: fake)
    target = _ftp_target("ftp")
    target.id, target.is_active = 3, True
    db = SimpleNamespace(query=lambda model: SimpleNamespace(filter=lambda *a: SimpleNamespace(first=lambda: target)))
    listing = restore_sources.list_source({"kind": "target", "target_id": 3}, db)
    assert listing["location"] == "ftp://nas.example.com/backups"
    assert [(r["username"], r["hostname"]) for r in listing["items"]] == [("alice", "srv1")]
