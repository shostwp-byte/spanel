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
from app.models.entities import DatabaseAccount, User, Website
from app.schemas.schemas import DatabaseCreate, DatabaseCreatedOut, DatabaseOut, DatabasePasswordUpdate
from app.services import mariadb, multi_server, panel_urls, resource_monitor, storage_quota
from app.services.audit import log_action
from app.services.sso_tokens import consume_phpmyadmin_token, create_phpmyadmin_token

router = APIRouter(prefix="/databases", tags=["databases"])

logger = logging.getLogger("spanel.databases")


def _is_loopback_peer(request: Request) -> bool:
    """Return True if the immediate TCP peer is loopback.

    With ``--forwarded-allow-ips 127.0.0.1`` (see installer/install.sh) any
    X-Forwarded-For from a non-trusted peer is dropped, so request.client.host
    is the real connecting address.
    """
    if not request.client:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def get_accessible_database(database_id: int, db: Session, current_user: User) -> DatabaseAccount:
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    if item.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    return item


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
    for row in rows:
        cached_sz = resource_monitor.cached_db_size_bytes(row.db_name)
        if cached_sz > 0 and getattr(row, "size_bytes", 0) != cached_sz:
            row.size_bytes = cached_sz
    return rows


@router.post("", response_model=DatabaseCreatedOut)
def create_database(payload: DatabaseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_admin_role(current_user.role):
        raw_db_lim = getattr(current_user, "database_limit", None)
        if raw_db_lim is None and current_user.package is not None:
            raw_db_lim = getattr(current_user.package, "database_limit", None)
        db_limit = int(-1 if raw_db_lim is None else raw_db_lim)
        if db_limit == 0:
            raise HTTPException(status_code=403, detail="Database creation is disabled for your hosting package (limit: 0).")
        if db_limit > 0:
            current_count = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == current_user.id).count()
            if current_count >= db_limit:
                raise HTTPException(status_code=403, detail=f"Database limit reached ({current_count}/{db_limit}). Upgrade your package to add more databases.")
        mysql_limit_mb = int(getattr(current_user, "mysql_storage_limit_mb", 0) or 0)
        if mysql_limit_mb > 0:
            used_mysql = storage_quota.mysql_storage_used_bytes(db, current_user)
            if used_mysql >= mysql_limit_mb * storage_quota.BYTES_PER_MB:
                raise HTTPException(status_code=403, detail=f"MySQL storage quota exceeded ({used_mysql // storage_quota.BYTES_PER_MB} MB / {mysql_limit_mb} MB).")
        try:
            storage_quota.enforce_user_storage_quota(db, current_user, incoming_bytes=1024)
        except storage_quota.StorageQuotaExceeded as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    db_name = payload.db_name
    db_user = payload.db_user or db_name
    db_password = payload.db_password or mariadb.random_password()

    if db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first():
        raise HTTPException(status_code=409, detail="Database name already exists")
    if db.query(DatabaseAccount).filter(DatabaseAccount.db_user == db_user).first():
        raise HTTPException(status_code=409, detail="Database user already exists")

    try:
        if getattr(payload, "remote_db_host_id", None):
            remote_host = multi_server.get_active_remote_db_host(payload.remote_db_host_id)
            if not remote_host:
                raise HTTPException(status_code=404, detail="Remote MySQL/MariaDB host not found or disabled")
            multi_server.provision_remote_database(remote_host, db_name, db_user, db_password)
        else:
            mariadb.create_database_credentials(db_name, db_user, db_password)
    except HTTPException:
        raise
    except ValueError as exc:
        # A reserved account, or one that already exists in MariaDB without
        # spanel knowing about it. Both are the caller's mistake, not a fault.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create MariaDB database/user")
        raise HTTPException(status_code=500, detail=f"MariaDB error: {exc}") from exc

    item = DatabaseAccount(
        owner_id=current_user.id,
        db_name=db_name,
        db_user=db_user,
        db_password=encrypt(db_password),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return DatabaseCreatedOut(
        id=item.id,
        owner_id=item.owner_id,
        website_id=item.website_id,
        db_name=item.db_name,
        db_user=item.db_user,
        db_password=db_password,
    )


@router.delete("/{database_id}")
def delete_database_record(database_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    try:
        mariadb.drop_database(item.db_name, item.db_user)
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
        mariadb.export_database(item.db_name, str(temp_path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    token = create_phpmyadmin_token(item.db_user, db_password, item.db_name)
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
    mariadb.change_database_password(item.db_user, payload.password)
    item.db_password = encrypt(payload.password)
    db.commit()
    return {"ok": True, "db_user": item.db_user}
