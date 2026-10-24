"""A user backup records which server each database was on, and a restore puts
it back there - or on the local MariaDB, with a note, when that host is not
configured on the restoring panel."""

import json
import tarfile

import pytest

from rdb_fakes import make_host, make_session, make_user


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import db_backends, remote_mysql, site_users

    monkeypatch.setattr(settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(site_users, "IMPORT_STAGE_BASE", tmp_path / "stage")
    calls = []

    def export(where):
        def run(self, db_name, output_file):
            calls.append(("export", where(self), db_name))
            with open(output_file, "w", encoding="utf-8") as fh:
                fh.write(f"-- dump of {db_name}\n")
        return run

    def create(where):
        def run(self, db_name, db_user, db_password, *, password_hash=None, allow_existing_user=False):
            calls.append(("create", where(self), db_name))
            return {"db_name": db_name, "db_user": db_user, "db_password": db_password}
        return run

    def import_(where):
        def run(self, db_name, input_file):
            calls.append(("import", where(self), db_name))
        return run

    local = lambda _self: "local"  # noqa: E731
    remote = lambda self: f"{self.host.host}:{self.host.port}"  # noqa: E731
    monkeypatch.setattr(db_backends.LocalBackend, "export_database", export(local))
    monkeypatch.setattr(db_backends.LocalBackend, "create_database_credentials", create(local))
    monkeypatch.setattr(db_backends.LocalBackend, "import_database", import_(local))
    monkeypatch.setattr(remote_mysql.RemoteMySQLBackend, "export_database", export(remote))
    monkeypatch.setattr(remote_mysql.RemoteMySQLBackend, "create_database_credentials", create(remote))
    monkeypatch.setattr(remote_mysql.RemoteMySQLBackend, "import_database", import_(remote))
    return calls


def _make_backup(recorded):
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount
    from app.services import backup

    db = make_session()
    user = make_user(db, username="alice")
    host = make_host(db, host="10.0.0.5", port=3307)
    db.add(DatabaseAccount(owner_id=user.id, db_name="shop", db_user="shop",
                           db_password=encrypt("pw-shop-1234567"), db_host_id=host.id))
    db.add(DatabaseAccount(owner_id=user.id, db_name="blog", db_user="blog", db_password=encrypt("pw-blog-1234567")))
    db.commit()
    archive = backup.create_user_backup(user, db)
    assert ("export", "10.0.0.5:3307", "shop") in recorded
    assert ("export", "local", "blog") in recorded
    return archive


def _manifest(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return json.load(tar.extractfile("manifest.json"))


def test_manifest_records_the_host_of_each_database(recorded):
    manifest = _manifest(_make_backup(recorded))
    by_name = {d["db_name"]: d for d in manifest["databases"]}
    assert by_name["shop"]["db_host"] == {"host": "10.0.0.5", "port": 3307, "name": "db1"}
    assert by_name["blog"]["db_host"] is None


def test_restore_returns_database_to_the_same_host(recorded):
    from app.models.entities import DatabaseAccount
    from app.services import backup

    archive = _make_backup(recorded)
    recorded.clear()
    db = make_session()  # another panel: the same server, under another id
    make_host(db, name="other", host="10.9.9.9")
    target = make_host(db, name="same server", host="10.0.0.5", port=3307)
    result = backup.restore_user_backup(archive, db, restore_mode="database_only")

    assert ("create", "10.0.0.5:3307", "shop") in recorded and ("import", "10.0.0.5:3307", "shop") in recorded
    assert ("create", "local", "blog") in recorded
    rows = {r.db_name: r for r in db.query(DatabaseAccount).all()}
    assert rows["shop"].db_host_id == target.id and rows["blog"].db_host_id is None
    assert result["database_notes"] == []


def test_restore_falls_back_to_local_when_the_host_is_gone(recorded):
    from app.models.entities import DatabaseAccount
    from app.services import backup

    archive = _make_backup(recorded)
    recorded.clear()
    db = make_session()
    result = backup.restore_user_backup(archive, db, restore_mode="database_only")

    assert ("create", "local", "shop") in recorded and ("import", "local", "shop") in recorded
    assert db.query(DatabaseAccount).filter(DatabaseAccount.db_name == "shop").one().db_host_id is None
    assert result["database_notes"] == [
        "shop: remote host 10.0.0.5:3307 is not configured here; restored on the local MariaDB"
    ]


def test_restore_over_an_existing_database_keeps_it_where_it_is(recorded):
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount
    from app.services import backup

    archive = _make_backup(recorded)
    recorded.clear()
    db = make_session()
    user = make_user(db, username="alice")
    moved_to = make_host(db, name="new home", host="10.0.0.77")
    db.add(DatabaseAccount(owner_id=user.id, db_name="shop", db_user="shop",
                           db_password=encrypt("x"), db_host_id=moved_to.id))
    db.commit()
    backup.restore_user_backup(archive, db, restore_mode="database_only")
    assert ("import", "10.0.0.77:3306", "shop") in recorded


def test_manifests_without_a_host_restore_locally():
    from app.services import remote_mysql

    db = make_session()
    assert remote_mysql.resolve_restore_host(db, None) == (None, "")
    host, note = remote_mysql.resolve_restore_host(db, {"host": "127.0.0.1"})
    assert host is None and "invalid" in note
