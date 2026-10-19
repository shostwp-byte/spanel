"""What has to go when a panel account goes.

Three code paths delete a user: DELETE /api/users/{id}, the billing-driven
terminate_account, and the DirectAdmin importer's replace-existing step. They
drifted, and the drift was invisible because the panel database does not
enforce the cleanup it declares.

Two tables were re-parented onto users by later migrations and neither
deletion path followed:

  database_accounts   Migration 0014 added owner_id and 0015 made website_id
                      nullable, so a database can belong to a user and to no
                      website - which is the only kind the UI now creates
                      (api/databases.py). Both paths looked for databases by
                      website_id, so those rows survived their owner with a
                      dangling owner_id, and their MariaDB accounts were never
                      dropped. 0014 creates the owner foreign key only on the
                      non-SQLite branch, and the shipped backend is SQLite, so
                      nothing at the database layer noticed.

  site_apps           Migration 0025 declared ON DELETE CASCADE on owner_id and
                      the deletion paths were written as though it would run.
                      It does not: core/database.py never issues
                      PRAGMA foreign_keys=ON, so SQLite ignores it. What
                      actually happened was worse than an orphan - User.apps is
                      a plain one-to-many, so SQLAlchemy tried to null the
                      child's owner_id, the NOT NULL column rejected it, and
                      the whole delete raised IntegrityError *after* the Linux
                      account and the websites' files were already gone.

da_import._delete_existing_user has always swept databases by owner_id. This
module is that sweep, extracted so the three paths cannot drift again.
"""

from __future__ import annotations

import logging

from app.models.entities import DatabaseAccount, SftpAccount, SiteApp, WebauthnCredential
from app.services import mariadb, sftp_accounts, site_apps

logger = logging.getLogger("spanel.teardown")


def drop_database_record(db, db_item: DatabaseAccount) -> None:
    """Drop the MariaDB database and account, then forget the panel row.

    The row goes even if the drop fails, so a MariaDB that is already missing
    the database cannot wedge an account deletion forever.
    """
    try:
        mariadb.drop_database(db_item.db_name, db_item.db_user)
    finally:
        db.delete(db_item)
        db.flush()


def purge_owner_databases(db, owner_id: int) -> list[str]:
    """Every database this user owns, including ones attached to no website."""
    dropped: list[str] = []
    rows = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == owner_id).all()
    for db_item in rows:
        name = db_item.db_name
        drop_database_record(db, db_item)
        dropped.append(name)
    return dropped


def purge_owner_apps(db, owner_id: int) -> list[str]:
    """Stop and remove this user's applications, then forget the rows.

    delete_runtime failing must not strand the account: the unit or compose
    project may already be gone, or the Application addon may have been
    uninstalled underneath it. The row is removed either way - leaving it would
    reproduce the IntegrityError this function exists to prevent - and the
    failure is logged rather than raised.
    """
    removed: list[str] = []
    rows = db.query(SiteApp).filter(SiteApp.owner_id == owner_id).all()
    for app in rows:
        try:
            site_apps.delete_runtime(app)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("could not remove runtime for app %s: %s", app.name, exc)
        db.delete(app)
        db.flush()
        removed.append(app.name)
    return removed


def purge_owner_sftp_accounts(db, owner_id: int) -> list[str]:
    """Remove every SFTP sub-account this user owns.

    These are real Linux users with a real password. Leaving one behind after
    the panel account is gone leaves a working credential into a home directory
    that is about to be handed to somebody else - the exact failure the
    database sweep above exists to prevent, with a login attached.

    Removal failing must not strand the deletion: the chroot may already be
    unmounted or the Linux user already gone. The row goes either way, and the
    failure is logged.
    """
    removed: list[str] = []
    rows = db.query(SftpAccount).filter(SftpAccount.owner_id == owner_id).all()
    for account in rows:
        try:
            sftp_accounts.delete_account(account.linux_user)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning(
                "could not remove SFTP account %s: %s", account.linux_user, exc
            )
        db.delete(account)
        db.flush()
        removed.append(account.linux_user)
    return removed


def purge_website_sftp_accounts(db, website_id: int) -> list[str]:
    """The same sweep, for one website going away rather than a whole account."""
    removed: list[str] = []
    rows = db.query(SftpAccount).filter(SftpAccount.website_id == website_id).all()
    for account in rows:
        try:
            sftp_accounts.delete_account(account.linux_user)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not remove SFTP account %s: %s", account.linux_user, exc
            )
        db.delete(account)
        db.flush()
        removed.append(account.linux_user)
    return removed


def set_owner_sftp_accounts_locked(db, owner_id: int, locked: bool) -> list[str]:
    """Suspending a panel account has to reach its file credentials too.

    A suspended customer whose SFTP sub-account still works keeps write access
    to the very sites the suspension disabled.
    """
    touched: list[str] = []
    rows = db.query(SftpAccount).filter(SftpAccount.owner_id == owner_id).all()
    for account in rows:
        try:
            sftp_accounts.set_locked(account.linux_user, locked)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not %s SFTP account %s: %s",
                "lock" if locked else "unlock",
                account.linux_user,
                exc,
            )
        account.is_active = not locked
        touched.append(account.linux_user)
    db.flush()
    return touched


def purge_owner_passkeys(db, owner_id: int) -> list[str]:
    """Passkeys are rows and nothing else - no Linux user, no files.

    They still have to go with the account. A credential left behind keeps a
    unique index entry pointing at a user id that no longer exists, and the
    next person to hold that id would inherit a way in.
    """
    removed: list[str] = []
    rows = db.query(WebauthnCredential).filter(WebauthnCredential.user_id == owner_id).all()
    for credential in rows:
        removed.append(f"{credential.name or 'Passkey'} ({credential.rp_id})")
        db.delete(credential)
    db.flush()
    return removed


def purge_owned_resources(db, owner_id: int) -> dict[str, list[str]]:
    """Everything keyed on owner_id that a user deletion has to take with it.

    Call this before db.delete(user), and after the websites have been removed
    - website-attached databases are dropped by the website path, and this
    sweep then catches whatever is left.
    """
    return {
        "databases": purge_owner_databases(db, owner_id),
        "applications": purge_owner_apps(db, owner_id),
        "sftp_accounts": purge_owner_sftp_accounts(db, owner_id),
        "passkeys": purge_owner_passkeys(db, owner_id),
    }
