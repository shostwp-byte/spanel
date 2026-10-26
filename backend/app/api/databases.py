from pathlib import Path
from tempfile import NamedTemporaryFile
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typing import List
import ipaddress
import logging

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, RemoteDbHost, User, Website
from app.schemas.schemas import DatabaseCreate, DatabaseCreatedOut, DatabaseOut, DatabasePasswordUpdate
from app.services import db_backends, mariadb, panel_urls, remote_mysql, resource_monitor, storage_quota
from app.services.remote_mysql import RemoteDatabaseError
from app.services.audit import log_action
from app.services.sso_tokens import consume_phpmyadmin_token, create_phpmyadmin_token

router = APIRouter(prefix="/databases", tags=["databases"])

logger = logging.getLogger("spanel.databases")


def _is_loopback_peer(request: Request) -> bool:
    """Return True if the immediate TCP peer is loopback.

    On a dual-stack AF_INET6 socket (see app.serve.dual_stack_socket), an IPv4
    connection from 127.0.0.1 arrives with peer address ``::ffff:127.0.0.1``.
    Python's ``IPv6Address.is_loopback`` only checks ``::1`` and returns False
    for IPv4-mapped loopback addresses unless ``addr.ipv4_mapped`` is inspected.
    """
    if not request.client or not request.client.host:
        return False
    host = request.client.host.strip()
    if host.lower() in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}:
        return True
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return True
        mapped = getattr(addr, "ipv4_mapped", None)
        if mapped is not None and mapped.is_loopback:
            return True
        return False
    except ValueError:
        return False


def get_accessible_database(database_id: int, db: Session, current_user: User) -> DatabaseAccount:
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    if item.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    return item


def effective_database_limit(user: User | None) -> int:
    """Return the effective database quota for ``user`` (-1 = unlimited, 0 = disabled, >0 = max DBs).

    When the user belongs to a hosting package, the package's ``database_limit``
    is authoritative so legacy accounts whose ``users.database_limit`` column
    defaulted to 5 in SQLite cannot bypass their package limit.
    """
    if user is None or is_admin_role(getattr(user, "role", None)):
        return -1
    pkg = getattr(user, "package", None)
    if pkg is not None:
        pkg_lim = getattr(pkg, "database_limit", None)
        if pkg_lim is not None:
            return int(pkg_lim)
    raw_db_lim = getattr(user, "database_limit", None)
    return int(-1 if raw_db_lim is None else raw_db_lim)


def enforce_user_database_limit(db: Session, owner: User | None, *, creating_count: int = 1) -> None:
    """Enforce package database count quota, reseller database pool quota, and storage quotas."""
    if owner is None or is_admin_role(getattr(owner, "role", None)):
        return

    db_limit = effective_database_limit(owner)
    if getattr(owner, "package", None) is not None and getattr(owner, "database_limit", None) != db_limit:
        owner.database_limit = db_limit

    if db_limit == 0:
        raise HTTPException(
            status_code=403,
            detail="Database creation is disabled for your hosting package (limit: 0).",
        )
    if db_limit > 0:
        current_count = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == owner.id).count()
        if current_count + max(1, creating_count) > db_limit:
            raise HTTPException(
                status_code=403,
                detail=f"Database limit reached ({current_count}/{db_limit}). Upgrade your package to add more databases.",
            )

    reseller_id = getattr(owner, "reseller_id", None)
    if reseller_id:
        reseller = db.query(User).filter(User.id == reseller_id).first()
        if reseller and not is_admin_role(getattr(reseller, "role", None)):
            rp = getattr(reseller, "reseller_package", None)
            up = getattr(reseller, "package", None)
            total_db_limit = 0
            if rp is not None:
                total_db_limit = int(getattr(rp, "total_database_limit", 0) or 0)
            elif up is not None and getattr(up, "is_reseller_package", False):
                total_db_limit = int(getattr(up, "database_limit", 0) or 0)
            if total_db_limit > 0:
                reseller_db_count = (
                    db.query(DatabaseAccount)
                    .join(User, DatabaseAccount.owner_id == User.id)
                    .filter(User.reseller_id == reseller.id)
                    .count()
                )
                if reseller_db_count + max(1, creating_count) > total_db_limit:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Reseller database quota exceeded ({reseller_db_count}/{total_db_limit}).",
                    )

    pkg = getattr(owner, "package", None)
    mysql_limit_mb = int(
        (getattr(pkg, "mysql_storage_limit_mb", 0) if pkg is not None else getattr(owner, "mysql_storage_limit_mb", 0))
        or getattr(owner, "mysql_storage_limit_mb", 0)
        or 0
    )
    if mysql_limit_mb > 0:
        used_mysql = storage_quota.mysql_storage_used_bytes(db, owner)
        if used_mysql >= mysql_limit_mb * storage_quota.BYTES_PER_MB:
            raise HTTPException(
                status_code=403,
                detail=f"MySQL storage quota exceeded ({used_mysql // storage_quota.BYTES_PER_MB} MB / {mysql_limit_mb} MB).",
            )
    try:
        storage_quota.enforce_user_storage_quota(db, owner, incoming_bytes=1024)
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("", response_model=List[DatabaseOut])
def list_databases(
    q: str = Query(default="", max_length=255),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(DatabaseAccount)
    if not is_admin_role(current_user.role):
        query = query.filter(DatabaseAccount.owner_id == current_user.id)
    search = (q or "").strip().lower()
    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            DatabaseAccount.db_name.ilike(like),
            DatabaseAccount.db_user.ilike(like),
        ))
    rows = query.order_by(DatabaseAccount.id.desc()).all()
    dirty_sizes = False
    for row in rows:
        cached_sz = resource_monitor.cached_db_size_bytes(row.db_name)
        if cached_sz > 0 and getattr(row, "size_bytes", 0) != cached_sz:
            row.size_bytes = cached_sz
            dirty_sizes = True
        elif int(getattr(row, "size_bytes", 0) or 0) == 0:
            try:
                live_tables = mariadb.get_database_tables_breakdown(row.db_name)
                if live_tables:
                    live_sz = sum(int(t.get("size_bytes", 0) or 0) for t in live_tables)
                    if live_sz > 0:
                        row.size_bytes = live_sz
                        dirty_sizes = True
            except Exception:
                pass
    if dirty_sizes:
        try:
            db.commit()
        except Exception:
            db.rollback()
    return rows


@router.get("/hosts")
def list_database_hosts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Where a new database can be created: the local MariaDB, then every
    enabled remote host. Names and addresses only."""
    return {"hosts": [{"id": None, "name": "localhost", "address": "localhost", "status": "online"}]
            + remote_mysql.host_choices(db)}


@router.post("", response_model=DatabaseCreatedOut)
def create_database(payload: DatabaseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_user_database_limit(db, current_user)

    db_name = payload.db_name
    db_user = payload.db_user or db_name
    db_password = payload.db_password or mariadb.random_password()

    if db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first():
        raise HTTPException(status_code=409, detail="Database name already exists")
    if db.query(DatabaseAccount).filter(DatabaseAccount.db_user == db_user).first():
        raise HTTPException(status_code=409, detail="Database user already exists")

    host = None
    if payload.db_host_id is not None:
        host = db.query(RemoteDbHost).filter(RemoteDbHost.id == payload.db_host_id).first()
        if host is None or not host.enabled:
            raise HTTPException(status_code=404, detail="Remote database host not found or disabled")

    try:
        db_backends.backend_for_host(host).create_database_credentials(db_name, db_user, db_password)
    except HTTPException:
        raise
    except ValueError as exc:
        # A reserved account, or one that already exists in MariaDB without
        # spanel knowing about it. Both are the caller's mistake, not a fault.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RemoteDatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create MariaDB database/user")
        raise HTTPException(status_code=500, detail=f"MariaDB error: {exc}") from exc

    item = DatabaseAccount(
        owner_id=current_user.id,
        db_name=db_name,
        db_user=db_user,
        db_password=encrypt(db_password),
        db_host_id=host.id if host else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    try:
        mariadb.enforce_db_user_hourly_limits(db_user, *mariadb.owner_hourly_limits(current_user))
    except Exception:
        # The periodic database pass applies them too; a new database is not
        # refused over it.
        logger.warning("Could not apply hourly MariaDB limits to %s", db_user, exc_info=True)
    return DatabaseCreatedOut(
        id=item.id,
        owner_id=item.owner_id,
        website_id=item.website_id,
        db_name=item.db_name,
        db_user=item.db_user,
        db_password=db_password,
        db_host_id=item.db_host_id,
        db_host_name=item.db_host_name,
        db_host_address=item.db_host_address,
    )


@router.delete("/{database_id}")
def delete_database_record(database_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    try:
        db_backends.backend_for(item).drop_database(item.db_name, item.db_user)
    except RemoteDatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete MariaDB database/user")
        raise HTTPException(status_code=500, detail=f"MariaDB error: {exc}") from exc
    try:
        db.delete(item)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to delete database record")
        raise HTTPException(status_code=500, detail="Panel database error") from exc
    return {"ok": True}


@router.get("/{database_id}/download")
def download_database(database_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    temp_path = None
    try:
        temp_file = NamedTemporaryFile(prefix=f"{item.db_name}-", suffix=".sql", delete=False)
        temp_file.close()
        temp_path = Path(temp_file.name)
        db_backends.backend_for(item).export_database(item.db_name, str(temp_path))
    except ValueError as exc:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RemoteDatabaseError as exc:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FileResponse(
        temp_path,
        filename=f"{item.db_name}.sql",
        media_type="application/sql",
        background=BackgroundTask(lambda path: path.unlink(missing_ok=True), temp_path),
    )


@router.get("/phpmyadmin-sso/{token}")
def consume_phpmyadmin_sso(token: str, request: Request):
    """Consume a one-shot phpMyAdmin SSO token.

    Security model:
      * 256-bit token entropy (secrets.token_urlsafe(32)).
      * One-shot (file removed on read), TTL 60 seconds.
      * Restricted to loopback callers. The phpMyAdmin signon script always
        curls ``http(s)://127.0.0.1:<port>/api/...`` from the same host, so a
        legitimate request never has a remote peer.
    """
    if not _is_loopback_peer(request):
        peer = request.client.host if request.client else "unknown"
        logger.warning("phpmyadmin-sso non-loopback access attempt from %s", peer)
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    data = consume_phpmyadmin_token(token)
    if not data:
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/{database_id}/phpmyadmin-sso")
def create_phpmyadmin_sso(database_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    try:
        db_password = decrypt(item.db_password)
    except RuntimeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to access stored database password; please re-save the password in panel settings",
        )
    if item.db_host_id is None:
        try:
            mariadb.create_database_credentials(
                item.db_name,
                item.db_user,
                db_password,
                allow_existing_user=True,
            )
        except Exception as exc:
            logger.debug("Could not refresh local MariaDB credentials before phpMyAdmin SSO: %s", exc)
    token = create_phpmyadmin_token(
        item.db_user, db_password, item.db_name,
        server=remote_mysql.phpmyadmin_server_index(db, item.db_host),
    )
    log_action(
        db,
        current_user.id,
        "phpmyadmin_sso",
        f"db={item.db_name}",
        request=request,
    )
    return {"url": f"{panel_urls.tools_base_url(request)}/phpmyadmin/spanel-signon.php?spanel_sso={token}"}


@router.post("/{database_id}/password")
def change_database_password(database_id: int, payload: DatabasePasswordUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    try:
        db_backends.backend_for(item).change_password(item.db_user, payload.password)
    except RemoteDatabaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    item.db_password = encrypt(payload.password)
    db.commit()
    return {"ok": True, "db_user": item.db_user}
