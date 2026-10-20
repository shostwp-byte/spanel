"""The dashboard's status summary: scoping, admin-only parts, failing probes.

The dashboard used to repeat the sidebar as three groups of links; the
operator asked for how things stand instead (2026-09-25), gathered in one
request so opening the panel costs one round trip.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import dashboard
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.models.entities import BackupSchedule, DatabaseAccount, User, Website


@pytest.fixture
def env(monkeypatch):
    # Imported here, not at the top: importing app.main runs the migrations,
    # and alembic's logging setup disables every logger that exists by then -
    # at collection time that silenced the CRS-reconcile tests' caplog.
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    for owner, domain, ssl, status, waf_on in (("alice", "a1.test", True, "active", True),
                                               ("alice", "a2.test", False, "active", False),
                                               ("bob", "b1.test", False, "suspended", True)):
        db.add(Website(domain=domain, owner_id=people[owner].id, root_path=f"/home/{owner}/{domain}",
                       document_root="public_html", linux_user=owner, php_version="8.3", app_type="php",
                       status=status, ssl_enabled=ssl, waf_enabled=waf_on))
    db.add(DatabaseAccount(owner_id=people["alice"].id, db_name="alice_db", db_user="alice_db", db_password="x"))
    db.commit()

    monkeypatch.setattr(dashboard.firewall, "is_enabled", lambda: True)
    monkeypatch.setattr(dashboard, "_waf_engine", lambda: "on")
    monkeypatch.setattr(dashboard, "_services", lambda: {"total": 5, "running": 4, "stopped": ["php8.1-fpm"]})
    monkeypatch.setattr(dashboard, "_malware", lambda: {"installed": True, "running": False, "last_scan": None})
    monkeypatch.setattr(dashboard.updates, "cached_release_summary",
                        lambda: {"current_version": "1.0.1", "latest_version": "1.0.2", "update_available": True})

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["alice"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client.get("/api/dashboard/summary").json()

    try:
        yield SimpleNamespace(as_user=as_user, db=db)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_a_customer_sees_its_own_numbers_and_no_server_state(env):
    body = env.as_user("alice")
    assert body["websites"] == {"total": 2, "suspended": 0, "waf_on": 1}
    assert body["ssl"]["secured"] == 1 and body["ssl"]["unsecured"] == ["a2.test"]
    assert body["databases"]["total"] == 1
    for key in ("firewall", "waf", "services", "updates", "backups", "malware"):
        assert key not in body, f"{key} describes the server, not the customer's account"


def test_an_administrator_sees_everything(env):
    body = env.as_user("root_admin")
    assert body["websites"] == {"total": 3, "suspended": 1, "waf_on": 2}
    assert body["ssl"]["unsecured_count"] == 2
    assert body["firewall"] == {"enabled": True}
    assert body["waf"] == {"engine": "on"}
    assert body["services"]["stopped"] == ["php8.1-fpm"]
    assert body["updates"]["update_available"] is True
    assert body["backups"] == {"schedules": 0, "last_run_at": None, "last_status": None, "failed": 0}


def test_a_failed_backup_schedule_is_counted(env):
    from datetime import datetime

    env.db.add(BackupSchedule(all_users=True, schedule="0 2 * * *", is_active=True,
                              last_run_at=datetime(2026, 9, 24, 2, 0), last_status="error"))
    env.db.add(BackupSchedule(all_users=True, schedule="0 3 * * *", is_active=True,
                              last_run_at=datetime(2026, 9, 23, 3, 0), last_status="ok"))
    env.db.commit()
    backups = env.as_user("root_admin")["backups"]
    assert backups["schedules"] == 2 and backups["failed"] == 1
    assert backups["last_status"] == "error" and backups["last_run_at"].startswith("2026-09-24")


def test_a_failing_probe_is_left_out_not_fatal(env, monkeypatch):
    def broken():
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(dashboard.firewall, "is_enabled", broken)
    body = env.as_user("root_admin")
    assert body["firewall"] == {"enabled": None}
    assert body["websites"]["total"] == 3


def test_every_service_the_services_page_lists_is_checked_in_one_call(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard, "list_services", lambda: ["spanel-api", "nginx", "php8.4-fpm", "mariadb"])
    monkeypatch.setattr(dashboard.shell, "run",
                        lambda args, check=False: calls.append(args) or SimpleNamespace(stdout="active\nactive\ninactive\n"))
    result = dashboard._services()
    assert len(calls) == 1, "one systemctl call, not one per unit"
    # mariadb printed nothing: a unit with no answer is not counted as running.
    assert result == {"total": 4, "running": 2, "stopped": ["php8.4-fpm", "mariadb"]}


def test_the_malware_card_reports_the_last_scan_that_finished(monkeypatch):
    """An interrupted run scanned nothing; "clean" beside it would be a lie."""
    monkeypatch.setattr(dashboard.maldet, "installed", lambda: True)
    monkeypatch.setattr(dashboard.panel_settings, "list_malware_scan_jobs", lambda limit=20: [
        {"status": "interrupted", "infected": 0, "finished_at": "2026-09-21T01:41:44"},
        {"status": "infected", "infected": 2, "finished_at": "2026-09-13T14:06:39"},
        {"status": "done", "infected": 0, "finished_at": "2026-09-06T14:32:53"},
    ])
    result = dashboard._malware()
    assert result["last_scan"] == {"status": "infected", "infected": 2, "finished_at": "2026-09-13T14:06:39"}
    assert result["installed"] is True and result["running"] is False


@pytest.mark.parametrize("output,expected", [
    ("Status: enabled\nEngine: iptables + ipset\nChain active: yes\n", True),
    ("Status: disabled\nChain active: no\n", False),
    # Switched on, but INPUT does not reach the chain: nothing is filtered.
    ("Status: enabled\nChain active: no\n", False),
    ("Status: unknown\nEngine: iptables + ipset\n", None),
])
def test_the_firewall_counts_as_on_only_while_it_filters(monkeypatch, output, expected):
    from app.services import firewall

    monkeypatch.setattr(firewall, "status", lambda: SimpleNamespace(stdout=output))
    assert firewall.is_enabled() is expected


def test_the_update_notice_reads_the_last_check_and_never_waits(monkeypatch):
    from app.services import updates

    started = []
    monkeypatch.setattr(updates, "APP_VERSION", "1.0.160")
    monkeypatch.setattr(updates, "_refresh_in_background", lambda: started.append(True))
    monkeypatch.setattr(updates, "_read_update_state", lambda: {
        "latest_version": "1.0.162", "last_checked_at": "2026-09-25T00:00:00Z", "last_checked_epoch": updates.time.time()})
    assert updates.cached_release_summary()["update_available"] is True
    assert not started, "a fresh check is not repeated"

    # The version just installed is not "an update": the answer compares
    # version numbers, which the update itself moved forward.
    monkeypatch.setattr(updates, "_read_update_state", lambda: {"latest_version": "1.0.160", "last_checked_epoch": 1})
    assert updates.cached_release_summary()["update_available"] is False
    assert started == [True], "a stale check is refreshed in the background"
