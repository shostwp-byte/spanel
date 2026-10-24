"""Remote MySQL hosts are panel database rows: CRUD, the guard rails around a
host that has databases on it, and the one-time import from the old JSON."""

import importlib.util
import json
from pathlib import Path

import pytest

from rdb_fakes import FakeMySQLServer, make_host, make_session, make_user

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_add_host_encrypts_password_and_serializes_without_it():
    from app.services import remote_mysql

    db = make_session()
    row = remote_mysql.add_host(db, {
        "name": "db1", "host": "DB1.Example.com", "port": 3307, "admin_user": "spanel_admin",
        "admin_password": "s3cret-admin", "allowed_client_hosts": "10.0.0.10, 10.0.0.%",
    })
    assert row.admin_password.startswith("fernet:")
    assert row.host == "db1.example.com"
    out = remote_mysql.serialize_host(db, row)
    assert "admin_password" not in out
    assert "s3cret-admin" not in json.dumps(out)
    assert out["has_password"] is True
    assert out["allowed_client_hosts"] == "10.0.0.10, 10.0.0.%"
    assert out["phpmyadmin_server"] == 2


def test_add_host_rejects_duplicates_loopback_and_bad_input():
    from app.services import remote_mysql

    db = make_session()
    remote_mysql.add_host(db, {"name": "a", "host": "10.0.0.5"})
    with pytest.raises(ValueError):
        remote_mysql.add_host(db, {"name": "b", "host": "10.0.0.5", "port": 3306})
    for bad in ("127.0.0.1", "localhost", "bad host;", "::1"):
        with pytest.raises(ValueError):
            remote_mysql.add_host(db, {"name": "x", "host": bad})
    with pytest.raises(ValueError):
        remote_mysql.add_host(db, {"name": "x", "host": "10.0.0.9", "allowed_client_hosts": "%"})
    with pytest.raises(ValueError):
        remote_mysql.add_host(db, {"name": "x", "host": "10.0.0.9", "ca_cert": "-----BEGIN PRIVATE KEY-----"})


def test_update_keeps_password_when_blank_and_refuses_moving_a_host_with_databases():
    from app.models.entities import DatabaseAccount
    from app.services import remote_mysql

    db = make_session()
    host = make_host(db)
    stored = host.admin_password
    remote_mysql.update_host(db, host.id, {"name": "renamed", "admin_password": ""})
    assert host.admin_password == stored and host.name == "renamed"

    user = make_user(db)
    db.add(DatabaseAccount(owner_id=user.id, db_name="shop", db_user="shop", db_password="x", db_host_id=host.id))
    db.commit()
    with pytest.raises(ValueError):
        remote_mysql.update_host(db, host.id, {"host": "10.0.0.6"})
    # Same address is not a move.
    remote_mysql.update_host(db, host.id, {"host": "10.0.0.5", "enabled": False})
    assert host.enabled is False
    with pytest.raises(ValueError):
        remote_mysql.delete_host(db, host.id)


def test_delete_host_without_databases():
    from app.models.entities import RemoteDbHost
    from app.services import remote_mysql

    db = make_session()
    host = make_host(db)
    remote_mysql.delete_host(db, host.id)
    assert db.query(RemoteDbHost).count() == 0
    with pytest.raises(LookupError):
        remote_mysql.delete_host(db, host.id)


def test_test_host_records_online_and_offline(monkeypatch):
    import pymysql
    from app.services import remote_mysql

    db = make_session()
    host = make_host(db)
    server = FakeMySQLServer(version="10.11.6-MariaDB-log")
    monkeypatch.setattr(pymysql, "connect", server.connect)
    remote_mysql.test_host(db, host.id)
    assert host.status == "online" and host.server_version == "10.11.6-MariaDB-log"
    assert server.connect_kwargs[0]["connect_timeout"] == remote_mysql.CONNECT_TIMEOUT

    def refuse(**_kw):
        raise pymysql.err.OperationalError(1045, "Access denied for user 'root'@'10.0.0.10'")

    monkeypatch.setattr(pymysql, "connect", refuse)
    remote_mysql.test_host(db, host.id)
    assert host.status == "offline"
    assert "access denied for admin user 'root'" in host.last_error
    assert "db1 (10.0.0.5:3306)" in host.last_error


def test_customer_host_choices_carry_no_credentials():
    from app.services import remote_mysql

    db = make_session()
    make_host(db)
    make_host(db, name="off", host="10.0.0.6", enabled=False)
    choices = remote_mysql.host_choices(db)
    assert [c["name"] for c in choices] == ["db1"]
    assert set(choices[0]) == {"id", "name", "address", "status"}


def _load_migration():
    path = REPO_ROOT / "backend" / "alembic" / "versions" / "rdb_remote_hosts.py"
    spec = importlib.util.spec_from_file_location("rdb_remote_hosts_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_imports_json_hosts_once_and_strips_them(tmp_path, monkeypatch):
    from app.core.secrets import decrypt, encrypt
    from app.models.entities import RemoteDbHost

    state_file = tmp_path / "multi-server.json"
    state_file.write_text(json.dumps({
        "settings": {"node_name": "web1"},
        "nodes": [{"id": "n1"}],
        "remote_db_hosts": [
            {"id": "rdb_1", "name": "Old", "host": "10.1.1.1", "port": 3306, "admin_user": "root",
             "admin_password": encrypt("pw1"), "use_tls": True, "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "rdb_2", "name": "Plain", "host": "10.1.1.2", "port": 3307, "admin_password": "legacy-plain"},
            {"id": "rdb_3", "name": "Dup", "host": "10.1.1.1", "port": 3306},
        ],
    }), encoding="utf-8")
    monkeypatch.setenv("SPANEL_MULTI_SERVER_FILE", str(state_file))
    monkeypatch.chdir(tmp_path)

    db = make_session()
    migration = _load_migration()
    migration._import_legacy_hosts(db.connection())
    db.commit()

    rows = db.query(RemoteDbHost).order_by(RemoteDbHost.id).all()
    assert [(r.host, r.port) for r in rows] == [("10.1.1.1", 3306), ("10.1.1.2", 3307)]
    assert rows[0].use_tls is True and decrypt(rows[0].admin_password) == "pw1"
    assert rows[1].admin_password.startswith("fernet:") and decrypt(rows[1].admin_password) == "legacy-plain"

    remaining = json.loads(state_file.read_text(encoding="utf-8"))
    assert "remote_db_hosts" not in remaining
    assert remaining["nodes"] == [{"id": "n1"}]

    migration._import_legacy_hosts(db.connection())
    assert db.query(RemoteDbHost).count() == 2


def test_cluster_overview_lists_hosts_from_the_table(monkeypatch):
    from app.services import multi_server

    db = make_session()
    make_host(db)
    monkeypatch.setattr(multi_server, "local_node_summary", lambda _db: {})
    overview = multi_server.get_cluster_overview(db)
    assert [h["name"] for h in overview["remote_db_hosts"]] == ["db1"]
    assert overview["summary"]["remote_db_count"] == 1


def test_admin_api_edits_hosts_and_refuses_deleting_one_in_use(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from app.api import remote_mysql as api
    from app.models.entities import DatabaseAccount
    from app.services import remote_mysql

    db = make_session()
    admin = make_user(db, username="admin1", role="admin")
    customer = make_user(db, username="bob")
    synced = []
    monkeypatch.setattr(api, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(remote_mysql, "test_host", lambda db, host_id: remote_mysql.get_host(db, host_id))
    monkeypatch.setattr(remote_mysql, "sync_phpmyadmin_servers", lambda db: synced.append(True) or True)
    req = SimpleNamespace()

    created = api.create_remote_db_host(
        api.RemoteDbHostIn(name="db1", host="10.0.0.5", admin_password="pw"), request=req, db=db, current_user=admin
    )["host"]
    updated = api.update_remote_db_host(
        created["id"], api.RemoteDbHostUpdateIn(name="db one", allowed_client_hosts="10.0.0.10"),
        request=req, db=db, current_user=admin,
    )["host"]
    assert updated["name"] == "db one" and updated["allowed_client_hosts"] == "10.0.0.10" and updated["has_password"]
    assert len(synced) == 2

    db.add(DatabaseAccount(owner_id=customer.id, db_name="shop", db_user="shop", db_password="x", db_host_id=created["id"]))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        api.delete_remote_db_host(created["id"], request=req, db=db, current_user=admin)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        api.list_remote_db_hosts(db=db, current_user=customer)
    assert exc.value.status_code == 403

    paths = {(route.path, tuple(sorted(route.methods))) for route in api.router.routes}
    assert ("/multi-server/remote-db/{host_id}", ("PUT",)) in paths
    assert ("/multi-server/remote-db/{host_id}/test", ("POST",)) in paths
    main_text = (REPO_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(remote_mysql_api.router" in main_text
