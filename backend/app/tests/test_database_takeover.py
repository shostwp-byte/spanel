"""Creating a database must not take over a MariaDB account it did not create."""

import pytest

from app.services import mariadb


def test_reserved_accounts_are_refused():
    """The panel authenticates to MariaDB with ALL PRIVILEGES ON *.*, so a
    request naming root was enough to reset root's password - and the API
    returned it to the caller."""
    for name in ("root", "mysql", "spanel"):
        with pytest.raises(ValueError) as exc:
            mariadb.create_database_credentials("somedb", name, "AnyPassword123")
        assert "reserved" in str(exc.value).lower()


def test_accounts_with_a_dot_never_get_that_far():
    """mariadb.sys and debian-sys-maint are on the reserved list as well, but
    the identifier check rejects them first - a dot and a hyphen are not legal
    in a spanel database user name. Asserted separately so the reserved-list
    test above is not silently passing for the wrong reason."""
    for name in ("mariadb.sys", "debian-sys-maint"):
        with pytest.raises(ValueError) as exc:
            mariadb.create_database_credentials("somedb", name, "AnyPassword123")
        assert "invalid database identifier" in str(exc.value).lower()


def test_reserved_check_ignores_case():
    with pytest.raises(ValueError):
        mariadb.create_database_credentials("somedb", "ROOT", "AnyPassword123")


def test_an_account_that_already_exists_is_refused(monkeypatch):
    """Not just the reserved names: any account spanel did not create.

    Otherwise one panel user could reset another tenant's database password by
    naming their account, which the duplicate check in the API cannot catch
    for accounts missing from spanel's own table.
    """
    monkeypatch.setattr(mariadb, "user_exists", lambda user: True)

    with pytest.raises(ValueError) as exc:
        mariadb.create_database_credentials("newdb", "someone_elses_user", "AnyPassword123")

    assert "already exists" in str(exc.value).lower()


def test_creating_a_fresh_account_still_works(monkeypatch):
    monkeypatch.setattr(mariadb, "user_exists", lambda user: False)
    sent = {}
    monkeypatch.setattr(mariadb, "_run_sql", lambda sql, **kw: sent.setdefault("sql", sql))

    result = mariadb.create_database_credentials("shopdb", "shopuser", "AnyPassword123")

    assert result["db_user"] == "shopuser"
    assert "CREATE USER IF NOT EXISTS" in sent["sql"]
    # The unconditional reset is what made takeover possible; a plain create
    # must not carry it.
    assert "ALTER USER" not in sent["sql"]


def test_a_restore_may_set_the_password_of_its_own_account(monkeypatch):
    """A backup or DirectAdmin import recreates the account that archive owned,
    so it needs the ALTER. It still cannot touch a reserved account."""
    monkeypatch.setattr(mariadb, "user_exists", lambda user: True)
    sent = {}
    monkeypatch.setattr(mariadb, "_run_sql", lambda sql, **kw: sent.setdefault("sql", sql))

    mariadb.create_database_credentials(
        "restoreddb", "restoreduser", "AnyPassword123", allow_existing_user=True
    )

    assert "ALTER USER" in sent["sql"]


def test_a_restore_still_cannot_touch_root():
    with pytest.raises(ValueError):
        mariadb.create_database_credentials(
            "anydb", "root", "AnyPassword123", allow_existing_user=True
        )


def test_dropping_and_repassword_also_refuse_reserved_accounts():
    """The same account name reaches two other statements. Guarding only the
    create path would leave `DROP USER root` and `ALTER USER root` reachable."""
    with pytest.raises(ValueError):
        mariadb.drop_database("somedb", "root")
    with pytest.raises(ValueError):
        mariadb.change_database_password("root", "AnyPassword123")
