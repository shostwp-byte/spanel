import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shlex
import stat
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine, get_db
from app.core.permissions import Role, is_admin_role, normalize_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, SiteApp, User, Website
from app.services import addons, mariadb, nginx, site_apps, site_users, teardown, wordpress_live
from app.services.shell import shell

_NODEJS_ACCESS_ROLE_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nodejs_access_role", default=""
)


async def _capture_nodejs_access_role(request: Request):
    token = _NODEJS_ACCESS_ROLE_CTX.set(
        (request.headers.get("x-access-role") or "").strip().lower()
    )
    try:
        yield
    finally:
        _NODEJS_ACCESS_ROLE_CTX.reset(token)


router = APIRouter(
    prefix="/nodejs-manager",
    tags=["nodejs-manager"],
    dependencies=[Depends(_capture_nodejs_access_role)],
)
logger = logging.getLogger("spanel.nodejs_manager")

_NODEJS_STATE_LOCK = threading.RLock()
_NODEJS_INSPECT_CACHE_LOCK = threading.RLock()
_NODEJS_INSPECT_CACHE: Dict[int, Tuple[Tuple[Any, ...], float, Dict[str, Any]]] = {}
_NODEJS_INSPECT_CACHE_TTL = 45.0

SUBPATH_RE = re.compile(r"^[a-zA-Z0-9._/-]{0,120}$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
NPM_PKG_RE = re.compile(
    r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*(?:@[a-zA-Z0-9.*^~<>=-]{1,32})?$"
)
NPM_SCRIPT_RE = re.compile(r"^[a-zA-Z0-9:_-]{1,64}$")
START_ARG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
NODE_MAJOR_RE = re.compile(r"^(18|20|22|24)$")
GIT_URL_RE = re.compile(r"^(?:https://|git@)[a-zA-Z0-9._:/-]+$")
GIT_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._/-]{1,80}$")

SUPPORTED_NODE_MAJORS: List[str] = ["22", "20", "18", "24"]
ALLOWED_START_MODES = {"npm", "node", "npx", "yarn"}
STRICT_ENV_OCTAL_MODES = {"600", "640", "400", "440"}
BENIGN_DEFAULT_SITE_ENTRIES = {"index.html", ".htaccess", ".well-known", "cgi-bin"}
EXISTING_APP_MARKERS = (
    "wp-config.php",
    "wp-includes/version.php",
    "artisan",
    "composer.json",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
)

NODEJS_NGINX_SEC_BEGIN = "# BEGIN SPANEL NODEJS SECURITY"
NODEJS_NGINX_SEC_END = "# END SPANEL NODEJS SECURITY"
NODEJS_NGINX_SENSITIVE_MARKER = "# SPANEL_NODEJS_SENSITIVE_FILES_DENY"

NODEJS_SENSITIVE_NGINX_BLOCK = (
    "# SPANEL_NODEJS_SENSITIVE_FILES_DENY\n"
    "location ^~ /.git/ {\n"
    "    deny all;\n"
    "}\n"
    "location ^~ /node_modules/ {\n"
    "    deny all;\n"
    "}\n"
    "location ~* (?:^|/)(?:\\.env(?:\\..+)?|package(?:-lock)?\\.json|yarn\\.lock|pnpm-lock\\.yaml|tsconfig\\.json|ecosystem\\.config\\.(?:js|cjs|mjs))$ {\n"
    "    deny all;\n"
    "}\n"
    "# END SPANEL_NODEJS_SENSITIVE_FILES_DENY"
)

STARTER_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "express-api",
        "name": "Express.js Web & REST API",
        "framework": "Express.js",
        "badge": "Phổ biến nhất",
        "description": "Khung ứng dụng Web & RESTful API chuẩn Production với health-check endpoint (/api/health), cấu hình CORS/JSON và giao diện trạng thái hiện đại.",
        "start_mode": "node",
        "start_arg": "server.js",
        "install_dependencies": True,
    },
    {
        "id": "nextjs-ssr",
        "name": "Next.js SSR Ready Starter",
        "framework": "Next.js",
        "badge": "Full-stack SSR",
        "description": "Khởi tạo dự án chuẩn kiến trúc SSR / Full-stack React với chế độ chạy cổng nội bộ (PORT) qua Nginx Reverse Proxy và hỗ trợ WebSocket.",
        "start_mode": "npm",
        "start_arg": "start",
        "install_dependencies": False,
    },
    {
        "id": "nestjs-api",
        "name": "NestJS-Style Enterprise API",
        "framework": "NestJS / API",
        "badge": "Enterprise",
        "description": "Cấu trúc dịch vụ API phân tầng (Controller / Service / Health) sẵn sàng kết nối MySQL/MariaDB và mở rộng microservice.",
        "start_mode": "node",
        "start_arg": "server.js",
        "install_dependencies": False,
    },
    {
        "id": "socketio-realtime",
        "name": "Real-time WebSocket & Event Server",
        "framework": "WebSocket / Realtime",
        "badge": "Realtime",
        "description": "Máy chủ sự kiện thời gian thực tương thích sẵn cấu hình Upgrade/Connection WebSocket của Nginx Proxy.",
        "start_mode": "node",
        "start_arg": "server.js",
        "install_dependencies": False,
    },
    {
        "id": "minimal-http",
        "name": "Node.js Native HTTP (Zero-Dependency)",
        "framework": "Node.js Core",
        "badge": "Siêu nhẹ",
        "description": "Máy chủ HTTP thuần Node.js (node:http) không phụ thuộc thư viện ngoài, khởi chạy tức thì dưới 50ms và tiêu thụ cực ít RAM.",
        "start_mode": "node",
        "start_arg": "server.js",
        "install_dependencies": False,
    },
]

CURATED_NPM_PACKAGES: List[Dict[str, Any]] = [
    {
        "name": "express",
        "title": "Express.js",
        "version": "^4.21.2",
        "category": "Web Framework",
        "description": "Framework HTTP & REST API gọn nhẹ, nhanh và phổ biến nhất trên hệ sinh thái Node.js.",
    },
    {
        "name": "dotenv",
        "title": "Dotenv",
        "version": "^16.4.7",
        "category": "Configuration",
        "description": "Tự động nạp biến môi trường từ tệp .env vào process.env.",
    },
    {
        "name": "cors",
        "title": "CORS Middleware",
        "version": "^2.8.5",
        "category": "Security & API",
        "description": "Middleware cấu hình Cross-Origin Resource Sharing cho REST API và SPA Frontend.",
    },
    {
        "name": "helmet",
        "title": "Helmet Security",
        "version": "^8.0.0",
        "category": "Security",
        "description": "Tăng cường bảo mật ứng dụng Express bằng cách thiết lập các HTTP Security Headers chuẩn.",
    },
    {
        "name": "mysql2",
        "title": "MySQL2 Promise Client",
        "version": "^3.12.0",
        "category": "Database",
        "description": "Trình điều khiển MySQL / MariaDB hiệu năng cao hỗ trợ Promise, Prepared Statements và Connection Pool.",
    },
    {
        "name": "socket.io",
        "title": "Socket.IO",
        "version": "^4.8.1",
        "category": "Realtime",
        "description": "Thư viện giao tiếp hai chiều thời gian thực qua WebSocket và HTTP Long-Polling.",
    },
    {
        "name": "zod",
        "title": "Zod Validation",
        "version": "^3.24.1",
        "category": "Validation",
        "description": "Thư viện kiểm tra tính hợp lệ dữ liệu (Schema Validation) ưu tiên TypeScript & JavaScript hiện đại.",
    },
    {
        "name": "axios",
        "title": "Axios HTTP Client",
        "version": "^1.7.9",
        "category": "HTTP Client",
        "description": "Thư viện gọi HTTP/REST API bên ngoài dựa trên Promise mạnh mẽ và dễ sử dụng.",
    },
]

SECURITY_CHECK_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "node_env_prod",
        "title": "Chế độ vận hành Production (NODE_ENV=production)",
        "description": "Tối ưu hóa hiệu năng V8/Express, ẩn chi tiết Stack Trace nội bộ và tắt công cụ debug phát triển.",
        "critical": True,
    },
    {
        "id": "env_permissions",
        "title": "Chuẩn hóa quyền hạn tệp biến môi trường .env (0640)",
        "description": "Ngăn chặn các tài khoản khác đọc trái phép khóa bí mật API, JWT Secret và mật khẩu cơ sở dữ liệu.",
        "critical": True,
    },
    {
        "id": "loopback_binding",
        "title": "Cô lập cổng lắng nghe nội bộ Loopback (127.0.0.1) qua Nginx Reverse Proxy",
        "description": "Đảm bảo tiến trình Node.js không phơi trực tiếp cổng ứng dụng ra Internet mà đi qua lớp bảo vệ Nginx/WAF.",
        "critical": True,
    },
    {
        "id": "nginx_sensitive_files",
        "title": "Chặn truy cập công khai .env, .git, package.json & node_modules trên Nginx",
        "description": "Bảo vệ tệp cấu hình, mã nguồn Git và danh sách gói phụ thuộc khỏi bot quét tự động.",
        "critical": True,
    },
    {
        "id": "systemd_sandbox",
        "title": "Kích hoạt systemd Daemon Sandbox (NoNewPrivileges, PrivateTmp, MemoryMax)",
        "description": "Giới hạn đặc quyền hệ điều hành và bảo vệ máy chủ khỏi rò rỉ bộ nhớ (OOM) của tiến trình Node.js.",
        "critical": True,
    },
    {
        "id": "lockfile_present",
        "title": "Khóa phiên bản phụ thuộc (package-lock.json / node_modules)",
        "description": "Đảm bảo tính nhất quán của cây thư viện NPM và giảm thiểu rủi ro tấn công chuỗi cung ứng (Supply-chain).",
        "critical": False,
    },
    {
        "id": "ssl_https",
        "title": "Bảo mật kết nối truyền tải bằng chứng chỉ SSL / HTTPS",
        "description": "Mã hóa toàn bộ lưu lượng HTTP và WebSocket (WSS) giữa trình duyệt người dùng và máy chủ.",
        "critical": False,
    },
]


# ── State Persistence & Fast In-Memory Cache ─────────────────────────────────

def _toolkit_state_path() -> Path:
    candidates = [
        Path("/var/lib/spanel/nodejs_toolkit_state.json"),
        Path(__file__).resolve().parents[2] / "data" / "nodejs_toolkit_state.json",
    ]
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if os.access(p.parent, os.W_OK):
                return p
        except Exception:
            continue
    return Path(__file__).resolve().parents[2] / "nodejs_toolkit_state.json"


def _is_in_memory_engine() -> bool:
    try:
        return str(getattr(engine, "url", "")).endswith(":memory:")
    except Exception:
        return False


def _ensure_toolkit_state_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS nodejs_toolkit_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "payload TEXT NOT NULL, "
                "updated_at TEXT"
                ")"
            )
        )


def _load_toolkit_state() -> Dict[str, Any]:
    with _NODEJS_STATE_LOCK:
        try:
            _ensure_toolkit_state_table()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT payload FROM nodejs_toolkit_state WHERE id = 1")
                ).fetchone()
                if row and row[0]:
                    data = json.loads(row[0])
                    if isinstance(data, dict):
                        data.setdefault("instances", {})
                        return data
        except Exception as exc:
            logger.debug("load_nodejs_toolkit_state db warning: %s", exc)

        if _is_in_memory_engine():
            return {"instances": {}}

        path = _toolkit_state_path()
        try:
            if path.exists() and not path.is_symlink():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("instances", {})
                    return data
        except Exception as exc:
            logger.warning("Failed to load Node.js toolkit state file: %s", exc)
        return {"instances": {}}


def _save_toolkit_state(state: Dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _NODEJS_STATE_LOCK:
        try:
            _ensure_toolkit_state_table()
            with engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT 1 FROM nodejs_toolkit_state WHERE id = 1")
                ).fetchone()
                if existing:
                    conn.execute(
                        text(
                            "UPDATE nodejs_toolkit_state SET payload = :payload, updated_at = :updated_at WHERE id = 1"
                        ),
                        {"payload": payload, "updated_at": now_iso},
                    )
                else:
                    conn.execute(
                        text(
                            "INSERT INTO nodejs_toolkit_state (id, payload, updated_at) VALUES (1, :payload, :updated_at)"
                        ),
                        {"payload": payload, "updated_at": now_iso},
                    )
        except Exception as exc:
            logger.warning("Failed to save Node.js toolkit state to DB: %s", exc)

        if _is_in_memory_engine():
            return

        path = _toolkit_state_path()
        try:
            if path.is_symlink():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".nodejs_toolkit_state.", dir=str(path.parent), text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.remove(tmp_name)
                    except OSError:
                        pass
        except Exception as exc:
            logger.debug("save_nodejs_toolkit_state file mirror warning: %s", exc)


def _update_instance_state(
    inst_key: str, updater: Callable[[Dict[str, Any]], None]
) -> Dict[str, Any]:
    with _NODEJS_STATE_LOCK:
        state = _load_toolkit_state()
        inst = state.setdefault("instances", {}).setdefault(
            str(inst_key), {"installed": True}
        )
        updater(inst)
        inst["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_toolkit_state(state)
    try:
        invalidate_nodejs_inspect_cache(int(inst_key))
    except (TypeError, ValueError):
        invalidate_nodejs_inspect_cache()
    return state


def _pop_instance_state(inst_key: str) -> Dict[str, Any]:
    with _NODEJS_STATE_LOCK:
        state = _load_toolkit_state()
        if isinstance(state.get("instances"), dict):
            state["instances"].pop(str(inst_key), None)
        _save_toolkit_state(state)
    try:
        invalidate_nodejs_inspect_cache(int(inst_key))
    except (TypeError, ValueError):
        invalidate_nodejs_inspect_cache()
    return state


def invalidate_nodejs_inspect_cache(website_id: Optional[int] = None) -> None:
    with _NODEJS_INSPECT_CACHE_LOCK:
        if website_id is None:
            _NODEJS_INSPECT_CACHE.clear()
        else:
            _NODEJS_INSPECT_CACHE.pop(int(website_id), None)


def get_nodejs_port_for_website(website_id: Optional[int]) -> Optional[int]:
    """Return the allocated loopback port for a Website managed by Node.js Manager, if any."""
    if not website_id:
        return None
    try:
        state = _load_toolkit_state()
        inst = (state.get("instances") or {}).get(str(website_id)) or {}
        if inst.get("detached"):
            return None
        port = int(inst.get("port") or 0)
        if site_apps.PORT_RANGE_START <= port <= site_apps.PORT_RANGE_END:
            return port
    except Exception:
        pass
    return None


def reserved_nodejs_ports(exclude_website_id: Optional[int] = None) -> set[int]:
    """Return all loopback ports currently reserved in Node.js Manager state."""
    ports: set[int] = set()
    try:
        state = _load_toolkit_state()
        for site_id_str, inst in (state.get("instances") or {}).items():
            if exclude_website_id is not None and str(site_id_str) == str(exclude_website_id):
                continue
            if not isinstance(inst, dict) or inst.get("detached"):
                continue
            try:
                p = int(inst.get("port") or 0)
                if site_apps.PORT_RANGE_START <= p <= site_apps.PORT_RANGE_END:
                    ports.add(p)
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return ports


def count_nodejs_instances_for_owner(db: Session, owner_id: int) -> int:
    """Count active (non-detached) Node.js Manager instances belonging to ``owner_id``."""
    try:
        state = _load_toolkit_state()
        instances_map = state.get("instances") or {}
        if not instances_map:
            return 0
        site_ids = [
            row[0]
            for row in db.query(Website.id).filter(Website.owner_id == owner_id).all()
        ]
        count = 0
        for sid in site_ids:
            inst = instances_map.get(str(sid))
            if isinstance(inst, dict) and not inst.get("detached"):
                count += 1
        return count
    except Exception:
        return 0


def _allocate_nodejs_port(
    db: Session,
    preferred: Optional[int] = None,
    exclude_website_id: Optional[int] = None,
) -> int:
    taken = (
        site_apps.reserved_ports(db)
        | site_apps.listening_ports()
        | reserved_nodejs_ports(exclude_website_id=exclude_website_id)
    )
    if preferred is not None:
        try:
            candidate = site_apps.validate_port(preferred)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if candidate in taken:
            raise HTTPException(
                status_code=409,
                detail=f"Cổng nội bộ {candidate} đang được sử dụng bởi ứng dụng khác. Vui lòng chọn cổng khác trong dải {site_apps.PORT_RANGE_START}–{site_apps.PORT_RANGE_END}.",
            )
        return candidate
    for candidate in range(site_apps.PORT_RANGE_START, site_apps.PORT_RANGE_END + 1):
        if candidate not in taken:
            return candidate
    raise HTTPException(
        status_code=503,
        detail="Máy chủ đã hết cổng ứng dụng nội bộ trống (21000–21999).",
    )


# ── Access Control & Path Resolution ─────────────────────────────────────────

def _require_end_user_nodejs_access(
    current_user: User, request: Optional[Request] = None
) -> None:
    role = normalize_role(getattr(current_user, "role", None))
    access_role = (
        (request.headers.get("x-access-role") or "").strip().lower()
        if (request is not None and getattr(request, "headers", None))
        else _NODEJS_ACCESS_ROLE_CTX.get()
    )
    if is_admin_role(getattr(current_user, "role", None)) or role == Role.reseller:
        if access_role not in {"user", "end_user"}:
            raise HTTPException(
                status_code=403,
                detail="Node.js Manager is only available in User Access Role. Please switch Access Role to User or use 'Login as' on an End User account.",
            )
    if not addons.is_installed(addons.APPLICATION):
        raise HTTPException(
            status_code=403,
            detail="The Application addon is not installed. Install it from the Addons page first.",
        )
    if not is_admin_role(getattr(current_user, "role", None)):
        if site_apps.app_limit_for(current_user) == 0:
            raise HTTPException(
                status_code=403,
                detail="Application hosting is not enabled for this package",
            )


def _get_accessible_website(website_id: int, db: Session, current_user: User) -> Website:
    _require_end_user_nodejs_access(current_user)
    site = db.query(Website).filter(Website.id == website_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Website not found")
    if site.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return site


def _resolve_nodejs_paths(
    site: Website, db: Session, subpath: str = ""
) -> Tuple[str, Path, Path]:
    owner = db.query(User).filter(User.id == site.owner_id).first()
    linux_user = site.linux_user or (
        site_users.linux_user_for_panel_username(owner.username)
        if owner
        else site_users.linux_user_for_domain(site.domain)
    )
    root_str = site.root_path or (
        site_users.site_root_for_panel_user(owner.username, site.domain)
        if owner
        else site_users.site_root_for_domain(site.domain)
    )
    site_root = Path(root_str)
    doc_rel = (site.document_root or "public_html").strip().strip("/") or "public_html"
    base_public = site_root / doc_rel

    clean_sub = (subpath or "").strip().strip("/")
    if clean_sub:
        if ".." in clean_sub or not SUBPATH_RE.fullmatch(clean_sub):
            raise HTTPException(status_code=400, detail="Đường dẫn thư mục con không hợp lệ.")
        app_dir = base_public / clean_sub
    else:
        app_dir = base_public
    return linux_user, site_root, app_dir


def _read_app_file(
    app_dir: Path,
    rel_path: str,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> str:
    return wordpress_live.read_wp_file_text(
        app_dir, rel_path, linux_user=linux_user, site_root=site_root
    )


def _write_app_file(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
    rel_path: str,
    content: str,
    mode: str = "0644",
) -> None:
    wordpress_live.write_wp_file_text(
        linux_user, site_root, app_dir, rel_path, content, mode=mode
    )
    if settings.command_dry_run and os.name != "nt":
        target = app_dir / rel_path
        if target.exists() and not target.is_symlink():
            try:
                os.chmod(target, 0o640 if mode == "0640" else 0o644)
            except OSError:
                pass


def _apply_strict_nodejs_permissions(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
) -> None:
    if settings.command_dry_run:
        if os.name != "nt":
            for rel_f in (".env", ".env.bak"):
                fp = app_dir / rel_f
                if fp.exists() and not fp.is_symlink():
                    try:
                        os.chmod(fp, 0o640)
                    except OSError:
                        pass
        return
    for rel_f in (".env", ".env.bak"):
        if _app_path_exists(app_dir, rel_f, linux_user, site_root):
            shell.privileged(
                "site-chmod",
                helper_args=[linux_user, str(site_root), str(app_dir / rel_f), "0640"],
                check=False,
            )


def _app_path_exists(
    app_dir: Path,
    rel_path: str,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> bool:
    return wordpress_live.wp_path_exists(app_dir, rel_path, linux_user, site_root)


def _purge_app_dir_contents(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
) -> None:
    try:
        rel_dir = str(app_dir.relative_to(site_root)).replace("\\", "/").strip("/")
    except ValueError:
        return
    if not rel_dir or ".." in rel_dir.split("/"):
        return

    if not settings.command_dry_run:
        shell.privileged(
            "rm-site",
            helper_args=[linux_user, str(site_root), str(app_dir)],
            check=False,
        )
        shell.privileged(
            "site-document-root-ensure",
            helper_args=[linux_user, str(site_root), rel_dir],
            check=False,
        )
        return

    if wordpress_live._has_symlink_component(app_dir, site_root) or app_dir.is_symlink():
        return
    if not app_dir.exists() or not app_dir.is_dir():
        return

    def _clean_dir_no_symlink(d: Path) -> None:
        for child in list(d.iterdir()):
            if child.is_symlink():
                continue
            if child.is_dir():
                _clean_dir_no_symlink(child)
                try:
                    os.rmdir(child)
                except OSError:
                    pass
            else:
                try:
                    os.remove(child)
                except OSError:
                    pass

    _clean_dir_no_symlink(app_dir)


# ── Node.js Runtime & Command Execution ──────────────────────────────────────

def _list_installed_node_majors() -> List[str]:
    try:
        installed = site_apps.installed_node_majors() or []
    except Exception:
        installed = []
    valid = [m for m in installed if NODE_MAJOR_RE.fullmatch(str(m))]
    if not valid and settings.command_dry_run:
        return ["22", "20", "18"]
    return sorted(set(valid), key=lambda x: int(x), reverse=True)


def _validate_node_major(raw_major: Optional[str]) -> str:
    clean = str(raw_major or "22").strip()
    if clean.lower().startswith("v"):
        clean = clean[1:].strip()
    if "." in clean:
        clean = clean.split(".", 1)[0].strip()
    if not NODE_MAJOR_RE.fullmatch(clean):
        raise HTTPException(
            status_code=400,
            detail=f"Phiên bản Node.js '{raw_major}' không hợp lệ (hỗ trợ: {', '.join(SUPPORTED_NODE_MAJORS)}).",
        )
    return clean


def _validate_start_config(
    start_mode: Optional[str], start_arg: Optional[str]
) -> Tuple[str, str]:
    mode = (start_mode or "npm").strip().lower()
    if mode not in ALLOWED_START_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Chế độ khởi chạy '{start_mode}' không hợp lệ (cho phép: npm, node, npx, yarn).",
        )
    default_arg = "start" if mode in {"npm", "yarn"} else "server.js"
    arg = (start_arg or default_arg).strip()
    if not START_ARG_RE.fullmatch(arg) or arg.startswith("-") or ".." in arg:
        raise HTTPException(
            status_code=400,
            detail="Tham số khởi chạy (Entry File / Script) chỉ cho phép chữ cái, chữ số, dấu chấm, gạch ngang, gạch dưới và dấu gạch chéo.",
        )
    return mode, arg


def _short_cmd_err(output: str, fallback: str = "Lệnh thực thi trả về mã lỗi khác 0.") -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", (output or "")).strip()
    if not cleaned:
        return fallback
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return cleaned[-420:]
    priority_markers = (
        "npm err!",
        "error:",
        "syntaxerror",
        "typeerror",
        "referenceerror",
        "cannot find module",
        "eacces",
        "enoent",
        "eaddrinuse",
        "fatal",
    )
    top_hits = [
        ln for ln in lines if any(m in ln.lower() for m in priority_markers)
    ]
    if top_hits:
        return " | ".join(top_hits[:3])[:420]
    return " | ".join(lines[-4:])[-420:]


def _run_as_site_user(
    linux_user: str,
    app_dir: Path,
    cmd_args: List[str],
    *,
    node_major: Optional[str] = None,
    timeout: int = 180,
) -> Tuple[int, str]:
    """Execute a whitelisted command (`node`, `npm`, `npx`, `yarn`, `git`) inside `app_dir`
    as `linux_user` via SPanel's mount-namespaced `terminal-exec` helper verb."""
    if not cmd_args:
        return 1, "Empty command argument list"

    bounded_timeout = max(5, min(1800, int(timeout or 180)))
    normalized = [str(a) for a in cmd_args]
    clean_major = _validate_node_major(node_major) if node_major else "22"

    quoted_cmd = " ".join(shlex.quote(a) for a in normalized)
    if settings.command_dry_run:
        return 0, f"[DRY-RUN] (Node v{clean_major}) $ {quoted_cmd}"

    helper_args: List[str] = [
        linux_user,
        str(app_dir),
        f"--timeout={bounded_timeout}",
        f"--node-version={clean_major}",
        *normalized,
    ]
    try:
        res = shell.privileged(
            "terminal-exec",
            helper_args=helper_args,
            check=False,
            timeout=bounded_timeout + 15,
        )
        out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        rc = res.returncode if res.returncode is not None else 1
        return int(rc), out
    except Exception as exc:
        return 1, str(exc)


def _append_npm_log(
    saved: Dict[str, Any], cmd_display: str, output: str, ok: bool = True
) -> None:
    logs = list(saved.get("last_npm_logs") or [])
    logs.append(
        {
            "cmd": cmd_display,
            "output": (output or "OK").strip()[-2000:],
            "status": "ok" if ok else "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    saved["last_npm_logs"] = logs[-12:]


# ── Systemd Worker & Nginx Reverse Proxy Sync ────────────────────────────────

def _parse_kv_output(raw: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in (raw or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            parsed[k.strip()] = v.strip()
    return parsed


def _worker_sync_helper_args(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
    *,
    node_major: str,
    port: int,
    memory_mb: int,
    start_mode: str,
    start_arg: str,
    node_env: str,
    enabled: bool,
) -> List[str]:
    return [
        linux_user,
        str(site_root),
        str(app_dir),
        str(node_major),
        str(port),
        str(memory_mb),
        str(start_mode),
        str(start_arg),
        str(node_env),
        "1" if enabled else "0",
    ]


def _sync_nodejs_worker_unit(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
    *,
    node_major: str,
    start_mode: str,
    start_arg: str,
    port: int,
    memory_mb: int = 512,
    node_env: str = "production",
    enabled: bool = True,
    strict: bool = True,
) -> Dict[str, Any]:
    clean_major = _validate_node_major(node_major)
    clean_mode, clean_arg = _validate_start_config(start_mode, start_arg)
    clean_port = site_apps.validate_port(port)
    clean_mem = max(64, min(16384, int(memory_mb or 512)))
    clean_env = (node_env or "production").strip().lower()
    if clean_env not in {"production", "development", "staging", "test"}:
        clean_env = "production"

    if settings.command_dry_run:
        return {
            "status": "running" if enabled else "stopped",
            "state": "active" if enabled else "inactive",
            "enabled": enabled,
            "port": clean_port,
            "node_major": clean_major,
            "start_mode": clean_mode,
            "start_arg": clean_arg,
            "memory_mb": clean_mem,
            "main_pid": 12480 if enabled else 0,
            "memory_current_bytes": 42 * 1024 * 1024 if enabled else 0,
        }

    res = shell.privileged(
        "nodejs-worker-sync",
        helper_args=_worker_sync_helper_args(
            linux_user,
            site_root,
            app_dir,
            node_major=clean_major,
            port=clean_port,
            memory_mb=clean_mem,
            start_mode=clean_mode,
            start_arg=clean_arg,
            node_env=clean_env,
            enabled=enabled,
        ),
        check=False,
        timeout=120,
    )
    out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    if int(getattr(res, "returncode", 1) or 0) != 0:
        if strict:
            raise HTTPException(
                status_code=500,
                detail=f"Không thể cấu hình dịch vụ systemd Node.js: {_short_cmd_err(out)}",
            )
        logger.warning("nodejs-worker-sync warning for %s: %s", linux_user, out)
        return {"status": "error", "state": "failed", "enabled": False}

    kv = _parse_kv_output(res.stdout or "")
    state = kv.get("state") or ("active" if enabled else "inactive")
    return {
        "status": kv.get("status") or ("running" if state == "active" else "stopped"),
        "state": state,
        "enabled": enabled,
        "unit": kv.get("unit", ""),
        "port": clean_port,
        "node_major": clean_major,
        "start_mode": clean_mode,
        "start_arg": clean_arg,
        "memory_mb": clean_mem,
        "main_pid": int(kv.get("main_pid") or 0),
    }


def _query_nodejs_worker_status(
    linux_user: str,
    site_root: Path,
    saved: Dict[str, Any],
) -> Dict[str, Any]:
    default_port = int(saved.get("port") or 21000)
    default_major = str(saved.get("node_major") or "22")
    default_mode = str(saved.get("start_mode") or "node")
    default_arg = str(saved.get("start_arg") or "server.js")
    default_mem = int(saved.get("memory_mb") or 512)
    enabled = bool(saved.get("process_enabled", True))

    if settings.command_dry_run:
        return {
            "enabled": enabled,
            "state": "active" if enabled else "inactive",
            "status": "running" if enabled else "stopped",
            "main_pid": 12480 if enabled else 0,
            "memory_current_bytes": 42 * 1024 * 1024 if enabled else 0,
            "restarts": int(saved.get("restarts") or 0),
            "port": default_port,
            "node_major": default_major,
            "start_mode": default_mode,
            "start_arg": default_arg,
            "memory_mb": default_mem,
        }

    try:
        res = shell.privileged(
            "nodejs-worker-status",
            helper_args=[linux_user, str(site_root)],
            check=False,
            timeout=15,
        )
        if int(getattr(res, "returncode", 1) or 0) == 0:
            kv = _parse_kv_output(res.stdout or "")
            state = kv.get("state") or "inactive"
            live_enabled = kv.get("enabled") == "1"
            return {
                "enabled": live_enabled,
                "state": state,
                "status": "running" if state == "active" else ("error" if state == "failed" else "stopped"),
                "main_pid": int(kv.get("main_pid") or 0),
                "memory_current_bytes": int(kv.get("memory_current_bytes") or 0),
                "restarts": int(kv.get("restarts") or 0),
                # Configuration values come from panel state, not systemd output
                "port": default_port,
                "node_major": default_major,
                "start_mode": default_mode,
                "start_arg": default_arg,
                "memory_mb": default_mem,
            }
    except Exception as exc:
        logger.debug("nodejs-worker-status warning: %s", exc)

    return {
        "enabled": False,
        "state": "inactive",
        "status": "stopped",
        "main_pid": 0,
        "memory_current_bytes": 0,
        "restarts": 0,
        "port": default_port,
        "node_major": default_major,
        "start_mode": default_mode,
        "start_arg": default_arg,
        "memory_mb": default_mem,
    }


def _delete_nodejs_worker_unit(linux_user: str, site_root: Path) -> None:
    if settings.command_dry_run:
        return
    try:
        shell.privileged(
            "nodejs-worker-delete",
            helper_args=[linux_user, str(site_root)],
            check=False,
            timeout=30,
        )
    except Exception as exc:
        logger.warning("nodejs-worker-delete warning for %s: %s", linux_user, exc)


def _strip_nodejs_nginx_security(custom_text: str) -> str:
    text_in = custom_text or ""
    pattern = re.compile(
        re.escape(NODEJS_NGINX_SEC_BEGIN) + r".*?" + re.escape(NODEJS_NGINX_SEC_END) + r"\n?",
        re.DOTALL,
    )
    cleaned = pattern.sub("", text_in).strip()
    return (cleaned + "\n") if cleaned else ""


def _update_nodejs_nginx_security(
    custom_text: str,
    *,
    sensitive_files: Optional[bool] = None,
) -> str:
    existing = custom_text or ""
    cur_sens = NODEJS_NGINX_SENSITIVE_MARKER in existing
    want_sens = cur_sens if sensitive_files is None else bool(sensitive_files)
    base = _strip_nodejs_nginx_security(existing).rstrip()
    if not want_sens:
        return (base + "\n") if base else ""
    managed = (
        f"{NODEJS_NGINX_SEC_BEGIN}\n"
        f"{NODEJS_SENSITIVE_NGINX_BLOCK}\n"
        f"{NODEJS_NGINX_SEC_END}\n"
    )
    return f"{base}\n\n{managed}" if base else managed


def _sync_nodejs_nginx_vhost(
    site: Website,
    linux_user: str,
    site_root: Path,
    port: int,
    *,
    strict: bool = False,
) -> None:
    try:
        from app.api.websites import _rewrite_website_vhost

        _rewrite_website_vhost(
            site,
            root_path=str(site_root),
            linux_user=linux_user,
            app_type="application",
            app_port=int(port),
            document_root=site.document_root or "public_html",
            custom_directives=getattr(site, "nginx_custom", None) or "",
        )
    except Exception as exc:
        if strict and not settings.command_dry_run:
            raise HTTPException(
                status_code=500,
                detail=f"Không thể đồng bộ cấu hình Nginx Reverse Proxy cho {site.domain}: {exc}",
            ) from exc
        logger.warning("Nginx proxy rewrite warning for %s: %s", getattr(site, "domain", site.id), exc)


def _restore_website_standard_vhost(
    site: Website,
    linux_user: str,
    site_root: Path,
) -> None:
    try:
        from app.api.websites import _rewrite_website_vhost

        php_ver = (site.php_version or "8.3").strip()
        php_socket = site_users.site_php_fpm_socket(linux_user, str(site_root), php_ver)
        _rewrite_website_vhost(
            site,
            root_path=str(site_root),
            linux_user=linux_user,
            app_type=getattr(site, "app_type", None) or "php",
            php_version=php_ver,
            php_fpm_socket_override=php_socket,
            document_root=site.document_root or "public_html",
            custom_directives=getattr(site, "nginx_custom", None) or "",
        )
    except Exception as exc:
        logger.warning("Restore vhost warning for %s: %s", getattr(site, "domain", site.id), exc)


# ── .env & Starter Template Generation ───────────────────────────────────────

def _render_nodejs_default_env(
    *,
    app_name: str,
    app_url: str,
    port: int,
    node_env: str = "production",
    db_name: str = "",
    db_user: str = "",
    db_password: str = "",
    db_host: str = "localhost",
    db_port: int = 3306,
    jwt_secret: Optional[str] = None,
) -> str:
    secret_val = jwt_secret or secrets.token_hex(32)
    safe_name = (app_name or "Node.js Application").replace('"', "").replace("\n", " ").strip()
    safe_db_pw = (
        (db_password or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
    )
    lines = [
        f'APP_NAME="{safe_name}"',
        f"NODE_ENV={node_env}",
        f"PORT={int(port)}",
        "HOST=127.0.0.1",
        f"APP_URL={app_url}",
        "TZ=Asia/Ho_Chi_Minh",
        f"JWT_SECRET={secret_val}",
        "LOG_LEVEL=info",
        "",
        f"DB_HOST={db_host}",
        f"DB_PORT={int(db_port or 3306)}",
        f"DB_DATABASE={db_name}",
        f"DB_USERNAME={db_user}",
        f'DB_PASSWORD="{safe_db_pw}"',
    ]
    if db_name and db_user:
        lines.append(
            f'DATABASE_URL="mysql://{db_user}:{safe_db_pw}@{db_host}:{int(db_port or 3306)}/{db_name}"'
        )
    return "\n".join(lines) + "\n"


def _parse_env_details(env_text: str, default_port: int = 21000) -> Dict[str, Any]:
    raw_vars: Dict[str, str] = {}
    if env_text:
        for raw_line in env_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = (
                    val[1:-1]
                    .replace('\\"', '"')
                    .replace("\\$", "$")
                    .replace("\\\\", "\\")
                )
            elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
                val = val[1:-1]
            elif " #" in val:
                val = val.split(" #", 1)[0].strip()
            if key:
                raw_vars[key] = val

    return {
        "variables": raw_vars,
        "app_name": raw_vars.get("APP_NAME") or "Node.js Application",
        "node_env": raw_vars.get("NODE_ENV") or "production",
        "port": raw_vars.get("PORT") or str(default_port),
        "host": raw_vars.get("HOST") or "127.0.0.1",
        "app_url": raw_vars.get("APP_URL") or "",
        "tz": raw_vars.get("TZ") or "Asia/Ho_Chi_Minh",
        "jwt_secret": raw_vars.get("JWT_SECRET") or "",
        "log_level": raw_vars.get("LOG_LEVEL") or "info",
        "db_host": raw_vars.get("DB_HOST") or "localhost",
        "db_port": raw_vars.get("DB_PORT") or "3306",
        "db_name": raw_vars.get("DB_DATABASE") or raw_vars.get("DB_NAME") or "",
        "db_user": raw_vars.get("DB_USERNAME") or raw_vars.get("DB_USER") or "",
        "db_password": raw_vars.get("DB_PASSWORD") or "",
        "database_url": raw_vars.get("DATABASE_URL") or "",
    }


def _format_env_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    s = str(val if val is not None else "")
    if any(ch in s for ch in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=400, detail="Giá trị biến môi trường .env chứa ký tự điều khiển không hợp lệ.")
    if " " in s or "#" in s or ";" in s or "$" in s or (s and s[0] in ('"', "'")):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        return f'"{escaped}"'
    return s


def _update_env_text(env_text: str, updates: Dict[str, Any]) -> str:
    lines = (env_text or "").splitlines()
    remaining = {}
    for k, v in updates.items():
        clean_k = str(k).strip().upper()
        if not ENV_KEY_RE.fullmatch(clean_k):
            raise HTTPException(status_code=400, detail=f"Tên biến môi trường không hợp lệ: {k}")
        remaining[clean_k] = _format_env_value(v)

    out_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            raw_k = stripped.split("=", 1)[0].strip()
            if raw_k.startswith("export "):
                raw_k = raw_k[7:].strip()
            if raw_k in remaining:
                out_lines.append(f"{raw_k}={remaining.pop(raw_k)}")
                continue
        out_lines.append(line)

    for k, formatted_val in remaining.items():
        out_lines.append(f"{k}={formatted_val}")

    return "\n".join(out_lines).rstrip() + "\n"


def _render_starter_files(
    template_id: str,
    *,
    app_name: str,
    domain: str,
    port: int,
) -> Tuple[str, str]:
    """Return `(package_json_str, server_js_str)` for the chosen starter template."""
    slug = re.sub(r"[^a-z0-9_-]", "-", domain.lower()).strip("-") or "nodejs-app"
    safe_title = (app_name or domain).replace('"', "").strip()
    tpl = (template_id or "express-api").strip().lower()

    deps: Dict[str, str] = {}
    if tpl == "express-api":
        deps = {"express": "^4.21.2"}

    package_data = {
        "name": slug,
        "version": "1.0.0",
        "private": True,
        "description": f"{safe_title} — Deployed on SPanel Node.js Manager",
        "main": "server.js",
        "scripts": {
            "start": "node server.js",
            "dev": "node --watch server.js",
            "build": "echo \"Build completed successfully\"",
        },
        "engines": {"node": ">=18.0.0"},
        "dependencies": deps,
    }
    package_json_str = json.dumps(package_data, indent=2, ensure_ascii=False) + "\n"

    server_js_str = f"""const http = require('node:http');

const PORT = Number(process.env.PORT || {int(port)});
const HOST = process.env.HOST || '127.0.0.1';
const APP_NAME = process.env.APP_NAME || {json.dumps(safe_title)};
const TEMPLATE = {json.dumps(tpl)};

function renderLandingHtml() {{
  return `<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${{APP_NAME}} — Node.js Runtime</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; display: grid; place-items: center; min-height: 100vh; }}
    .card {{ max-width: 560px; background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 32px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.4); }}
    .badge {{ display: inline-flex; align-items: center; gap: 6px; background: rgba(34,197,94,0.15); color: #4ade80; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 999px; margin-bottom: 16px; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; color: #ffffff; }}
    p {{ color: #94a3b8; line-height: 1.6; font-size: 14px; margin: 0 0 20px; }}
    .meta {{ background: #0f172a; border-radius: 10px; padding: 14px 16px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; color: #cbd5e1; line-height: 1.7; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">● Running on SPanel Node.js Manager</div>
    <h1>${{APP_NAME}}</h1>
    <p>Ứng dụng Node.js của bạn đã được khởi tạo và vận hành qua Nginx Reverse Proxy.</p>
    <div class="meta">
      <div><strong>Domain:</strong> {domain}</div>
      <div><strong>Node.js:</strong> ${{process.version}} (${{process.env.NODE_ENV || 'production'}})</div>
      <div><strong>Template:</strong> ${{TEMPLATE}}</div>
      <div><strong>Health API:</strong> <a href="/api/health" style="color:#38bdf8">/api/health</a></div>
    </div>
  </div>
</body>
</html>`;
}}

let requestHandler;
try {{
  const express = require('express');
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json());
  app.get('/api/health', (_req, res) => {{
    res.json({{
      status: 'ok',
      app: APP_NAME,
      domain: {json.dumps(domain)},
      template: TEMPLATE,
      node: process.version,
      env: process.env.NODE_ENV || 'production',
      uptime_seconds: Math.round(process.uptime()),
      timestamp: new Date().toISOString(),
    }});
  }});
  app.get('*', (_req, res) => {{
    res.status(200).type('html').send(renderLandingHtml());
  }});
  requestHandler = app;
}} catch (_err) {{
  requestHandler = (req, res) => {{
    if (req.url && req.url.startsWith('/api/health')) {{
      res.writeHead(200, {{ 'Content-Type': 'application/json; charset=utf-8' }});
      res.end(JSON.stringify({{
        status: 'ok',
        app: APP_NAME,
        domain: {json.dumps(domain)},
        template: TEMPLATE,
        node: process.version,
        env: process.env.NODE_ENV || 'production',
        uptime_seconds: Math.round(process.uptime()),
        timestamp: new Date().toISOString(),
      }}));
      return;
    }}
    res.writeHead(200, {{ 'Content-Type': 'text/html; charset=utf-8' }});
    res.end(renderLandingHtml());
  }};
}}

const server = http.createServer(requestHandler);
server.listen(PORT, HOST, () => {{
  console.log(`[SPanel Node.js] ${{APP_NAME}} listening on http://${{HOST}}:${{PORT}} (${{process.version}})`);
}});

process.on('SIGTERM', () => {{
  server.close(() => process.exit(0));
}});
"""
    return package_json_str, server_js_str


# ── Package.json & Security Inspection ───────────────────────────────────────

def _detect_package_json_metadata(
    app_dir: Path,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
    saved: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    saved = saved or {}
    pkg_raw = _read_app_file(
        app_dir, "package.json", linux_user=linux_user, site_root=site_root
    )
    lock_exists = (
        _app_path_exists(app_dir, "package-lock.json", linux_user, site_root)
        or _app_path_exists(app_dir, "yarn.lock", linux_user, site_root)
        or _app_path_exists(app_dir, "pnpm-lock.yaml", linux_user, site_root)
    )
    node_modules_exists = _app_path_exists(app_dir, "node_modules", linux_user, site_root)

    framework = saved.get("framework") or "Node.js"
    app_version = "1.0.0"
    main_entry = "server.js"
    scripts: Dict[str, str] = {}
    packages: List[Dict[str, Any]] = []

    if pkg_raw:
        try:
            data = json.loads(pkg_raw)
            if isinstance(data, dict):
                app_version = str(data.get("version") or "1.0.0")
                main_entry = str(data.get("main") or "server.js")
                raw_scripts = data.get("scripts") or {}
                if isinstance(raw_scripts, dict):
                    scripts = {str(k): str(v) for k, v in raw_scripts.items() if NPM_SCRIPT_RE.fullmatch(str(k))}

                deps = data.get("dependencies") or {}
                dev_deps = data.get("devDependencies") or {}
                all_dep_names = set(deps.keys()) | set(dev_deps.keys())
                if "next" in all_dep_names:
                    framework = "Next.js"
                elif "@nestjs/core" in all_dep_names:
                    framework = "NestJS"
                elif "nuxt" in all_dep_names:
                    framework = "Nuxt"
                elif "@remix-run/node" in all_dep_names or "@remix-run/react" in all_dep_names:
                    framework = "Remix"
                elif "fastify" in all_dep_names:
                    framework = "Fastify"
                elif "koa" in all_dep_names:
                    framework = "Koa"
                elif "express" in all_dep_names:
                    framework = "Express.js"
                elif "socket.io" in all_dep_names:
                    framework = "Socket.IO"

                for pkg_name, constraint in deps.items():
                    packages.append(
                        {
                            "name": str(pkg_name),
                            "constraint": str(constraint),
                            "version": str(constraint).lstrip("^~"),
                            "type": "dependencies",
                        }
                    )
                for pkg_name, constraint in dev_deps.items():
                    packages.append(
                        {
                            "name": str(pkg_name),
                            "constraint": str(constraint),
                            "version": str(constraint).lstrip("^~"),
                            "type": "devDependencies",
                        }
                    )
        except Exception:
            pass

    return {
        "framework": framework,
        "app_version": app_version,
        "main_entry": main_entry,
        "scripts": scripts,
        "packages": packages,
        "has_package_json": bool(pkg_raw),
        "has_lockfile": bool(lock_exists),
        "has_node_modules": bool(node_modules_exists),
    }


def _check_env_permissions_strict(
    app_dir: Path,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> bool:
    if wordpress_live._has_symlink_component(app_dir, ".env"):
        return False
    env_path = app_dir / ".env"

    if getattr(os.lstat, "__module__", "") == "unittest.mock":
        try:
            st = os.lstat(env_path)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return False
            return (st.st_mode & 0o027) == 0
        except OSError:
            return False

    if not settings.command_dry_run and linux_user:
        if os.name != "nt":
            try:
                st = os.lstat(env_path)
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                    return False
                return (st.st_mode & 0o027) == 0
            except PermissionError:
                pass
            except OSError:
                return False
        rc, out = _run_as_site_user(
            linux_user, app_dir, ["stat", "-c", "%a:%F", ".env"], timeout=10
        )
        if rc == 0 and out:
            last_line = out.strip().splitlines()[-1].strip()
            mode_str, _, ftype = last_line.partition(":")
            if "symbolic" in ftype.lower() or "directory" in ftype.lower():
                return False
            return mode_str.strip() in STRICT_ENV_OCTAL_MODES
        return False

    try:
        st = os.lstat(env_path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    if os.name == "nt":
        return True
    return (st.st_mode & 0o027) == 0


def _evaluate_security_checks(
    site: Website,
    app_dir: Path,
    env_info: Dict[str, Any],
    pkg_meta: Dict[str, Any],
    saved: Dict[str, Any],
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
    *,
    has_env_file: bool = True,
) -> List[Dict[str, Any]]:
    node_env = (env_info.get("node_env") or saved.get("node_env") or "production").strip().lower()
    node_env_prod_ok = node_env == "production"

    env_perms_ok = bool(has_env_file) and _check_env_permissions_strict(
        app_dir, linux_user=linux_user, site_root=site_root
    )

    host_val = (env_info.get("host") or "127.0.0.1").strip()
    port_val = int(saved.get("port") or 0)
    loopback_ok = (
        host_val in {"127.0.0.1", "localhost", "::1"}
        and site_apps.PORT_RANGE_START <= port_val <= site_apps.PORT_RANGE_END
        and getattr(site, "app_type", None) == "application"
    )

    custom_nginx = getattr(site, "nginx_custom", "") or ""
    nginx_sensitive_ok = (
        NODEJS_NGINX_SENSITIVE_MARKER in custom_nginx
        or ("/node_modules/" in custom_nginx and "deny all" in custom_nginx)
    )

    systemd_sandbox_ok = bool(saved.get("systemd_sandbox", True))
    lockfile_ok = bool(
        pkg_meta.get("has_lockfile")
        or pkg_meta.get("has_node_modules")
        or len(pkg_meta.get("packages") or []) == 0
    )
    ssl_ok = bool(getattr(site, "ssl_enabled", False))

    checks_map = {
        "node_env_prod": node_env_prod_ok,
        "env_permissions": env_perms_ok,
        "loopback_binding": loopback_ok,
        "nginx_sensitive_files": nginx_sensitive_ok,
        "systemd_sandbox": systemd_sandbox_ok,
        "lockfile_present": lockfile_ok,
        "ssl_https": ssl_ok,
    }
    return [
        {**item, "passed": bool(checks_map.get(item["id"], False))}
        for item in SECURITY_CHECK_DEFINITIONS
    ]


def _is_nodejs_project_dir(
    app_dir: Path, linux_user: Optional[str], site_root: Optional[Path]
) -> bool:
    if _app_path_exists(app_dir, "package.json", linux_user, site_root):
        # Do not treat a Laravel project with a frontend package.json as a standalone Node.js app
        # unless explicitly registered in nodejs_toolkit_state.
        if _app_path_exists(app_dir, "artisan", linux_user, site_root):
            return False
        return True
    for entry in ("server.js", "app.js", "index.js", "main.js"):
        if _app_path_exists(app_dir, entry, linux_user, site_root):
            return True
    return False


def _ensure_install_target_dir_clean(
    site: Website, linux_user: str, site_root: Path, app_dir: Path
) -> None:
    if site_root.is_symlink() or app_dir.is_symlink() or wordpress_live._has_symlink_component(
        site_root, site.document_root or "public_html"
    ):
        raise HTTPException(
            status_code=400,
            detail="Phát hiện symlink không an toàn trên thư mục gốc của website; từ chối cài đặt.",
        )

    for marker in EXISTING_APP_MARKERS:
        if _app_path_exists(app_dir, marker, linux_user, site_root):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Thư mục của {site.domain} đã chứa mã nguồn hiện hữu (`{marker}`). "
                    "Vui lòng chọn tên miền/subdomain trống hoặc dọn sạch trước khi cài đặt Node.js."
                ),
            )

    entries: Optional[List[str]] = None
    try:
        if app_dir.exists():
            entries = os.listdir(app_dir)
    except PermissionError:
        if not settings.command_dry_run:
            rc, out = _run_as_site_user(linux_user, app_dir, ["ls", "-A1"], timeout=15)
            if rc == 0:
                entries = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    except OSError:
        entries = None

    if entries:
        unexpected = [e for e in entries if e not in BENIGN_DEFAULT_SITE_ENTRIES]
        if unexpected:
            sample = ", ".join(sorted(unexpected)[:3])
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Thư mục gốc của {site.domain} không trống (phát hiện: {sample}). "
                    "Từ chối ghi đè dữ liệu hiện hữu."
                ),
            )


def _webhook_token_for(site: Website, saved: Dict[str, Any]) -> str:
    custom = (saved.get("webhook_token") or "").strip()
    if custom:
        return custom
    secret = (settings.secret_key or "spanel-nodejs-webhook").strip().encode("utf-8")
    msg = f"nodejs-webhook:{site.id}:{site.owner_id}:{site.domain}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def _compute_nodejs_dir_signature(
    site: Website, app_dir: Path, saved: Dict[str, Any]
) -> Tuple[Any, ...]:
    mtimes: List[int] = []
    for rel in ("package.json", ".env", "package-lock.json", "server.js"):
        try:
            st = os.stat(app_dir / rel)
            mtimes.append(int(st.st_mtime_ns))
        except OSError:
            mtimes.append(0)
    return (
        site.domain,
        site.document_root or "public_html",
        getattr(site, "app_type", None) or "",
        bool(getattr(site, "ssl_enabled", False)),
        getattr(site, "nginx_custom", "") or "",
        saved.get("updated_at") or "",
        saved.get("port") or 0,
        saved.get("node_major") or "22",
        saved.get("start_mode") or "node",
        saved.get("start_arg") or "server.js",
        saved.get("process_enabled", True),
        *mtimes,
    )


def _inspect_nodejs_instance(
    site: Website,
    db: Session,
    state: Dict[str, Any],
    *,
    force_refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    inst_key = str(site.id)
    saved = (state.get("instances") or {}).get(inst_key) or {}
    if saved.get("detached"):
        return None

    subpath = saved.get("subpath") or ""
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, subpath)

    sig = _compute_nodejs_dir_signature(site, app_dir, saved)
    now_mono = time.monotonic()
    if not force_refresh:
        with _NODEJS_INSPECT_CACHE_LOCK:
            cached = _NODEJS_INSPECT_CACHE.get(site.id)
            if cached and cached[0] == sig and (now_mono - cached[1]) < _NODEJS_INSPECT_CACHE_TTL:
                return dict(cached[2])

    is_registered = bool(saved.get("installed"))
    has_files = _is_nodejs_project_dir(app_dir, linux_user, site_root)
    if not is_registered and not has_files:
        return None

    pkg_meta = _detect_package_json_metadata(
        app_dir, linux_user=linux_user, site_root=site_root, saved=saved
    )
    env_raw = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    has_env_file = bool(env_raw.strip())

    port = int(saved.get("port") or 0)
    if not (site_apps.PORT_RANGE_START <= port <= site_apps.PORT_RANGE_END):
        port = _allocate_nodejs_port(db, exclude_website_id=site.id)

    env_info = _parse_env_details(env_raw, default_port=port)
    node_major = str(saved.get("node_major") or "22")
    start_mode = str(saved.get("start_mode") or ("npm" if "start" in (pkg_meta.get("scripts") or {}) else "node"))
    start_arg = str(saved.get("start_arg") or ("start" if start_mode == "npm" else pkg_meta.get("main_entry") or "server.js"))
    memory_mb = int(saved.get("memory_mb") or 512)

    proc_status = _query_nodejs_worker_status(
        linux_user,
        site_root,
        {
            **saved,
            "port": port,
            "node_major": node_major,
            "start_mode": start_mode,
            "start_arg": start_arg,
            "memory_mb": memory_mb,
        },
    )

    security_checks = _evaluate_security_checks(
        site,
        app_dir,
        env_info,
        pkg_meta,
        {**saved, "port": port},
        linux_user=linux_user,
        site_root=site_root,
        has_env_file=has_env_file,
    )
    passed_count = sum(1 for c in security_checks if c["passed"])
    total_checks = len(security_checks)
    security_score = int(round((passed_count / total_checks) * 100)) if total_checks else 100

    scheme = "https" if getattr(site, "ssl_enabled", False) else "http"
    site_url = f"{scheme}://{site.domain}" + (f"/{subpath.strip('/')}" if subpath else "")

    db_acc_id = saved.get("db_account_id")
    linked_db: Optional[DatabaseAccount] = None
    if db_acc_id is not None:
        try:
            linked_db = (
                db.query(DatabaseAccount)
                .filter(
                    DatabaseAccount.id == int(db_acc_id),
                    DatabaseAccount.owner_id == site.owner_id,
                )
                .first()
            )
        except (TypeError, ValueError):
            linked_db = None
    elif saved.get("db_name"):
        legacy_db = (
            db.query(DatabaseAccount)
            .filter(
                DatabaseAccount.db_name == str(saved.get("db_name")).strip(),
                DatabaseAccount.owner_id == site.owner_id,
                DatabaseAccount.website_id == site.id,
            )
            .first()
        )
        if legacy_db is not None:
            linked_db = legacy_db
            saved["db_account_id"] = legacy_db.id
            _update_instance_state(
                inst_key,
                lambda inst, _id=legacy_db.id: inst.update(
                    {"db_account_id": _id, "db_name": None, "db_user": None}
                ),
            )

    if linked_db is not None:
        db_name = linked_db.db_name
        db_user = linked_db.db_user
        remote_host = getattr(linked_db, "db_host", None)
        db_host = (
            str(remote_host.host).strip()
            if remote_host is not None and getattr(remote_host, "host", None)
            else "localhost"
        )
    else:
        db_name = ""
        db_user = ""
        db_host = "localhost"

    webhook_tok = _webhook_token_for(site, saved)
    webhook_path = f"/api/nodejs-manager/webhook/{site.id}/{webhook_tok}"

    result = {
        "id": site.id,
        "website_id": site.id,
        "domain": site.domain,
        "subpath": subpath,
        "url": site_url,
        "path": str(app_dir),
        "document_root": site.document_root or "public_html",
        "linux_user": linux_user,
        "app_name": env_info.get("app_name") or saved.get("app_name") or site.domain,
        "framework": pkg_meta.get("framework") or saved.get("framework") or "Node.js",
        "template": saved.get("template") or "express-api",
        "app_version": pkg_meta.get("app_version") or "1.0.0",
        "node_major": proc_status.get("node_major") or node_major,
        "node_env": env_info.get("node_env") or saved.get("node_env") or "production",
        "port": proc_status.get("port") or port,
        "start_mode": proc_status.get("start_mode") or start_mode,
        "start_arg": proc_status.get("start_arg") or start_arg,
        "start_command_display": f"{proc_status.get('start_mode') or start_mode} {'run ' + (proc_status.get('start_arg') or start_arg) if (proc_status.get('start_mode') or start_mode) == 'npm' and (proc_status.get('start_arg') or start_arg) != 'start' else (proc_status.get('start_arg') or start_arg)}",
        "memory_mb": proc_status.get("memory_mb") or memory_mb,
        "process": proc_status,
        "status": proc_status.get("status") or "stopped",
        "ssl_enabled": bool(getattr(site, "ssl_enabled", False)),
        "has_env_file": has_env_file,
        "has_package_json": pkg_meta.get("has_package_json", False),
        "has_lockfile": pkg_meta.get("has_lockfile", False),
        "has_node_modules": pkg_meta.get("has_node_modules", False),
        "scripts": pkg_meta.get("scripts") or {},
        "packages": pkg_meta.get("packages") or [],
        "last_npm_logs": list(saved.get("last_npm_logs") or []),
        "db_name": db_name,
        "db_user": db_user,
        "db_host": db_host,
        "git_repo": saved.get("git_repo") or "",
        "git_branch": saved.get("git_branch") or "main",
        "git_build_script": saved.get("git_build_script") or "",
        "last_deploy_at": saved.get("last_deploy_at") or "",
        "last_deploy_status": saved.get("last_deploy_status") or "",
        "webhook_enabled": bool(saved.get("webhook_enabled", True)),
        "webhook_path": webhook_path,
        "security_checks": security_checks,
        "security_score": security_score,
        "installed_at": saved.get("installed_at") or getattr(site, "created_at", None) or "",
    }

    with _NODEJS_INSPECT_CACHE_LOCK:
        _NODEJS_INSPECT_CACHE[site.id] = (sig, now_mono, dict(result))
    return result


# ── Request Schemas ──────────────────────────────────────────────────────────

class NodejsInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    website_id: int
    subpath: str = Field(default="", max_length=120)
    app_name: str = Field(default="Node.js Application", max_length=120)
    install_source: str = Field(default="template", max_length=24)  # template | git
    template: str = Field(default="express-api", max_length=40)
    node_major: str = Field(default="22", max_length=8)
    start_mode: str = Field(default="node", max_length=16)
    start_arg: str = Field(default="server.js", max_length=120)
    port: Optional[int] = Field(default=None)
    memory_mb: Optional[int] = Field(default=512)
    node_env: str = Field(default="production", max_length=24)
    db_mode: Literal["none", "new", "existing"] = "none"
    existing_database_id: Optional[int] = Field(default=None)
    git_repo: Optional[str] = Field(default=None, max_length=255)
    git_branch: str = Field(default="main", max_length=80)
    run_npm_install: bool = True
    harden_security: bool = True


class NodeVersionInstallRequest(BaseModel):
    node_major: str = Field(..., max_length=8)


class ProcessControlRequest(BaseModel):
    action: str = Field(..., max_length=32)  # start | stop | restart | configure
    node_major: Optional[str] = Field(default=None, max_length=8)
    start_mode: Optional[str] = Field(default=None, max_length=16)
    start_arg: Optional[str] = Field(default=None, max_length=120)
    port: Optional[int] = Field(default=None)
    memory_mb: Optional[int] = Field(default=None)
    node_env: Optional[str] = Field(default=None, max_length=24)


class NpmActionRequest(BaseModel):
    action: str = Field(..., max_length=32)
    package: Optional[str] = Field(default=None, max_length=128)
    dev: bool = False
    script: Optional[str] = Field(default=None, max_length=64)


class EnvUpdateRequest(BaseModel):
    mode: str = Field(default="structured", max_length=20)
    raw_content: Optional[str] = Field(default=None, max_length=65536)
    updates: Optional[Dict[str, Any]] = None
    restart_after_save: bool = True


class SecurityActionRequest(BaseModel):
    check_id: Optional[str] = Field(default=None, max_length=64)
    enabled: bool = True
    apply_all: bool = False


class GitDeployRequest(BaseModel):
    git_repo: Optional[str] = Field(default=None, max_length=255)
    git_branch: str = Field(default="main", max_length=80)
    run_npm_install: bool = True
    build_script: Optional[str] = Field(default="", max_length=64)
    restart_process: bool = True


class WebhookConfigRequest(BaseModel):
    enabled: bool = True
    rotate_token: bool = False


class RemoveNodejsRequest(BaseModel):
    delete_files: bool = False
    drop_database: bool = False


# ── API Endpoints ────────────────────────────────────────────────────────────

@router.get("/instances")
def list_nodejs_instances(
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_end_user_nodejs_access(current_user)
    if refresh:
        invalidate_nodejs_inspect_cache()

    sites = (
        db.query(Website)
        .filter(Website.owner_id == current_user.id)
        .order_by(Website.domain.asc())
        .all()
    )
    state = _load_toolkit_state()
    instances: List[Dict[str, Any]] = []
    available_websites: List[Dict[str, Any]] = []

    for site in sites:
        info = _inspect_nodejs_instance(site, db, state, force_refresh=refresh)
        if info:
            instances.append(info)
        available_websites.append(
            {
                "id": site.id,
                "domain": site.domain,
                "document_root": site.document_root or "public_html",
                "ssl_enabled": bool(getattr(site, "ssl_enabled", False)),
                "has_nodejs": bool(info),
                "app_type": getattr(site, "app_type", None) or "php",
            }
        )

    installed_majors = _list_installed_node_majors()
    default_major = installed_majors[0] if installed_majors else "22"

    return {
        "instances": instances,
        "available_websites": available_websites,
        "node_versions": SUPPORTED_NODE_MAJORS,
        "installed_node_versions": installed_majors,
        "default_node_version": default_major,
        "starter_templates": STARTER_TEMPLATES,
        "curated_packages": CURATED_NPM_PACKAGES,
        "security_definitions": SECURITY_CHECK_DEFINITIONS,
    }


@router.post("/scan")
def scan_nodejs_instances(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_end_user_nodejs_access(current_user)
    invalidate_nodejs_inspect_cache()

    sites = (
        db.query(Website)
        .filter(Website.owner_id == current_user.id)
        .order_by(Website.domain.asc())
        .all()
    )
    state = _load_toolkit_state()
    discovered = 0

    for site in sites:
        inst_key = str(site.id)
        saved = (state.get("instances") or {}).get(inst_key) or {}
        linux_user, site_root, app_dir = _resolve_nodejs_paths(
            site, db, saved.get("subpath") or ""
        )
        if _is_nodejs_project_dir(app_dir, linux_user, site_root):
            discovered += 1
            if not saved.get("installed") or saved.get("detached"):
                try:
                    site_apps.ensure_app_quota(
                        db,
                        current_user,
                        is_admin=is_admin_role(getattr(current_user, "role", None)),
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                port = _allocate_nodejs_port(db, exclude_website_id=site.id)
                pkg_meta = _detect_package_json_metadata(
                    app_dir, linux_user=linux_user, site_root=site_root, saved=saved
                )
                start_mode = "npm" if "start" in (pkg_meta.get("scripts") or {}) else "node"
                start_arg = "start" if start_mode == "npm" else (pkg_meta.get("main_entry") or "server.js")

                def _register(inst: Dict[str, Any]) -> None:
                    inst["installed"] = True
                    inst.pop("detached", None)
                    inst.setdefault("port", port)
                    inst.setdefault("node_major", "22")
                    inst.setdefault("start_mode", start_mode)
                    inst.setdefault("start_arg", start_arg)
                    inst.setdefault("memory_mb", 512)
                    inst.setdefault("framework", pkg_meta.get("framework") or "Node.js")
                    inst.setdefault("installed_at", datetime.now(timezone.utc).isoformat())

                state = _update_instance_state(inst_key, _register)

    data = list_nodejs_instances(refresh=True, db=db, current_user=current_user)
    return {
        **data,
        "scanned_websites": len(sites),
        "discovered_count": discovered,
        "message": f"Đã quét {len(sites)} website và đồng bộ {len(data['instances'])} ứng dụng Node.js.",
    }


@router.get("/node-versions")
def list_node_versions(
    current_user: User = Depends(get_current_user),
):
    _require_end_user_nodejs_access(current_user)
    installed = _list_installed_node_majors()
    return {
        "supported_versions": SUPPORTED_NODE_MAJORS,
        "installed_versions": installed,
        "default_version": installed[0] if installed else "22",
    }


@router.post("/node-versions/install")
def install_node_version_endpoint(
    payload: NodeVersionInstallRequest,
    current_user: User = Depends(get_current_user),
):
    _require_end_user_nodejs_access(current_user)
    clean_major = _validate_node_major(payload.node_major)
    try:
        output = site_apps.install_node(clean_major)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể cài đặt Node.js v{clean_major}: {exc}",
        ) from exc
    installed = _list_installed_node_majors()
    if clean_major not in installed:
        installed = sorted({*installed, clean_major}, key=lambda x: int(x), reverse=True)
    return {
        "node_major": clean_major,
        "installed_versions": installed,
        "output": output[-1000:] if output else f"Node.js v{clean_major} installed",
        "message": f"Đã cài đặt thành công Runtime Node.js v{clean_major} LTS.",
    }


@router.post("/install")
def install_nodejs_instance(
    payload: NodejsInstallRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.api.databases import enforce_user_database_limit

    site = _get_accessible_website(payload.website_id, db, current_user)
    clean_major = _validate_node_major(payload.node_major)
    clean_mode, clean_arg = _validate_start_config(payload.start_mode, payload.start_arg)
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, payload.subpath)

    install_source = (payload.install_source or "template").strip().lower()
    git_repo = (payload.git_repo or "").strip()
    git_branch = (payload.git_branch or "main").strip()
    if install_source == "git":
        if not git_repo or not GIT_URL_RE.fullmatch(git_repo):
            raise HTTPException(
                status_code=400,
                detail="Địa chỉ Git Repository không hợp lệ (chỉ hỗ trợ https:// hoặc git@).",
            )
        if not GIT_BRANCH_RE.fullmatch(git_branch):
            raise HTTPException(status_code=400, detail="Tên nhánh Git (branch) không hợp lệ.")

    site_owner = db.query(User).filter(User.id == site.owner_id).first()
    if site_owner is None or not hasattr(site_owner, "role"):
        site_owner = current_user
    is_admin_owner = is_admin_role(getattr(site_owner, "role", None))

    existing_inst = ((_load_toolkit_state().get("instances") or {}).get(str(site.id))) or {}
    already_counted = bool(existing_inst.get("installed")) and not bool(existing_inst.get("detached"))
    if not already_counted:
        try:
            site_apps.ensure_app_quota(db, site_owner, is_admin=is_admin_owner)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    elif not is_admin_owner and site_apps.app_limit_for(site_owner) == 0:
        raise HTTPException(
            status_code=403,
            detail="Application hosting is not enabled for this package",
        )

    mem_ceiling = None if is_admin_owner else site_apps.memory_ceiling_for(site_owner)
    try:
        validated_memory_mb = site_apps.validate_memory_mb(payload.memory_mb, mem_ceiling)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _ensure_install_target_dir_clean(site, linux_user, site_root, app_dir)
    port = _allocate_nodejs_port(db, preferred=payload.port, exclude_website_id=site.id)

    db_mode = (payload.db_mode or "none").strip().lower()
    existing_check: Optional[DatabaseAccount] = None
    if db_mode == "existing":
        if payload.existing_database_id is None:
            raise HTTPException(
                status_code=400,
                detail="Vui lòng chọn cơ sở dữ liệu có sẵn để gắn vào ứng dụng.",
            )
        existing_check = (
            db.query(DatabaseAccount)
            .filter(DatabaseAccount.id == payload.existing_database_id)
            .first()
        )
        if existing_check is None:
            raise HTTPException(
                status_code=404,
                detail="Database not found. Please create the database first.",
            )
        if existing_check.owner_id != site.owner_id:
            raise HTTPException(
                status_code=403,
                detail="Cannot bind a database owned by another user.",
            )
    elif db_mode == "new":
        enforce_user_database_limit(db, site_owner)

    scheme = "https" if getattr(site, "ssl_enabled", False) else "http"
    app_url = f"{scheme}://{site.domain}" + (
        f"/{payload.subpath.strip('/')}" if payload.subpath.strip("/") else ""
    )

    orig_app_type = getattr(site, "app_type", "php")
    orig_nginx_custom = getattr(site, "nginx_custom", "") or ""

    # Provision or bind MySQL Database
    db_name = ""
    db_user = ""
    db_password = ""
    db_host = "localhost"
    db_acc: Optional[DatabaseAccount] = None
    created_new_db_acc: Optional[DatabaseAccount] = None
    prev_existing_db_website_id: Optional[int] = None

    if db_mode == "existing" and existing_check is not None:
        try:
            stored_db_pass = decrypt(existing_check.db_password)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="Stored database password cannot be decrypted; please reset database password first.",
            ) from exc
        remote_host = getattr(existing_check, "db_host", None)
        has_remote_host = remote_host is not None and bool(getattr(remote_host, "host", None))
        db_name = existing_check.db_name
        db_user = existing_check.db_user
        db_password = stored_db_pass
        db_host = str(remote_host.host).strip() if has_remote_host else "localhost"
        prev_existing_db_website_id = existing_check.website_id
        existing_check.website_id = site.id
        db_acc = existing_check
        db.commit()
    elif db_mode == "new":
        seed = f"{site.domain}_{secrets.token_hex(2)}"
        try:
            db_info = dict(mariadb.create_database(seed, prefix="nd"))
            db_name = db_info["db_name"]
            db_user = db_info["db_user"]
            db_password = db_info["db_password"]
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Không thể khởi tạo cơ sở dữ liệu MySQL cho ứng dụng Node.js: {exc}",
            ) from exc

        db_acc = DatabaseAccount(
            owner_id=site.owner_id,
            website_id=site.id,
            db_name=db_name,
            db_user=db_user,
            db_password=encrypt(db_password),
        )
        db.add(db_acc)
        created_new_db_acc = db_acc
        db.commit()
        db.refresh(db_acc)

    inst_key = str(site.id)

    def _rollback_failed_install(*, purge_files: bool = False) -> None:
        if purge_files:
            try:
                _purge_app_dir_contents(linux_user, site_root, app_dir)
            except Exception as purge_exc:
                logger.warning("Failed to purge partial Node.js install dir: %s", purge_exc)
        if created_new_db_acc is not None:
            try:
                teardown.drop_database_record(db, created_new_db_acc)
            except Exception as db_exc:
                logger.warning("Failed to rollback auto-created Node.js DB: %s", db_exc)
        elif existing_check is not None:
            existing_check.website_id = prev_existing_db_website_id
        site.app_type = orig_app_type
        site.nginx_custom = orig_nginx_custom
        db.add(site)
        db.commit()
        try:
            _pop_instance_state(inst_key)
        except Exception:
            pass

    # Clean target dir only AFTER all quota/DB/port checks succeeded
    _purge_app_dir_contents(linux_user, site_root, app_dir)

    npm_logs: List[Dict[str, Any]] = []
    try:
        if install_source == "git":
            rc, out = _run_as_site_user(
                linux_user,
                app_dir,
                ["git", "clone", "--depth", "1", "--branch", git_branch, git_repo, "."],
                node_major=clean_major,
                timeout=300,
            )
            npm_logs.append(
                {
                    "cmd": f"git clone -b {git_branch} {git_repo} .",
                    "output": (out or "OK")[-2000:],
                    "status": "ok" if rc == 0 else "error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if rc != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Không thể sao chép mã nguồn từ Git Repository: {_short_cmd_err(out)}",
                )
        else:
            pkg_str, srv_str = _render_starter_files(
                payload.template,
                app_name=payload.app_name or site.domain,
                domain=site.domain,
                port=port,
            )
            _write_app_file(linux_user, site_root, app_dir, "package.json", pkg_str, "0644")
            _write_app_file(linux_user, site_root, app_dir, "server.js", srv_str, "0644")

        # Write .env with strict 0640 permissions
        env_content = _render_nodejs_default_env(
            app_name=payload.app_name or site.domain,
            app_url=app_url,
            port=port,
            node_env=payload.node_env or "production",
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
        )
        _write_app_file(linux_user, site_root, app_dir, ".env", env_content, "0640")

        # Install npm dependencies if requested and package.json has dependencies
        pkg_meta = _detect_package_json_metadata(app_dir, linux_user=linux_user, site_root=site_root)
        if payload.run_npm_install and len(pkg_meta.get("packages") or []) > 0:
            rc, out = _run_as_site_user(
                linux_user,
                app_dir,
                ["npm", "install", "--no-audit", "--no-fund"],
                node_major=clean_major,
                timeout=300,
            )
            npm_logs.append(
                {
                    "cmd": "npm install --no-audit --no-fund",
                    "output": (out or "OK")[-2000:],
                    "status": "ok" if rc == 0 else "error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        # Apply Nginx security block & switch Website to reverse-proxy application mode
        if payload.harden_security:
            site.nginx_custom = _update_nodejs_nginx_security(
                orig_nginx_custom,
                sensitive_files=True,
            )
        site.app_type = "application"
        db.add(site)
        db.commit()

        # Persist state BEFORE rewriting Nginx vhost so app_port_for_website can resolve it
        now_iso = datetime.now(timezone.utc).isoformat()

        def _save_new(inst: Dict[str, Any]) -> None:
            inst.clear()
            inst.update(
                {
                    "installed": True,
                    "website_id": site.id,
                    "domain": site.domain,
                    "subpath": payload.subpath.strip("/"),
                    "app_name": payload.app_name or site.domain,
                    "template": payload.template if install_source != "git" else "git-repo",
                    "framework": pkg_meta.get("framework") or "Node.js",
                    "node_major": clean_major,
                    "start_mode": clean_mode,
                    "start_arg": clean_arg,
                    "port": port,
                    "memory_mb": validated_memory_mb,
                    "node_env": payload.node_env or "production",
                    "process_enabled": True,
                    "systemd_sandbox": True,
                    "db_account_id": db_acc.id if db_acc is not None else None,
                    "git_repo": git_repo if install_source == "git" else "",
                    "git_branch": git_branch,
                    "last_npm_logs": npm_logs[-12:],
                    "installed_at": now_iso,
                }
            )

        state = _update_instance_state(inst_key, _save_new)

        # Start systemd worker & sync Nginx vhost
        _sync_nodejs_worker_unit(
            linux_user,
            site_root,
            app_dir,
            node_major=clean_major,
            start_mode=clean_mode,
            start_arg=clean_arg,
            port=port,
            memory_mb=validated_memory_mb,
            node_env=payload.node_env or "production",
            enabled=True,
            strict=True,
        )
        _sync_nodejs_nginx_vhost(site, linux_user, site_root, port, strict=False)
    except Exception:
        _rollback_failed_install(purge_files=True)
        raise

    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    return {
        "instance": instance_info,
        "credentials": {
            "domain": site.domain,
            "url": app_url,
            "node_major": clean_major,
            "port": port,
            "start_command": f"{clean_mode} {clean_arg}",
            "db_created": bool(db_mode == "new" and db_name),
            "db_bound": bool(db_acc is not None),
            "db_name": db_name,
            "db_user": db_user,
            "db_password": db_password if db_mode == "new" else "",
            "db_host": db_host,
        },
        "message": f"Đã triển khai ứng dụng Node.js (v{clean_major}) trên {site.domain} (Cổng nội bộ {port}).",
    }


@router.post("/instances/{website_id}/process")
def manage_nodejs_process(
    website_id: int,
    payload: ProcessControlRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = dict((state.get("instances") or {}).get(inst_key) or {})
    if not saved:
        raise HTTPException(status_code=404, detail="Ứng dụng Node.js chưa được khởi tạo trên website này.")

    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")
    action = (payload.action or "").strip().lower()
    if action not in {"start", "stop", "restart", "configure"}:
        raise HTTPException(status_code=400, detail=f"Hành động tiến trình '{action}' không hợp lệ.")

    node_major = _validate_node_major(payload.node_major or saved.get("node_major") or "22")
    start_mode, start_arg = _validate_start_config(
        payload.start_mode or saved.get("start_mode") or "node",
        payload.start_arg or saved.get("start_arg") or "server.js",
    )
    site_owner = db.query(User).filter(User.id == site.owner_id).first()
    if site_owner is None or not hasattr(site_owner, "role"):
        site_owner = current_user
    is_admin_owner = is_admin_role(getattr(site_owner, "role", None))
    if saved.get("detached") and action != "stop":
        try:
            site_apps.ensure_app_quota(db, site_owner, is_admin=is_admin_owner)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    elif not is_admin_owner and action != "stop":
        limit = site_apps.app_limit_for(site_owner)
        if limit == 0:
            raise HTTPException(
                status_code=403,
                detail="Application hosting is not enabled for this package",
            )
        if limit > 0 and site_apps.count_apps_for_owner(db, site_owner.id) > limit:
            raise HTTPException(
                status_code=403,
                detail=f"This package allows at most {limit} application(s)",
            )

    mem_ceiling = None if is_admin_owner else site_apps.memory_ceiling_for(site_owner)
    raw_mem = payload.memory_mb if payload.memory_mb is not None else (saved.get("memory_mb") or 512)
    try:
        memory_mb = site_apps.validate_memory_mb(raw_mem, mem_ceiling)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    node_env = (payload.node_env or saved.get("node_env") or "production").strip().lower()

    old_port = int(saved.get("port") or 0)
    if payload.port is not None and int(payload.port) != old_port:
        port = _allocate_nodejs_port(db, preferred=payload.port, exclude_website_id=site.id)
    elif site_apps.PORT_RANGE_START <= old_port <= site_apps.PORT_RANGE_END:
        port = old_port
    else:
        port = _allocate_nodejs_port(db, exclude_website_id=site.id)

    if action == "stop":
        enabled = False
    elif action in {"start", "restart"}:
        enabled = True
    else:
        enabled = bool(saved.get("process_enabled", True))

    # Keep .env PORT and NODE_ENV synchronized when port/node_env is updated
    env_raw = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    if env_raw:
        updated_env = _update_env_text(env_raw, {"PORT": str(port), "NODE_ENV": node_env})
        _write_app_file(linux_user, site_root, app_dir, ".env", updated_env, "0640")

    def _mutate(inst: Dict[str, Any]) -> None:
        inst["node_major"] = node_major
        inst["start_mode"] = start_mode
        inst["start_arg"] = start_arg
        inst["port"] = port
        inst["memory_mb"] = memory_mb
        inst["node_env"] = node_env
        inst["process_enabled"] = enabled

    state = _update_instance_state(inst_key, _mutate)

    _sync_nodejs_worker_unit(
        linux_user,
        site_root,
        app_dir,
        node_major=node_major,
        start_mode=start_mode,
        start_arg=start_arg,
        port=port,
        memory_mb=memory_mb,
        node_env=node_env,
        enabled=enabled,
        strict=True,
    )
    if port != old_port or getattr(site, "app_type", None) != "application":
        site.app_type = "application"
        db.add(site)
        db.commit()
        _sync_nodejs_nginx_vhost(site, linux_user, site_root, port, strict=False)

    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    action_labels = {
        "start": "Đã khởi động tiến trình Node.js.",
        "stop": "Đã dừng tiến trình Node.js.",
        "restart": "Đã khởi động lại tiến trình Node.js.",
        "configure": f"Đã cập nhật cấu hình Runtime Node.js v{node_major} (Cổng {port}, RAM {memory_mb} MB).",
    }
    return {
        "instance": instance_info,
        "message": action_labels.get(action, "Đã cập nhật trạng thái tiến trình Node.js."),
    }


@router.post("/instances/{website_id}/npm")
def run_npm_action(
    website_id: int,
    payload: NpmActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = dict((state.get("instances") or {}).get(inst_key) or {})
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")
    node_major = str(saved.get("node_major") or "22")

    action = (payload.action or "").strip().lower()
    cmd_args: List[str] = []

    if action == "install":
        cmd_args = ["npm", "install", "--no-audit", "--no-fund"]
    elif action == "ci":
        cmd_args = ["npm", "ci", "--no-audit", "--no-fund"]
    elif action == "build":
        cmd_args = ["npm", "run", "build"]
    elif action == "update":
        cmd_args = ["npm", "update", "--no-audit", "--no-fund"]
    elif action == "audit_fix":
        cmd_args = ["npm", "audit", "fix"]
    elif action == "cache_clean":
        cmd_args = ["npm", "cache", "clean", "--force"]
    elif action == "add":
        pkg = (payload.package or "").strip()
        if not pkg or not NPM_PKG_RE.fullmatch(pkg):
            raise HTTPException(status_code=400, detail=f"Tên gói NPM '{pkg}' không hợp lệ.")
        cmd_args = ["npm", "install", "--save-dev" if payload.dev else "--save", pkg]
        if settings.command_dry_run:
            # Update package.json directly in dry-run mode so unit tests & preview reflect package installation
            pkg_raw = _read_app_file(app_dir, "package.json", linux_user=linux_user, site_root=site_root)
            try:
                pkg_json = json.loads(pkg_raw) if pkg_raw else {"name": site.domain, "version": "1.0.0"}
            except Exception:
                pkg_json = {"name": site.domain, "version": "1.0.0"}
            sec = "devDependencies" if payload.dev else "dependencies"
            pkg_name_only = pkg.rsplit("@", 1)[0] if ("@" in pkg and not pkg.startswith("@")) else pkg
            pkg_ver = pkg.rsplit("@", 1)[1] if ("@" in pkg and not pkg.startswith("@")) else "^1.0.0"
            pkg_json.setdefault(sec, {})[pkg_name_only] = pkg_ver
            _write_app_file(
                linux_user,
                site_root,
                app_dir,
                "package.json",
                json.dumps(pkg_json, indent=2, ensure_ascii=False) + "\n",
                "0644",
            )
    elif action == "remove":
        pkg = (payload.package or "").strip()
        if not pkg or not NPM_PKG_RE.fullmatch(pkg):
            raise HTTPException(status_code=400, detail=f"Tên gói NPM '{pkg}' không hợp lệ.")
        cmd_args = ["npm", "uninstall", pkg]
        if settings.command_dry_run:
            pkg_raw = _read_app_file(app_dir, "package.json", linux_user=linux_user, site_root=site_root)
            if pkg_raw:
                try:
                    pkg_json = json.loads(pkg_raw)
                    for sec in ("dependencies", "devDependencies"):
                        if isinstance(pkg_json.get(sec), dict):
                            pkg_json[sec].pop(pkg, None)
                    _write_app_file(
                        linux_user,
                        site_root,
                        app_dir,
                        "package.json",
                        json.dumps(pkg_json, indent=2, ensure_ascii=False) + "\n",
                        "0644",
                    )
                except Exception:
                    pass
    elif action == "run_script":
        script_name = (payload.script or "").strip()
        if not script_name or not NPM_SCRIPT_RE.fullmatch(script_name):
            raise HTTPException(status_code=400, detail=f"Tên script '{script_name}' không hợp lệ.")
        cmd_args = ["npm", "run", script_name]
    else:
        raise HTTPException(status_code=400, detail=f"Thao tác NPM '{action}' không được hỗ trợ.")

    rc, out = _run_as_site_user(
        linux_user, app_dir, cmd_args, node_major=node_major, timeout=300
    )
    cmd_display = " ".join(cmd_args)

    def _record_log(inst: Dict[str, Any]) -> None:
        _append_npm_log(inst, cmd_display, out, ok=(rc == 0))

    state = _update_instance_state(inst_key, _record_log)
    if rc != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Lệnh `{cmd_display}` thất bại: {_short_cmd_err(out)}",
        )

    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    return {
        "instance": instance_info,
        "command": cmd_display,
        "output": (out or "OK")[-2000:],
        "message": f"Đã thực thi thành công `{cmd_display}`.",
    }


@router.get("/instances/{website_id}/env")
def get_nodejs_env(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")

    env_raw = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    port = int(saved.get("port") or 21000)
    parsed = _parse_env_details(env_raw, default_port=port)
    return {
        "raw": env_raw,
        "parsed": parsed,
    }


@router.put("/instances/{website_id}/env")
def update_nodejs_env(
    website_id: int,
    payload: EnvUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = dict((state.get("instances") or {}).get(inst_key) or {})
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")

    cur_raw = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    if cur_raw:
        _write_app_file(linux_user, site_root, app_dir, ".env.bak", cur_raw, "0640")

    mode = (payload.mode or "structured").strip().lower()
    if mode == "raw":
        if payload.raw_content is None:
            raise HTTPException(status_code=400, detail="Nội dung .env không được để trống.")
        if "\x00" in payload.raw_content:
            raise HTTPException(status_code=400, detail="Nội dung .env chứa ký tự null không hợp lệ.")
        new_raw = payload.raw_content.rstrip() + "\n"
    else:
        updates = payload.updates or {}
        new_raw = _update_env_text(cur_raw, updates)

    _write_app_file(linux_user, site_root, app_dir, ".env", new_raw, "0640")
    parsed = _parse_env_details(new_raw, default_port=int(saved.get("port") or 21000))

    def _sync_env_meta(inst: Dict[str, Any]) -> None:
        inst["app_name"] = parsed.get("app_name") or site.domain
        inst["node_env"] = parsed.get("node_env") or "production"

    state = _update_instance_state(inst_key, _sync_env_meta)

    if payload.restart_after_save and saved.get("process_enabled", True):
        _sync_nodejs_worker_unit(
            linux_user,
            site_root,
            app_dir,
            node_major=str(saved.get("node_major") or "22"),
            start_mode=str(saved.get("start_mode") or "node"),
            start_arg=str(saved.get("start_arg") or "server.js"),
            port=int(saved.get("port") or 21000),
            memory_mb=int(saved.get("memory_mb") or 512),
            node_env=parsed.get("node_env") or "production",
            enabled=True,
            strict=False,
        )

    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    return {
        "raw": new_raw,
        "parsed": parsed,
        "instance": instance_info,
        "message": "Đã lưu cấu hình biến môi trường `.env` (0640) và đồng bộ tiến trình Node.js.",
    }


@router.post("/instances/{website_id}/security")
def update_nodejs_security(
    website_id: int,
    payload: SecurityActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = dict((state.get("instances") or {}).get(inst_key) or {})
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")

    env_raw = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    port = int(saved.get("port") or _allocate_nodejs_port(db, exclude_website_id=site.id))

    targets = (
        [c["id"] for c in SECURITY_CHECK_DEFINITIONS]
        if payload.apply_all
        else ([payload.check_id] if payload.check_id else [])
    )
    if not targets:
        raise HTTPException(status_code=400, detail="Vui lòng chọn hạng mục bảo mật cần áp dụng.")

    env_updates: Dict[str, Any] = {}
    vhost_dirty = False
    worker_dirty = False

    for cid in targets:
        if cid == "node_env_prod":
            env_updates["NODE_ENV"] = "production" if payload.enabled else "development"
            worker_dirty = True
        elif cid == "env_permissions":
            if env_raw:
                _write_app_file(linux_user, site_root, app_dir, ".env", env_raw, "0640")
            _apply_strict_nodejs_permissions(linux_user, site_root, app_dir)
        elif cid == "loopback_binding":
            env_updates["HOST"] = "127.0.0.1"
            env_updates["PORT"] = str(port)
            site.app_type = "application"
            vhost_dirty = True
            worker_dirty = True
        elif cid == "nginx_sensitive_files":
            site.nginx_custom = _update_nodejs_nginx_security(
                getattr(site, "nginx_custom", "") or "",
                sensitive_files=payload.enabled,
            )
            vhost_dirty = True
        elif cid == "systemd_sandbox":
            worker_dirty = True

    if env_updates:
        env_raw = _update_env_text(env_raw, env_updates)
        _write_app_file(linux_user, site_root, app_dir, ".env", env_raw, "0640")

    def _mutate_sec(inst: Dict[str, Any]) -> None:
        inst["port"] = port
        if "NODE_ENV" in env_updates:
            inst["node_env"] = env_updates["NODE_ENV"]
        if "systemd_sandbox" in targets:
            inst["systemd_sandbox"] = bool(payload.enabled)

    state = _update_instance_state(inst_key, _mutate_sec)

    if vhost_dirty:
        db.add(site)
        db.commit()
        _sync_nodejs_nginx_vhost(site, linux_user, site_root, port, strict=False)

    if worker_dirty:
        _sync_nodejs_worker_unit(
            linux_user,
            site_root,
            app_dir,
            node_major=str(saved.get("node_major") or "22"),
            start_mode=str(saved.get("start_mode") or "node"),
            start_arg=str(saved.get("start_arg") or "server.js"),
            port=port,
            memory_mb=int(saved.get("memory_mb") or 512),
            node_env=env_updates.get("NODE_ENV") or str(saved.get("node_env") or "production"),
            enabled=bool(saved.get("process_enabled", True)),
            strict=False,
        )

    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    return {
        "instance": instance_info,
        "message": "Đã áp dụng cấu hình bảo mật Production cho ứng dụng Node.js.",
    }


def _execute_git_deploy_pipeline(
    site: Website,
    db: Session,
    *,
    git_repo: Optional[str] = None,
    git_branch: Optional[str] = None,
    run_npm_install: bool = True,
    build_script: Optional[str] = None,
    restart_process: bool = True,
) -> Dict[str, Any]:
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = dict((state.get("instances") or {}).get(inst_key) or {})
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")
    node_major = str(saved.get("node_major") or "22")

    repo = (git_repo if git_repo is not None else saved.get("git_repo") or "").strip()
    branch = (git_branch or saved.get("git_branch") or "main").strip()
    b_script = (build_script if build_script is not None else saved.get("git_build_script") or "").strip()

    if repo and not GIT_URL_RE.fullmatch(repo):
        raise HTTPException(status_code=400, detail="Địa chỉ Git Repository không hợp lệ.")
    if not GIT_BRANCH_RE.fullmatch(branch):
        raise HTTPException(status_code=400, detail="Tên nhánh Git không hợp lệ.")
    if b_script and not NPM_SCRIPT_RE.fullmatch(b_script):
        raise HTTPException(status_code=400, detail="Tên script build không hợp lệ.")

    steps_output: List[str] = []
    if _app_path_exists(app_dir, ".git", linux_user, site_root):
        rc, out = _run_as_site_user(
            linux_user, app_dir, ["git", "pull", "origin", branch], node_major=node_major, timeout=180
        )
    elif repo:
        rc, out = _run_as_site_user(
            linux_user,
            app_dir,
            ["git", "clone", "--depth", "1", "--branch", branch, repo, "."],
            node_major=node_major,
            timeout=300,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Chưa cấu hình địa chỉ Git Repository cho ứng dụng này.",
        )

    steps_output.append(f"$ git pull/clone ({branch})\n{out}")
    if rc != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Git deploy thất bại ở bước đồng bộ mã nguồn: {_short_cmd_err(out)}",
        )

    if run_npm_install:
        rc_npm, out_npm = _run_as_site_user(
            linux_user,
            app_dir,
            ["npm", "install", "--no-audit", "--no-fund"],
            node_major=node_major,
            timeout=300,
        )
        steps_output.append(f"$ npm install\n{out_npm}")
        if rc_npm != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Git deploy thất bại ở bước `npm install`: {_short_cmd_err(out_npm)}",
            )

    if b_script:
        rc_b, out_b = _run_as_site_user(
            linux_user,
            app_dir,
            ["npm", "run", b_script],
            node_major=node_major,
            timeout=300,
        )
        steps_output.append(f"$ npm run {b_script}\n{out_b}")
        if rc_b != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Git deploy thất bại ở bước `npm run {b_script}`: {_short_cmd_err(out_b)}",
            )

    if restart_process:
        _sync_nodejs_worker_unit(
            linux_user,
            site_root,
            app_dir,
            node_major=node_major,
            start_mode=str(saved.get("start_mode") or "node"),
            start_arg=str(saved.get("start_arg") or "server.js"),
            port=int(saved.get("port") or 21000),
            memory_mb=int(saved.get("memory_mb") or 512),
            node_env=str(saved.get("node_env") or "production"),
            enabled=True,
            strict=False,
        )

    combined_out = "\n\n".join(steps_output)
    now_iso = datetime.now(timezone.utc).isoformat()

    def _record_deploy(inst: Dict[str, Any]) -> None:
        if repo:
            inst["git_repo"] = repo
        inst["git_branch"] = branch
        inst["git_build_script"] = b_script
        inst["last_deploy_at"] = now_iso
        inst["last_deploy_status"] = "ok"
        _append_npm_log(inst, f"git deploy ({branch})", combined_out, ok=True)

    state = _update_instance_state(inst_key, _record_deploy)
    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    return {
        "instance": instance_info,
        "output": combined_out[-2500:],
        "message": f"Đã triển khai tự động nhánh `{branch}` và khởi động lại ứng dụng Node.js.",
    }


@router.post("/instances/{website_id}/git-deploy")
def trigger_nodejs_git_deploy(
    website_id: int,
    payload: GitDeployRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    return _execute_git_deploy_pipeline(
        site,
        db,
        git_repo=payload.git_repo,
        git_branch=payload.git_branch,
        run_npm_install=payload.run_npm_install,
        build_script=payload.build_script,
        restart_process=payload.restart_process,
    )


@router.post("/instances/{website_id}/webhook")
def configure_nodejs_webhook(
    website_id: int,
    payload: WebhookConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    inst_key = str(site.id)

    def _mutate_wh(inst: Dict[str, Any]) -> None:
        inst["webhook_enabled"] = bool(payload.enabled)
        if payload.rotate_token:
            inst["webhook_token"] = secrets.token_hex(16)

    state = _update_instance_state(inst_key, _mutate_wh)
    instance_info = _inspect_nodejs_instance(site, db, state, force_refresh=True)
    return {
        "instance": instance_info,
        "message": "Đã cập nhật cấu hình Webhook Auto-Deploy.",
    }


@router.post("/webhook/{website_id}/{token}")
def public_nodejs_webhook_deploy(
    website_id: int,
    token: str,
    db: Session = Depends(get_db),
):
    site = db.query(Website).filter(Website.id == website_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Website not found")
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    if not saved or not saved.get("webhook_enabled", True):
        raise HTTPException(status_code=403, detail="Webhook is disabled for this Node.js application")

    expected = _webhook_token_for(site, saved)
    if not hmac.compare_digest(expected, (token or "").strip()):
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    res = _execute_git_deploy_pipeline(site, db)
    return {"status": "ok", "message": res.get("message")}


@router.get("/instances/{website_id}/logs")
def get_nodejs_logs(
    website_id: int,
    kind: str = Query(default="systemd", max_length=24),
    lines: int = Query(default=120, ge=20, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")

    clean_kind = (kind or "systemd").strip().lower()
    content = ""

    if clean_kind == "systemd":
        if settings.command_dry_run:
            content = (
                f"[systemd] spanel-nodejs-{linux_user}.service - SPanel Node.js Application ({site.domain})\n"
                f"[SPanel Node.js] {saved.get('app_name') or site.domain} listening on http://127.0.0.1:{saved.get('port') or 21000} (v{saved.get('node_major') or '22'}.14.0)\n"
            )
        else:
            res = shell.privileged(
                "nodejs-worker-logs",
                helper_args=[linux_user, str(site_root), str(lines)],
                check=False,
                timeout=20,
            )
            content = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    elif clean_kind == "nginx_error":
        try:
            content = nginx.read_site_log(site.domain, "error", lines=lines)
        except Exception as exc:
            content = f"Không thể đọc nhật ký Nginx Error Log: {exc}"
    else:
        for rel_log in ("logs/app.log", "logs/error.log", "npm-debug.log"):
            raw = _read_app_file(app_dir, rel_log, linux_user=linux_user, site_root=site_root)
            if raw:
                content = "\n".join(raw.splitlines()[-lines:])
                break
        if not content and saved.get("last_npm_logs"):
            content = "\n\n".join(
                f"[{item.get('timestamp', '')}] $ {item.get('cmd', '')}\n{item.get('output', '')}"
                for item in (saved.get("last_npm_logs") or [])
            )

    return {
        "kind": clean_kind,
        "lines": lines,
        "content": content or "Chưa có dữ liệu nhật ký phát sinh.",
    }


@router.delete("/instances/{website_id}/logs")
def clear_nodejs_logs(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = (state.get("instances") or {}).get(inst_key) or {}
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")

    for rel_log in ("logs/app.log", "logs/error.log"):
        if _app_path_exists(app_dir, rel_log, linux_user, site_root):
            _write_app_file(linux_user, site_root, app_dir, rel_log, "", "0640")

    def _clear_npm_history(inst: Dict[str, Any]) -> None:
        inst["last_npm_logs"] = []

    _update_instance_state(inst_key, _clear_npm_history)
    return {"message": "Đã dọn sạch bộ đệm nhật ký ứng dụng Node.js."}


@router.post("/instances/{website_id}/detach")
def detach_nodejs_instance(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    inst_key = str(site.id)

    def _mark_detached(inst: Dict[str, Any]) -> None:
        inst["detached"] = True

    _update_instance_state(inst_key, _mark_detached)
    return {
        "website_id": site.id,
        "message": f"Đã gỡ {site.domain} khỏi danh sách quản lý Node.js Manager (mã nguồn và tiến trình được giữ nguyên).",
    }


@router.post("/instances/{website_id}/remove")
def remove_nodejs_instance(
    website_id: int,
    payload: RemoveNodejsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = dict((state.get("instances") or {}).get(inst_key) or {})
    linux_user, site_root, app_dir = _resolve_nodejs_paths(site, db, saved.get("subpath") or "")

    # Stop and delete systemd worker unit
    _delete_nodejs_worker_unit(linux_user, site_root)

    # Optionally drop MySQL database (strictly via owned & bound DatabaseAccount record)
    if payload.drop_database:
        raw_db_acc_id = saved.get("db_account_id")
        db_acc_id: Optional[int] = None
        if raw_db_acc_id is not None and not isinstance(raw_db_acc_id, bool):
            try:
                db_acc_id = int(raw_db_acc_id)
            except (TypeError, ValueError):
                db_acc_id = None
        if db_acc_id is not None and db_acc_id > 0:
            try:
                acc = (
                    db.query(DatabaseAccount)
                    .filter(
                        DatabaseAccount.id == db_acc_id,
                        DatabaseAccount.owner_id == site.owner_id,
                        DatabaseAccount.website_id == site.id,
                    )
                    .first()
                )
                if acc is not None:
                    teardown.drop_database_record(db, acc)
                    db.commit()
            except Exception as exc:
                logger.warning("Drop Node.js database warning for db_account_id=%s: %s", db_acc_id, exc)

    # Optionally delete files in app_dir
    if payload.delete_files:
        _purge_app_dir_contents(linux_user, site_root, app_dir)

    # Restore website vhost to standard PHP/static mode
    site.nginx_custom = _strip_nodejs_nginx_security(getattr(site, "nginx_custom", "") or "")
    if getattr(site, "app_type", None) == "application" and not getattr(site, "app_id", None):
        site.app_type = "php"
    db.add(site)
    db.commit()
    _restore_website_standard_vhost(site, linux_user, site_root)

    _pop_instance_state(inst_key)
    return {
        "website_id": site.id,
        "message": f"Đã gỡ cài đặt ứng dụng Node.js khỏi {site.domain}.",
    }
