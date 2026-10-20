"""Deleting a panel account has to take its databases and applications with it.

Two tables were re-parented onto users by later migrations, and neither
deletion path followed:

  database_accounts   0014 added owner_id, 0015 made website_id nullable. A
                      database can now belong to a user and to no website -
                      which is the only kind POST /api/databases creates. Both
                      delete_user and terminate_account looked for databases by
                      website_id, so those rows outlived their owner with a
                      dangling owner_id and their MariaDB accounts were never
                      dropped.

  site_apps           0025 declared ON DELETE CASCADE and the deletion paths
                      were written as though it would run. core/database.py
                      never issues PRAGMA foreign_keys=ON, so SQLite ignores
                      it. What actually happened was worse than an orphan:
                      User.apps is a plain one-to-many, so SQLAlchemy tried to
                      null the child's owner_id, the NOT NULL column rejected
                      it, and the whole delete raised IntegrityError - after
                      the Linux account and the sites' files were already gone.

da_import._delete_existing_user always swept by owner_id. These tests hold the
other two paths to the same rule, and hold all three to one implementation.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import Base, DatabaseAccount, SiteApp, User
from app.services import teardown


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _user(db, username="tenant"):
    user = User(username=username, email=f"{username}@x.test",
                hashed_password="x", role="end_user")
    db.add(user)
    db.flush()
    return user


def test_a_standalone_database_is_dropped_with_its_owner(db, monkeypatch):
    """website_id IS NULL is the normal case, not an edge case."""
    dropped = []
    monkeypatch.setattr(teardown.mariadb, "drop_database",
                        lambda name, user: dropped.append((name, user)))

    user = _user(db)
    db.add(DatabaseAccount(owner_id=user.id, website_id=None,
                           db_name="orphan_db", db_user="orphan_u",
                           db_password="enc"))
    db.flush()

    result = teardown.purge_owner_databases(db, user.id)

    assert result == ["orphan_db"]
    assert dropped == [("orphan_db", "orphan_u")], (
        "the MariaDB database and account must be dropped, not just the panel row"
    )
    assert db.query(DatabaseAccount).filter_by(owner_id=user.id).count() == 0


def test_the_panel_row_goes_even_if_the_drop_fails(db, monkeypatch):
    """A MariaDB that has already lost the database must not wedge a deletion."""
    def boom(name, user):
        raise RuntimeError("mysql is not running")

    monkeypatch.setattr(teardown.mariadb, "drop_database", boom)
    user = _user(db)
    db.add(DatabaseAccount(owner_id=user.id, website_id=None, db_name="d",
                           db_user="u", db_password="enc"))
    db.flush()

    with pytest.raises(RuntimeError):
        teardown.purge_owner_databases(db, user.id)

    assert db.query(DatabaseAccount).filter_by(owner_id=user.id).count() == 0


def test_applications_are_torn_down_with_their_owner(db, monkeypatch):
    """Without this the delete does not orphan the row - it raises.

    User.apps is a one-to-many with no cascade, so SQLAlchemy de-associates by
    setting owner_id to NULL, and site_apps.owner_id is NOT NULL (0025:68).
    """
    removed = []
    monkeypatch.setattr(teardown.site_apps, "delete_runtime",
                        lambda app, name=None: removed.append(app.name))

    user = _user(db)
    db.add(SiteApp(owner_id=user.id, name="queue", port=21001))
    db.flush()

    assert teardown.purge_owner_apps(db, user.id) == ["queue"]
    assert removed == ["queue"], "the systemd unit or compose project must go too"
    assert db.query(SiteApp).filter_by(owner_id=user.id).count() == 0


def test_a_failing_runtime_removal_does_not_strand_the_account(db, monkeypatch):
    """The unit may already be gone, or the addon uninstalled underneath it."""
    def boom(app, name=None):
        raise RuntimeError("unit not found")

    monkeypatch.setattr(teardown.site_apps, "delete_runtime", boom)
    user = _user(db)
    db.add(SiteApp(owner_id=user.id, name="queue", port=21002))
    db.flush()

    assert teardown.purge_owner_apps(db, user.id) == ["queue"]
    assert db.query(SiteApp).filter_by(owner_id=user.id).count() == 0, (
        "leaving the row would reproduce the IntegrityError this prevents"
    )


def test_deleting_the_user_afterwards_no_longer_raises(db, monkeypatch):
    """The end-to-end shape: purge, then delete, and the commit succeeds."""
    monkeypatch.setattr(teardown.mariadb, "drop_database", lambda n, u: None)
    monkeypatch.setattr(teardown.site_apps, "delete_runtime", lambda a, name=None: None)

    user = _user(db)
    db.add(DatabaseAccount(owner_id=user.id, website_id=None, db_name="d",
                           db_user="u", db_password="enc"))
    db.add(SiteApp(owner_id=user.id, name="app", port=21003))
    db.flush()

    teardown.purge_owned_resources(db, user.id)
    db.delete(user)
    db.commit()

    assert db.query(User).filter_by(username="tenant").count() == 0
    assert db.query(SiteApp).count() == 0
    assert db.query(DatabaseAccount).count() == 0


def test_all_three_deletion_paths_use_the_shared_sweep():
    """They drifted once. One implementation is what stops it happening again."""
    root = Path(__file__).resolve().parents[3] / "backend" / "app"
    users_py = (root / "api" / "users.py").read_text(encoding="utf-8")
    prov_py = (root / "services" / "provisioning.py").read_text(encoding="utf-8")
    da_py = (root / "services" / "da_import.py").read_text(encoding="utf-8")

    assert "teardown.purge_owned_resources(db, user.id)" in users_py
    assert "teardown.purge_owned_resources(db, user.id)" in prov_py
    assert "teardown.drop_database_record(db, db_item)" in da_py, (
        "da_import had the right sweep first; it should now share the helper"
    )
