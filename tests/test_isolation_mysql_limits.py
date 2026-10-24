"""MariaDB per-account hourly limits, and the per-user limit columns they ride on.

The SQL is checked as text here; MariaDB actually refusing the 1001st query
of the hour is a real-server check.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HOURLY = ("mysql_max_queries_per_hour", "mysql_max_updates_per_hour", "mysql_max_connections_per_hour")


def test_hourly_limits_sql_sets_all_three_and_zero_means_unlimited():
    from app.services import mariadb

    assert mariadb.hourly_limits_sql("u_shop", 50000, 5000, 600) == (
        "ALTER USER 'u_shop'@'localhost' WITH MAX_QUERIES_PER_HOUR 50000"
        " MAX_UPDATES_PER_HOUR 5000 MAX_CONNECTIONS_PER_HOUR 600;\n"
    )
    # Lowering a package to "no limit" must lift what was set before.
    assert mariadb.hourly_limits_sql("u_shop", 0, -1, None).endswith(
        "MAX_QUERIES_PER_HOUR 0 MAX_UPDATES_PER_HOUR 0 MAX_CONNECTIONS_PER_HOUR 0;\n")
    assert "MAX_QUERIES_PER_HOUR 1000000000 " in mariadb.hourly_limits_sql("u_shop", 10**12, "junk", 1)


@pytest.mark.parametrize("bad", ["root", "mysql", "u'; DROP USER x; --", "", "a b"])
def test_hourly_limits_refuse_system_and_malformed_accounts(bad):
    from app.services import mariadb

    with pytest.raises(ValueError):
        mariadb.hourly_limits_sql(bad, 1, 1, 1)


def _memory_db():
    from app.core.database import Base
    import app.models.entities  # noqa: F401

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_user_limits_now_survive_the_request():
    """User had none of these mapped: a package's CPU, memory, tasks, IO,
    inode and MySQL limits copied onto a user vanished with the request, so
    every account ran on the defaults."""
    from app.api.users import _apply_package_limits
    from app.models.entities import User, UserPackage

    engine = _memory_db()
    with Session(engine) as db:
        package = UserPackage(
            name="shop", cpu_quota_percent=150, memory_high_mb=700, memory_max_mb=900, tasks_max=120,
            io_read_mbps=40, io_write_mbps=20, iops_read_max=900, iops_write_max=800, inode_limit=300000,
            mysql_storage_limit_mb=3000, mysql_max_db_size_mb=1000, mysql_max_queries_per_hour=50000,
            mysql_max_updates_per_hour=5000, mysql_max_connections_per_hour=600,
        )
        db.add(package)
        db.commit()
        user = User(username="alice", email="a@example.com", hashed_password="x")
        _apply_package_limits(user, package)
        db.add(user)
        db.commit()
        user_id = user.id
    with Session(engine) as db:
        stored = db.get(User, user_id)
        assert (stored.cpu_quota_percent, stored.memory_high_mb, stored.memory_max_mb, stored.tasks_max) == (150, 700, 900, 120)
        assert (stored.io_read_mbps, stored.io_write_mbps, stored.iops_read_max, stored.iops_write_max) == (40, 20, 900, 800)
        assert (stored.inode_limit, stored.mysql_storage_limit_mb, stored.mysql_max_db_size_mb) == (300000, 3000, 1000)
        assert [getattr(stored, f) for f in HOURLY] == [50000, 5000, 600]


def test_every_database_of_the_owner_gets_its_package_limits():
    from app.models.entities import DatabaseAccount, User
    from app.services import mariadb, resource_monitor

    engine = _memory_db()
    with Session(engine) as db:
        owner = User(username="alice", email="a@example.com", hashed_password="x",
                     mysql_max_queries_per_hour=1000, mysql_max_updates_per_hour=100, mysql_max_connections_per_hour=60)
        other = User(username="bob", email="b@example.com", hashed_password="x")
        db.add_all([owner, other])
        db.commit()
        db.add_all([
            DatabaseAccount(owner_id=owner.id, db_name="alice_wp", db_user="alice_wp", db_password="x"),
            DatabaseAccount(owner_id=owner.id, db_name="alice_shop", db_user="alice_shop", db_password="x"),
            DatabaseAccount(owner_id=other.id, db_name="bob_wp", db_user="bob_wp", db_password="x"),
        ])
        db.commit()
        sent = []
        with patch.object(mariadb.settings, "command_dry_run", False), \
                patch.object(mariadb, "_run_sql", side_effect=lambda sql, check=True: sent.append(sql) or SimpleNamespace(returncode=0)):
            assert resource_monitor.apply_user_mysql_limits(db, owner) == 2
    assert sorted(sent) == sorted(
        mariadb.hourly_limits_sql(u, 1000, 100, 60) for u in ("alice_wp", "alice_shop")
    )


def test_package_update_carries_hourly_limits_to_its_users():
    text = (REPO_ROOT / "backend/app/api/packages.py").read_text(encoding="utf-8")
    for field in HOURLY:
        assert f"user.{field} = package.{field}" in text


def test_alembic_alone_builds_the_limit_columns(tmp_path):
    """CI builds the schema with alembic only; the model now maps the user
    limit columns, so the migrations have to create them too."""
    db_file = tmp_path / "alembic.sqlite"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file.as_posix()}", PYTHONPATH=str(REPO_ROOT / "backend"))
    for args in (["upgrade", "head"], ["heads"]):
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=REPO_ROOT / "backend",
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert len([line for line in result.stdout.splitlines() if line.strip()]) == 1
    inspector = inspect(create_engine(f"sqlite:///{db_file.as_posix()}"))
    users = {c["name"] for c in inspector.get_columns("users")}
    packages = {c["name"] for c in inspector.get_columns("user_packages")}
    for column in HOURLY + ("cpu_quota_percent", "memory_max_mb", "tasks_max", "io_read_mbps", "mysql_storage_limit_mb"):
        assert column in users and column in packages, column
    assert {"inode_limit", "database_limit", "bandwidth_limit_mb"} <= users
