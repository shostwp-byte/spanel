import json
import logging
import os
import threading
import tarfile
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.core.permissions import Role, ensure_role, is_admin_role, normalize_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import BackupSchedule, DatabaseAccount, SftpBackupTarget, SiteApp, User, Website
from app.schemas.schemas import (
    BackupScheduleCreate,
    BackupScheduleOut,
    BackupCreate,
    CronCreate,
    CronDelete,
    DaBulkImportRequest,
    PhpConfigUpdate,
    PhpConfigRestore,
    PhpExtensionInstall,
    PhpOpcacheToggle,
    RestoreBackup,
    RestoreListRequest,
    RestoreRunRequest,
    RestoreSource,
    SftpBackupRun,
    SftpBackupTargetCreate,
    SftpBackupTargetOut,
    UserBackupCreate,
    UserRestoreBackup,
    WpAction,
)
from app.services import (
    addons,
    backup,
    backup_s3,
    cron,
    file_manager,
    php,
    restore_sources,
    site_apps,
    site_users,
    storage_quota,
    wordpress,
)
from app.services.audit import log_action

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
logger = logging.getLogger(__name__)


FILE_JOB_LIMIT = 50
_file_job_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spanel-file-job")
_file_jobs: dict[str, dict] = {}
_file_jobs_lock = threading.Lock()

BACKUP_JOB_LIMIT = 50
_backup_job_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spanel-backup-job")
_backup_jobs: dict[str, dict] = {}
_backup_jobs_lock = threading.Lock()


class FileWrite(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    path: str
    content: str


class FileMkdir(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    path: str = site_users.PUBLIC_DIR
    name: str


class FileCreate(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    path: str = ""
    name: str


class FileRename(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    path: str
    new_name: str


class FileChmod(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    path: str
    mode: str


class FileBulkDelete(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    paths: list[str]


class FileTransfer(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    paths: list[str]
    destination_path: str = site_users.PUBLIC_DIR


class FileArchive(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    base_path: str = site_users.PUBLIC_DIR
    paths: list[str]
    output_name: str = ""
    format: str = "zip"


class FileExtract(BaseModel):
    # Exactly one of these picks the tree to work in.
    website_id: Optional[int] = None
    app_id: Optional[int] = None
    archive_path: str
    destination_path: str = ""


def file_target_key(target) -> str:
    """Identifies the tree a file job ran in.

    A website id and an app id are separate sequences, so a bare number would
    let one target's jobs show up under the other.
    """
    app_id = getattr(target, "app_id", None)
    if app_id:
        return f"app:{app_id}"
    return f"site:{getattr(target, 'id', '') or ''}"


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _public_file_job(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "website_id": job.get("website_id"),
        "target_key": job.get("target_key", ""),
        "archive_path": job.get("archive_path", ""),
        "destination_path": job.get("destination_path", ""),
        "target": job.get("target", ""),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
    }


def _set_file_job(job_id: str, **updates) -> None:
    with _file_jobs_lock:
        job = _file_jobs.get(job_id)
        if not job:
            return
        job.update(updates)


def _remember_file_job(job: dict) -> dict:
    with _file_jobs_lock:
        _file_jobs[job["job_id"]] = job
        if len(_file_jobs) > FILE_JOB_LIMIT:
            # Find oldest completed jobs to remove (O(n) instead of O(n log n))
            to_remove = len(_file_jobs) - FILE_JOB_LIMIT
            removable = [
                (job.get("created_at", ""), job_id)
                for job_id, job in _file_jobs.items()
                if job.get("status") not in {"queued", "running"}
            ]
            # Sort only the removable jobs, take the oldest to_remove items
            removable.sort(key=lambda x: x[0])
            for _, job_id in removable[:to_remove]:
                _file_jobs.pop(job_id, None)
    return _public_file_job(job)


def _get_file_job(job_id: str) -> dict | None:
    with _file_jobs_lock:
        job = _file_jobs.get(job_id)
        return dict(job) if job else None


def _list_file_jobs(current_user: User, website_id: int | None = None) -> list[dict]:
    with _file_jobs_lock:
        jobs = [dict(job) for job in _file_jobs.values()]
    visible = []
    for job in jobs:
        if job.get("user_id") != current_user.id and not is_admin_role(current_user.role):
            continue
        if website_id is not None and job.get("website_id") != website_id:
            continue
        # Finished work is already reported by the completion notice and the
        # refreshed file listing, so only work still in flight or failed is
        # worth a card. Otherwise every archive ever made piles up in the UI.
        if job.get("status") == "done":
            continue
        visible.append(_public_file_job(job))
    return sorted(visible, key=lambda item: item.get("created_at", ""), reverse=True)[:10]


def _public_backup_job(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "website_id": job.get("website_id"),
        "user_id": job.get("target_user_id"),
        "target_id": job.get("target_id"),
        "backup_file": job.get("backup_file", ""),
        "remote_file": job.get("remote_file", ""),
        "target": job.get("target", ""),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        # A restore reports each account it was asked for.
        "results": [dict(row) for row in job.get("results") or []],
    }


def _set_backup_job(job_id: str, **updates) -> None:
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        if not job:
            return
        job.update(updates)


def _remember_backup_job(job: dict) -> dict:
    with _backup_jobs_lock:
        _backup_jobs[job["job_id"]] = job
        if len(_backup_jobs) > BACKUP_JOB_LIMIT:
            to_remove = len(_backup_jobs) - BACKUP_JOB_LIMIT
            removable = [
                (job.get("created_at", ""), job_id)
                for job_id, job in _backup_jobs.items()
                if job.get("status") not in {"queued", "running"}
            ]
            removable.sort(key=lambda x: x[0])
            for _, job_id in removable[:to_remove]:
                _backup_jobs.pop(job_id, None)
    return _public_backup_job(job)


def _get_backup_job(job_id: str) -> dict | None:
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        return dict(job) if job else None


def _list_backup_jobs(current_user: User) -> list[dict]:
    with _backup_jobs_lock:
        jobs = [dict(job) for job in _backup_jobs.values()]
    visible = []
    for job in jobs:
        if job.get("request_user_id") != current_user.id and not is_admin_role(current_user.role):
            continue
        visible.append(_public_backup_job(job))
    return sorted(visible, key=lambda item: item.get("created_at", ""), reverse=True)[:12]


def _queue_backup_job(current_user: User, kind: str, message: str, **extra) -> dict:
    job = {
        "job_id": uuid.uuid4().hex,
        "kind": kind,
        "status": "queued",
        "request_user_id": current_user.id,
        "message": message,
        "error": "",
        "backup_file": "",
        "remote_file": "",
        "target": "",
        "created_at": _now_iso(),
        "started_at": "",
        "finished_at": "",
        **extra,
    }
    return _remember_backup_job(job)



def _can_access_website(db: Session, user: User, website: Website) -> bool:
    if website.owner_id == user.id or is_admin_role(user.role):
        return True
    if normalize_role(user.role) == Role.reseller:
        owner = db.query(User).filter(User.id == website.owner_id).first()
        if owner and owner.reseller_id == user.id:
            return True
    return False


def _reload_file_target(db: Session, user: User, target_key: str):
    """Re-resolve the tree in the worker's own session.

    The job only carries a key, so the ownership check runs again here rather
    than trusting whatever the request thread resolved.
    """
    kind, _, raw_id = target_key.partition(":")
    if kind == "app":
        app = db.query(SiteApp).filter(SiteApp.id == int(raw_id)).first()
        if not app:
            raise ValueError("Application not found")
        if app.owner_id != user.id and not is_admin_role(user.role):
            raise ValueError("Access denied")
        return site_apps.file_target(app)
    website = db.query(Website).filter(Website.id == int(raw_id)).first()
    if not website:
        raise ValueError("Website not found")
    if not _can_access_website(db, user, website):
        raise ValueError("Access denied")
    return website


def _run_extract_job(job_id: str, user_id: int, target_key: str, archive_path: str, destination_path: str, allow_executable: bool) -> None:
    _set_file_job(job_id, status="running", started_at=_now_iso(), message="Extracting archive")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.is_active:
            raise ValueError("User not found")
        website = _reload_file_target(db, user, target_key)
        target = file_manager.extract_archive(
            website,
            archive_path,
            destination_path,
            allow_executable,
            quota_check=_quota_check_for_website(db, website),
        )
        log_action(db, user.id, "extract_archive", website.domain, archive_path)
        _set_file_job(
            job_id,
            status="done",
            target=target,
            message="Extraction completed",
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("File extract job failed: job_id=%s target=%s", job_id, target_key)
        _set_file_job(
            job_id,
            status="error",
            error=str(exc),
            message="Extraction failed",
            finished_at=_now_iso(),
        )
    finally:
        db.close()


def _queue_extract_job(user: User, website: Website, archive_path: str, destination_path: str, allow_executable: bool) -> dict:
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "kind": "extract_archive",
        "status": "queued",
        "user_id": user.id,
        "website_id": getattr(website, "id", None),
        "target_key": file_target_key(website),
        "archive_path": archive_path,
        "destination_path": destination_path,
        "target": "",
        "message": "Extraction queued",
        "error": "",
        "created_at": _now_iso(),
        "started_at": "",
        "finished_at": "",
    }
    public_job = _remember_file_job(job)
    _file_job_executor.submit(
        _run_extract_job,
        job_id,
        user.id,
        file_target_key(website),
        archive_path,
        destination_path,
        allow_executable,
    )
    return public_job


def get_owned_website(db: Session, current_user: User, website_id: int) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _can_access_website(db, current_user, website):
        ensure_role(current_user.role, Role.admin)
    return website


def get_owned_app(db: Session, current_user: User, app_id: int) -> SiteApp:
    app = db.query(SiteApp).filter(SiteApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    return app


def get_file_target(db: Session, current_user: User, website_id=None, app_id=None):
    """The tree a file operation runs in: a website root or an application root.

    file_manager only reads root_path and linux_user off this, so an app can be
    passed straight through instead of teaching every call site about two types.
    """
    if app_id:
        addons.require(addons.APPLICATION)
        target = site_apps.file_target(get_owned_app(db, current_user, app_id))
        # Self-heal: an app created before the directory was made at creation
        # time, or by a release that left it unreadable by the panel user.
        if not os.access(target.root_path, os.R_OK):
            site_apps.ensure_directory(get_owned_app(db, current_user, app_id))
        return target
    if website_id:
        return get_owned_website(db, current_user, website_id)
    raise HTTPException(status_code=400, detail="Pick a website or an application to browse")


def get_backup_user(db: Session, current_user: User, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id != current_user.id and not (normalize_role(current_user.role) == Role.reseller and user.reseller_id == current_user.id):
        ensure_role(current_user.role, Role.admin)
    return user


def _is_manager_role(role: str) -> bool:
    return normalize_role(role) in {Role.admin, Role.reseller}


def _get_accessible_sftp_target(db: Session, current_user: User, target_id: int) -> SftpBackupTarget:
    target = db.query(SftpBackupTarget).filter(SftpBackupTarget.id == target_id, SftpBackupTarget.is_active == True).first()  # noqa: E712
    if not target:
        raise HTTPException(status_code=404, detail="Backup destination not found")
    if is_admin_role(current_user.role):
        return target
    if target.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied to this backup destination")
    return target


def upload_archive_to_target(
    db: Session,
    target_id: int,
    archive: str,
    *,
    account_username: str = "",
    use_hierarchy: bool = True,
) -> tuple[str, str]:
    target = db.query(SftpBackupTarget).filter(SftpBackupTarget.id == target_id).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Backup destination not found")
    acct = account_username or backup.username_from_archive_name(archive)
    subpath = None
    if use_hierarchy:
        host_part = backup.get_server_hostname()
        subpath = f"{host_part}/{acct}" if acct else host_part

    if (target.kind or "sftp") == "s3":
        try:
            result = backup_s3.upload(target, archive, subpath=subpath)
            return target.name, result["remote_file"]
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        result = backup.upload_to_sftp(
            archive,
            subpath=subpath,
            host=target.host,
            port=target.port,
            username=target.username,
            password=decrypt(target.password) if target.password else None,
            private_key=decrypt(target.private_key) if target.private_key else None,
            remote_path=target.remote_path,
            expected_host_key_type=target.host_key_type,
            expected_host_key_fingerprint=target.host_key_fingerprint,
        )
    except backup.SftpHostKeyMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not target.host_key_fingerprint and result.get("host_key_fingerprint"):
        target.host_key_type = result["host_key_type"]
        target.host_key_fingerprint = result["host_key_fingerprint"]
        db.commit()
    return target.name, result["remote_file"]


def _run_site_backup_job(
    job_id: str,
    request_user_id: int,
    website_id: int,
    backup_mode: str = "full",
    passphrase: str | None = None,
) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Creating website backup")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == request_user_id).first()
        if not user or not user.is_active:
            raise ValueError("User not found")
        website = get_owned_website(db, user, website_id)
        db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
        archive = backup.create_backup(
            website,
            db_item.db_name if db_item else None,
            backup_mode=backup_mode,
            passphrase=passphrase,
        )
        log_action(db, user.id, "backup", website.domain, f"{archive} ({backup_mode})")
        _set_backup_job(
            job_id,
            status="done",
            backup_file=archive,
            message="Website backup completed",
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Website backup job failed: job_id=%s website_id=%s", job_id, website_id)
        _set_backup_job(job_id, status="error", error=str(exc), message="Website backup failed", finished_at=_now_iso())
    finally:
        db.close()


def _run_user_backup_job(
    job_id: str,
    request_user_id: int,
    target_user_id: int,
    target_id: int | None,
    backup_mode: str = "full",
    passphrase: str | None = None,
) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Creating full user backup")
    db = SessionLocal()
    try:
        request_user = db.query(User).filter(User.id == request_user_id).first()
        if not request_user or not request_user.is_active:
            raise ValueError("User not found")
        user = get_backup_user(db, request_user, target_user_id)
        use_hierarchy = _is_manager_role(request_user.role)
        archive = backup.create_user_backup(
            user,
            db,
            use_hierarchy=use_hierarchy,
            backup_mode=backup_mode,
            target_id=target_id,
            passphrase=passphrase,
        )
        remote_file = ""
        target_name = ""
        if target_id:
            _get_accessible_sftp_target(db, request_user, target_id)
            target_name, remote_file = upload_archive_to_target(
                db,
                target_id,
                archive,
                account_username=user.username,
                use_hierarchy=use_hierarchy,
            )
        detail = f"{archive}" + (f" -> {target_name}:{remote_file}" if remote_file else "")
        log_action(db, request_user.id, "backup_user", user.username, detail)
        if remote_file:
            backup.discard_local_copy(archive)
        _set_backup_job(
            job_id,
            status="done",
            backup_file="" if remote_file else archive,
            remote_file=remote_file,
            target=target_name,
            message="Full user backup completed",
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Full user backup job failed: job_id=%s user_id=%s", job_id, target_user_id)
        _set_backup_job(job_id, status="error", error=str(exc), message="Full user backup failed", finished_at=_now_iso())
    finally:
        db.close()


def _run_sftp_backup_job(
    job_id: str,
    request_user_id: int,
    website_id: int,
    target_id: int,
    backup_mode: str = "full",
) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Creating and uploading remote backup")
    db = SessionLocal()
    try:
        request_user = db.query(User).filter(User.id == request_user_id).first()
        if not request_user or not request_user.is_active:
            raise ValueError("User not found")
        _get_accessible_sftp_target(db, request_user, target_id)
        website = get_owned_website(db, request_user, website_id)
        db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
        archive = backup.create_backup(
            website,
            db_item.db_name if db_item else None,
            backup_mode=backup_mode,
            target_id=target_id,
        )
        owner_username = getattr(getattr(website, "owner", None), "username", "") or request_user.username
        use_hierarchy = _is_manager_role(request_user.role)
        target_name, remote_file = upload_archive_to_target(
            db,
            target_id,
            archive,
            account_username=owner_username,
            use_hierarchy=use_hierarchy,
        )
        log_action(db, request_user.id, "backup_sftp", website.domain, f"{target_name}:{remote_file}")
        backup.discard_local_copy(archive)
        _set_backup_job(
            job_id,
            status="done",
            backup_file="",
            remote_file=remote_file,
            target=target_name,
            message="Remote backup completed",
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("SFTP backup job failed: job_id=%s website_id=%s", job_id, website_id)
        _set_backup_job(job_id, status="error", error=str(exc), message="SFTP backup failed", finished_at=_now_iso())
    finally:
        db.close()


def _queue_site_backup(
    current_user: User,
    website: Website,
    backup_mode: str = "full",
    passphrase: str | None = None,
) -> dict:
    job = _queue_backup_job(current_user, "site_backup", "Website backup queued", website_id=website.id)
    _backup_job_executor.submit(_run_site_backup_job, job["job_id"], current_user.id, website.id, backup_mode, passphrase)
    return job


def _queue_user_backup(
    current_user: User,
    user: User,
    target_id: int | None,
    backup_mode: str = "full",
    passphrase: str | None = None,
) -> dict:
    job = _queue_backup_job(
        current_user,
        "user_backup",
        "Full user backup queued",
        target_user_id=user.id,
        target_id=target_id,
    )
    _backup_job_executor.submit(
        _run_user_backup_job,
        job["job_id"],
        current_user.id,
        user.id,
        target_id,
        backup_mode,
        passphrase,
    )
    return job


def _queue_sftp_backup(current_user: User, website: Website, target_id: int, backup_mode: str = "full") -> dict:
    job = _queue_backup_job(
        current_user,
        "sftp_backup",
        "SFTP backup queued",
        website_id=website.id,
        target_id=target_id,
    )
    _backup_job_executor.submit(_run_sftp_backup_job, job["job_id"], current_user.id, website.id, target_id, backup_mode)
    return job


def _save_user_restore_upload(file: UploadFile) -> dict:
    target = ""
    try:
        target = backup.save_uploaded_user_backup(file.filename or "user-backup.tar.gz", file.file)
        manifest = backup.read_backup_manifest(target)
        if manifest.get("kind") not in backup.RESTORABLE_BACKUP_KINDS:
            raise ValueError("This is not a full user backup")
    except (ValueError, FileNotFoundError, tarfile.TarError, OSError, EOFError) as exc:
        # A file that is not a gzip'd tar at all used to raise ReadError past
        # this, answer 500 and stay in the restore folder looking like a backup.
        if target:
            try:
                backup.delete_user_backup(target)
            except Exception:
                pass
        detail = str(exc) if isinstance(exc, ValueError) else "This is not a full user backup"
        raise HTTPException(status_code=400, detail=detail) from exc
    path = backup.user_backup_path(target)
    return {
        "backup_file": target,
        "filename": path.name,
        "username": (manifest.get("user") or {}).get("username"),
        "generated_at": manifest.get("generated_at"),
        "websites": len(manifest.get("websites") or []),
        "size": path.stat().st_size,
        "valid": True,
        "error": "",
    }


def _quota_check_for_website(db: Session, website: Website):
    owner = website.owner

    def check(incoming_bytes: int, replaced_bytes: int = 0) -> None:
        storage_quota.enforce_user_storage_quota(
            db,
            owner,
            incoming_bytes=incoming_bytes,
            replaced_bytes=replaced_bytes,
        )

    return check


@router.post("/backup")
def create_backup(payload: BackupCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    if payload.passphrase and len(payload.passphrase.strip()) < backup.MIN_BACKUP_PASSPHRASE_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Encryption passphrase must be at least {backup.MIN_BACKUP_PASSPHRASE_LEN} characters",
        )
    return _queue_site_backup(current_user, website, backup_mode=payload.backup_mode, passphrase=payload.passphrase)


@router.get("/backup-jobs")
def list_backup_jobs(current_user: User = Depends(get_current_user)):
    return {"jobs": _list_backup_jobs(current_user)}


@router.get("/backup-jobs/{job_id}")
@router.get("/restore/jobs/{job_id}")
def get_backup_job(job_id: str, current_user: User = Depends(get_current_user)):
    job = _get_backup_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Backup job not found")
    if job.get("request_user_id") != current_user.id and not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="Access denied")
    return _public_backup_job(job)


@router.post("/restore")
def restore_backup(payload: RestoreBackup, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    mode = getattr(payload, "restore_mode", "full") or "full"
    try:
        path = backup.restore_backup(
            website,
            payload.backup_file,
            db=db,
            restore_mode=mode,
            passphrase=payload.passphrase,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if website.linux_user and mode in {"full", "files_only"}:
        runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
        site_users.ensure_site_runtime(website.domain, website.root_path, runtime_php_version, website.linux_user)
        wordpress.fix_permissions(website.root_path, website.linux_user)
    log_action(db, current_user.id, "restore", website.domain, f"{payload.backup_file} ({mode})")
    return {"restored_to": path, "restore_mode": mode}


@router.get("/backups/{website_id}")
def list_backups(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    return {"items": backup.list_backups(website.domain), "server_hostname": backup.get_server_hostname()}


@router.get("/backups/{website_id}/download")
def download_backup(website_id: int, backup_file: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        path = backup.backup_path(website.domain, backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    return FileResponse(str(path), filename=path.name, media_type="application/gzip")


@router.delete("/backups/{website_id}")
def delete_backup(website_id: int, backup_file: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        deleted = backup.delete_backup(website.domain, backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found")
    log_action(db, current_user.id, "delete_backup", website.domain, deleted)
    return {"deleted": deleted}


@router.post("/backups/{website_id}/upload")
def upload_backup(website_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        target = backup.save_uploaded_backup(website.domain, file.filename or "backup.tar.gz", file.file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_backup", website.domain, target)
    return {"backup_file": target}


@router.get("/user-restore-backups")
def list_user_restore_backups(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"directory": backup.user_restore_dir(), "items": backup.list_user_restore_backups()}


@router.post("/user-restore-backups/upload")
def upload_user_restore_backups(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    if not files:
        raise HTTPException(status_code=400, detail="No backup files uploaded")
    items = [_save_user_restore_upload(file) for file in files]
    users = ", ".join(item.get("username") or item.get("filename") or "user" for item in items)
    log_action(db, current_user.id, "upload_user_restore_backups", "restore_folder", users)
    return {"directory": backup.user_restore_dir(), "items": items}


@router.delete("/user-restore-backups")
def delete_user_restore_backup(backup_file: str, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        deleted = backup.delete_user_restore_backup(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    log_action(db, current_user.id, "delete_user_restore_backup", "restore_folder", deleted, request=request)
    return {"deleted": deleted}


def _allowed_backup_usernames(db: Session, current_user: User) -> set[str] | None:
    if is_admin_role(current_user.role):
        return None
    if normalize_role(current_user.role) == Role.reseller:
        sub_names = {
            u.username
            for u in db.query(User).filter(User.reseller_id == current_user.id).all()
        }
        sub_names.add(current_user.username)
        return sub_names
    return {current_user.username}


@router.get("/user-backups")
def list_all_user_backups(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    allowed = _allowed_backup_usernames(db, current_user)
    single_user = current_user.username if normalize_role(current_user.role) == Role.end_user else None
    return {
        "items": backup.list_all_user_backups(username=single_user, allowed_usernames=allowed),
        "server_hostname": backup.get_server_hostname(),
    }


@router.get("/user-backups-download")
@router.get("/user-backups/download")
def download_user_backup(backup_file: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        path = backup.user_backup_path(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    allowed = _allowed_backup_usernames(db, current_user)
    if allowed is not None:
        info = backup.describe_user_backup(str(path))
        if info.get("username") not in allowed:
            raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(str(path), filename=path.name, media_type="application/gzip")


@router.get("/user-backups/{user_id}")
def list_user_backups(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = get_backup_user(db, current_user, user_id)
    items = backup.list_user_backups(user.username)
    if is_admin_role(current_user.role):
        items.extend(item for item in backup.list_uploaded_user_backups(user.username) if item not in items)
    return {"items": items}


@router.post("/user-backup")
@router.post("/user-backups")
def create_user_backup(payload: UserBackupCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = get_backup_user(db, current_user, payload.user_id)
    if payload.passphrase and len(payload.passphrase.strip()) < backup.MIN_BACKUP_PASSPHRASE_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Encryption passphrase must be at least {backup.MIN_BACKUP_PASSPHRASE_LEN} characters",
        )
    if payload.target_id:
        _get_accessible_sftp_target(db, current_user, payload.target_id)
    log_action(db, current_user.id, "queue_backup_user", user.username, payload.backup_mode, request=request)
    return _queue_user_backup(
        current_user,
        user,
        payload.target_id,
        backup_mode=payload.backup_mode,
        passphrase=payload.passphrase,
    )


@router.post("/user-backups/upload")
def upload_user_backup(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    item = _save_user_restore_upload(file)
    log_action(db, current_user.id, "upload_user_backup", item.get("username") or "user", item["backup_file"])
    return {"backup_file": item["backup_file"], "username": item.get("username")}


@router.post("/user-restore")
@router.post("/user-backups/restore")
def restore_user_backup(payload: UserRestoreBackup, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    mode = getattr(payload, "restore_mode", "full") or "full"
    try:
        result = backup.restore_user_backup(payload.backup_file, db, restore_mode=mode, passphrase=payload.passphrase)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "restore_user", result.get("username", "user"), f"{payload.backup_file} ({mode})", request=request)
    return result


class BackupVerifyPayload(BaseModel):
    backup_file: Optional[str] = None
    website_id: Optional[int] = None
    passphrase: Optional[str] = None


@router.post("/backups/verify")
def verify_backup_archive(
    payload: Optional[BackupVerifyPayload] = Body(default=None),
    backup_file: Optional[str] = Query(default=None),
    website_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_file = (payload.backup_file if payload and payload.backup_file else backup_file) or ""
    target_site_id = payload.website_id if (payload and payload.website_id is not None) else website_id
    secret = payload.passphrase if payload else None
    if not target_file:
        raise HTTPException(status_code=400, detail="backup_file is required")
    if target_site_id is not None:
        website = get_owned_website(db, current_user, target_site_id)
        try:
            path = backup.backup_path(website.domain, target_file)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Backup not found") from exc
    else:
        try:
            path = backup.user_backup_path(target_file)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Backup not found") from exc
        allowed = _allowed_backup_usernames(db, current_user)
        if allowed is not None:
            info = backup.describe_user_backup(str(path))
            if info.get("username") not in allowed:
                raise HTTPException(status_code=403, detail="Access denied")
    try:
        return backup.verify_backup_integrity(path, passphrase=secret)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc


class BackupEncryptionPayload(BaseModel):
    backup_file: str
    passphrase: str
    website_id: Optional[int] = None


@router.post("/backups/encrypt")
def encrypt_backup_endpoint(
    payload: BackupEncryptionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.website_id is not None:
        website = get_owned_website(db, current_user, payload.website_id)
        path = backup.backup_path(website.domain, payload.backup_file)
    else:
        ensure_role(current_user.role, Role.admin)
        path = backup.user_backup_path(payload.backup_file)
    try:
        enc_path = backup.encrypt_backup_archive(path, payload.passphrase, replace_original=True)
        return backup.verify_backup_integrity(enc_path, passphrase=payload.passphrase)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backups/decrypt")
def decrypt_backup_endpoint(
    payload: BackupEncryptionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.website_id is not None:
        website = get_owned_website(db, current_user, payload.website_id)
        path = backup.backup_path(website.domain, payload.backup_file)
    else:
        ensure_role(current_user.role, Role.admin)
        path = backup.user_backup_path(payload.backup_file)
    try:
        dec_path = backup.decrypt_backup_archive(path, payload.passphrase, replace_original=True)
        return backup.verify_backup_integrity(dec_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/user-backups")
def delete_user_backup(backup_file: str, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        path = backup.user_backup_path(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    allowed = _allowed_backup_usernames(db, current_user)
    if allowed is not None:
        info = backup.describe_user_backup(str(path))
        if info.get("username") not in allowed:
            raise HTTPException(status_code=403, detail="Access denied")
    try:
        deleted = backup.delete_user_backup(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    log_action(db, current_user.id, "delete_user_backup", "user", deleted, request=request)
    return {"deleted": deleted}


@router.get("/backup-schedules", response_model=list[BackupScheduleOut])
def list_backup_schedules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return db.query(BackupSchedule).order_by(BackupSchedule.id.desc()).all()


@router.post("/backup-schedules", response_model=BackupScheduleOut)
def create_backup_schedule(payload: BackupScheduleCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    user_ids = [] if payload.all_users else (payload.user_ids or ([payload.user_id] if payload.user_id else []))
    user_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
    users = []
    if not payload.all_users:
        if not user_ids:
            raise HTTPException(status_code=400, detail="Select at least one user")
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        found_ids = {user.id for user in users}
        missing_ids = [str(user_id) for user_id in user_ids if user_id not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail=f"User not found: {', '.join(missing_ids)}")
    if payload.target_id and not db.query(SftpBackupTarget).filter(SftpBackupTarget.id == payload.target_id, SftpBackupTarget.is_active == True).first():  # noqa: E712
        raise HTTPException(status_code=404, detail="SFTP target not found")
    item = BackupSchedule(
        user_id=user_ids[0] if user_ids else None,
        user_ids=json.dumps(user_ids),
        all_users=payload.all_users,
        target_id=payload.target_id,
        schedule=payload.schedule,
        name_suffix=payload.name_suffix,
        backup_mode=payload.backup_mode,
        full_every_n=payload.full_every_n,
        retention=payload.retention,
        is_active=payload.is_active,
        last_status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    target = "all_users" if payload.all_users else ",".join(user.username for user in users)
    log_action(db, current_user.id, "create_backup_schedule", target, payload.schedule, request=request)
    return item


@router.post("/backup-schedules/{schedule_id}/run")
def run_backup_schedule_now(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    schedule = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    schedule.last_status = "running"
    schedule.last_message = f"Started by {current_user.username}"
    db.commit()

    _backup_job_executor.submit(_run_schedule_now_job, schedule_id)
    log_action(db, current_user.id, "run_backup_schedule", str(schedule_id), request=request)
    return {"ok": True, "schedule_id": schedule_id,
            "detail": "Running now. Refresh to see the result on this schedule."}


def _run_schedule_now_job(schedule_id: int) -> None:
    from app.services import backup_scheduler

    db = SessionLocal()
    try:
        schedule = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
        if schedule:
            backup_scheduler.run_one(db, schedule)
    except Exception as exc:  # noqa: BLE001
        try:
            schedule = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
            if schedule:
                schedule.last_status = "error"
                schedule.last_message = str(exc)[:4000]
                db.commit()
        except Exception:  # pragma: no cover
            pass
    finally:
        db.close()


@router.put("/backup-schedules/{schedule_id}", response_model=BackupScheduleOut)
def update_backup_schedule(
    schedule_id: int,
    payload: BackupScheduleCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    item = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    user_ids = [] if payload.all_users else (payload.user_ids or ([payload.user_id] if payload.user_id else []))
    user_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
    users = []
    if not payload.all_users:
        if not user_ids:
            raise HTTPException(status_code=400, detail="Select at least one user")
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        found_ids = {user.id for user in users}
        missing_ids = [str(user_id) for user_id in user_ids if user_id not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail=f"User not found: {', '.join(missing_ids)}")
    if payload.target_id and not db.query(SftpBackupTarget).filter(SftpBackupTarget.id == payload.target_id, SftpBackupTarget.is_active == True).first():  # noqa: E712
        raise HTTPException(status_code=404, detail="Backup destination not found")
    item.user_id = user_ids[0] if user_ids else None
    item.user_ids = json.dumps(user_ids)
    item.all_users = payload.all_users
    item.target_id = payload.target_id
    item.schedule = payload.schedule
    item.name_suffix = payload.name_suffix
    item.backup_mode = payload.backup_mode
    item.full_every_n = payload.full_every_n
    item.retention = payload.retention
    item.is_active = payload.is_active
    db.commit()
    db.refresh(item)
    target = "all_users" if payload.all_users else ",".join(user.username for user in users)
    log_action(db, current_user.id, "update_backup_schedule", target, payload.schedule, request=request)
    return item


@router.patch("/backup-schedules/{schedule_id}/toggle", response_model=BackupScheduleOut)
def toggle_backup_schedule(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    item = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    item.is_active = not bool(item.is_active)
    db.commit()
    db.refresh(item)
    log_action(db, current_user.id, "toggle_backup_schedule", str(schedule_id), f"is_active={item.is_active}", request=request)
    return item


@router.delete("/backup-schedules/{schedule_id}")
def delete_backup_schedule(schedule_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    item = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    db.delete(item)
    db.commit()
    log_action(db, current_user.id, "delete_backup_schedule", str(schedule_id), request=request)
    return {"ok": True}


@router.get("/sftp-targets", response_model=list[SftpBackupTargetOut])
def list_sftp_targets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(SftpBackupTarget)
    if is_admin_role(current_user.role):
        query = query.filter((SftpBackupTarget.owner_id == None) | (SftpBackupTarget.owner_id == current_user.id))  # noqa: E711
    else:
        query = query.filter(SftpBackupTarget.owner_id == current_user.id)
    return query.order_by(SftpBackupTarget.id.desc()).all()


@router.post("/sftp-targets", response_model=SftpBackupTargetOut)
def create_sftp_target(
    payload: SftpBackupTargetCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if db.query(SftpBackupTarget).filter(SftpBackupTarget.name == payload.name).first():
        raise HTTPException(status_code=409, detail="A target with that name already exists")

    target = SftpBackupTarget(
        owner_id=None if is_admin_role(current_user.role) else current_user.id,
        name=payload.name,
        kind=payload.kind,
        is_active=True,
        host=payload.host or "",
        port=payload.port,
        username=payload.username or "",
        password=encrypt(payload.password) if payload.password else None,
        private_key=encrypt(payload.private_key) if payload.private_key else None,
        remote_path=payload.remote_path,
        endpoint=payload.endpoint,
        region=payload.region,
        bucket=payload.bucket,
        access_key=payload.access_key,
        secret_key=encrypt(payload.secret_key) if payload.secret_key else None,
        prefix=payload.prefix,
        secure=payload.secure,
    )

    if payload.kind == "s3":
        try:
            backup_s3.check(target)
        except backup_s3.S3Error as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(target)
    db.commit()
    db.refresh(target)
    log_action(db, current_user.id, "create_backup_target", f"{target.kind}:{target.name}", request=request)
    return target


@router.post("/restore/upload")
def restore_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    source: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    try:
        spec = RestoreSource.model_validate_json(source).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="The source is not valid") from exc
    if not files:
        raise HTTPException(status_code=400, detail="No backup files uploaded")
    uploaded = []
    for file in files:
        item = _save_user_restore_upload(file)
        if spec["kind"] == "local":
            uploaded.append(item["filename"])
            continue
        try:
            uploaded.append(restore_sources.push(spec, item["backup_file"], db))
        except backup.SftpHostKeyMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except restore_sources.RestoreSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            Path(item["backup_file"]).unlink(missing_ok=True)
    log_action(db, current_user.id, "upload_restore_backups", restore_sources.describe(spec, db),
               ", ".join(uploaded), request=request)
    return {"uploaded": uploaded}


@router.post("/restore/list")
def restore_list(payload: RestoreListRequest, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        result = restore_sources.list_source(payload.source.model_dump(), db)
    except backup.SftpHostKeyMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except restore_sources.RestoreSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("restore listing failed")
        raise HTTPException(status_code=502, detail=f"Could not list the source: {type(exc).__name__}: {exc}"[:500]) from exc
    names = {row["username"] for row in result["items"] if row["username"]}
    existing = {name for (name,) in db.query(User.username).filter(User.username.in_(names))} if names else set()
    result["existing"] = sorted(existing)
    return result


_restore_run_lock = threading.Lock()


def _set_restore_result(job_id: str, index: int, **updates) -> None:
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        if job and index < len(job.get("results") or []):
            job["results"][index].update(updates)


def _run_restore_job(job_id: str, request_user_id: int, source: dict, items: list[dict]) -> None:
    with _restore_run_lock:
        _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Restoring")
        db = SessionLocal()
        restored = 0
        try:
            for index, item in enumerate(items):
                cleanup_paths: list[str] = []
                try:
                    _set_restore_result(job_id, index, status="fetching" if source["kind"] != "local" else "restoring")
                    path, chain_paths, cleanup_paths = restore_sources.fetch_chain(
                        source, item["key"], item.get("size") or 0, db
                    )
                    _set_restore_result(job_id, index, status="restoring")
                    outcome = backup.restore_user_backup(
                        path,
                        db,
                        chain_files=chain_paths if chain_paths else None,
                    )
                    username = outcome.get("username", "") if isinstance(outcome, dict) else ""
                    sites_count = len(outcome.get("websites") or []) if isinstance(outcome, dict) else 0
                    gen_pwd = outcome.get("generated_password", "") if isinstance(outcome, dict) else ""
                    detail_msg = outcome.get("message", "") if isinstance(outcome, dict) else ""
                    if not detail_msg and isinstance(outcome, dict):
                        detail_msg = f"{sites_count} website(s) restored"
                        if gen_pwd:
                            detail_msg += f" · New password: {gen_pwd}"
                    _set_restore_result(
                        job_id,
                        index,
                        status="done",
                        username=username or item.get("username", ""),
                        detail=detail_msg,
                        generated_password=gen_pwd,
                        created_user=bool(outcome.get("created_user")) if isinstance(outcome, dict) else False,
                    )
                    restored += 1
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    logger.warning("restore %s failed: %s", item.get("key"), exc)
                    _set_restore_result(job_id, index, status="failed", detail=str(exc)[:500])
                finally:
                    for cp in cleanup_paths:
                        Path(cp).unlink(missing_ok=True)
            total = len(items)
            if restored == total:
                _set_backup_job(job_id, status="done", message=f"Restored {restored} of {total}",
                                finished_at=_now_iso())
            else:
                _set_backup_job(job_id, status="error", message=f"Restored {restored} of {total}",
                                error=f"Restored {restored} of {total}", finished_at=_now_iso())
            log_action(db, request_user_id, "restore_users", restore_sources.describe(source, db),
                       f"{restored}/{total}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("restore job %s failed", job_id)
            _set_backup_job(job_id, status="error", error=str(exc)[:500], finished_at=_now_iso())
        finally:
            db.close()


@router.post("/restore/run")
def restore_run(payload: RestoreRunRequest, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    source = payload.source.model_dump()
    if source["kind"] == "remote" and source["protocol"] == "sftp" and not source["host_key"]:
        raise HTTPException(status_code=400, detail="List the server first, so its key can be checked")
    results = []
    for item in payload.items:
        try:
            name = restore_sources.check_key(source, item.key)
        except restore_sources.RestoreSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        results.append({"name": name, "username": item.username, "status": "queued", "detail": ""})
    job = _queue_backup_job(current_user, "user_restore", "Restore queued", results=results)
    _backup_job_executor.submit(_run_restore_job, job["job_id"], current_user.id, source,
                                [item.model_dump() for item in payload.items])
    log_action(db, current_user.id, "queue_restore_users", restore_sources.describe(source, db),
               f"{len(results)} backup(s)", request=request)
    return job


@router.delete("/sftp-targets/{target_id}")
def delete_sftp_target(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = _get_accessible_sftp_target(db, current_user, target_id)
    name = target.name
    db.delete(target)
    db.commit()
    log_action(db, current_user.id, "delete_sftp_target", name, request=request)
    return {"ok": True}


@router.post("/backup-sftp")
def create_sftp_backup(
    payload: SftpBackupRun,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = get_owned_website(db, current_user, payload.website_id)
    target = _get_accessible_sftp_target(db, current_user, payload.target_id)
    log_action(db, current_user.id, "queue_backup_sftp", website.domain, f"{target.name} ({payload.backup_mode})", request=request)
    return _queue_sftp_backup(current_user, website, target.id, backup_mode=payload.backup_mode)


@router.get("/php-config")
def get_php_config(php_version: str = Query(default="8.4"), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        return php.read_php_ini(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/php-tune")
def get_php_tune(php_version: str = Query(default="8.4"), current_user: User = Depends(get_current_user)):
    """What this machine can carry, and what PHP is set to right now.

    Read-only: the numbers and the reasoning, so an administrator can see what
    would change before anything does.
    """
    ensure_role(current_user.role, Role.admin)
    from app.services import php_tune

    try:
        return php_tune.plan(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/php-tune")
def apply_php_tune(
    payload: PhpConfigRestore,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    from app.services import php_tune

    try:
        result = php_tune.apply(payload.php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # One button, both halves: the settings PHP reads and the pool sizes FPM
    # runs with, which are otherwise only recalculated when a site is touched.
    try:
        result["pools"] = php_tune.retune_pools().get("output", "")
    except RuntimeError as exc:
        result["pools"] = ""
        result["pools_error"] = str(exc)[-500:]
    log_action(db, current_user.id, "php_tune", payload.php_version, request=request)
    return result


@router.post("/php-opcache")
def toggle_php_opcache(
    payload: PhpOpcacheToggle,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Turn OPcache on or off for one PHP version.

    Kept out of the tuning file so that running Auto tune afterwards does not
    switch it back on behind the administrator's back.
    """
    ensure_role(current_user.role, Role.admin)
    from app.services import php_tune

    try:
        result = php_tune.set_opcache(payload.php_version, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "php_opcache", f"{payload.php_version}={int(payload.enabled)}", request=request)
    return result


@router.post("/php-tune/pools")
def retune_php_pools(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recalculate every site's FPM pool against the machine as it is now."""
    ensure_role(current_user.role, Role.admin)
    from app.services import php_tune

    try:
        result = php_tune.retune_pools()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "php_retune_pools", "php-fpm", request=request)
    return result


@router.post("/php-config")
def update_php_config(payload: PhpConfigUpdate, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        target = php.update_php_ini(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": target}


@router.post("/php-config/defaults")
def restore_php_config_defaults(payload: PhpConfigRestore, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        target = php.restore_default_php_ini(payload.php_version)
        values = php.default_php_config(payload.php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": target, "values": values}


@router.get("/php-versions")
def get_php_versions(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return {
        "installed": php.list_installed_php(),
        "supported": list(php.SUPPORTED_PHP_VERSIONS),
    }


@router.post("/php-versions/{php_version}/install")
def install_php_version(php_version: str, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        result = php.install_php(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


@router.post("/php-versions/{php_version}/uninstall")
@router.delete("/php-versions/{php_version}")
def uninstall_php_version(
    php_version: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    if php_version == "8.4":
        raise HTTPException(status_code=400, detail="Cannot uninstall the default PHP version (8.4).")
    sites_using = db.query(Website).filter(Website.php_version == php_version).count()
    if sites_using > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot uninstall PHP {php_version}: {sites_using} website(s) are currently assigned to it. Please switch those websites to another PHP version first.",
        )
    installed = php.list_installed_php()
    if len(installed) <= 1 and php_version in installed:
        raise HTTPException(status_code=400, detail="Cannot uninstall the last remaining PHP version on the server.")
    try:
        result = php.uninstall_php(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "php_version_uninstall", php_version, request=request)
    return result


@router.get("/php-extensions")
def get_php_extensions(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return php.list_extensions()


@router.post("/php-extensions")
def install_php_extension(
    payload: PhpExtensionInstall,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Install one PHP extension for one installed version (admin only).

    Only from the list the panel offers; the helper checks it again. Returns
    the table afresh so the page shows what is loaded now.
    """
    ensure_role(current_user.role, Role.admin)
    try:
        output = php.install_extension(payload.php_version, payload.extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "php_extension_install", f"php{payload.php_version}-{payload.extension}", request=request)
    return {"output": output, **php.list_extensions()}


@router.post("/cron")
def add_cron(payload: CronCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    cron_user = cron.cron_user_for_website(website)
    if not website.linux_user and cron_user != "www-data":
        website.linux_user = cron_user
        db.add(website)
    try:
        line = cron.add_cron(website, payload.schedule, payload.command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    log_action(db, current_user.id, "add_cron", website.domain, line)
    return {"line": line, "cron_user": cron_user}


@router.get("/cron/{website_id}")
def list_cron(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    cron_user = cron.cron_user_for_website(website)
    return {
        "items": cron.list_cron_entries(website.domain, cron_user),
        "cron_user": cron_user,
        "php_version": website.php_version,
        # Shown in the add-cron help so it is obvious which interpreter a job runs on.
        "php_binary": cron.php_binary(website),
        "document_root": str(site_users.document_root(website.root_path)),
    }


@router.delete("/cron")
def delete_cron(payload: CronDelete, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    cron_user = cron.cron_user_for_website(website)
    try:
        line = cron.delete_cron(website.domain, payload.index, cron_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "delete_cron", website.domain, line)
    return {"deleted": line, "cron_user": cron_user}


@router.post("/wordpress")
def wordpress_action(payload: WpAction, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    result = wordpress.wp_update(
        str(site_users.document_root(website.root_path, website.document_root or "public_html")),
        payload.action,
        website.linux_user,
        php_version=website.php_version,
    )
    return result.__dict__


@router.post("/wordpress/{website_id}/fix-permissions")
def fix_wordpress_permissions(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    if website.linux_user:
        runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
        site_users.ensure_site_runtime(website.domain, website.root_path, runtime_php_version, website.linux_user)
    wordpress.fix_permissions(website.root_path, website.linux_user)
    log_action(db, current_user.id, "fix_permissions", website.domain, website.root_path)
    return {"message": f"Fixed permissions for {website.domain}", "root_path": website.root_path}


@router.get("/files/jobs")
def list_file_jobs(website_id: int | None = Query(default=None), current_user: User = Depends(get_current_user)):
    return {"jobs": _list_file_jobs(current_user, website_id)}


@router.get("/files/jobs/{job_id}")
def get_file_job(job_id: str, current_user: User = Depends(get_current_user)):
    job = _get_file_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="File job not found")
    if job.get("user_id") != current_user.id and not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="Access denied")
    return _public_file_job(job)


@router.get("/app-files/{app_id}")
def list_app_files(app_id: int, path: str = Query(default=""), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Separate prefix on purpose: under /files this would be shadowed by
    /files/{website_id} and answer 422 instead of dispatching here."""
    target = get_file_target(db, current_user, app_id=app_id)
    return {"items": file_manager.list_files(target, path)}


@router.get("/app-files/{app_id}/read")
def read_app_file(app_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    target = get_file_target(db, current_user, app_id=app_id)
    try:
        content = file_manager.read_text_file(target, path, allow_sensitive=is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": content}


@router.get("/app-files/{app_id}/download")
def download_app_file(app_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    target = get_file_target(db, current_user, app_id=app_id)
    try:
        resolved = file_manager.download_file_path(target, path, allow_sensitive=is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(resolved), filename=resolved.name)


@router.post("/app-files/{app_id}/upload")
async def upload_app_file(
    app_id: int,
    path: str = Query(default=""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = get_file_target(db, current_user, app_id=app_id)
    try:
        stored = file_manager.upload_file(
            target,
            path,
            file.filename or "upload.bin",
            file.file,
            allow_executable=True,
            quota_check=_quota_check_for_website(db, target),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_app_file", target.domain, stored)
    return {"stored": stored}


@router.get("/files/{website_id}")
def list_files(website_id: int, path: str = Query(default=""), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    return {"items": file_manager.list_files(website, path)}


@router.get("/files/{website_id}/read")
def read_file(website_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        content = file_manager.read_text_file(
            website,
            path,
            allow_sensitive=is_admin_role(current_user.role),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": content}


@router.get("/files/{website_id}/download")
def download_file(website_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        target = file_manager.download_file_path(website, path, allow_sensitive=is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(str(target), filename=target.name)


@router.post("/files/mkdir")
def make_directory(payload: FileMkdir, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        target = file_manager.make_directory(website, payload.path, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "mkdir", website.domain, target)
    return {"target": target}


@router.post("/files/create")
def create_file(payload: FileCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        target = file_manager.create_text_file(
            website,
            payload.path,
            payload.name,
            is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "create_file", website.domain, target)
    return {"target": target}


@router.post("/files/rename")
def rename_entry(payload: FileRename, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        target = file_manager.rename_entry(website, payload.path, payload.new_name, is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "rename_file", website.domain, target)
    return {"target": target}


@router.post("/files/chmod")
def chmod_entry(payload: FileChmod, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        target = file_manager.chmod_entry(website, payload.path, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "chmod_file", website.domain, f"{target} {payload.mode}")
    return {"target": target, "mode": payload.mode}


@router.post("/files/delete")
def delete_entries(payload: FileBulkDelete, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        deleted = file_manager.delete_entries(website, payload.paths, is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "delete_files", website.domain, ",".join(payload.paths[:20]))
    return {"deleted": deleted}


@router.post("/files/copy")
def copy_entries(payload: FileTransfer, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        copied = file_manager.copy_entries(
            website,
            payload.paths,
            payload.destination_path,
            allow_executable=is_admin_role(current_user.role),
            allow_sensitive=is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "copy_files", website.domain, f"{','.join(payload.paths[:20])} -> {payload.destination_path}")
    return {"copied": copied}


@router.post("/files/move")
def move_entries(payload: FileTransfer, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        moved = file_manager.move_entries(
            website,
            payload.paths,
            payload.destination_path,
            allow_executable=is_admin_role(current_user.role),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "move_files", website.domain, f"{','.join(payload.paths[:20])} -> {payload.destination_path}")
    return {"moved": moved}


@router.post("/files/archive")
def archive_entries(payload: FileArchive, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        target = file_manager.archive_entries(
            website,
            payload.base_path,
            payload.paths,
            payload.output_name,
            payload.format,
            allow_sensitive=is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "archive_files", website.domain, target)
    return {"target": target}


@router.post("/files/extract")
def extract_archive(payload: FileExtract, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    # Validate archive format before queuing to catch bad archives early
    archive_file = file_manager._safe_path(website, payload.archive_path)  # noqa: SLF001
    if not archive_file.exists() or not archive_file.is_file():
        raise HTTPException(status_code=400, detail="Archive not found")
    if archive_file.is_symlink():
        raise HTTPException(status_code=400, detail="Symlinks are not allowed")
    suffix = archive_file.name.lower()
    if not (suffix.endswith(".zip") or suffix.endswith(".tar.gz") or suffix.endswith(".tgz")):
        raise HTTPException(status_code=400, detail="Only .zip, .tar.gz, and .tgz archives are supported")
    # Validate archive is readable and not corrupted
    try:
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(archive_file) as _:
                pass
        else:
            with tarfile.open(archive_file, "r:gz") as _:
                pass
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP archive") from None
    except tarfile.TarError:
        raise HTTPException(status_code=400, detail="Invalid or corrupted tar archive") from None
    job = _queue_extract_job(
        current_user,
        website,
        payload.archive_path,
        payload.destination_path,
        True,
    )
    log_action(db, current_user.id, "extract_archive_queued", website.domain, payload.archive_path)
    return {**job, "message": "Extraction started in the background"}


@router.post("/files/write")
def write_file(payload: FileWrite, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_file_target(db, current_user, payload.website_id, payload.app_id)
    try:
        target = file_manager.write_text_file(
            website,
            payload.path,
            payload.content,
            is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"target": target}


@router.post("/files/{website_id}/upload")
def upload_file(
    website_id: int,
    path: str = Query(default=site_users.PUBLIC_DIR),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, website_id)
    try:
        target = file_manager.upload_file(
            website,
            path,
            file.filename or "upload.bin",
            file.file,
            is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_file", website.domain, target)
    return {"target": target}


@router.delete("/files/{website_id}")
def delete_file(website_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, website_id)
    try:
        target = file_manager.delete_file(website, path, is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": target}


# ---------------------------------------------------------------------------
# DirectAdmin Import
# ---------------------------------------------------------------------------

@router.get("/da-import/backups")
def list_da_backups(current_user: User = Depends(get_current_user)):
    """List all uploaded DirectAdmin backup archives."""
    ensure_role(current_user.role, Role.admin)
    from app.services import da_import
    return da_import.list_da_backups()


@router.post("/da-import/upload")
async def upload_da_backup(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a DirectAdmin backup archive to the DA backup directory."""
    ensure_role(current_user.role, Role.admin)
    from app.services import da_import

    da_import.ensure_backup_dir()
    if not os.access(da_import.DA_BACKUP_DIR, os.W_OK):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Cannot write to {da_import.DA_BACKUP_DIR}. "
                "Run spanel-update, or create it with: "
                f"install -d -m 0770 -o root -g spanel {da_import.DA_BACKUP_DIR}"
            ),
        )
    # The browser controls file.filename, so strip any directory component
    # before it is joined onto the backup directory.
    try:
        filename = da_import.safe_upload_name(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    destination = da_import.DA_BACKUP_DIR / filename
    if destination.exists():
        raise HTTPException(status_code=409, detail="A backup with this name already exists")

    try:
        with open(destination, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    log_action(db, current_user.id, "da_backup_upload", filename)
    return {"filename": filename, "path": str(destination), "size": destination.stat().st_size}


@router.post("/da-import/scan")
def scan_da_backup(body: dict, current_user: User = Depends(get_current_user)):
    """Scan a DirectAdmin backup to preview users/domains/databases."""
    ensure_role(current_user.role, Role.admin)
    from app.services import da_import

    archive_path = body.get("archive_path", "")
    if not archive_path:
        raise HTTPException(status_code=400, detail="archive_path is required")
    try:
        return da_import.scan_da_backup(archive_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


DA_IMPORT_JOB_LIMIT = 10
# The single-import guard and the job record both live in systemd now: see
# da_import.start_detached_import(). The ThreadPoolExecutor that used to run
# imports in this process is gone, because a restart of spanel-api killed
# whatever it was doing and took the job record with it.

@router.post("/da-import/import")
def import_da_backup(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Start an async DA backup import job."""
    ensure_role(current_user.role, Role.admin)
    from app.services import da_import

    archive_path = body.get("archive_path", "")
    force = bool(body.get("force", False))
    try:
        path = da_import.resolve_backup_path(archive_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")

    # systemd runs the import, not a thread in this process: an import of a few
    # GB takes minutes, and a restart of spanel-api used to kill it halfway.
    if da_import.detached_import_status()["status"] == "running":
        raise HTTPException(status_code=429, detail="An import is already running. Please wait.")
    try:
        job_id = da_import.start_detached_import(str(path), force)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action(db, current_user.id, "da_backup_import_start", f"{job_id} archive={path.name}")
    return {"job_id": job_id, "status": "running", "archive": path.name}


@router.get("/da-import/jobs/{job_id}")
def get_da_import_job(job_id: str, current_user: User = Depends(get_current_user)):
    """Get the status and result of a DA import job."""
    ensure_role(current_user.role, Role.admin)
    from app.services import da_import

    info = da_import.detached_import_status()
    # A job id from before a restart no longer matches the running unit. Say so
    # rather than 404: the page used to poll a missing id in silence for half an
    # hour and then stop, leaving whatever was on screen.
    stale = bool(job_id) and info.get("invocation") and job_id != info["invocation"]
    return {
        "id": info.get("invocation") or job_id,
        "status": "unknown" if stale else info["status"],
        "stale": stale,
        "error": "" if not stale else (
            "This import was started by an earlier session of the panel. "
            "The log below is from the import the server is tracking now."
        ),
        "log": info.get("log", []),
        "result": None,
    }


# ---------------------------------------------------------------------------
#  DA Import – Bulk (sequential) import
# ---------------------------------------------------------------------------
_da_bulk_import_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spanel-da-bulk")
_da_bulk_import_jobs: dict[str, dict] = {}
_da_bulk_import_jobs_lock = threading.Lock()


def _run_da_bulk_import_job(job_id: str, archive_paths: list[str], force: bool):
    """Process multiple DA backup archives sequentially."""
    with _da_bulk_import_jobs_lock:
        _da_bulk_import_jobs[job_id]["status"] = "running"
    results = []
    for idx, archive_path in enumerate(archive_paths):
        with _da_bulk_import_jobs_lock:
            _da_bulk_import_jobs[job_id]["current"] = idx
            _da_bulk_import_jobs[job_id]["current_archive"] = Path(archive_path).name
        try:
            from app.services import da_import
            result = da_import.import_da_backup(archive_path, force=force)
            results.append({"archive": Path(archive_path).name, "status": "completed", "result": result})
        except Exception as exc:
            logger.exception("DA bulk import: failed for %s", archive_path)
            results.append({"archive": Path(archive_path).name, "status": "failed", "error": str(exc)})
    with _da_bulk_import_jobs_lock:
        _da_bulk_import_jobs[job_id].update(
            status="completed",
            results=results,
            current=len(archive_paths),
        )


@router.post("/da-import/bulk-import")
def bulk_import_da_backups(body: DaBulkImportRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Start a sequential bulk import of multiple DA backup archives."""
    ensure_role(current_user.role, Role.admin)
    if not body.archive_paths:
        raise HTTPException(status_code=400, detail="archive_paths is required")

    from app.services import da_import

    paths = []
    for p in body.archive_paths:
        try:
            path = da_import.resolve_backup_path(p)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Backup file not found: {path.name}")
        paths.append(path)

    with _da_bulk_import_jobs_lock:
        running = sum(1 for j in _da_bulk_import_jobs.values() if j["status"] == "running")
        if running >= 1:
            raise HTTPException(status_code=429, detail="A bulk import is already running. Please wait.")
    # The single import lives in systemd now, so ask systemd rather than a dict
    # this process no longer keeps.
    if da_import.detached_import_status()["status"] == "running":
        raise HTTPException(status_code=429, detail="A single import is already running. Please wait.")

    job_id = str(uuid.uuid4())
    with _da_bulk_import_jobs_lock:
        _da_bulk_import_jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "total": len(paths),
            "current": 0,
            "current_archive": "",
            "archive_paths": [str(p) for p in paths],
            "created_at": datetime.utcnow().isoformat(),
            "results": None,
        }

    _da_bulk_import_executor.submit(_run_da_bulk_import_job, job_id, [str(p) for p in paths], body.force)
    log_action(db, current_user.id, "da_bulk_import_start", f"{job_id} archives={len(paths)}")
    return {"job_id": job_id, "status": "pending", "total": len(paths)}


@router.get("/da-import/bulk-jobs/{job_id}")
def get_da_bulk_import_job(job_id: str, current_user: User = Depends(get_current_user)):
    """Get the status and results of a DA bulk import job."""
    ensure_role(current_user.role, Role.admin)
    with _da_bulk_import_jobs_lock:
        job = _da_bulk_import_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/da-import/backups")
def delete_da_backup(body: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete an uploaded DirectAdmin backup archive."""
    ensure_role(current_user.role, Role.admin)
    from app.services import da_import

    archive_path = body.get("archive_path", "")
    if not archive_path:
        raise HTTPException(status_code=400, detail="archive_path is required")
    try:
        name = da_import.delete_da_backup(archive_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "da_backup_delete", name)
    return {"deleted": name}
