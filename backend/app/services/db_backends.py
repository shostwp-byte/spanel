"""Which MySQL server a customer database lives on, and the operations on it.

Every caller that acts on an existing DatabaseAccount goes through
backend_for(account) instead of calling app.services.mariadb directly, so a
database on a remote host is dropped, dumped, locked and measured where it
actually is. The local backend is a thin pass-through to mariadb; the remote
one is app.services.remote_mysql.RemoteMySQLBackend. Both offer the same
methods, so callers never branch on where a database is.

Paths that create a brand new database for a website (WordPress install,
provisioning, DirectAdmin import) keep creating it locally through mariadb
directly: a remote host is chosen explicitly on the Databases page.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable, Optional, Union

from sqlalchemy.orm import Session

from app.models.entities import DatabaseAccount, RemoteDbHost
from app.services import mariadb
from app.services.remote_mysql import RemoteDatabaseError, RemoteMySQLBackend

logger = logging.getLogger("spanel.db_backends")

# A remote host that just failed is left alone this long by the background
# collectors, so one dead host cannot stall every telemetry pass.
REMOTE_BACKOFF_SECONDS = 120
_backoff_lock = threading.Lock()
_backoff_until: dict[int, float] = {}


class LocalBackend:
    """The local MariaDB, reached through app.services.mariadb."""

    is_remote = False
    host = None
    label = "localhost"

    def user_exists(self, db_user: str) -> bool:
        return mariadb.user_exists(db_user)

    def create_database_credentials(self, db_name, db_user, db_password, *, password_hash=None, allow_existing_user=False):
        return mariadb.create_database_credentials(
            db_name, db_user, db_password, password_hash=password_hash, allow_existing_user=allow_existing_user
        )

    def drop_database(self, db_name: str, db_user: str):
        return mariadb.drop_database(db_name, db_user)

    def change_password(self, db_user: str, db_password: str):
        return mariadb.change_database_password(db_user, db_password)

    def export_database(self, db_name: str, output_file: str):
        return mariadb.export_database(db_name, output_file)

    def import_database(
        self,
        db_name: str,
        input_file: str,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
    ):
        return mariadb.import_database_as_tenant(
            db_name, input_file, db_user=db_user, db_password=db_password
        )

    def import_database_as_tenant(
        self,
        db_name: str,
        input_file: str,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
    ):
        if LocalBackend.import_database is not _default_local_import_database:
            with mariadb.tenant_import_credentials(db_user, db_password):
                return self.import_database(db_name, input_file)
        return mariadb.import_database_as_tenant(
            db_name, input_file, db_user=db_user, db_password=db_password
        )

    def set_write_lock(self, db_name: str, db_user: str, locked: bool) -> None:
        mariadb.set_database_write_lock(db_name, db_user, locked)

    def set_account_locked(self, db_user: str, locked: bool) -> None:
        mariadb.set_account_locked(db_user, locked)

    def enforce_resource_limits(self, db_user: str, max_user_connections: int = 25, max_statement_time: int = 15) -> None:
        mariadb.enforce_db_user_resource_limits(
            db_user, max_user_connections=max_user_connections, max_statement_time=max_statement_time
        )

    def tables_breakdown(self, db_name: str) -> list[dict]:
        return mariadb.get_database_tables_breakdown(db_name)

    def optimize(self, db_name: str) -> dict:
        return mariadb.optimize_database(db_name)

    def snipe_slow_queries(self, db_users, max_time_sec: int = 3) -> list[dict]:
        return mariadb.snipe_slow_scraping_queries(list(db_users), max_time_sec=max_time_sec)


_default_local_import_database = LocalBackend.import_database
LOCAL = LocalBackend()

Backend = Union[LocalBackend, RemoteMySQLBackend]


def backend_for_host(host: Optional[RemoteDbHost]) -> Backend:
    return LOCAL if host is None else RemoteMySQLBackend(host)


def backend_for(account: DatabaseAccount) -> Backend:
    """The backend holding this database."""
    if not getattr(account, "db_host_id", None):
        return LOCAL
    host = account.db_host
    if host is None:
        raise RemoteDatabaseError(
            f"Database '{account.db_name}' belongs to a remote host that is no longer configured."
        )
    return RemoteMySQLBackend(host)


def backend_for_db_name(db: Optional[Session], db_name: str) -> Backend:
    """For code that only has a database name (single-site backups)."""
    def lookup(session: Session, detach: bool) -> Backend:
        account = session.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first()
        if account is None:
            return LOCAL
        backend = backend_for(account)
        if backend.is_remote and detach:
            # Our session closes before the backend is used.
            session.expunge(backend.host)
        return backend

    if db is not None:
        return lookup(db, False)
    from app.core.database import SessionLocal

    with SessionLocal() as session:
        return lookup(session, True)


def group_by_backend(accounts: Iterable[DatabaseAccount]) -> list[tuple[Backend, list[DatabaseAccount]]]:
    """Accounts bucketed per server, local first. Accounts whose host is gone
    are skipped with a warning rather than failing the whole batch."""
    local: list[DatabaseAccount] = []
    remote: dict[int, tuple[Backend, list[DatabaseAccount]]] = {}
    for account in accounts:
        if not getattr(account, "db_host_id", None):
            local.append(account)
            continue
        if account.db_host is None:
            logger.warning("Database %s points at a missing remote host", account.db_name)
            continue
        bucket = remote.setdefault(account.db_host_id, (RemoteMySQLBackend(account.db_host), []))
        bucket[1].append(account)
    groups: list[tuple[Backend, list[DatabaseAccount]]] = []
    if local:
        groups.append((LOCAL, local))
    groups.extend(remote.values())
    return groups


def _in_backoff(host_id: int) -> bool:
    with _backoff_lock:
        return _backoff_until.get(host_id, 0.0) > time.monotonic()


def _mark_failed(host_id: int) -> None:
    with _backoff_lock:
        _backoff_until[host_id] = time.monotonic() + REMOTE_BACKOFF_SECONDS


def remote_database_sizes(accounts: Iterable[DatabaseAccount]) -> dict[str, int]:
    """Sizes of the remote databases among accounts, by db_name. A host that
    cannot be reached contributes nothing, so the caller keeps the last size."""
    sizes: dict[str, int] = {}
    for backend, members in group_by_backend(accounts):
        if not backend.is_remote or _in_backoff(backend.host.id):
            continue
        try:
            sizes.update(backend.database_sizes([a.db_name for a in members]))
        except Exception:  # noqa: BLE001
            _mark_failed(backend.host.id)
            logger.warning("Could not measure databases on %s", backend.label, exc_info=True)
    return sizes


def remote_user_statistics(accounts: Iterable[DatabaseAccount]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for backend, members in group_by_backend(accounts):
        if not backend.is_remote or _in_backoff(backend.host.id):
            continue
        try:
            stats.update(backend.user_statistics([a.db_user for a in members]))
        except Exception:  # noqa: BLE001
            _mark_failed(backend.host.id)
    return stats


def snipe_slow_queries(accounts: Iterable[DatabaseAccount], max_time_sec: int = 3) -> list[dict]:
    killed: list[dict] = []
    for backend, members in group_by_backend(accounts):
        users = [a.db_user for a in members if a.db_user]
        if not users:
            continue
        try:
            killed.extend(backend.snipe_slow_queries(users, max_time_sec=max_time_sec))
        except Exception:  # noqa: BLE001
            logger.warning("Could not snipe slow queries on %s", backend.label, exc_info=True)
    return killed


def set_accounts_locked(accounts: Iterable[DatabaseAccount], locked: bool) -> list[str]:
    """Suspend/unsuspend: lock or unlock every account, wherever it lives.
    Returns the names that could not be changed; one failure does not stop
    the rest."""
    failed: list[str] = []
    for account in accounts:
        try:
            backend_for(account).set_account_locked(account.db_user, locked)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not %s database account %s: %s", "lock" if locked else "unlock", account.db_user, exc
            )
            failed.append(account.db_user)
    return failed
