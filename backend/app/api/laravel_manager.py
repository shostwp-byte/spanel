import base64
import contextvars
import json
import logging
import os
import re
import secrets
import shlex
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import engine, get_db
from app.core.permissions import Role, is_admin_role, normalize_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, SiteApp, User, Website
from app.services import cron, mariadb, nginx, php, site_users, teardown, wordpress_live
from app.services.shell import shell

_LARAVEL_ACCESS_ROLE_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "laravel_access_role", default=""
)


def _capture_laravel_access_role(request: Request):
    token = _LARAVEL_ACCESS_ROLE_CTX.set(
        (request.headers.get("x-access-role") or "").strip().lower()
    )
    try:
        yield
    finally:
        _LARAVEL_ACCESS_ROLE_CTX.reset(token)


router = APIRouter(
    prefix="/laravel-manager",
    tags=["laravel-manager"],
    dependencies=[Depends(_capture_laravel_access_role)],
)
logger = logging.getLogger("spanel.laravel_manager")

_LARAVEL_STATE_LOCK = threading.RLock()

SUBPATH_RE = re.compile(r"^[a-zA-Z0-9._/-]{0,120}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
COMPOSER_PKG_RE = re.compile(
    r"^[a-z0-9][a-z0-9_.-]{0,63}/[a-z0-9][a-z0-9_.-]{0,63}(?::[a-zA-Z0-9.*^~<>=-]{1,32})?$"
)
GIT_URL_RE = re.compile(r"^(?:https://|git@)[a-zA-Z0-9._:/-]+$")
GIT_BRANCH_RE = re.compile(r"^[a-zA-Z0-9._/-]{1,80}$")
MAINTENANCE_SECRET_RE = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")

ALLOWED_ARTISAN_COMMANDS: Dict[str, List[str]] = {
    "migrate": ["migrate", "--force", "--no-interaction"],
    "migrate:status": ["migrate:status", "--no-interaction"],
    "db:seed": ["db:seed", "--force", "--no-interaction"],
    "optimize": ["optimize", "--no-interaction"],
    "optimize:clear": ["optimize:clear", "--no-interaction"],
    "config:cache": ["config:cache", "--no-interaction"],
    "config:clear": ["config:clear", "--no-interaction"],
    "route:cache": ["route:cache", "--no-interaction"],
    "route:clear": ["route:clear", "--no-interaction"],
    "view:cache": ["view:cache", "--no-interaction"],
    "view:clear": ["view:clear", "--no-interaction"],
    "cache:clear": ["cache:clear", "--no-interaction"],
    "event:cache": ["event:cache", "--no-interaction"],
    "event:clear": ["event:clear", "--no-interaction"],
    "storage:link": ["storage:link", "--no-interaction"],
    "queue:restart": ["queue:restart", "--no-interaction"],
    "queue:retry": ["queue:retry", "all", "--no-interaction"],
    "queue:flush": ["queue:flush", "--no-interaction"],
    "queue:failed": ["queue:failed", "--no-interaction"],
    "schedule:run": ["schedule:run", "--no-interaction"],
    "schedule:list": ["schedule:list", "--no-interaction"],
    "key:generate": ["key:generate", "--force", "--no-interaction"],
    "down": ["down", "--no-interaction"],
    "up": ["up", "--no-interaction"],
    "about": ["about", "--no-interaction"],
    "version": ["--version", "--no-interaction"],
}

CURATED_COMPOSER_PACKAGES: List[Dict[str, Any]] = [
    {
        "name": "laravel/sanctum",
        "title": "Laravel Sanctum",
        "version": "^4.0",
        "category": "Authentication",
        "description": "Hệ thống xác thực API Token & SPA gọn nhẹ chính thức của Laravel.",
    },
    {
        "name": "laravel/horizon",
        "title": "Laravel Horizon",
        "version": "^5.29",
        "category": "Queue & Redis",
        "description": "Dashboard giám sát và quản lý Redis Queue chuyên sâu theo thời gian thực.",
    },
    {
        "name": "spatie/laravel-permission",
        "title": "Spatie Roles & Permissions",
        "version": "^6.10",
        "category": "Authorization",
        "description": "Quản lý phân quyền Role & Permission trong Database chuẩn doanh nghiệp.",
    },
    {
        "name": "livewire/livewire",
        "title": "Laravel Livewire",
        "version": "^3.5",
        "category": "Full-stack UI",
        "description": "Xây dựng giao diện động hiện đại bằng PHP thuần không cần viết tách SPA.",
    },
    {
        "name": "filament/filament",
        "title": "Filament Admin Panel",
        "version": "^3.2",
        "category": "Admin Builder",
        "description": "Bộ khung tạo trang quản trị Admin, Form & Table cực nhanh trên nền TALL stack.",
    },
    {
        "name": "barryvdh/laravel-debugbar",
        "title": "Laravel Debugbar",
        "version": "^3.14",
        "category": "Developer Tools",
        "description": "Thanh công cụ kiểm tra truy vấn SQL, Route, View và bộ nhớ khi phát triển.",
    },
    {
        "name": "predis/predis",
        "title": "Predis Client",
        "version": "^2.3",
        "category": "Cache & Redis",
        "description": "Thư viện kết nối Redis thuần PHP ổn định cho Cache, Session và Queue.",
    },
    {
        "name": "spatie/laravel-backup",
        "title": "Spatie Laravel Backup",
        "version": "^9.1",
        "category": "Backup",
        "description": "Tự động sao lưu mã nguồn và cơ sở dữ liệu ứng dụng Laravel định kỳ.",
    },
]

SECURITY_CHECK_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "public_docroot",
        "title": "Document Root trỏ đúng vào thư mục /public",
        "description": "Ngăn truy cập trực tiếp mã nguồn app/, config/, database/ và vendor/ từ trình duyệt.",
        "critical": True,
    },
    {
        "id": "dotenv_git_blocked",
        "title": "Chặn truy cập công khai tệp .env, .git & composer.json",
        "description": "Bảo vệ biến môi trường, khóa bí mật và cấu trúc gói khỏi bot quét tự động trên Nginx.",
        "critical": True,
    },
    {
        "id": "app_debug_off",
        "title": "Tắt chế độ gỡ lỗi (APP_DEBUG=false) trên môi trường Production",
        "description": "Ngăn lộ Stack Trace, câu lệnh SQL và biến môi trường nhạy cảm khi ứng dụng gặp lỗi.",
        "critical": True,
    },
    {
        "id": "app_key_valid",
        "title": "Khóa mã hóa ứng dụng (APP_KEY) hợp lệ chuẩn AES-256-CBC",
        "description": "Đảm bảo toàn vẹn dữ liệu mã hóa, Session và CSRF Token của Laravel.",
        "critical": True,
    },
    {
        "id": "env_permissions_strict",
        "title": "Chuẩn hóa quyền hạn tệp .env (0640) & thư mục storage, bootstrap/cache (0755)",
        "description": "Chống đọc trái phép thông tin kết nối Database và đảm bảo quyền ghi bộ đệm.",
        "critical": True,
    },
    {
        "id": "storage_php_blocked",
        "title": "Chặn thực thi mã độc PHP trong thư mục /storage/app/public",
        "description": "Ngăn chặn chạy Webshell giả dạng hình ảnh hoặc tài liệu tải lên.",
        "critical": False,
    },
    {
        "id": "secure_session_cookie",
        "title": "Bảo vệ Cookie phiên làm việc (SESSION_SECURE_COOKIE & HttpOnly)",
        "description": "Bảo vệ phiên đăng nhập người dùng khỏi tấn công nghe lén trên kết nối HTTPS.",
        "critical": False,
    },
]


# ── State Persistence Helper ─────────────────────────────────────────────────

def _toolkit_state_path() -> Path:
    candidates = [
        Path("/var/lib/spanel/laravel_toolkit_state.json"),
        Path(__file__).resolve().parents[2] / "data" / "laravel_toolkit_state.json",
    ]
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if os.access(p.parent, os.W_OK):
                return p
        except Exception:
            continue
    return Path(__file__).resolve().parents[2] / "laravel_toolkit_state.json"


def _is_in_memory_engine() -> bool:
    try:
        return str(getattr(engine, "url", "")).endswith(":memory:")
    except Exception:
        return False


def _ensure_toolkit_state_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS laravel_toolkit_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "payload TEXT NOT NULL, "
                "updated_at TEXT"
                ")"
            )
        )


def _load_toolkit_state() -> Dict[str, Any]:
    with _LARAVEL_STATE_LOCK:
        try:
            _ensure_toolkit_state_table()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT payload FROM laravel_toolkit_state WHERE id = 1")
                ).fetchone()
                if row and row[0]:
                    data = json.loads(row[0])
                    if isinstance(data, dict):
                        data.setdefault("instances", {})
                        return data
        except Exception as exc:
            logger.debug("load_laravel_toolkit_state db warning: %s", exc)

        if _is_in_memory_engine():
            return {"instances": {}}

        path = _toolkit_state_path()
        if path.exists() and not path.is_symlink():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("instances", {})
                    _save_toolkit_state(data)
                    return data
            except Exception as exc:
                logger.warning("Failed to load Laravel toolkit state file: %s", exc)
        return {"instances": {}}


def _save_toolkit_state(state: Dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _LARAVEL_STATE_LOCK:
        try:
            _ensure_toolkit_state_table()
            with engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT 1 FROM laravel_toolkit_state WHERE id = 1")
                ).fetchone()
                if existing:
                    conn.execute(
                        text(
                            "UPDATE laravel_toolkit_state SET payload = :payload, updated_at = :updated_at WHERE id = 1"
                        ),
                        {"payload": payload, "updated_at": now_iso},
                    )
                else:
                    conn.execute(
                        text(
                            "INSERT INTO laravel_toolkit_state (id, payload, updated_at) VALUES (1, :payload, :updated_at)"
                        ),
                        {"payload": payload, "updated_at": now_iso},
                    )
        except Exception as exc:
            logger.warning("Failed to save Laravel toolkit state to DB: %s", exc)

        if _is_in_memory_engine():
            return

        path = _toolkit_state_path()
        try:
            if path.is_symlink():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".laravel_toolkit_state.", dir=str(path.parent), text=True
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
            logger.debug("save_laravel_toolkit_state file mirror warning: %s", exc)


# ── Access Control & Path Resolution ─────────────────────────────────────────

def _require_end_user_laravel_access(
    current_user: User, request: Optional[Request] = None
) -> None:
    role = normalize_role(getattr(current_user, "role", None))
    access_role = (
        (request.headers.get("x-access-role") or "").strip().lower()
        if (request is not None and getattr(request, "headers", None))
        else _LARAVEL_ACCESS_ROLE_CTX.get()
    )
    if is_admin_role(getattr(current_user, "role", None)) or role == Role.reseller:
        if access_role not in {"user", "end_user"}:
            raise HTTPException(
                status_code=403,
                detail="Laravel Manager is only available in User Access Role. Please switch Access Role to User or use 'Login as' on an End User account.",
            )


def _get_accessible_website(website_id: int, db: Session, current_user: User) -> Website:
    _require_end_user_laravel_access(current_user)
    site = db.query(Website).filter(Website.id == website_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Website not found")
    if site.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return site


def _resolve_laravel_paths(
    site: Website, db: Session, subpath: str = ""
) -> tuple[str, Path, Path]:
    """Return (linux_user, site_root, app_dir) where app_dir is the Laravel project root
    containing `artisan` and `composer.json` (e.g. `.../public_html`), even when
    `site.document_root` is `public_html/public`."""
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
    base_public = site_root / "public_html"

    clean_sub = (subpath or "").strip().strip("/")
    if clean_sub:
        if ".." in clean_sub or not SUBPATH_RE.fullmatch(clean_sub):
            raise HTTPException(status_code=400, detail="Invalid subdirectory path")
        app_dir = base_public / clean_sub
    else:
        doc_rel = (site.document_root or "public_html").strip().strip("/")
        if doc_rel.endswith("/public") and doc_rel != "public":
            parent_rel = doc_rel[: -len("/public")].strip("/")
            app_dir = site_root / (parent_rel or "public_html")
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


def _delete_app_file(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
    rel_path: str,
    *,
    strict: bool = False,
) -> bool:
    """Safely delete a single file inside `app_dir` via `site-file-delete` (`O_NOFOLLOW` + `st_uid`),
    never unlinking paths directly from the panel (`spanel`) user across symlinks."""
    clean_rel = (rel_path or "").strip().lstrip("/\\")
    if not clean_rel or ".." in Path(clean_rel).parts or "\x00" in clean_rel or "\n" in clean_rel:
        if strict:
            raise HTTPException(status_code=400, detail=f"Đường dẫn tệp không hợp lệ: {rel_path}")
        return False

    target = app_dir / clean_rel
    try:
        rel_to_root = str(target.relative_to(site_root)).replace("\\", "/")
    except ValueError:
        if strict:
            raise HTTPException(
                status_code=400, detail=f"Đường dẫn nằm ngoài thư mục gốc của website: {rel_path}"
            )
        return False

    if ".." in rel_to_root.split("/") or not rel_to_root:
        if strict:
            raise HTTPException(status_code=400, detail=f"Đường dẫn không an toàn: {rel_path}")
        return False

    if not settings.command_dry_run:
        res = shell.privileged(
            "site-file-delete",
            helper_args=[linux_user, str(site_root), rel_to_root],
            check=False,
        )
        if int(getattr(res, "returncode", 1) or 0) != 0:
            err = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "site-file-delete failed").strip()
            logger.warning("site-file-delete refused for %s (%s): %s", linux_user, rel_to_root, err)
            if strict:
                raise HTTPException(
                    status_code=500,
                    detail=f"Không thể xóa tệp {clean_rel} an toàn: {err[:200]}",
                )
            return False
        return True

    # Dry-run / local unit test guard: refuse if any path component is a symlink or escapes site_root.
    if wordpress_live._has_symlink_component(target, site_root) or target.is_symlink():
        if strict:
            raise HTTPException(
                status_code=400,
                detail=f"Từ chối xóa tệp qua liên kết tượng trưng (symlink): {clean_rel}",
            )
        return False
    try:
        resolved_root = site_root.resolve(strict=False)
        resolved_parent = target.parent.resolve(strict=False)
        resolved_parent.relative_to(resolved_root)
    except Exception:
        if strict:
            raise HTTPException(
                status_code=400,
                detail=f"Đường dẫn cha thoát khỏi thư mục website: {clean_rel}",
            )
        return False
    try:
        if target.exists():
            if target.is_dir():
                if strict:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Từ chối xóa thư mục qua _delete_app_file: {clean_rel}",
                    )
                return False
            os.remove(target)
        return True
    except OSError as exc:
        if strict:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return False


def _app_path_exists(
    app_dir: Path,
    rel_path: str,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> bool:
    return wordpress_live.wp_path_exists(app_dir, rel_path, linux_user, site_root)


def _php_cli_for_version(php_version: str) -> str:
    clean = (php_version or "8.4").strip()
    if re.fullmatch(r"\d\.\d", clean):
        candidate = Path(f"/usr/bin/php{clean}")
        if candidate.exists():
            return str(candidate)
        return f"php{clean}"
    return "php"


def _short_cmd_err(output: str, fallback: str = "Lệnh thực thi trả về mã lỗi khác 0.") -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", (output or "")).strip()
    if not cleaned:
        return fallback
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    tail = " | ".join(lines[-4:]) if lines else cleaned
    return tail[-420:]


def _purge_app_dir_contents(
    linux_user: str,
    site_root: Path,
    app_dir: Path,
) -> None:
    """Safely remove files inside `app_dir` via helper `rm-site` (`delete_no_follow`) and
    re-ensure an empty `public_html` directory when `app_dir` is `site_root / 'public_html'`."""
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


def _extract_php_version_hint(token: str) -> Optional[str]:
    base = Path(token or "").name
    m = re.fullmatch(r"php(\d\.\d)", base)
    return m.group(1) if m else None


def _run_as_site_user(
    linux_user: str,
    app_dir: Path,
    cmd_args: List[str],
    *,
    php_version: Optional[str] = None,
    timeout: int = 180,
) -> tuple[int, str]:
    """Execute a whitelisted command (`artisan`, `composer`, `php`, `git`) inside `app_dir`
    as `linux_user` via SPanel's mount-namespaced `terminal-exec` helper verb."""
    if not cmd_args:
        return 1, "Empty command argument list"

    bounded_timeout = max(5, min(1800, int(timeout or 180)))
    normalized = [str(a) for a in cmd_args]
    resolved_php = (php_version or "").strip()

    first_hint = _extract_php_version_hint(normalized[0])
    if first_hint and not resolved_php:
        resolved_php = first_hint

    first_base = Path(normalized[0]).name
    if first_base.startswith("php"):
        if len(normalized) >= 2 and normalized[1] == "artisan":
            normalized = ["artisan", *normalized[2:]]
        else:
            normalized = ["php", *normalized[1:]]

    quoted_cmd = " ".join(shlex.quote(a) for a in normalized)
    if settings.command_dry_run:
        return 0, f"[DRY-RUN] $ {quoted_cmd}"

    helper_args: List[str] = [
        linux_user,
        str(app_dir),
        f"--timeout={bounded_timeout}",
    ]
    if resolved_php and re.fullmatch(r"\d\.\d", resolved_php):
        helper_args.append(f"--php-version={resolved_php}")
    helper_args.extend(normalized)

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


def _append_artisan_log(
    saved: Dict[str, Any], cmd_display: str, output: str, ok: bool = True
) -> None:
    logs = list(saved.get("last_artisan_logs") or [])
    logs.append(
        {
            "cmd": cmd_display,
            "output": (output or "OK").strip()[-2000:],
            "status": "ok" if ok else "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    saved["last_artisan_logs"] = logs[-12:]


# ── .env Parsing & Generation ────────────────────────────────────────────────

def _generate_laravel_app_key() -> str:
    return "base64:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _render_default_env(
    *,
    app_name: str,
    app_url: str,
    db_name: str,
    db_user: str,
    db_password: str,
    db_host: str = "127.0.0.1",
    app_env: str = "production",
    app_debug: bool = False,
    app_key: Optional[str] = None,
    cache_driver: str = "file",
    queue_connection: str = "database",
    session_driver: str = "file",
) -> str:
    key_val = app_key or _generate_laravel_app_key()
    safe_name = (app_name or "Laravel").replace('"', "").replace("\n", " ").strip()
    debug_str = "true" if app_debug else "false"
    return (
        f'APP_NAME="{safe_name}"\n'
        f"APP_ENV={app_env}\n"
        f"APP_KEY={key_val}\n"
        f"APP_DEBUG={debug_str}\n"
        f"APP_TIMEZONE=Asia/Ho_Chi_Minh\n"
        f"APP_URL={app_url}\n"
        f"APP_LOCALE=vi\n"
        f"APP_FALLBACK_LOCALE=en\n\n"
        f"LOG_CHANNEL=stack\n"
        f"LOG_STACK=single\n"
        f"LOG_LEVEL=debug\n\n"
        f"DB_CONNECTION=mysql\n"
        f"DB_HOST={db_host}\n"
        f"DB_PORT=3306\n"
        f"DB_DATABASE={db_name}\n"
        f"DB_USERNAME={db_user}\n"
        f'DB_PASSWORD="{db_password}"\n\n'
        f"SESSION_DRIVER={session_driver}\n"
        f"SESSION_LIFETIME=120\n"
        f"SESSION_ENCRYPT=false\n"
        f"SESSION_PATH=/\n"
        f"SESSION_SECURE_COOKIE=true\n\n"
        f"BROADCAST_CONNECTION=log\n"
        f"FILESYSTEM_DISK=local\n"
        f"QUEUE_CONNECTION={queue_connection}\n\n"
        f"CACHE_STORE={cache_driver}\n"
        f"CACHE_DRIVER={cache_driver}\n\n"
        f"REDIS_CLIENT=phpredis\n"
        f"REDIS_HOST=127.0.0.1\n"
        f"REDIS_PASSWORD=null\n"
        f"REDIS_PORT=6379\n\n"
        f"MAIL_MAILER=log\n"
        f"MAIL_HOST=127.0.0.1\n"
        f"MAIL_PORT=2525\n"
        f"MAIL_USERNAME=null\n"
        f"MAIL_PASSWORD=null\n"
        f"MAIL_ENCRYPTION=null\n"
        f'MAIL_FROM_ADDRESS="hello@{app_url.split("://")[-1].split("/")[0]}"\n'
        f'MAIL_FROM_NAME="${{APP_NAME}}"\n'
    )


def _parse_env_details(env_text: str) -> Dict[str, Any]:
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
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            elif " #" in val:
                val = val.split(" #", 1)[0].strip()
            if key:
                raw_vars[key] = val

    def _bool(key: str, default: bool = False) -> bool:
        val = (raw_vars.get(key) or "").strip().lower()
        if not val:
            return default
        return val in {"true", "1", "yes", "on"}

    cache_store = raw_vars.get("CACHE_STORE") or raw_vars.get("CACHE_DRIVER") or "file"
    return {
        "variables": raw_vars,
        "app_name": raw_vars.get("APP_NAME") or "Laravel Application",
        "app_env": raw_vars.get("APP_ENV") or "production",
        "app_key": raw_vars.get("APP_KEY") or "",
        "app_debug": _bool("APP_DEBUG", False),
        "app_url": raw_vars.get("APP_URL") or "",
        "log_channel": raw_vars.get("LOG_CHANNEL") or "stack",
        "log_level": raw_vars.get("LOG_LEVEL") or "debug",
        "db_connection": raw_vars.get("DB_CONNECTION") or "mysql",
        "db_host": raw_vars.get("DB_HOST") or "127.0.0.1",
        "db_port": raw_vars.get("DB_PORT") or "3306",
        "db_name": raw_vars.get("DB_DATABASE") or "",
        "db_user": raw_vars.get("DB_USERNAME") or "",
        "db_password": raw_vars.get("DB_PASSWORD") or "",
        "cache_driver": cache_store,
        "queue_connection": raw_vars.get("QUEUE_CONNECTION") or "database",
        "session_driver": raw_vars.get("SESSION_DRIVER") or "file",
        "session_lifetime": raw_vars.get("SESSION_LIFETIME") or "120",
        "session_secure_cookie": _bool("SESSION_SECURE_COOKIE", True),
        "redis_host": raw_vars.get("REDIS_HOST") or "127.0.0.1",
        "redis_port": raw_vars.get("REDIS_PORT") or "6379",
        "mail_mailer": raw_vars.get("MAIL_MAILER") or "log",
        "mail_host": raw_vars.get("MAIL_HOST") or "127.0.0.1",
        "mail_port": raw_vars.get("MAIL_PORT") or "587",
        "mail_username": raw_vars.get("MAIL_USERNAME") or "",
        "mail_encryption": raw_vars.get("MAIL_ENCRYPTION") or "tls",
        "mail_from_address": raw_vars.get("MAIL_FROM_ADDRESS") or "",
    }


def _format_env_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    s = str(val if val is not None else "")
    if any(ch in s for ch in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=400, detail="Invalid control character in .env value")
    if " " in s or "#" in s or ";" in s or (s and s[0] in ('"', "'")):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _update_env_text(env_text: str, updates: Dict[str, Any]) -> str:
    lines = (env_text or "").splitlines()
    remaining = {}
    for k, v in updates.items():
        clean_k = str(k).strip().upper()
        if not ENV_KEY_RE.fullmatch(clean_k):
            raise HTTPException(status_code=400, detail=f"Invalid .env variable name: {k}")
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


# ── Composer Metadata Detection ──────────────────────────────────────────────

def _detect_composer_metadata(
    app_dir: Path,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
    saved: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    saved = saved or {}
    composer_raw = _read_app_file(
        app_dir, "composer.json", linux_user=linux_user, site_root=site_root
    )
    lock_raw = _read_app_file(
        app_dir, "composer.lock", linux_user=linux_user, site_root=site_root
    )
    laravel_version = saved.get("laravel_version") or "11.x"
    packages: List[Dict[str, Any]] = []
    locked_versions: Dict[str, str] = {}

    if lock_raw:
        try:
            lock_data = json.loads(lock_raw)
            for section in ("packages", "packages-dev"):
                for pkg in lock_data.get(section) or []:
                    if isinstance(pkg, dict) and pkg.get("name"):
                        locked_versions[pkg["name"]] = str(pkg.get("version") or "").lstrip("v")
            if "laravel/framework" in locked_versions:
                laravel_version = locked_versions["laravel/framework"]
        except Exception:
            pass

    if composer_raw:
        try:
            comp_data = json.loads(composer_raw)
            req = comp_data.get("require") or {}
            req_dev = comp_data.get("require-dev") or {}
            if "laravel/framework" in req and "laravel/framework" not in locked_versions:
                raw_ver = str(req["laravel/framework"]).lstrip("^~v")
                if raw_ver.startswith("12"):
                    laravel_version = "12.x"
                elif raw_ver.startswith("11"):
                    laravel_version = "11.x"
                elif raw_ver.startswith("10"):
                    laravel_version = "10.x"
                elif raw_ver.startswith("9"):
                    laravel_version = "9.x"
                else:
                    laravel_version = raw_ver

            for pkg_name, constraint in req.items():
                if pkg_name == "php" or pkg_name.startswith("ext-"):
                    continue
                packages.append(
                    {
                        "name": pkg_name,
                        "constraint": str(constraint),
                        "version": locked_versions.get(pkg_name) or str(constraint),
                        "type": "require",
                    }
                )
            for pkg_name, constraint in req_dev.items():
                packages.append(
                    {
                        "name": pkg_name,
                        "constraint": str(constraint),
                        "version": locked_versions.get(pkg_name) or str(constraint),
                        "type": "require-dev",
                    }
                )
        except Exception:
            pass

    if not packages and saved.get("composer_packages"):
        packages = list(saved.get("composer_packages") or [])

    return {
        "laravel_version": laravel_version,
        "composer_version": saved.get("composer_version") or "2.8",
        "packages": packages,
    }


def _evaluate_security_checks(
    site: Website,
    app_dir: Path,
    env_info: Dict[str, Any],
    saved_inst: Dict[str, Any],
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    sec_overrides = saved_inst.get("security_overrides") or {}
    doc_root = (site.document_root or "public_html").strip().rstrip("/")
    public_docroot_ok = doc_root.endswith("/public") or bool(sec_overrides.get("public_docroot"))

    custom_nginx = (getattr(site, "nginx_custom", "") or "").lower()
    dotenv_blocked_ok = (
        public_docroot_ok
        or (".env" in custom_nginx and "deny all" in custom_nginx)
        or getattr(site, "nginx_rewrite_mode", "") in {"laravel", "front_controller"}
        or bool(sec_overrides.get("dotenv_git_blocked", True))
    )

    app_debug_off = not bool(env_info.get("app_debug", False))
    app_key = (env_info.get("app_key") or "").strip()
    app_key_valid = (app_key.startswith("base64:") and len(app_key) >= 40) or len(app_key) >= 32
    env_perms_ok = bool(sec_overrides.get("env_permissions_strict", True))

    storage_ht = _read_app_file(
        app_dir, "storage/app/public/.htaccess", linux_user=linux_user, site_root=site_root
    )
    storage_php_blocked = (
        ("storage" in custom_nginx and "deny all" in custom_nginx)
        or ("php" in storage_ht.lower())
        or bool(sec_overrides.get("storage_php_blocked", True))
    )
    secure_cookie_ok = bool(env_info.get("session_secure_cookie", True)) or bool(
        sec_overrides.get("secure_session_cookie")
    )

    checks_map = {
        "public_docroot": public_docroot_ok,
        "dotenv_git_blocked": dotenv_blocked_ok,
        "app_debug_off": app_debug_off,
        "app_key_valid": app_key_valid,
        "env_permissions_strict": env_perms_ok,
        "storage_php_blocked": storage_php_blocked,
        "secure_session_cookie": secure_cookie_ok,
    }

    results: List[Dict[str, Any]] = []
    for item in SECURITY_CHECK_DEFINITIONS:
        cid = item["id"]
        if cid in sec_overrides and cid not in {"app_debug_off", "app_key_valid", "public_docroot"}:
            passed = bool(sec_overrides[cid])
        else:
            passed = bool(checks_map.get(cid, False))
        results.append({**item, "passed": passed})
    return results


def _has_scheduler_cron(site: Website, linux_user: str) -> bool:
    try:
        entries = cron.list_cron_entries(site.domain, linux_user)
        for item in entries:
            cmd = (item.get("command") or "").lower()
            if "schedule:run" in cmd or "spanel-schedule-cron.php" in cmd:
                return True
    except Exception:
        pass
    return False


def _build_instance_summary(
    site: Website, db: Session, state: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    inst_key = str(site.id)
    saved = (state.get("instances") or {}).get(inst_key) or {}
    if saved.get("detached"):
        return None

    subpath = saved.get("subpath") or ""
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, subpath)

    has_artisan = _app_path_exists(app_dir, "artisan", linux_user, site_root)
    composer_raw = _read_app_file(
        app_dir, "composer.json", linux_user=linux_user, site_root=site_root
    )
    has_laravel_composer = "laravel/framework" in (composer_raw or "")
    is_laravel_rewrite = getattr(site, "nginx_rewrite_mode", "") == "laravel" or (
        site.document_root or ""
    ).rstrip("/").endswith("/public")

    if not has_artisan and not has_laravel_composer and not saved.get("installed") and not is_laravel_rewrite:
        return None

    env_text = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    env_info = _parse_env_details(env_text)
    comp_meta = _detect_composer_metadata(
        app_dir, linux_user=linux_user, site_root=site_root, saved=saved
    )

    db_acc = wordpress_live.resolve_owned_wp_database(
        db, site, env_info.get("db_name") or saved.get("db_name")
    )
    db_name = db_acc.db_name if db_acc else (env_info.get("db_name") or saved.get("db_name") or "")
    db_user = db_acc.db_user if db_acc else (env_info.get("db_user") or saved.get("db_user") or "")

    db_size_mb = 0.0
    table_count = 0
    if db_name:
        try:
            tables = mariadb.get_database_tables_breakdown(db_name)
            table_count = len(tables)
            sz_bytes = sum(int(t.get("total_bytes") or 0) for t in tables)
            if db_acc and sz_bytes > 0 and getattr(db_acc, "size_bytes", 0) != sz_bytes:
                db_acc.size_bytes = sz_bytes
                db.commit()
            if sz_bytes > 0:
                db_size_mb = round(float(sz_bytes) / (1024 * 1024), 2)
        except Exception:
            if db_acc and int(getattr(db_acc, "size_bytes", 0) or 0) > 0:
                db_size_mb = round(float(db_acc.size_bytes) / (1024 * 1024), 2)

    q_saved = saved.get("queue_worker") or {}
    queue_worker = {
        "enabled": bool(q_saved.get("enabled", False)),
        "workers": max(1, min(8, int(q_saved.get("workers") or 1))),
        "queue": str(q_saved.get("queue") or "default"),
        "tries": max(1, min(10, int(q_saved.get("tries") or 3))),
        "timeout": max(15, min(3600, int(q_saved.get("timeout") or 90))),
        "memory": max(64, min(1024, int(q_saved.get("memory") or 128))),
        "connection": env_info.get("queue_connection") or "database",
        "failed_count": len(saved.get("failed_jobs") or []),
    }

    scheduler_live = _has_scheduler_cron(site, linux_user)
    scheduler_enabled = bool(saved.get("scheduler_enabled", scheduler_live)) or scheduler_live

    maintenance_down = _app_path_exists(
        app_dir, "storage/framework/down", linux_user, site_root
    ) or _app_path_exists(
        app_dir, "storage/framework/maintenance.php", linux_user, site_root
    ) or bool(saved.get("maintenance_mode", False))

    storage_linked = _app_path_exists(
        app_dir, "public/storage", linux_user, site_root
    ) or bool(saved.get("storage_linked", True))

    sec_checks = _evaluate_security_checks(
        site, app_dir, env_info, saved, linux_user=linux_user, site_root=site_root
    )
    passed_count = sum(1 for c in sec_checks if c["passed"])
    critical_failed = any(c["critical"] and not c["passed"] for c in sec_checks)
    security_status = (
        "ok"
        if passed_count == len(sec_checks)
        else ("warning" if not critical_failed or passed_count >= 5 else "critical")
    )

    url_scheme = "https" if site.ssl_enabled else "http"
    clean_sub = f"/{subpath.strip('/')}" if subpath.strip("/") else ""
    doc_root_rel = site.document_root or "public_html/public"

    return {
        "id": site.id,
        "website_id": site.id,
        "domain": site.domain,
        "subpath": clean_sub,
        "url": f"{url_scheme}://{site.domain}{clean_sub}",
        "root_path": str(site_root),
        "app_path": str(app_dir),
        "public_path": str(app_dir / "public"),
        "document_root": doc_root_rel,
        "public_docroot_active": doc_root_rel.rstrip("/").endswith("/public"),
        "linux_user": linux_user,
        "php_version": site.php_version or "8.3",
        "ssl_enabled": bool(site.ssl_enabled),
        "waf_enabled": bool(getattr(site, "waf_enabled", True)),
        "app_name": env_info["app_name"] if env_text else (saved.get("app_name") or site.domain),
        "app_env": env_info["app_env"] if env_text else (saved.get("app_env") or "production"),
        "app_debug": bool(env_info["app_debug"]) if env_text else bool(saved.get("app_debug", False)),
        "app_url": env_info["app_url"] or f"{url_scheme}://{site.domain}{clean_sub}",
        "app_key_set": bool(env_info["app_key"]) or bool(saved.get("app_key_set", False)),
        "laravel_version": comp_meta["laravel_version"],
        "composer_version": comp_meta["composer_version"],
        "cache_driver": env_info["cache_driver"],
        "queue_connection": env_info["queue_connection"],
        "session_driver": env_info["session_driver"],
        "db_connection": env_info["db_connection"],
        "db_host": env_info["db_host"],
        "db_port": env_info["db_port"],
        "db_name": db_name,
        "db_user": db_user,
        "db_size_mb": db_size_mb,
        "database": {"table_count": table_count, "size_mb": db_size_mb},
        "queue_worker": queue_worker,
        "scheduler_enabled": scheduler_enabled,
        "maintenance_mode": maintenance_down,
        "maintenance_secret": saved.get("maintenance_secret") or "",
        "storage_linked": storage_linked,
        "git_repo": saved.get("git_repo") or "",
        "git_branch": saved.get("git_branch") or "main",
        "git_commit": saved.get("git_commit") or "",
        "packages": comp_meta["packages"],
        "packages_count": len(comp_meta["packages"]),
        "failed_jobs": list(saved.get("failed_jobs") or []),
        "last_artisan_logs": list(saved.get("last_artisan_logs") or []),
        "security_status": security_status,
        "security_passed": passed_count,
        "security_total": len(sec_checks),
        "security_checks": sec_checks,
        "installed_at": saved.get("installed_at")
        or (site.created_at.isoformat() if getattr(site, "created_at", None) else None),
    }


# ── Pydantic Request Models ──────────────────────────────────────────────────

class LaravelInstallPayload(BaseModel):
    website_id: Optional[int] = None
    domain: Optional[str] = None
    subpath: str = ""
    app_name: str = "My Laravel Project"
    laravel_version: str = "11.x"
    php_version: str = "8.3"
    install_source: str = "composer"  # "composer" or "git"
    git_repo: str = ""
    git_branch: str = "main"
    db_mode: str = "auto"  # "auto" or "existing"
    database_id: Optional[int] = Field(default=None, ge=1)
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    cache_driver: str = "file"
    queue_connection: str = "database"
    session_driver: str = "file"
    enable_scheduler: bool = True
    enable_queue_worker: bool = True
    queue_workers: int = Field(default=2, ge=1, le=8)


class LaravelArtisanPayload(BaseModel):
    command: str
    maintenance_secret: Optional[str] = None


class LaravelQueuePayload(BaseModel):
    action: str = "toggle"  # start, stop, restart, toggle, update_config, retry_failed, flush_failed
    enabled: Optional[bool] = None
    workers: Optional[int] = Field(default=None, ge=1, le=8)
    queue: Optional[str] = None
    tries: Optional[int] = Field(default=None, ge=1, le=10)
    timeout: Optional[int] = Field(default=None, ge=15, le=3600)
    memory: Optional[int] = Field(default=None, ge=64, le=1024)


class LaravelSchedulerPayload(BaseModel):
    enabled: bool = True


class LaravelEnvUpdatePayload(BaseModel):
    raw_env: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None
    app_name: Optional[str] = None
    app_env: Optional[str] = None
    app_debug: Optional[bool] = None
    app_url: Optional[str] = None
    cache_driver: Optional[str] = None
    queue_connection: Optional[str] = None
    session_driver: Optional[str] = None
    db_host: Optional[str] = None
    db_port: Optional[str] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    redis_host: Optional[str] = None
    redis_port: Optional[str] = None
    mail_mailer: Optional[str] = None
    mail_host: Optional[str] = None
    mail_port: Optional[str] = None
    mail_username: Optional[str] = None
    mail_password: Optional[str] = None
    mail_encryption: Optional[str] = None
    mail_from_address: Optional[str] = None
    php_version: Optional[str] = None


class LaravelComposerPayload(BaseModel):
    action: str  # install, update, dump_autoload, require, remove, git_pull
    package: Optional[str] = None
    dev: bool = False
    git_repo: Optional[str] = None
    git_branch: Optional[str] = None


class LaravelSecurityPayload(BaseModel):
    action: str = "secure"  # secure, revert, fix_permissions, fix_docroot
    check_ids: List[str] = Field(default_factory=list)


def _load_wp_state_readonly() -> Dict[str, Any]:
    wp_path = Path("/etc/spanel/wp_toolkit.json")
    if wp_path.is_file():
        try:
            data = json.loads(wp_path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"instances": {}}


def _is_website_occupied(
    site: Any,
    lv_state: Dict[str, Any],
    wp_state: Dict[str, Any],
    active_lv_ids: Optional[set] = None,
) -> bool:
    sid = getattr(site, "id", None)
    inst_key = str(sid)
    lv_inst = (lv_state.get("instances") or {}).get(inst_key) or {}
    wp_inst = (wp_state.get("instances") or {}).get(inst_key) or {}
    docroot_rel = (getattr(site, "document_root", "") or "").strip().rstrip("/")
    is_lv = (
        (active_lv_ids is not None and sid in active_lv_ids)
        or getattr(site, "nginx_rewrite_mode", None) == "laravel"
        or docroot_rel.endswith("/public")
        or docroot_rel == "public"
        or (bool(lv_inst.get("installed")) and not bool(lv_inst.get("detached")))
    )
    is_wp = (
        getattr(site, "app_type", None) == "wordpress"
        or (bool(wp_inst.get("installed")) and not bool(wp_inst.get("detached")))
    )
    is_app = getattr(site, "app_id", None) is not None or getattr(site, "app_type", None) in {
        "application",
        "node",
        "python",
        "docker",
        "compose",
    }
    return bool(is_lv or is_wp or is_app)


def _evaluate_user_app_quota(
    db: Session,
    user: Any,
    websites: List[Any],
    lv_state: Dict[str, Any],
    active_lv_ids: Optional[set] = None,
) -> Tuple[Dict[str, Any], set]:
    from app.services import quota

    wp_state = _load_wp_state_readonly()
    occupied_ids: set = set()
    for w in websites or []:
        if _is_website_occupied(w, lv_state, wp_state, active_lv_ids):
            occupied_ids.add(getattr(w, "id", None))

    w_limit = quota.effective_limit(user, "website_limit")
    s_limit = quota.effective_limit(user, "subdomain_limit")
    if w_limit < 0:
        max_total_websites = -1
    elif w_limit == 0:
        max_total_websites = 0
    else:
        max_total_websites = -1 if s_limit < 0 else (w_limit + s_limit)

    occupied_count = len(occupied_ids)
    available_websites_count = sum(
        1 for w in (websites or []) if getattr(w, "id", None) not in occupied_ids
    )
    db_limit = quota.effective_limit(user, "database_limit")
    try:
        raw_db_cnt = db.query(DatabaseAccount).filter(
            DatabaseAccount.owner_id == getattr(user, "id", 0)
        ).count()
        databases_used = raw_db_cnt if isinstance(raw_db_cnt, int) else 0
    except Exception:
        databases_used = 0

    website_quota_ok = (max_total_websites < 0) or (
        max_total_websites > 0 and occupied_count < max_total_websites
    )
    db_quota_ok = db_limit != 0
    can_install = bool(website_quota_ok and db_quota_ok and available_websites_count > 0)
    quota = {
        "website_limit": w_limit,
        "subdomain_limit": s_limit,
        "max_total_websites": max_total_websites,
        "occupied_websites_count": occupied_count,
        "available_websites_count": available_websites_count,
        "database_limit": db_limit,
        "databases_used": databases_used,
        "can_install": can_install,
    }
    return quota, occupied_ids


# ── API Endpoints ────────────────────────────────────────────────────────────

@router.get("/instances")
def list_laravel_instances(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_end_user_laravel_access(current_user)
    websites = (
        db.query(Website)
        .filter(Website.owner_id == current_user.id)
        .order_by(Website.id.desc())
        .all()
    )

    state = _load_toolkit_state()
    instances: List[Dict[str, Any]] = []
    for site in websites:
        try:
            summary = _build_instance_summary(site, db, state)
            if summary:
                instances.append(summary)
        except Exception as exc:
            logger.error(
                "Error building Laravel instance summary for %s: %s",
                getattr(site, "domain", site.id),
                exc,
            )
    _save_toolkit_state(state)

    active_lv_ids = {inst.get("website_id") or inst.get("id") for inst in instances}
    quota, occupied_ids = _evaluate_user_app_quota(
        db, current_user, websites, state, active_lv_ids
    )
    return {
        "instances": instances,
        "curated_packages": CURATED_COMPOSER_PACKAGES,
        "quota": quota,
        "websites": [
            {
                "id": w.id,
                "domain": w.domain,
                "php_version": w.php_version or "8.3",
                "ssl_enabled": bool(w.ssl_enabled),
                "app_type": w.app_type,
                "document_root": w.document_root or "public_html",
                "occupied": w.id in occupied_ids,
            }
            for w in websites
        ],
    }


@router.post("/scan")
def scan_laravel_instances(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_end_user_laravel_access(current_user)
    websites = db.query(Website).filter(Website.owner_id == current_user.id).all()

    state = _load_toolkit_state()
    instances_state = state.setdefault("instances", {})
    discovered = 0

    for site in websites:
        inst_key = str(site.id)
        linux_user, site_root, app_dir = _resolve_laravel_paths(site, db)
        has_artisan = _app_path_exists(app_dir, "artisan", linux_user, site_root)
        composer_raw = _read_app_file(
            app_dir, "composer.json", linux_user=linux_user, site_root=site_root
        )
        has_laravel_composer = "laravel/framework" in (composer_raw or "")
        is_laravel_rewrite = getattr(site, "nginx_rewrite_mode", "") == "laravel"

        if has_artisan or has_laravel_composer or is_laravel_rewrite:
            cur = instances_state.get(inst_key) or {}
            if not cur.get("installed") or cur.get("detached"):
                discovered += 1
            cur["installed"] = True
            cur["detached"] = False
            instances_state[inst_key] = cur

    db.commit()
    _save_toolkit_state(state)
    return {
        "discovered": discovered,
        "message": f"Đã quét {len(websites)} tên miền và đồng bộ {discovered} dự án Laravel.",
    }


@router.post("/install")
def install_laravel_instance(
    payload: LaravelInstallPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_end_user_laravel_access(current_user)
    site: Optional[Website] = None
    if payload.website_id:
        site = _get_accessible_website(payload.website_id, db, current_user)
    elif payload.domain:
        site = (
            db.query(Website)
            .filter(Website.domain == payload.domain.strip().lower())
            .first()
        )
        if site:
            site = _get_accessible_website(site.id, db, current_user)
    if not site:
        raise HTTPException(
            status_code=404, detail="Vui lòng chọn tên miền hợp lệ để cài đặt dự án Laravel."
        )

    if payload.install_source == "git":
        clean_repo = (payload.git_repo or "").strip()
        if not clean_repo or not GIT_URL_RE.fullmatch(clean_repo):
            raise HTTPException(
                status_code=400,
                detail="Địa chỉ Git Repository không hợp lệ (chỉ hỗ trợ https:// hoặc git@).",
            )
        clean_branch = (payload.git_branch or "main").strip()
        if not GIT_BRANCH_RE.fullmatch(clean_branch):
            raise HTTPException(status_code=400, detail="Tên nhánh Git không hợp lệ.")

    from app.api.databases import enforce_user_database_limit

    site_owner = db.query(User).filter(User.id == site.owner_id).first()
    if site_owner is None or not hasattr(site_owner, "role"):
        site_owner = current_user
    subpath_clean = (payload.subpath or "").strip("/")
    if not subpath_clean:
        try:
            owner_websites = db.query(Website).filter(Website.owner_id == site.owner_id).all()
            if not isinstance(owner_websites, list) or not owner_websites:
                owner_websites = [site]
        except Exception:
            owner_websites = [site]
        state_check = _load_toolkit_state()
        quota_check, occupied_ids_check = _evaluate_user_app_quota(
            db, site_owner, owner_websites, state_check
        )
        if getattr(site, "id", None) in occupied_ids_check:
            raise HTTPException(
                status_code=403,
                detail="Tên miền này đã được cài đặt ứng dụng. Vui lòng chọn tên miền khác hoặc gỡ ứng dụng hiện tại.",
            )
        max_total_sites = quota_check.get("max_total_websites", -1)
        if max_total_sites == 0 or (
            max_total_sites > 0 and len(occupied_ids_check) >= max_total_sites
        ):
            raise HTTPException(
                status_code=403,
                detail="Đã đạt giới hạn số lượng website cho phép của gói dịch vụ.",
            )

    reusing_existing_db = False
    existing_check: Optional[DatabaseAccount] = None
    if payload.db_mode == "existing":
        if payload.database_id is not None:
            existing_check = (
                db.query(DatabaseAccount)
                .filter(DatabaseAccount.id == payload.database_id)
                .first()
            )
        elif payload.db_name:
            existing_check = (
                db.query(DatabaseAccount)
                .filter(DatabaseAccount.db_name == payload.db_name.strip())
                .first()
            )
        if existing_check is None:
            raise HTTPException(
                status_code=404, detail="Database not found. Please create the database first."
            )
        if existing_check.owner_id != site.owner_id:
            raise HTTPException(
                status_code=403, detail="Cannot bind a database owned by another user."
            )
        reusing_existing_db = True

    if not reusing_existing_db:
        enforce_user_database_limit(db, site_owner)

    php_ver = (payload.php_version or site.php_version or "8.3").strip()
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, payload.subpath)
    try:
        linux_user = site_users.ensure_site_runtime(
            site.domain, str(site_root), php_ver, linux_user
        )
    except Exception as exc:
        logger.warning("ensure_site_runtime warning on Laravel install: %s", exc)

    orig_app_type = site.app_type
    orig_php_version = site.php_version
    orig_linux_user = site.linux_user
    orig_root_path = site.root_path
    orig_document_root = site.document_root
    orig_rewrite_mode = site.nginx_rewrite_mode

    subpath_clean = (payload.subpath or "").strip("/")
    new_docroot = f"public_html/{subpath_clean}/public" if subpath_clean else "public_html/public"
    site.app_type = "php"
    site.php_version = php_ver
    site.linux_user = linux_user
    site.root_path = str(site_root)
    site.document_root = new_docroot
    site.nginx_rewrite_mode = "laravel"

    # Provision or bind MySQL Database
    db_acc: Optional[DatabaseAccount] = None
    created_new_db_acc: Optional[DatabaseAccount] = None
    prev_existing_db_website_id: Optional[int] = None
    if payload.db_mode == "existing" and existing_check is not None:
        try:
            stored_db_pass = decrypt(existing_check.db_password)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="Stored database password cannot be decrypted; please reset database password first.",
            ) from exc
        if (
            payload.db_password
            and payload.db_password.strip()
            and payload.db_password.strip() != stored_db_pass
        ):
            raise HTTPException(
                status_code=403,
                detail="Provided database password does not match the database account.",
            )
        db_info = {
            "db_name": existing_check.db_name,
            "db_user": existing_check.db_user,
            "db_password": stored_db_pass,
        }
        prev_existing_db_website_id = existing_check.website_id
        existing_check.website_id = site.id
        db_acc = existing_check
    else:
        seed = f"{site.domain}_{secrets.token_hex(2)}"
        try:
            db_info = mariadb.create_database(seed, prefix="lv")
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Không thể khởi tạo cơ sở dữ liệu MySQL cho dự án Laravel: {exc}",
            ) from exc

        existing_acc = (
            db.query(DatabaseAccount)
            .filter(DatabaseAccount.db_name == db_info["db_name"])
            .first()
        )
        if existing_acc and existing_acc.owner_id == site.owner_id:
            existing_acc.website_id = site.id
            existing_acc.db_user = db_info["db_user"]
            existing_acc.db_password = encrypt(db_info["db_password"])
            db_acc = existing_acc
        elif not existing_acc:
            db_acc = DatabaseAccount(
                owner_id=site.owner_id,
                website_id=site.id,
                db_name=db_info["db_name"],
                db_user=db_info["db_user"],
                db_password=encrypt(db_info["db_password"]),
            )
            db.add(db_acc)
            created_new_db_acc = db_acc

    db.commit()

    def _rollback_failed_install(*, purge_files: bool = False) -> None:
        if purge_files:
            try:
                _purge_app_dir_contents(linux_user, site_root, app_dir)
            except Exception as purge_exc:
                logger.warning("Failed to purge partial Laravel install dir: %s", purge_exc)
        if created_new_db_acc is not None:
            try:
                teardown.drop_database_record(db, created_new_db_acc)
            except Exception as db_exc:
                logger.warning("Failed to rollback auto-created Laravel DB: %s", db_exc)
        elif existing_check is not None:
            existing_check.website_id = prev_existing_db_website_id
        site.app_type = orig_app_type
        site.php_version = orig_php_version
        site.linux_user = orig_linux_user
        site.root_path = orig_root_path
        site.document_root = orig_document_root
        site.nginx_rewrite_mode = orig_rewrite_mode
        db.commit()

    # Remove default placeholder files safely via helper (never as panel user across symlinks)
    _delete_app_file(linux_user, site_root, app_dir, "index.html")
    _delete_app_file(linux_user, site_root, app_dir, ".htaccess")

    php_cli = _php_cli_for_version(php_ver)
    install_logs: List[Dict[str, Any]] = []
    git_commit_sha = ""

    if payload.install_source == "git" and payload.git_repo:
        clean_branch = (payload.git_branch or "main").strip()
        clean_repo = payload.git_repo.strip()
        rc, out = _run_as_site_user(
            linux_user,
            app_dir,
            [
                "git",
                "-c",
                "credential.interactive=never",
                "-c",
                "core.askPass=",
                "clone",
                "--depth",
                "1",
                "--branch",
                clean_branch,
                clean_repo,
                ".",
            ],
            php_version=php_ver,
            timeout=300,
        )
        if rc != 0:
            _rollback_failed_install(purge_files=False)
            raise HTTPException(
                status_code=502,
                detail=f"Không thể clone Git repository ({clean_repo}): {_short_cmd_err(out)}",
            )
        install_logs.append(
            {
                "cmd": f"git clone --depth 1 -b {clean_branch} {clean_repo} .",
                "output": out or "Cloned repository.",
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        rc2, out2 = _run_as_site_user(
            linux_user,
            app_dir,
            ["composer", "install", "--no-dev", "--optimize-autoloader", "--no-interaction"],
            php_version=php_ver,
            timeout=600,
        )
        if rc2 != 0:
            _rollback_failed_install(purge_files=True)
            raise HTTPException(
                status_code=502,
                detail=f"Lỗi chạy `composer install` sau khi clone Git: {_short_cmd_err(out2)}",
            )
        install_logs.append(
            {
                "cmd": "composer install --no-dev --optimize-autoloader",
                "output": out2 or "Composer dependencies installed.",
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        rc_sha, sha_out = _run_as_site_user(
            linux_user,
            app_dir,
            ["git", "rev-parse", "--short", "HEAD"],
            php_version=php_ver,
            timeout=30,
        )
        if rc_sha == 0 and sha_out and not sha_out.startswith("[DRY-RUN]"):
            git_commit_sha = sha_out.splitlines()[-1].strip()[:16]
    else:
        raw_lv_ver = (payload.laravel_version or "11.x").strip()
        if "12" in raw_lv_ver:
            ver_constraint = "^12.0"
        elif "10" in raw_lv_ver:
            ver_constraint = "^10.0"
        else:
            ver_constraint = "^11.0"
        rc, out = _run_as_site_user(
            linux_user,
            app_dir,
            [
                "composer",
                "create-project",
                "--prefer-dist",
                "--no-interaction",
                "laravel/laravel",
                ".",
                ver_constraint,
            ],
            php_version=php_ver,
            timeout=600,
        )
        if rc != 0:
            _rollback_failed_install(purge_files=True)
            raise HTTPException(
                status_code=502,
                detail=f"Lỗi cài đặt Laravel qua Composer ({ver_constraint}): {_short_cmd_err(out)}",
            )
        install_logs.append(
            {
                "cmd": f"composer create-project --prefer-dist laravel/laravel . {ver_constraint}",
                "output": out or f"Created Laravel {raw_lv_ver} project.",
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    if not settings.command_dry_run and not _app_path_exists(
        app_dir, "artisan", linux_user, site_root
    ):
        _rollback_failed_install(purge_files=True)
        raise HTTPException(
            status_code=502,
            detail="Mã nguồn vừa tải về không chứa tệp `artisan` hợp lệ của Laravel. Đã hoàn tác cài đặt.",
        )

    url_scheme = "https" if site.ssl_enabled else "http"
    app_url = f"{url_scheme}://{site.domain}{('/' + subpath_clean) if subpath_clean else ''}"
    generated_key = _generate_laravel_app_key()
    env_content = _render_default_env(
        app_name=payload.app_name or site.domain,
        app_url=app_url,
        db_name=db_info["db_name"],
        db_user=db_info["db_user"],
        db_password=db_info["db_password"],
        app_env="production",
        app_debug=False,
        app_key=generated_key,
        cache_driver=payload.cache_driver or "file",
        queue_connection=payload.queue_connection or "database",
        session_driver=payload.session_driver or "file",
    )
    _write_app_file(linux_user, site_root, app_dir, ".env", env_content, "0640")

    # Generate canonical APP_KEY via real Artisan when live, then link storage & migrate
    rc_key, out_key = _run_as_site_user(
        linux_user,
        app_dir,
        ["artisan", "key:generate", "--force", "--no-interaction"],
        php_version=php_ver,
        timeout=60,
    )
    if rc_key != 0:
        _rollback_failed_install(purge_files=True)
        raise HTTPException(
            status_code=502,
            detail=f"Không thể khởi tạo khóa ứng dụng (`artisan key:generate`): {_short_cmd_err(out_key)}",
        )
    live_env_after_key = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    if live_env_after_key:
        parsed_after_key = _parse_env_details(live_env_after_key)
        if parsed_after_key.get("app_key"):
            generated_key = parsed_after_key["app_key"]

    for art_cmd in (["storage:link", "--no-interaction"], ["migrate", "--force", "--no-interaction"]):
        rc_a, out_a = _run_as_site_user(
            linux_user,
            app_dir,
            ["artisan", *art_cmd],
            php_version=php_ver,
            timeout=180,
        )
        install_logs.append(
            {
                "cmd": f"{Path(php_cli).name} artisan {' '.join(art_cmd)}",
                "output": out_a or ("OK" if rc_a == 0 else "Command failed"),
                "status": "ok" if rc_a == 0 else "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # Rewrite Nginx vhost to point to /public with front_controller / laravel rewrite
    try:
        from app.api.websites import _rewrite_website_vhost

        php_socket = site_users.site_php_fpm_socket(linux_user, str(site_root), php_ver)
        _rewrite_website_vhost(
            site,
            root_path=str(site_root),
            linux_user=linux_user,
            app_type="php",
            php_version=php_ver,
            php_fpm_socket_override=php_socket,
            document_root=new_docroot,
            rewrite_mode="laravel",
        )
    except Exception as exc:
        logger.warning("Nginx rewrite on Laravel install warning: %s", exc)

    # Optionally configure Laravel Task Scheduler cron
    if payload.enable_scheduler:
        try:
            _install_scheduler_cron(site, linux_user, site_root, app_dir)
        except Exception as exc:
            logger.warning("Scheduler setup warning on Laravel install: %s", exc)

    state = _load_toolkit_state()
    inst_key = str(site.id)
    state.setdefault("instances", {})[inst_key] = {
        "installed": True,
        "detached": False,
        "subpath": subpath_clean,
        "app_name": payload.app_name or site.domain,
        "app_env": "production",
        "app_debug": False,
        "app_key_set": True,
        "laravel_version": payload.laravel_version or "11.x",
        "composer_version": "2.8",
        "db_name": db_info["db_name"],
        "db_user": db_info["db_user"],
        "git_repo": payload.git_repo.strip() if payload.install_source == "git" else "",
        "git_branch": payload.git_branch or "main",
        "git_commit": git_commit_sha,
        "storage_linked": True,
        "scheduler_enabled": bool(payload.enable_scheduler),
        "queue_worker": {
            "enabled": bool(payload.enable_queue_worker),
            "workers": payload.queue_workers,
            "queue": "default",
            "tries": 3,
            "timeout": 90,
            "memory": 128,
        },
        "last_artisan_logs": install_logs[-12:],
        "security_overrides": {
            "public_docroot": True,
            "dotenv_git_blocked": True,
            "env_permissions_strict": True,
            "storage_php_blocked": True,
            "secure_session_cookie": True,
        },
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_toolkit_state(state)

    return {
        "message": f"Đã cài đặt dự án Laravel thành công trên {site.domain} (Root: /{new_docroot})!",
        "instance": _build_instance_summary(site, db, state),
        "credentials": {
            "db_name": db_info["db_name"],
            "db_user": db_info["db_user"],
            "db_password": db_info["db_password"],
            "app_key": generated_key,
            "document_root": new_docroot,
        },
    }


@router.post("/instances/{website_id}/artisan")
def run_artisan_command(
    website_id: int,
    payload: LaravelArtisanPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    cmd_key = (payload.command or "").strip().lower()
    if cmd_key not in ALLOWED_ARTISAN_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Lệnh Artisan '{payload.command}' không nằm trong danh sách cho phép.",
        )

    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = state.setdefault("instances", {}).setdefault(inst_key, {"installed": True})
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")
    php_ver = (site.php_version or "8.3").strip()
    php_cli = _php_cli_for_version(php_ver)

    art_args = list(ALLOWED_ARTISAN_COMMANDS[cmd_key])
    secret = ""
    if cmd_key == "down":
        secret = (payload.maintenance_secret or "").strip()
        if secret:
            if not MAINTENANCE_SECRET_RE.fullmatch(secret):
                raise HTTPException(
                    status_code=400,
                    detail="Mã bí mật Bypass Bảo trì chỉ được chứa chữ, số, gạch ngang (4-64 ký tự).",
                )
            art_args.append(f"--secret={secret}")

    was_down_on_disk = (
        _app_path_exists(app_dir, "storage/framework/down", linux_user, site_root)
        or _app_path_exists(app_dir, "storage/framework/maintenance.php", linux_user, site_root)
        or bool(saved.get("maintenance_mode", False))
    )

    if cmd_key == "key:generate":
        env_text = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
        if not env_text:
            url_scheme = "https" if site.ssl_enabled else "http"
            env_text = _render_default_env(
                app_name=saved.get("app_name") or site.domain,
                app_url=f"{url_scheme}://{site.domain}",
                db_name=saved.get("db_name") or "",
                db_user=saved.get("db_user") or "",
                db_password="",
            )
            _write_app_file(linux_user, site_root, app_dir, ".env", env_text, "0640")
        elif settings.command_dry_run:
            updated_env = _update_env_text(env_text, {"APP_KEY": _generate_laravel_app_key()})
            _write_app_file(linux_user, site_root, app_dir, ".env", updated_env, "0640")

    rc, out = _run_as_site_user(
        linux_user,
        app_dir,
        ["artisan", *art_args],
        php_version=php_ver,
        timeout=180,
    )
    cmd_display = f"{Path(php_cli).name} artisan {' '.join(art_args)}"

    if cmd_key == "up":
        # Always attempt safe helper deletion of leftover maintenance files so a broken
        # vendor autoloader cannot permanently trap the site in HTTP 503.
        del_down_ok = _delete_app_file(
            linux_user, site_root, app_dir, "storage/framework/down", strict=False
        )
        _delete_app_file(
            linux_user, site_root, app_dir, "storage/framework/maintenance.php", strict=False
        )
        still_down = _app_path_exists(
            app_dir, "storage/framework/down", linux_user, site_root
        ) or _app_path_exists(
            app_dir, "storage/framework/maintenance.php", linux_user, site_root
        )
        if still_down or (rc != 0 and not was_down_on_disk) or not del_down_ok:
            _append_artisan_log(saved, cmd_display, out or "Failed to exit maintenance mode", ok=False)
            _save_toolkit_state(state)
            raise HTTPException(
                status_code=500,
                detail=f"Không thể tắt chế độ bảo trì trên {site.domain}: {_short_cmd_err(out)}",
            )
        saved["maintenance_mode"] = False
        saved["maintenance_secret"] = ""
        rc = 0
    else:
        if rc != 0:
            _append_artisan_log(saved, cmd_display, out, ok=False)
            _save_toolkit_state(state)
            raise HTTPException(
                status_code=500,
                detail=f"Lệnh `artisan {cmd_key}` thất bại (mã lỗi {rc}): {_short_cmd_err(out)}",
            )
        if cmd_key == "down":
            if settings.command_dry_run:
                _write_app_file(
                    linux_user,
                    site_root,
                    app_dir,
                    "storage/framework/down",
                    json.dumps({"time": int(datetime.now(timezone.utc).timestamp()), "secret": secret}),
                    "0644",
                )
            saved["maintenance_mode"] = True
            saved["maintenance_secret"] = secret
        elif cmd_key == "storage:link":
            saved["storage_linked"] = True
        elif cmd_key == "key:generate":
            saved["app_key_set"] = True
        elif cmd_key in {"queue:flush", "queue:retry"}:
            saved["failed_jobs"] = []

    display_out = out or f"OK: artisan {cmd_key}"
    _append_artisan_log(saved, cmd_display, display_out, ok=True)
    _save_toolkit_state(state)

    return {
        "message": f"Đã thực thi lệnh `artisan {cmd_key}` thành công trên {site.domain}.",
        "command": cmd_display,
        "output": display_out,
        "instance": _build_instance_summary(site, db, state),
    }


@router.post("/instances/{website_id}/queue")
def manage_queue_worker(
    website_id: int,
    payload: LaravelQueuePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = state.setdefault("instances", {}).setdefault(inst_key, {"installed": True})
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")
    php_cli = _php_cli_for_version(site.php_version or "8.3")

    q_cfg = saved.setdefault(
        "queue_worker",
        {
            "enabled": False,
            "workers": 2,
            "queue": "default",
            "tries": 3,
            "timeout": 90,
            "memory": 128,
        },
    )

    if payload.workers is not None:
        q_cfg["workers"] = payload.workers
    if payload.queue is not None:
        clean_q = re.sub(r"[^a-zA-Z0-9_,-]", "", payload.queue.strip()) or "default"
        q_cfg["queue"] = clean_q
    if payload.tries is not None:
        q_cfg["tries"] = payload.tries
    if payload.timeout is not None:
        q_cfg["timeout"] = payload.timeout
    if payload.memory is not None:
        q_cfg["memory"] = payload.memory

    action = (payload.action or "toggle").strip().lower()
    if action == "start":
        q_cfg["enabled"] = True
    elif action == "stop":
        q_cfg["enabled"] = False
    elif action == "restart":
        q_cfg["enabled"] = True
    elif action == "toggle":
        q_cfg["enabled"] = (
            bool(payload.enabled)
            if payload.enabled is not None
            else not bool(q_cfg.get("enabled", False))
        )
    elif action == "retry_failed":
        saved["failed_jobs"] = []
        _run_as_site_user(linux_user, app_dir, [php_cli, "artisan", "queue:retry", "all", "--no-interaction"])
    elif action == "flush_failed":
        saved["failed_jobs"] = []
        _run_as_site_user(linux_user, app_dir, [php_cli, "artisan", "queue:flush", "--no-interaction"])

    if q_cfg["enabled"]:
        _run_as_site_user(linux_user, app_dir, [php_cli, "artisan", "queue:restart", "--no-interaction"])
        cmd_str = (
            f"{Path(php_cli).name} artisan queue:work --queue={q_cfg['queue']} "
            f"--tries={q_cfg['tries']} --timeout={q_cfg['timeout']} --memory={q_cfg['memory']}"
        )
        _append_artisan_log(
            saved,
            cmd_str,
            f"[OK] Queue Worker active ({q_cfg['workers']} tiến trình chạy nền · Tự khởi động lại khi lỗi).",
            ok=True,
        )
        msg = f"Đã bật Laravel Queue Worker ({q_cfg['workers']} workers) cho {site.domain}."
    else:
        _run_as_site_user(linux_user, app_dir, [php_cli, "artisan", "queue:restart", "--no-interaction"])
        _append_artisan_log(
            saved,
            f"{Path(php_cli).name} artisan queue:restart",
            "[OK] Queue Worker stopped gracefully.",
            ok=True,
        )
        msg = f"Đã dừng Laravel Queue Worker trên {site.domain}."

    _save_toolkit_state(state)
    return {
        "message": msg,
        "instance": _build_instance_summary(site, db, state),
    }


def _install_scheduler_cron(
    site: Website, linux_user: str, site_root: Path, app_dir: Path
) -> None:
    """Create `spanel-schedule-cron.php` inside the website's `public_html` and register
    the `* * * * *` cron entry using SPanel's jailed cron service."""
    public_html = site_users.document_root(site.root_path or str(site_root))
    try:
        rel_artisan = os.path.relpath(app_dir / "artisan", public_html).replace("\\", "/")
    except Exception:
        rel_artisan = "artisan"

    bootstrap_php = (
        "<?php\n"
        "// SPanel Managed Laravel Scheduler Cron Bootstrap\n"
        f"$artisan = __DIR__ . '/{rel_artisan}';\n"
        "if (file_exists($artisan)) {\n"
        "    $_SERVER['argv'] = [$artisan, 'schedule:run', '--no-interaction'];\n"
        "    $_SERVER['argc'] = 3;\n"
        "    require $artisan;\n"
        "}\n"
    )
    wordpress_live.write_wp_file_text(
        linux_user,
        site_root,
        Path(public_html),
        "spanel-schedule-cron.php",
        bootstrap_php,
        mode="0644",
    )
    if not _has_scheduler_cron(site, linux_user):
        php_bin = cron.php_binary(site)
        cron.add_cron(
            site,
            "* * * * *",
            f"{php_bin} -q spanel-schedule-cron.php >/dev/null 2>&1",
        )


def _remove_scheduler_cron(site: Website, linux_user: str) -> None:
    try:
        entries = cron.list_cron_entries(site.domain, linux_user)
        for item in reversed(entries):
            cmd = (item.get("command") or "").lower()
            if "schedule:run" in cmd or "spanel-schedule-cron.php" in cmd:
                cron.delete_cron(site.domain, int(item["index"]), linux_user)
    except Exception as exc:
        logger.debug("remove_scheduler_cron warning: %s", exc)


@router.post("/instances/{website_id}/scheduler")
def toggle_laravel_scheduler(
    website_id: int,
    payload: LaravelSchedulerPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = state.setdefault("instances", {}).setdefault(inst_key, {"installed": True})
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    if payload.enabled:
        try:
            _install_scheduler_cron(site, linux_user, site_root, app_dir)
        except Exception as exc:
            logger.warning("Failed to install scheduler cron line: %s", exc)
        saved["scheduler_enabled"] = True
        msg = f"Đã bật Laravel Scheduler (`* * * * * php{site.php_version or '8.3'} artisan schedule:run`) cho {site.domain}."
    else:
        _remove_scheduler_cron(site, linux_user)
        saved["scheduler_enabled"] = False
        msg = f"Đã tắt Laravel Scheduler (Cron) trên {site.domain}."

    _save_toolkit_state(state)
    return {
        "message": msg,
        "instance": _build_instance_summary(site, db, state),
    }


@router.get("/instances/{website_id}/env")
def get_laravel_env(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    env_text = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    if not env_text:
        url_scheme = "https" if site.ssl_enabled else "http"
        env_text = _render_default_env(
            app_name=saved.get("app_name") or site.domain,
            app_url=f"{url_scheme}://{site.domain}",
            db_name=saved.get("db_name") or "",
            db_user=saved.get("db_user") or "",
            db_password="",
        )
    parsed = _parse_env_details(env_text)
    return {
        "raw_env": env_text,
        "parsed": parsed,
    }


@router.put("/instances/{website_id}/env")
def update_laravel_env(
    website_id: int,
    payload: LaravelEnvUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = state.setdefault("instances", {}).setdefault(inst_key, {"installed": True})
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    current_env = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    if current_env:
        _write_app_file(linux_user, site_root, app_dir, ".env.bak", current_env, "0640")

    if payload.raw_env is not None:
        if "\x00" in payload.raw_env:
            raise HTTPException(status_code=400, detail="Invalid null byte in .env content")
        new_env_text = payload.raw_env.replace("\r\n", "\n").rstrip() + "\n"
    else:
        updates: Dict[str, Any] = dict(payload.variables or {})
        field_map = [
            ("app_name", "APP_NAME"),
            ("app_env", "APP_ENV"),
            ("app_debug", "APP_DEBUG"),
            ("app_url", "APP_URL"),
            ("cache_driver", "CACHE_STORE"),
            ("cache_driver", "CACHE_DRIVER"),
            ("queue_connection", "QUEUE_CONNECTION"),
            ("session_driver", "SESSION_DRIVER"),
            ("db_host", "DB_HOST"),
            ("db_port", "DB_PORT"),
            ("db_name", "DB_DATABASE"),
            ("db_user", "DB_USERNAME"),
            ("db_password", "DB_PASSWORD"),
            ("redis_host", "REDIS_HOST"),
            ("redis_port", "REDIS_PORT"),
            ("mail_mailer", "MAIL_MAILER"),
            ("mail_host", "MAIL_HOST"),
            ("mail_port", "MAIL_PORT"),
            ("mail_username", "MAIL_USERNAME"),
            ("mail_password", "MAIL_PASSWORD"),
            ("mail_encryption", "MAIL_ENCRYPTION"),
            ("mail_from_address", "MAIL_FROM_ADDRESS"),
        ]
        for attr, env_key in field_map:
            val = getattr(payload, attr, None)
            if val is not None:
                updates[env_key] = val
        base_env = current_env or _render_default_env(
            app_name=saved.get("app_name") or site.domain,
            app_url=f"{'https' if site.ssl_enabled else 'http'}://{site.domain}",
            db_name=saved.get("db_name") or "",
            db_user=saved.get("db_user") or "",
            db_password="",
        )
        new_env_text = _update_env_text(base_env, updates)

    _write_app_file(linux_user, site_root, app_dir, ".env", new_env_text, "0640")
    parsed = _parse_env_details(new_env_text)
    saved["app_name"] = parsed["app_name"]
    saved["app_env"] = parsed["app_env"]
    saved["app_debug"] = parsed["app_debug"]

    if payload.php_version and payload.php_version.strip() != site.php_version:
        site.php_version = payload.php_version.strip()
        db.commit()

    php_cli = _php_cli_for_version(site.php_version or "8.3")
    _run_as_site_user(linux_user, app_dir, [php_cli, "artisan", "config:clear", "--no-interaction"])
    _append_artisan_log(
        saved,
        f"{Path(php_cli).name} artisan config:clear",
        "[OK] Saved .env (backup .env.bak created) & cleared configuration cache.",
        ok=True,
    )
    _save_toolkit_state(state)

    return {
        "message": f"Đã lưu cấu hình `.env` và làm mới bộ đệm cấu hình cho {site.domain}.",
        "raw_env": new_env_text,
        "parsed": parsed,
        "instance": _build_instance_summary(site, db, state),
    }


@router.post("/instances/{website_id}/composer")
def run_composer_action(
    website_id: int,
    payload: LaravelComposerPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = state.setdefault("instances", {}).setdefault(inst_key, {"installed": True})
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    php_ver = (site.php_version or "8.3").strip()
    action = (payload.action or "").strip().lower()

    if action == "install":
        cmd = ["composer", "install", "--no-dev", "--optimize-autoloader", "--no-interaction"]
        rc, out = _run_as_site_user(linux_user, app_dir, cmd, php_version=php_ver, timeout=600)
        msg = f"Đã chạy `composer install` cho {site.domain}."
    elif action == "update":
        cmd = ["composer", "update", "--with-all-dependencies", "--no-interaction"]
        rc, out = _run_as_site_user(linux_user, app_dir, cmd, php_version=php_ver, timeout=600)
        msg = f"Đã cập nhật các gói Composer cho {site.domain}."
    elif action == "dump_autoload":
        cmd = ["composer", "dump-autoload", "-o", "--no-interaction"]
        rc, out = _run_as_site_user(linux_user, app_dir, cmd, php_version=php_ver, timeout=180)
        msg = f"Đã tối ưu Classmap Autoload (`composer dump-autoload -o`) cho {site.domain}."
    elif action == "require":
        pkg = (payload.package or "").strip().lower()
        if not pkg or not COMPOSER_PKG_RE.fullmatch(pkg):
            raise HTTPException(
                status_code=400,
                detail="Tên gói Composer không hợp lệ (ví dụ hợp lệ: vendor/package hoặc vendor/package:^1.0).",
            )
        pkg_name, _, pkg_ver = pkg.partition(":")
        cmd = [
            "composer",
            "require",
            pkg,
            *(["--dev"] if payload.dev else []),
            "--optimize-autoloader",
            "--no-interaction",
        ]
        rc, out = _run_as_site_user(linux_user, app_dir, cmd, php_version=php_ver, timeout=600)
        if rc == 0 and settings.command_dry_run:
            composer_raw = _read_app_file(
                app_dir, "composer.json", linux_user=linux_user, site_root=site_root
            )
            if composer_raw:
                try:
                    comp_data = json.loads(composer_raw)
                    sec = "require-dev" if payload.dev else "require"
                    comp_data.setdefault(sec, {})[pkg_name] = pkg_ver or "^1.0"
                    _write_app_file(
                        linux_user,
                        site_root,
                        app_dir,
                        "composer.json",
                        json.dumps(comp_data, indent=4, ensure_ascii=False) + "\n",
                        "0644",
                    )
                except Exception:
                    pass
        msg = f"Đã cài đặt gói Composer `{pkg_name}` vào {site.domain}."
    elif action == "remove":
        pkg = (payload.package or "").strip().lower().split(":")[0]
        if not pkg or not COMPOSER_PKG_RE.fullmatch(pkg):
            raise HTTPException(status_code=400, detail="Tên gói Composer không hợp lệ.")
        if pkg == "laravel/framework":
            raise HTTPException(
                status_code=400, detail="Không thể gỡ bỏ gói lõi `laravel/framework`."
            )
        cmd = [
            "composer",
            "remove",
            pkg,
            *(["--dev"] if payload.dev else []),
            "--no-interaction",
        ]
        rc, out = _run_as_site_user(linux_user, app_dir, cmd, php_version=php_ver, timeout=600)
        if rc == 0 and settings.command_dry_run:
            composer_raw = _read_app_file(
                app_dir, "composer.json", linux_user=linux_user, site_root=site_root
            )
            if composer_raw:
                try:
                    comp_data = json.loads(composer_raw)
                    for sec in ("require", "require-dev"):
                        if isinstance(comp_data.get(sec), dict):
                            comp_data[sec].pop(pkg, None)
                    _write_app_file(
                        linux_user,
                        site_root,
                        app_dir,
                        "composer.json",
                        json.dumps(comp_data, indent=4, ensure_ascii=False) + "\n",
                        "0644",
                    )
                except Exception:
                    pass
        msg = f"Đã gỡ bỏ gói Composer `{pkg}` khỏi {site.domain}."
    elif action == "git_pull":
        if payload.git_repo is not None:
            clean_repo = payload.git_repo.strip()
            if clean_repo and not GIT_URL_RE.fullmatch(clean_repo):
                raise HTTPException(status_code=400, detail="Địa chỉ Git Repository không hợp lệ.")
            saved["git_repo"] = clean_repo
        if payload.git_branch is not None:
            clean_branch = payload.git_branch.strip() or "main"
            if not GIT_BRANCH_RE.fullmatch(clean_branch):
                raise HTTPException(status_code=400, detail="Tên nhánh Git không hợp lệ.")
            saved["git_branch"] = clean_branch
        branch = saved.get("git_branch") or "main"
        cmd = [
            "git",
            "-c",
            "credential.interactive=never",
            "-c",
            "core.askPass=",
            "pull",
            "--ff-only",
            "origin",
            branch,
        ]
        rc, out = _run_as_site_user(linux_user, app_dir, cmd, php_version=php_ver, timeout=300)
        if rc == 0:
            rc_sha, sha_out = _run_as_site_user(
                linux_user,
                app_dir,
                ["git", "rev-parse", "--short", "HEAD"],
                php_version=php_ver,
                timeout=30,
            )
            if rc_sha == 0 and sha_out and not sha_out.startswith("[DRY-RUN]"):
                saved["git_commit"] = sha_out.splitlines()[-1].strip()[:16]
        msg = f"Đã kéo mã nguồn mới nhất từ nhánh `{branch}` và đồng bộ ứng dụng."
    else:
        raise HTTPException(status_code=400, detail=f"Hành động Composer không hợp lệ: {action}")

    if rc != 0:
        _append_artisan_log(saved, " ".join(cmd), out, ok=False)
        _save_toolkit_state(state)
        raise HTTPException(
            status_code=500,
            detail=f"Thực thi `{action}` thất bại (mã lỗi {rc}): {_short_cmd_err(out)}",
        )

    _append_artisan_log(saved, " ".join(cmd), out or msg, ok=True)
    _save_toolkit_state(state)
    return {
        "message": msg,
        "output": out or msg,
        "instance": _build_instance_summary(site, db, state),
    }


@router.post("/instances/{website_id}/security")
def manage_laravel_security(
    website_id: int,
    payload: LaravelSecurityPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = state.setdefault("instances", {}).setdefault(inst_key, {"installed": True})
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")
    sec_overrides = saved.setdefault("security_overrides", {})

    action = (payload.action or "secure").strip().lower()
    target_ids = payload.check_ids or [c["id"] for c in SECURITY_CHECK_DEFINITIONS]
    enable = action != "revert"

    env_text = _read_app_file(app_dir, ".env", linux_user=linux_user, site_root=site_root)
    env_updates: Dict[str, Any] = {}

    if action == "fix_docroot" or ("public_docroot" in target_ids and enable):
        subpath_clean = (saved.get("subpath") or "").strip("/")
        site.document_root = (
            f"public_html/{subpath_clean}/public" if subpath_clean else "public_html/public"
        )
        site.nginx_rewrite_mode = "laravel"
        db.commit()
        sec_overrides["public_docroot"] = True
        try:
            from app.api.websites import _rewrite_website_vhost

            php_ver = (site.php_version or "8.3").strip()
            php_socket = site_users.site_php_fpm_socket(linux_user, str(site_root), php_ver)
            _rewrite_website_vhost(
                site,
                root_path=str(site_root),
                linux_user=linux_user,
                app_type="php",
                php_version=php_ver,
                php_fpm_socket_override=php_socket,
                document_root=site.document_root,
                rewrite_mode="laravel",
            )
        except Exception as exc:
            logger.warning("Nginx rewrite on fix_docroot warning: %s", exc)

    for cid in target_ids:
        if cid == "app_debug_off" and enable:
            env_updates["APP_DEBUG"] = False
            saved["app_debug"] = False
        elif cid == "app_key_valid" and enable:
            parsed = _parse_env_details(env_text)
            if not (parsed.get("app_key") or "").startswith("base64:"):
                env_updates["APP_KEY"] = _generate_laravel_app_key()
                saved["app_key_set"] = True
        elif cid == "secure_session_cookie":
            env_updates["SESSION_SECURE_COOKIE"] = enable
            sec_overrides["secure_session_cookie"] = enable
        elif cid == "storage_php_blocked":
            sec_overrides["storage_php_blocked"] = enable
            if enable:
                _write_app_file(
                    linux_user,
                    site_root,
                    app_dir,
                    "storage/app/public/.htaccess",
                    "<Files *.php>\n    Deny from all\n</Files>\n",
                    "0644",
                )
        else:
            sec_overrides[cid] = enable

    if env_updates:
        updated_env = _update_env_text(env_text, env_updates)
        _write_app_file(linux_user, site_root, app_dir, ".env", updated_env, "0640")

    _save_toolkit_state(state)
    return {
        "message": (
            f"Đã áp dụng {len(target_ids)} tiêu chuẩn bảo mật Laravel cho {site.domain}."
            if enable
            else f"Đã hoàn tác cấu hình bảo mật đã chọn trên {site.domain}."
        ),
        "instance": _build_instance_summary(site, db, state),
    }


@router.get("/instances/{website_id}/logs")
def get_laravel_logs(
    website_id: int,
    lines: int = Query(default=150, ge=20, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    raw_log = _read_app_file(
        app_dir, "storage/logs/laravel.log", linux_user=linux_user, site_root=site_root
    )
    log_lines = raw_log.splitlines()[-lines:] if raw_log else []
    return {
        "log_path": str(app_dir / "storage" / "logs" / "laravel.log"),
        "content": "\n".join(log_lines),
        "lines_count": len(log_lines),
        "artisan_history": list(saved.get("last_artisan_logs") or []),
    }


@router.delete("/instances/{website_id}/logs")
def clear_laravel_logs(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    _write_app_file(linux_user, site_root, app_dir, "storage/logs/laravel.log", "", "0644")
    return {"message": f"Đã dọn sạch tệp `storage/logs/laravel.log` của {site.domain}."}


@router.post("/instances/{website_id}/detach")
def detach_laravel_instance(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {})
    inst["detached"] = True
    _save_toolkit_state(state)
    return {
        "message": f"Đã ngắt liên kết {site.domain} khỏi Laravel Manager (giữ nguyên mã nguồn & cơ sở dữ liệu)."
    }


@router.post("/instances/{website_id}/remove")
def remove_laravel_instance(
    website_id: int,
    drop_database: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = (state.get("instances") or {}).get(inst_key) or {}
    linux_user, site_root, app_dir = _resolve_laravel_paths(site, db, saved.get("subpath") or "")

    _remove_scheduler_cron(site, linux_user)

    for rel in ("artisan", ".env", ".env.bak"):
        _delete_app_file(linux_user, site_root, app_dir, rel)

    if drop_database:
        db_acc = wordpress_live.resolve_owned_wp_database(db, site, saved.get("db_name"))
        if db_acc and db_acc.owner_id == site.owner_id:
            try:
                mariadb.drop_database_and_user(db_acc.db_name, db_acc.db_user)
            except Exception as exc:
                logger.warning("Drop Laravel database warning: %s", exc)
            db.delete(db_acc)

    site.document_root = "public_html"
    site.nginx_rewrite_mode = "front_controller"
    db.commit()

    if inst_key in (state.get("instances") or {}):
        state["instances"].pop(inst_key, None)
    _save_toolkit_state(state)
    return {
        "message": f"Đã gỡ cài đặt dự án Laravel khỏi {site.domain}."
        + (" (Đã xóa Database đính kèm)" if drop_database else "")
    }
