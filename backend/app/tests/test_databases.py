from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import databases
from app.core.database import Base
from app.models.entities import DatabaseAccount, User


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    db.add_all([
        User(id=1, username="admin", email="a@agency.io", hashed_password="x", role="admin"),
        User(id=2, username="shop", email="s@agency.io", hashed_password="x", role="end_user"),
        DatabaseAccount(id=1, owner_id=1, db_name="nhs_data1402", db_user="nhs_data1402", db_password="x"),
        DatabaseAccount(id=2, owner_id=1, db_name="nhs_data2802", db_user="nhs_data2802", db_password="x"),
        DatabaseAccount(id=3, owner_id=2, db_name="shop_wp", db_user="shop_wp", db_password="x"),
    ])
    db.commit()


def _admin():
    return User(id=1, username="admin", email="a@agency.io", hashed_password="x", role="admin")


def _shop():
    return User(id=2, username="shop", email="s@agency.io", hashed_password="x", role="end_user")


def test_empty_query_returns_everything_for_an_admin():
    db = _db(); _seed(db)
    rows = databases.list_databases(q="", db=db, current_user=_admin())
    assert {r.db_name for r in rows} == {"nhs_data1402", "nhs_data2802", "shop_wp"}


def test_query_matches_db_name_case_insensitively():
    db = _db(); _seed(db)
    rows = databases.list_databases(q="NHS", db=db, current_user=_admin())
    assert {r.db_name for r in rows} == {"nhs_data1402", "nhs_data2802"}


def test_query_matches_db_user_too():
    db = _db(); _seed(db)
    rows = databases.list_databases(q="shop_wp", db=db, current_user=_admin())
    assert [r.db_name for r in rows] == ["shop_wp"]


def test_a_non_admin_only_sees_their_own_even_while_searching():
    db = _db(); _seed(db)
    rows = databases.list_databases(q="data", db=db, current_user=_shop())
    assert rows == []
    rows = databases.list_databases(q="", db=db, current_user=_shop())
    assert [r.db_name for r in rows] == ["shop_wp"]
