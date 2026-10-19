from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import ipaddress
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import User

router = APIRouter(prefix="/migration", tags=["migration"])

MIGRATIONS_BASE = Path("/var/backups/spanel/migrations")
FALLBACK_MIGRATIONS_BASE = Path(tempfile.gettempdir()) / "spanel-migrations"


class MigrationRequest(BaseModel):
    panel_type: str  # cpanel, directadmin, plesk
    method: str      # url, ftp
    source_url: Optional[str] = None
    ftp_host: Optional[str] = None
    ftp_user: Optional[str] = None
    ftp_pass: Optional[str] = None
    ftp_port: int = 21
    ftp_path: Optional[str] = None
    
    import_websites: bool = True
    import_databases: bool = True
    import_emails: bool = True


_jobs = {}


def _validate_public_host(hostname: str) -> str:
    host = (hostname or "").strip().lower()
    if not host or host in {"localhost", "localhost.localdomain"}:
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
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise HTTPException(
                status_code=400,
                detail="Private, loopback, or link-local IP addresses are not allowed",
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


@router.post("/start")
def start_migration(
    payload: MigrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_role(current_user.role, Role.reseller)

    if payload.panel_type not in {"cpanel", "directadmin", "plesk"}:
        raise HTTPException(status_code=400, detail="Unsupported panel_type")
    if payload.method not in {"url", "ftp"}:
        raise HTTPException(status_code=400, detail="Unsupported migration method")

    validated_url = None
    if payload.method == "url":
        validated_url = _validate_source_url(payload.source_url)
    elif payload.method == "ftp":
        if not payload.ftp_host or not payload.ftp_user:
            raise HTTPException(status_code=400, detail="FTP host and user are required")
        _validate_public_host(payload.ftp_host)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "progress": "Khởi tạo kết nối...",
        "user_id": current_user.id
    }
    
    def worker():
        work_dir = Path(f"/var/backups/spanel/migrations/{job_id}")
        try:
            from app.services import cpanel_import, da_import
            
            _jobs[job_id]["status"] = "running"
            
            work_dir = _allocate_work_dir(job_id)
            archive_path = str(work_dir / "backup.tar.gz")
            
            # Step 1: Download
            _jobs[job_id]["progress"] = f"Đang tải dữ liệu qua {payload.method.upper()} từ máy chủ cũ..."
            if payload.method == "url":
                if not validated_url:
                    raise ValueError("Thiếu URL tải file.")
                subprocess.run(["wget", "-q", "-O", archive_path, "--", validated_url], check=True, timeout=3600)
            elif payload.method == "ftp":
                if not payload.ftp_host or not payload.ftp_user:
                    raise ValueError("Thiếu thông tin FTP.")
                ftp_path = (payload.ftp_path or "").lstrip("/")
                ftp_url = f"ftp://{payload.ftp_user}:{payload.ftp_pass or ''}@{payload.ftp_host}:{payload.ftp_port}/{ftp_path}"
                subprocess.run(["curl", "-s", "-o", archive_path, "--", ftp_url], check=True, timeout=3600)
            
            # Step 2: Extraction & Restoration
            _jobs[job_id]["progress"] = f"Đang bóc tách và khôi phục dữ liệu từ định dạng {payload.panel_type.upper()}..."
            
            if payload.panel_type == "cpanel":
                cpanel_import.restore_to_user(archive_path, current_user.id, payload.import_websites, payload.import_databases, payload.import_emails)
            elif payload.panel_type == "directadmin":
                da_import.restore_to_user(archive_path, current_user.id, payload.import_websites, payload.import_databases, payload.import_emails)
            elif payload.panel_type == "plesk":
                raise ValueError("Định dạng Plesk hiện chưa được hỗ trợ.")
                
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["progress"] = "Chuyển dữ liệu hoàn tất thành công!"
        except subprocess.CalledProcessError as e:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = f"Lỗi tải file hoặc trích xuất (Mã lỗi: {e.returncode})"
        except Exception as e:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}

@router.get("/status/{job_id}")
def get_migration_status(job_id: str, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình Migration.")
    if job["user_id"] != current_user.id and not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="Không có quyền truy cập tiến trình này.")
    return job
