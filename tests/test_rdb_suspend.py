"""Suspending an account locks its database accounts (local and remote),
unsuspending unlocks them, and terminating drops remote databases."""

from types import SimpleNamespace

import pytest

from rdb_fakes import FakeMySQLServer, make_host, make_session, make_user


@pytest.fixture
def server(monkeypatch):
    import pymysql

    fake = FakeMySQLServer()
    monkeypatch.setattr(pymysql, "connect", fake.connect)
    return fake


def _accounts(db):
    from app.core.secrets import encrypt
    from app.models.entities import DatabaseAccount

    user = make_user(db, username="alice")
    host = make_host(db)
    db.add(DatabaseAccount(owner_id=user.id, db_name="blog", db_user="blog", db_password=encrypt("x")))
    db.add(DatabaseAccount(owner_id=user.id, db_name="shop", db_user="shop", db_password=encrypt("x"), db_host_id=host.id))
    db.commit()
    return user


def test_local_account_lock_sql():
    from app.services import mariadb

    assert mariadb.account_lock_sql("blog", True) == "ALTER USER 'blog'@'localhost' ACCOUNT LOCK;\n"
    assert mariadb.account_lock_sql("blog", False) == "ALTER USER 'blog'@'localhost' ACCOUNT UNLOCK;\n"
    with pytest.raises(ValueError):
        mariadb.account_lock_sql("root", True)
    with pytest.raises(ValueError):
        mariadb.account_lock_sql("blog'@'%", True)


def test_local_lock_kills_open_sessions(monkeypatch):
    from app.core.config import settings
    from app.services import mariadb
    from app.services.shell import CommandResult

    sent = []

    def fake_run_sql(sql, *, check=True):
        sent.append(sql)
        out = "ID\n41\n42\n" if sql.startswith("SELECT ID FROM information_schema.PROCESSLIST") else ""
        return CommandResult("[redacted]", 0, out, "")

    monkeypatch.setattr(settings, "command_dry_run", False)
    monkeypatch.setattr(mariadb, "_run_sql", fake_run_sql)
    mariadb.set_account_locked("blog", True)
    assert sent[0] == "ALTER USER 'blog'@'localhost' ACCOUNT LOCK;\n"
    assert "WHERE USER = 'blog'" in sent[1]
    assert sent[2] == "KILL CONNECTION 41;\nKILL CONNECTION 42;\n"

    sent.clear()
    mariadb.set_account_locked("blog", False)
    assert sent == ["ALTER USER 'blog'@'localhost' ACCOUNT UNLOCK;\n"]


def test_remote_lock_covers_every_host_part_and_kills_sessions(server):
    from app.services import db_backends

    db = make_session()
    host = make_host(db)
    server.accounts["shop"] = ["10.0.0.10", "10.0.0.11"]
    server.processlist = [(7, "shop", "shop", 0, "", ""), (8, "other", "other", 0, "", "")]
    backend = db_backends.backend_for_host(host)
    backend.set_account_locked("shop", True)
    assert server.locked == {("shop", "10.0.0.10"), ("shop", "10.0.0.11")}
    assert server.statements("ALTER USER %s@%s ACCOUNT LOCK")[0][0] == "ALTER USER %s@%s ACCOUNT LOCK"
    assert server.statements("KILL CONNECTION") == [("KILL CONNECTION %s", (7,))]
    backend.set_account_locked("shop", False)
    assert server.locked == set()


def test_suspend_and_unsuspend_lock_both_kinds(server, monkeypatch):
    from app.models.entities import ProvisioningAccount
    from app.services import mariadb, provisioning

    db = make_session()
    user = _accounts(db)
    server.accounts["shop"] = ["10.0.0.10"]
    local_calls = []
    monkeypatch.setattr(mariadb, "set_account_locked", lambda u, locked: local_calls.append((u, locked)))
    account = ProvisioningAccount(external_id="whmcs-1", user_id=user.id, status="active")
    db.add(account)
    db.commit()

    provisioning.suspend_account(db, account, reason="unpaid")
    assert local_calls == [("blog", True)]
    assert server.locked == {("shop", "10.0.0.10")}
    assert account.status == "suspended"

    provisioning.unsuspend_account(db, account)
    assert local_calls[-1] == ("blog", False)
    assert server.locked == set()


def test_admin_suspend_endpoint_locks_databases(server, monkeypatch):
    from app.api import users as users_api
    from app.services import mariadb

    db = make_session()
    user = _accounts(db)
    admin = make_user(db, username="admin1", role="admin")
    server.accounts["shop"] = ["10.0.0.10"]
    local_calls = []
    monkeypatch.setattr(mariadb, "set_account_locked", lambda u, locked: local_calls.append((u, locked)))
    monkeypatch.setattr(users_api, "log_action", lambda *a, **k: None)

    users_api.suspend_user(user.id, request=SimpleNamespace(), db=db, current_user=admin)
    assert local_calls == [("blog", True)] and server.locked == {("shop", "10.0.0.10")}
    users_api.unsuspend_user(user.id, request=SimpleNamespace(), db=db, current_user=admin)
    assert local_calls[-1] == ("blog", False) and server.locked == set()


def test_a_dead_remote_host_does_not_block_suspension(monkeypatch):
    import pymysql
    from app.services import mariadb, teardown

    db = make_session()
    user = _accounts(db)
    monkeypatch.setattr(mariadb, "set_account_locked", lambda u, locked: None)

    def unreachable(**_kw):
        raise pymysql.err.OperationalError(2003, "timed out")

    monkeypatch.setattr(pymysql, "connect", unreachable)
    assert teardown.set_owner_databases_locked(db, user.id, True) == ["shop"]


def test_terminate_sweep_drops_remote_databases(server, monkeypatch):
    from app.models.entities import DatabaseAccount
    from app.services import mariadb, teardown

    db = make_session()
    user = _accounts(db)
    server.accounts["shop"] = ["10.0.0.10"]
    dropped_local = []
    monkeypatch.setattr(mariadb, "drop_database", lambda name, u: dropped_local.append(name))
    assert sorted(teardown.purge_owner_databases(db, user.id)) == ["blog", "shop"]
    assert dropped_local == ["blog"]
    assert server.statements("DROP DATABASE IF EXISTS `shop`") and server.accounts == {}
    assert db.query(DatabaseAccount).count() == 0
