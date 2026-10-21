from __future__ import annotations

import base64
import datetime as _dt
import ftplib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.packages import (
    _da_login_session_headers,
    _da_parse_response_kv,
    _http_fetch,
    _validate_remote_url_candidates,
)
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import User, Website
from app.services.audit import log_action

logger = logging.getLogger("spanel.migration")

router = APIRouter(prefix="/migration", tags=["migration"])

MIGRATIONS_BASE = Path("/var/backups/spanel/migrations")
FALLBACK_MIGRATIONS_BASE = Path(tempfile.gettempdir()) / "spanel-migrations"
JOBS_STATE_FILE = Path("/var/lib/spanel/migration-jobs.json")
FALLBACK_JOBS_STATE_FILE = Path(tempfile.gettempdir()) / "spanel-migration-jobs.json"
MAX_STORED_JOBS = 25


class MigrationRequest(BaseModel):
    panel_type: str = Field(default="cpanel")  # cpanel, directadmin, plesk
    method: str = Field(default="local")       # local, url, ftp, sftp, remote_panel
    archive_path: Optional[str] = None
    archive_paths: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    ftp_host: Optional[str] = None
    ftp_user: Optional[str] = None
    ftp_pass: Optional[str] = None
    ftp_port: int = 21
    ftp_path: Optional[str] = None

    # Optional remote panel API parameters (when method == "remote_panel")
    remote_host_url: Optional[str] = None
    remote_username: Optional[str] = None
    remote_secret: Optional[str] = None
    remote_auth_type: str = Field(default="password")  # password | token
    remote_verify_ssl: bool = False
    selected_accounts: List[str] = Field(default_factory=list)

    import_websites: bool = True
    import_databases: bool = True
    import_emails: bool = True
    force: bool = False
    keep_archive: bool = True


class MigrationScanRequest(BaseModel):
    archive_path: str
    panel_type: Optional[str] = None  # auto-detected if omitted


class RemoteAccountsRequest(BaseModel):
    panel_type: str = Field(default="cpanel")  # directadmin | cpanel | plesk
    host_url: str
    username: str = Field(default="root")
    secret: str
    auth_type: str = Field(default="password")  # password | token
    verify_ssl: bool = False


class RemoteTriggerBackupRequest(BaseModel):
    panel_type: str = Field(default="cpanel")
    host_url: str
    username: str = Field(default="root")
    secret: str
    auth_type: str = Field(default="password")
    verify_ssl: bool = False
    accounts: List[str] = Field(default_factory=list)


_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _load_persisted_jobs() -> None:
    global _jobs
    for candidate in (JOBS_STATE_FILE, FALLBACK_JOBS_STATE_FILE):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(data, dict):
                    _jobs = data
                    return
            except Exception:
                pass


def _save_persisted_jobs() -> None:
    with _jobs_lock:
        # Keep only the most recent MAX_STORED_JOBS
        sorted_items = sorted(
            _jobs.items(),
            key=lambda kv: kv[1].get("created_at") or "",
            reverse=True,
        )[:MAX_STORED_JOBS]
        _jobs.clear()
        for k, v in sorted_items:
            _jobs[k] = v
        payload = json.dumps(_jobs, ensure_ascii=False, indent=2)

    for candidate in (JOBS_STATE_FILE, FALLBACK_JOBS_STATE_FILE):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(payload, encoding="utf-8")
            return
        except OSError:
            continue


_load_persisted_jobs()


def _append_job_log(job_id: str, message: str, *, progress: Optional[str] = None, percent: Optional[int] = None) -> None:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%H:%M:%S")
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append(f"[{stamp}] {message}")
        if progress is not None:
            job["progress"] = progress
        if percent is not None:
            job["percent"] = max(0, min(100, int(percent)))
    _save_persisted_jobs()


def _update_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(updates)
    _save_persisted_jobs()


def detect_archive_panel_type(filename: str) -> str:
    name = (filename or "").lower()
    if name.startswith("cpmove-") or re.match(r"^backup-\d+\.\d+\.\d+_", name):
        return "cpanel"
    if name.endswith(".zip") or "plesk" in name or name.startswith("backup_info_"):
        return "plesk"
    if name.startswith(("user.", "reseller.", "admin.")) or name.endswith((".tar.zst", ".tzst")):
        return "directadmin"
    return "cpanel"


def _validate_public_host(hostname: str) -> str:
    host = (hostname or "").strip().lower()
    if not host or host in {"localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"}:
        raise HTTPException(status_code=400, detail="Loopback or empty host is not allowed")
    try:
        ip_list = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(host, None)
            ip_list = [ipaddress.ip_address(item[4][0].split("%")[0]) for item in addr_info]
        except socket.gaierror as exc:
            raise HTTPException(status_code=400, detail=f"Could not resolve hostname: {host}") from exc
    for ip_obj in ip_list:
        if (
            ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="Loopback or link-local IP addresses are not allowed",
            )
    return host


def _validate_source_url(raw_url: Optional[str]) -> str:
    url = (raw_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="source_url is required")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="source_url must start with http:// or https://")
    _validate_public_host(parsed.hostname)
    return url


def _allocate_work_dir(job_id: str) -> Path:
    target = MIGRATIONS_BASE / job_id
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except OSError:
        fallback = FALLBACK_MIGRATIONS_BASE / job_id
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _download_via_ftp(host: str, port: int, user: str, password: str, remote_path: str, dest_file: Path) -> None:
    """Download a backup file via FTP/FTPS using Python's ftplib (safe against special chars in passwords)."""
    rel = (remote_path or "").strip()
    if not rel:
        raise ValueError("Đường dẫn file trên FTP (ftp_path) không được để trống.")

    ftp = ftplib.FTP()
    try:
        ftp.connect(host=host, port=port or 21, timeout=30)
        ftp.login(user=user, passwd=password or "")
        ftp.voidcmd("TYPE I")
        with open(dest_file, "wb") as out_f:
            ftp.retrbinary(f"RETR {rel}", out_f.write, blocksize=1024 * 256)
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def _download_via_sftp(
    host: str,
    port: int,
    user: str,
    password: str,
    remote_path: str,
    dest_file: Path,
    account_hint: str = "",
) -> Path:
    """Download a backup archive via SFTP (paramiko).

    If *remote_path* points to a directory (e.g. ``/home/admin/admin_backups`` or ``/backup``)
    and *account_hint* is provided, automatically locates the newest matching backup archive
    for that account inside the directory.
    """
    import paramiko
    from app.services import da_import

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=port or 22,
            username=user,
            password=password or "",
            timeout=25,
            allow_agent=False,
            look_for_keys=False,
        )
        sftp = ssh.open_sftp()
        try:
            target_remote = (remote_path or "").strip() or "/backup"
            try:
                st = sftp.stat(target_remote)
            except FileNotFoundError:
                raise FileNotFoundError(f"Không tìm thấy đường dẫn SFTP: {target_remote}")

            import stat as _stat
            if _stat.S_ISDIR(st.st_mode or 0):
                entries = sftp.listdir_attr(target_remote)
                archives = [
                    e for e in entries
                    if any(e.filename.lower().endswith(s) for s in da_import.ARCHIVE_SUFFIXES)
                ]
                if account_hint:
                    hint_lower = account_hint.lower()
                    matched = [
                        e for e in archives
                        if hint_lower in e.filename.lower()
                    ]
                    if matched:
                        archives = matched
                if not archives:
                    raise FileNotFoundError(
                        f"Không tìm thấy file backup nén nào trong thư mục SFTP '{target_remote}'"
                        + (f" cho tài khoản '{account_hint}'" if account_hint else "")
                    )
                archives.sort(key=lambda e: getattr(e, "st_mtime", 0) or 0, reverse=True)
                chosen = archives[0].filename
                target_remote = f"{target_remote.rstrip('/')}/{chosen}"
                dest_file = dest_file.parent / da_import.safe_upload_name(chosen)

            sftp.get(target_remote, str(dest_file))
            return dest_file
        finally:
            sftp.close()
    finally:
        ssh.close()


# ---------------------------------------------------------------------------
# Backup Archive Management (List / Upload / Scan / Delete)
# ---------------------------------------------------------------------------

@router.get("/backups")
def list_migration_backups(current_user: User = Depends(get_current_user)):
    """List all backup archives available on the server for migration."""
    ensure_role(current_user.role, Role.reseller)
    from app.services import da_import

    items = da_import.list_da_backups()
    for item in items:
        item["detected_panel"] = detect_archive_panel_type(item.get("filename", ""))
    return {
        "directory": str(da_import.DA_BACKUP_DIR),
        "items": items,
    }


@router.post("/upload")
async def upload_migration_backup(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a cPanel, DirectAdmin, or Plesk backup archive to the server."""
    ensure_role(current_user.role, Role.reseller)
    from app.services import da_import

    da_import.ensure_backup_dir()
    if not os.access(da_import.DA_BACKUP_DIR, os.W_OK):
        raise HTTPException(
            status_code=500,
            detail=f"Không thể ghi vào thư mục {da_import.DA_BACKUP_DIR}. Hãy chạy spanel-update để cấp quyền.",
        )
    try:
        filename = da_import.safe_upload_name(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    destination = da_import.DA_BACKUP_DIR / filename
    try:
        with open(destination, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    log_action(db, current_user.id, "migration_backup_upload", filename)
    return {
        "filename": filename,
        "path": str(destination),
        "size": destination.stat().st_size,
        "detected_panel": detect_archive_panel_type(filename),
    }


@router.delete("/backups")
def delete_migration_backup(
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an uploaded backup archive from the migration directory."""
    ensure_role(current_user.role, Role.reseller)
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

    log_action(db, current_user.id, "migration_backup_delete", name)
    return {"deleted": name}


@router.post("/scan")
def scan_migration_backup(
    payload: MigrationScanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Scan a backup archive (cPanel, DirectAdmin, or Plesk) and preview all users, domains, DBs, and emails."""
    ensure_role(current_user.role, Role.reseller)
    from app.services import cpanel_import, da_import, plesk_import

    try:
        path = da_import.resolve_backup_path(payload.archive_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy file backup: {path.name}")

    panel = (payload.panel_type or "").strip().lower() or detect_archive_panel_type(path.name)
    if panel not in {"cpanel", "directadmin", "plesk"}:
        panel = detect_archive_panel_type(path.name)

    try:
        if panel == "cpanel":
            scan_data = cpanel_import.scan_cpanel_backup(str(path))
        elif panel == "plesk":
            scan_data = plesk_import.scan_plesk_backup(str(path))
        else:
            scan_data = da_import.scan_da_backup(str(path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Lỗi quét file backup: {exc}") from exc

    # Annotate existing conflicts in SPanel DB
    existing_users: list[str] = []
    existing_domains: list[str] = []
    for u in scan_data.get("users") or []:
        uname = u.get("username")
        if uname and db.query(User).filter(User.username == uname).first():
            existing_users.append(uname)
        for d in u.get("domains") or []:
            dname = d.get("domain") if isinstance(d, dict) else str(d)
            if dname and db.query(Website).filter(Website.domain == dname).first():
                existing_domains.append(dname)

    scan_data["existing_users"] = existing_users
    scan_data["existing_domains"] = existing_domains
    return scan_data


# ---------------------------------------------------------------------------
# Remote Panel API Discovery & Backup Trigger
# ---------------------------------------------------------------------------

def _fetch_remote_da_accounts(payload: RemoteAccountsRequest) -> list[dict]:
    candidates = _validate_remote_url_candidates(payload.host_url, panel="directadmin")
    user = payload.username.strip()
    secret = payload.secret.strip()
    basic_token = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
    basic_headers = {"Authorization": f"Basic {basic_token}"}

    last_err = None
    for base_url in candidates:
        session_headers, twofa_err = _da_login_session_headers(base_url, user, secret, verify_ssl=payload.verify_ssl)
        if twofa_err:
            raise HTTPException(status_code=400, detail=twofa_err)

        header_sets = [basic_headers]
        if session_headers:
            header_sets.insert(0, session_headers)

        for hdrs in header_sets:
            users_list: list[str] = []
            for ep in ("/CMD_API_SHOW_ALL_USERS", "/CMD_API_SHOW_USERS"):
                try:
                    raw = _http_fetch(f"{base_url}{ep}", hdrs, verify_ssl=payload.verify_ssl, include_origin=True)
                    kv = _da_parse_response_kv(raw)
                    if "list" in kv and isinstance(kv["list"], list):
                        users_list = [str(x).strip() for x in kv["list"] if str(x).strip()]
                    elif kv:
                        users_list = [str(v).strip() for k, v in kv.items() if str(v).strip() and k != "error"]
                    else:
                        parsed_qs = urllib.parse.parse_qs(raw.strip())
                        if "list[]" in parsed_qs:
                            users_list = [x.strip() for x in parsed_qs["list[]"] if x.strip()]
                    if users_list:
                        break
                except Exception as exc:
                    last_err = exc
                    continue

            if users_list:
                accounts: list[dict] = []
                for uname in users_list[:100]:
                    acct_info = {
                        "username": uname,
                        "domain": "",
                        "email": "",
                        "package": "",
                        "disk_used": "",
                        "suspended": False,
                    }
                    try:
                        u_raw = _http_fetch(
                            f"{base_url}/CMD_API_SHOW_USER_CONFIG?user={urllib.parse.quote(uname)}",
                            hdrs,
                            verify_ssl=payload.verify_ssl,
                            include_origin=True,
                        )
                        ukv = _da_parse_response_kv(u_raw)
                        acct_info["domain"] = str(ukv.get("domain") or ukv.get("domains") or "")
                        acct_info["email"] = str(ukv.get("email") or "")
                        acct_info["package"] = str(ukv.get("package") or "")
                        acct_info["suspended"] = str(ukv.get("suspended") or "no").lower() in {"yes", "on", "1", "true"}
                    except Exception:
                        pass
                    accounts.append(acct_info)
                return accounts

    raise HTTPException(
        status_code=400,
        detail=f"Không thể lấy danh sách tài khoản DirectAdmin: {last_err or 'Kiểm tra lại URL/Tài khoản/Login Key'}",
    )


def _fetch_remote_cpanel_accounts(payload: RemoteAccountsRequest) -> list[dict]:
    candidates = _validate_remote_url_candidates(payload.host_url, panel="cpanel")
    user = payload.username.strip()
    secret = payload.secret.strip()
    if payload.auth_type == "token":
        headers = {"Authorization": f"whm {user}:{secret}"}
    else:
        b64 = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {b64}"}

    last_err = None
    for base_url in candidates:
        try:
            raw = _http_fetch(
                f"{base_url}/json-api/listaccts?api.version=1",
                headers,
                verify_ssl=payload.verify_ssl,
            )
            data = json.loads(raw)
            accts = (data.get("data") or {}).get("acct") or data.get("acct") or []
            if isinstance(accts, list):
                out = []
                for a in accts:
                    if not isinstance(a, dict):
                        continue
                    out.append({
                        "username": str(a.get("user") or ""),
                        "domain": str(a.get("domain") or ""),
                        "email": str(a.get("email") or ""),
                        "package": str(a.get("plan") or ""),
                        "disk_used": str(a.get("diskused") or ""),
                        "disk_limit": str(a.get("disklimit") or ""),
                        "ip": str(a.get("ip") or ""),
                        "suspended": bool(int(a.get("suspended") or 0)) if str(a.get("suspended") or "0").isdigit() else False,
                    })
                return out
        except Exception as exc:
            last_err = exc
            continue

    raise HTTPException(
        status_code=400,
        detail=f"Không thể kết nối WHM/cPanel API (:2087): {last_err or 'Kiểm tra lại WHM API Token hoặc mật khẩu root/reseller'}",
    )


def _fetch_remote_plesk_accounts(payload: RemoteAccountsRequest) -> list[dict]:
    candidates = _validate_remote_url_candidates(payload.host_url, panel="plesk")
    user = payload.username.strip()
    secret = payload.secret.strip()
    headers = {
        "Content-Type": "text/xml",
        "HTTP_AUTH_LOGIN": user,
        "HTTP_AUTH_PASSWD": secret,
    }
    if payload.auth_type == "token":
        headers["X-API-Key"] = secret

    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<packet><webspace><get><filter/><dataset><gen_info/></dataset></get></webspace></packet>'
    ).encode("utf-8")

    last_err = None
    for base_url in candidates:
        try:
            raw = _http_fetch(
                f"{base_url}/enterprise/control/agent.php",
                headers,
                data=xml_body,
                verify_ssl=payload.verify_ssl,
            )
            root_el = ET.fromstring(raw)
            accounts = []
            for res in root_el.findall(".//result"):
                gen = res.find("data/gen_info")
                if gen is None:
                    continue
                dname = (gen.findtext("name") or "").strip()
                if not dname:
                    continue
                accounts.append({
                    "username": dname.split(".")[0][:16],
                    "domain": dname,
                    "email": "",
                    "package": "",
                    "disk_used": (gen.findtext("real_size") or ""),
                    "suspended": (gen.findtext("status") or "0") != "0",
                })
            if accounts:
                return accounts
        except Exception as exc:
            last_err = exc
            continue

    raise HTTPException(
        status_code=400,
        detail=f"Không thể kết nối Plesk XML-RPC API (:8443): {last_err or 'Kiểm tra lại thông tin đăng nhập Plesk'}",
    )


@router.post("/remote-accounts")
def list_remote_panel_accounts(
    payload: RemoteAccountsRequest,
    current_user: User = Depends(get_current_user),
):
    """Connect to a remote DirectAdmin, cPanel/WHM, or Plesk server and list its hosting accounts."""
    ensure_role(current_user.role, Role.reseller)
    panel = (payload.panel_type or "").strip().lower()
    if panel == "directadmin":
        accounts = _fetch_remote_da_accounts(payload)
    elif panel == "cpanel":
        accounts = _fetch_remote_cpanel_accounts(payload)
    elif panel == "plesk":
        accounts = _fetch_remote_plesk_accounts(payload)
    else:
        raise HTTPException(status_code=400, detail="Unsupported panel_type")

    return {
        "panel_type": panel,
        "count": len(accounts),
        "accounts": accounts,
    }


@router.post("/remote-trigger-backup")
def trigger_remote_panel_backup(
    payload: RemoteTriggerBackupRequest,
    current_user: User = Depends(get_current_user),
):
    """Trigger remote backup generation on DirectAdmin or cPanel/WHM for selected accounts."""
    ensure_role(current_user.role, Role.reseller)
    panel = (payload.panel_type or "").strip().lower()
    if not payload.accounts:
        raise HTTPException(status_code=400, detail="Hãy chọn ít nhất 1 tài khoản để tạo backup từ xa.")

    if panel == "directadmin":
        candidates = _validate_remote_url_candidates(payload.host_url, panel="directadmin")
        user = payload.username.strip()
        secret = payload.secret.strip()
        basic_token = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {basic_token}"}
        form_params: dict[str, str] = {
            "action": "backup",
            "where": "local",
            "when": "now",
            "type": "admin",
        }
        for idx, acct in enumerate(payload.accounts):
            form_params[f"select{idx}"] = acct
        data_bytes = urllib.parse.urlencode(form_params).encode("utf-8")
        for base_url in candidates:
            try:
                _http_fetch(f"{base_url}/CMD_API_ADMIN_BACKUP", headers, data=data_bytes, verify_ssl=payload.verify_ssl)
                return {
                    "ok": True,
                    "message": f"Đã gửi lệnh tạo backup trên DirectAdmin cho {len(payload.accounts)} tài khoản (lưu tại /home/admin/admin_backups).",
                }
            except Exception:
                continue
        raise HTTPException(status_code=400, detail="Không thể gửi lệnh tạo backup tới DirectAdmin từ xa.")

    if panel == "cpanel":
        candidates = _validate_remote_url_candidates(payload.host_url, panel="cpanel")
        user = payload.username.strip()
        secret = payload.secret.strip()
        if payload.auth_type == "token":
            headers = {"Authorization": f"whm {user}:{secret}"}
        else:
            b64 = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {b64}"}

        triggered = []
        for acct in payload.accounts:
            for base_url in candidates:
                try:
                    # Trigger cPanel UAPI Backup::fullbackup_to_homedir via WHM API 1 uapi_cpanel
                    uapi_url = (
                        f"{base_url}/json-api/cpanel?cpanel_jsonapi_user={urllib.parse.quote(acct)}"
                        "&cpanel_jsonapi_apiversion=3&cpanel_jsonapi_module=Backup"
                        "&cpanel_jsonapi_func=fullbackup_to_homedir"
                    )
                    _http_fetch(uapi_url, headers, verify_ssl=payload.verify_ssl)
                    triggered.append(acct)
                    break
                except Exception:
                    continue
        if not triggered:
            raise HTTPException(status_code=400, detail="Không thể kích hoạt lệnh tạo fullbackup trên WHM/cPanel.")
        return {
            "ok": True,
            "message": f"Đã kích hoạt tạo Full Backup trên cPanel cho: {', '.join(triggered)} (file sẽ được lưu trong /home/<user>/backup-*.tar.gz).",
        }

    raise HTTPException(status_code=400, detail="Panel này chưa hỗ trợ kích hoạt tạo backup tự động qua API.")


# ---------------------------------------------------------------------------
# Migration Job Execution & Status
# ---------------------------------------------------------------------------

def _run_panel_importer(
    panel_type: str,
    archive_path: str,
    *,
    force: bool,
    import_websites: bool,
    import_databases: bool,
    import_emails: bool,
) -> dict:
    from app.services import cpanel_import, da_import, plesk_import

    if panel_type == "cpanel":
        return cpanel_import.import_cpanel_backup(
            archive_path,
            force=force,
            import_websites=import_websites,
            import_databases=import_databases,
            import_emails=import_emails,
        )
    if panel_type == "directadmin":
        return da_import.import_da_backup(
            archive_path,
            force=force,
            import_websites=import_websites,
            import_databases=import_databases,
            import_emails=import_emails,
        )
    if panel_type == "plesk":
        return plesk_import.import_plesk_backup(
            archive_path,
            force=force,
            import_websites=import_websites,
            import_databases=import_databases,
            import_emails=import_emails,
        )
    raise ValueError(f"Unsupported panel_type: {panel_type}")


@router.post("/start")
def start_migration(
    payload: MigrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    from app.services import da_import

    panel_type = (payload.panel_type or "").strip().lower()
    if panel_type not in {"cpanel", "directadmin", "plesk"}:
        raise HTTPException(status_code=400, detail="Unsupported panel_type")

    method = (payload.method or "").strip().lower()
    if method not in {"local", "url", "ftp", "sftp"}:
        raise HTTPException(status_code=400, detail="Unsupported migration method")

    local_paths: list[Path] = []
    validated_url: Optional[str] = None

    if method == "local":
        raw_list = list(payload.archive_paths or [])
        if payload.archive_path and payload.archive_path not in raw_list:
            raw_list.insert(0, payload.archive_path)
        if not raw_list:
            raise HTTPException(status_code=400, detail="Hãy chọn ít nhất 1 file backup để khôi phục.")
        for raw_p in raw_list:
            try:
                p = da_import.resolve_backup_path(raw_p)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not p.exists():
                raise HTTPException(status_code=404, detail=f"Không tìm thấy file backup: {p.name}")
            local_paths.append(p)
    elif method == "url":
        validated_url = _validate_source_url(payload.source_url)
    elif method in {"ftp", "sftp"}:
        if not payload.ftp_host or not payload.ftp_user:
            raise HTTPException(status_code=400, detail="Vui lòng nhập đầy đủ Host và Username cho FTP/SFTP.")
        _validate_public_host(payload.ftp_host)

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "panel_type": panel_type,
            "method": method,
            "status": "queued",
            "progress": "Đang khởi tạo tiến trình Migration...",
            "percent": 5,
            "logs": [],
            "summary": [],
            "credentials": [],
            "errors": [],
            "error": None,
            "created_at": _now_iso(),
            "finished_at": None,
            "user_id": current_user.id,
        }
    _save_persisted_jobs()
    log_action(db, current_user.id, "migration_start", f"panel={panel_type} method={method} job={job_id}")

    def worker() -> None:
        work_dir = _allocate_work_dir(job_id)
        try:
            _update_job(job_id, status="running")
            _append_job_log(
                job_id,
                f"Bắt đầu tiến trình chuyển dữ liệu ({panel_type.upper()} qua {method.upper()})...",
                progress=f"Đang chuẩn bị dữ liệu {panel_type.upper()}...",
                percent=10,
            )

            archives_to_process: list[Path] = []
            cleanup_downloaded: list[Path] = []

            if method == "local":
                archives_to_process.extend(local_paths)
                _append_job_log(
                    job_id,
                    f"Đã nhận {len(archives_to_process)} file backup trên máy chủ: {', '.join(p.name for p in archives_to_process)}",
                    percent=20,
                )
            elif method == "url":
                assert validated_url is not None
                url_path = urlparse(validated_url).path
                url_fname = Path(url_path).name or "backup.tar.gz"
                try:
                    safe_name = da_import.safe_upload_name(url_fname)
                except ValueError:
                    safe_name = "backup.tar.gz"
                dest_archive = work_dir / safe_name
                _append_job_log(
                    job_id,
                    f"Đang tải file backup từ URL: {validated_url} ...",
                    progress="Đang tải file backup qua HTTP/HTTPS...",
                    percent=15,
                )
                subprocess.run(["wget", "-q", "-O", str(dest_archive), "--", validated_url], check=True, timeout=3600)
                if not dest_archive.exists() or dest_archive.stat().st_size == 0:
                    raise RuntimeError("File tải về từ URL bị rỗng hoặc không tồn tại.")
                _append_job_log(
                    job_id,
                    f"Tải hoàn tất {dest_archive.name} ({dest_archive.stat().st_size / (1024 * 1024):.2f} MB).",
                    percent=40,
                )
                archives_to_process.append(dest_archive)
                cleanup_downloaded.append(dest_archive)
            elif method == "ftp":
                raw_name = Path((payload.ftp_path or "backup.tar.gz").replace("\\", "/")).name or "backup.tar.gz"
                try:
                    safe_name = da_import.safe_upload_name(raw_name)
                except ValueError:
                    safe_name = "backup.tar.gz"
                dest_archive = work_dir / safe_name
                _append_job_log(
                    job_id,
                    f"Đang tải file '{payload.ftp_path}' từ FTP {payload.ftp_host}:{payload.ftp_port} ...",
                    progress=f"Đang tải file backup qua FTP ({payload.ftp_host})...",
                    percent=15,
                )
                _download_via_ftp(
                    payload.ftp_host or "",
                    payload.ftp_port or 21,
                    payload.ftp_user or "",
                    payload.ftp_pass or "",
                    payload.ftp_path or "",
                    dest_archive,
                )
                _append_job_log(
                    job_id,
                    f"Tải FTP hoàn tất: {dest_archive.name} ({dest_archive.stat().st_size / (1024 * 1024):.2f} MB).",
                    percent=40,
                )
                archives_to_process.append(dest_archive)
                cleanup_downloaded.append(dest_archive)
            elif method == "sftp":
                sftp_port = payload.ftp_port if payload.ftp_port and payload.ftp_port != 21 else 22
                selected_accts = [a.strip() for a in (payload.selected_accounts or []) if a.strip()]
                if selected_accts:
                    for idx, acct in enumerate(selected_accts):
                        dest_archive = work_dir / f"backup-{acct}.tar.gz"
                        _append_job_log(
                            job_id,
                            f"Đang tải backup của tài khoản '{acct}' qua SFTP ({payload.ftp_host}:{sftp_port})...",
                            progress=f"Đang tải backup SFTP cho tài khoản {acct} ({idx + 1}/{len(selected_accts)})...",
                            percent=15 + int(25 * (idx / max(1, len(selected_accts)))),
                        )
                        actual_file = _download_via_sftp(
                            payload.ftp_host or "",
                            sftp_port,
                            payload.ftp_user or "",
                            payload.ftp_pass or "",
                            payload.ftp_path or "/backup",
                            dest_archive,
                            account_hint=acct,
                        )
                        _append_job_log(
                            job_id,
                            f"Đã tải xong {actual_file.name} ({actual_file.stat().st_size / (1024 * 1024):.2f} MB).",
                        )
                        archives_to_process.append(actual_file)
                        cleanup_downloaded.append(actual_file)
                else:
                    raw_name = Path((payload.ftp_path or "backup.tar.gz").replace("\\", "/")).name or "backup.tar.gz"
                    try:
                        safe_name = da_import.safe_upload_name(raw_name)
                    except ValueError:
                        safe_name = "backup.tar.gz"
                    dest_archive = work_dir / safe_name
                    _append_job_log(
                        job_id,
                        f"Đang tải file '{payload.ftp_path}' qua SFTP ({payload.ftp_host}:{sftp_port})...",
                        progress=f"Đang tải file backup qua SFTP ({payload.ftp_host})...",
                        percent=15,
                    )
                    actual_file = _download_via_sftp(
                        payload.ftp_host or "",
                        sftp_port,
                        payload.ftp_user or "",
                        payload.ftp_pass or "",
                        payload.ftp_path or "",
                        dest_archive,
                    )
                    _append_job_log(
                        job_id,
                        f"Tải SFTP hoàn tất: {actual_file.name} ({actual_file.stat().st_size / (1024 * 1024):.2f} MB).",
                        percent=40,
                    )
                    archives_to_process.append(actual_file)
                    cleanup_downloaded.append(actual_file)

            all_summaries: list[dict] = []
            all_credentials: list[str] = []
            all_errors: list[str] = []

            total_archives = len(archives_to_process)
            for idx, arch_path in enumerate(archives_to_process):
                base_pct = 40 + int(55 * (idx / max(1, total_archives)))
                _append_job_log(
                    job_id,
                    f"Đang giải nén và khôi phục [{idx + 1}/{total_archives}]: {arch_path.name} ({panel_type.upper()})...",
                    progress=f"Đang khôi phục {arch_path.name} ({idx + 1}/{total_archives})...",
                    percent=base_pct,
                )
                res = _run_panel_importer(
                    panel_type,
                    str(arch_path),
                    force=payload.force,
                    import_websites=payload.import_websites,
                    import_databases=payload.import_databases,
                    import_emails=payload.import_emails,
                )
                summaries = res.get("summary") or []
                creds = res.get("credentials") or []
                errs = res.get("errors") or []

                all_summaries.extend(summaries)
                all_credentials.extend(creds)
                all_errors.extend(errs)

                for s_item in summaries:
                    uname = s_item.get("username") or "?"
                    doms = s_item.get("imported_domains") or []
                    dbs = s_item.get("databases") or []
                    mboxes = s_item.get("mailboxes") or []
                    _append_job_log(
                        job_id,
                        f"Hoàn tất tài khoản '{uname}': {len(doms)} website, {len(dbs)} database, {len(mboxes)} email.",
                    )
                    for w in s_item.get("warnings") or []:
                        _append_job_log(job_id, f"Cảnh báo ({uname}): {w}")

            if all_errors and not any(s.get("imported_domains") or s.get("databases") for s in all_summaries):
                err_msg = "; ".join(all_errors)
                _append_job_log(job_id, f"Migration dừng do xung đột/lỗi: {err_msg}", progress=err_msg, percent=100)
                _update_job(
                    job_id,
                    status="failed",
                    error=err_msg,
                    summary=all_summaries,
                    credentials=all_credentials,
                    errors=all_errors,
                    finished_at=_now_iso(),
                )
            else:
                final_status = "completed_with_warnings" if all_errors else "completed"
                _append_job_log(
                    job_id,
                    "Chuyển dữ liệu hoàn tất thành công!",
                    progress="Chuyển dữ liệu hoàn tất thành công!",
                    percent=100,
                )
                _update_job(
                    job_id,
                    status=final_status,
                    summary=all_summaries,
                    credentials=all_credentials,
                    errors=all_errors,
                    finished_at=_now_iso(),
                )
        except subprocess.CalledProcessError as exc:
            msg = f"Lỗi tải file hoặc giải nén (Mã lỗi: {exc.returncode})"
            _append_job_log(job_id, msg, progress=msg)
            _update_job(job_id, status="failed", error=msg, finished_at=_now_iso())
        except Exception as exc:
            logger.exception("Migration job %s failed", job_id)
            msg = str(exc)
            _append_job_log(job_id, f"Lỗi: {msg}", progress=f"Thất bại: {msg}")
            _update_job(job_id, status="failed", error=msg, finished_at=_now_iso())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs")
def list_migration_jobs(current_user: User = Depends(get_current_user)):
    """Return recent migration jobs for the current user (persisted across page reloads)."""
    ensure_role(current_user.role, Role.reseller)
    with _jobs_lock:
        items = [
            job for job in _jobs.values()
            if job.get("user_id") == current_user.id or is_admin_role(current_user.role)
        ]
    items.sort(key=lambda j: j.get("created_at") or "", reverse=True)
    return {"jobs": items[:15]}


@router.get("/status/{job_id}")
def get_migration_status(job_id: str, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình Migration.")
    if job.get("user_id") != current_user.id and not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="Không có quyền truy cập tiến trình này.")
    return job
