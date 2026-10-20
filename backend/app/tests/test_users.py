import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import users
from app.core.database import Base
from app.models.entities import User, Website
from app.schemas.schemas import UserCreate
from app.services import storage_quota


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _admin():
    return User(id=1, username="admin", email="admin@agency.io", hashed_password="x", role="admin")


@pytest.fixture(autouse=True)
def _no_real_side_effects(monkeypatch):
    monkeypatch.setattr(users.site_users, "ensure_panel_user", lambda *a, **k: None)
    monkeypatch.setattr(users, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(users.storage_quota, "storage_usage_summary", lambda *a, **k: {})


def test_two_users_may_share_an_email_if_the_username_differs():
    db = _db()
    db.add(_admin())
    db.commit()

    users.create_user(
        UserCreate(username="reseller_a", email="ops@agency.io", password="longenoughpw1"),
        request=None, db=db, current_user=_admin(),
    )
    users.create_user(
        UserCreate(username="reseller_b", email="ops@agency.io", password="longenoughpw2"),
        request=None, db=db, current_user=_admin(),
    )
    assert db.query(User).filter(User.email == "ops@agency.io").count() == 2


def test_a_duplicate_username_is_still_refused():
    db = _db()
    db.add(_admin())
    db.commit()
    users.create_user(
        UserCreate(username="taken", email="a@one.io", password="longenoughpw1"),
        request=None, db=db, current_user=_admin(),
    )
    with pytest.raises(Exception) as exc:
        users.create_user(
            UserCreate(username="taken", email="b@two.io", password="longenoughpw2"),
            request=None, db=db, current_user=_admin(),
        )
    assert getattr(exc.value, "status_code", None) == 409


def test_user_storage_usage_is_cached_and_forgettable(monkeypatch):
    db = _db()
    user = User(id=7, username="client", email="c@one.io", hashed_password="x", role="end_user")
    db.add(user)
    db.commit()

    walks = {"n": 0}
    monkeypatch.setattr(storage_quota, "website_storage_used_bytes", lambda w: walks.__setitem__("n", walks["n"] + 1) or 10)
    monkeypatch.setattr(storage_quota, "app_storage_used_bytes", lambda *a: 0)
    db.add(Website(id=1, domain="c.io", owner_id=7, root_path="/home/client/c.io"))
    db.commit()
    storage_quota._user_usage_cache.clear()

    storage_quota.user_storage_used_bytes(db, user, use_cache=True)
    storage_quota.user_storage_used_bytes(db, user, use_cache=True)
    assert walks["n"] == 1  # second call served from cache, no filesystem walk

    storage_quota.forget_user_storage(7)
    storage_quota.user_storage_used_bytes(db, user, use_cache=True)
    assert walks["n"] == 2  # recomputed after forget

    # the uncached path (quota enforcement) always recomputes
    storage_quota.user_storage_used_bytes(db, user)
    assert walks["n"] == 3
