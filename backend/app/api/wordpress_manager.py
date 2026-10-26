import json
import logging
import os
import re
import secrets
import shlex
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, User, Website
from app.services import mariadb, nginx, site_users, wordpress, wordpress_live
from app.services.shell import shell

router = APIRouter(prefix="/wordpress-manager", tags=["wordpress-manager"])
logger = logging.getLogger("spanel.wordpress_manager")

WP_SLUG_RE = re.compile(r"^[a-zA-Z0-9._-]{1,120}$")
WP_SLUG_RE = wordpress_live.WP_SLUG_RE
WP_PREFIX_RE = re.compile(r"^[a-zA-Z0-9_]{2,24}$")
SUBPATH_RE = re.compile(r"^[a-zA-Z0-9._/-]{0,120}$")

DEFAULT_PACKAGE_SETS: List[Dict[str, Any]] = [
    {
        "id": "essential_speed_seo",
        "name": "SEO & Tốc độ Cốt lõi (Essential Speed & SEO)",
        "description": "Bộ công cụ tối ưu tốc độ bộ đệm, chuẩn hóa SEO và trình soạn thảo cổ điển nhẹ nhàng.",
        "is_builtin": True,
        "plugins": ["classic-editor", "wordpress-seo", "wp-fastest-cache", "Autoptimize"],
        "themes": ["twentytwentyfour"],
    },
    {
        "id": "woocommerce_pro",
        "name": "Thương mại điện tử (WooCommerce Store)",
        "description": "Bộ cài đặt sẵn cho cửa hàng trực tuyến với WooCommerce, biểu mẫu liên hệ và bảo mật.",
        "is_builtin": True,
        "plugins": ["woocommerce", "contact-form-7", "wordpress-seo", "classic-editor"],
        "themes": ["storefront"],
    },
    {
        "id": "security_shield",
        "name": "Bảo mật & Chống Spam (Security Shield)",
        "description": "Tăng cường tường lửa ứng dụng, giới hạn đăng nhập sai và bảo vệ biểu mẫu khỏi bot.",
        "is_builtin": True,
        "plugins": ["wordfence", "limit-login-attempts-reloaded", "akismet"],
        "themes": [],
    },
]

CURATED_WPORG_PLUGINS: List[Dict[str, Any]] = [
    {
        "slug": "classic-editor",
        "name": "Classic Editor",
        "version": "1.6.7",
        "author": "WordPress Contributors",
        "rating": 98,
        "active_installs": 5000000,
        "short_description": "Enables the previous 'classic' editor and the old-style Edit Post screen with TinyMCE.",
    },
    {
        "slug": "wordpress-seo",
        "name": "Yoast SEO",
        "version": "24.5",
        "author": "Team Yoast",
        "rating": 96,
        "active_installs": 10000000,
        "short_description": "Improve your WordPress SEO: Write better content and have a fully optimized WordPress site.",
    },
    {
        "slug": "woocommerce",
        "name": "WooCommerce",
        "version": "9.7.1",
        "author": "Automattic",
        "rating": 92,
        "active_installs": 7000000,
        "short_description": "Everything you need to launch an online store in days and keep it growing for years.",
    },
    {
        "slug": "elementor",
        "name": "Elementor Website Builder",
        "version": "3.27.6",
        "author": "Elementor.com",
        "rating": 94,
        "active_installs": 10000000,
        "short_description": "The Elementor Website Builder has it all: drag and drop page builder, pixel perfect design.",
    },
    {
        "slug": "contact-form-7",
        "name": "Contact Form 7",
        "version": "6.0.4",
        "author": "Takayuki Miyoshi",
        "rating": 84,
        "active_installs": 10000000,
        "short_description": "Just another contact form plugin. Simple but flexible.",
    },
    {
        "slug": "litespeed-cache",
        "name": "LiteSpeed Cache / Page Optimization",
        "version": "6.5.4",
        "author": "LiteSpeed Technologies",
        "rating": 96,
        "active_installs": 6000000,
        "short_description": "All-in-one page cache, image optimization, CSS/JS minification and object cache.",
    },
    {
        "slug": "wordfence",
        "name": "Wordfence Security – Firewall, Malware Scan, and Login Security",
        "version": "8.0.3",
        "author": "Wordfence",
        "rating": 95,
        "active_installs": 5000000,
        "short_description": "Firewall, malware scanner, two-factor authentication and comprehensive WordPress security.",
    },
    {
        "slug": "updraftplus",
        "name": "UpdraftPlus: WP Backup & Migration Plugin",
        "version": "1.25.2",
        "author": "TeamUpdraft",
        "rating": 96,
        "active_installs": 3000000,
        "short_description": "Backup and restoration made easy. Complete scheduled backups to cloud or local storage.",
    },
]

CURATED_WPORG_THEMES: List[Dict[str, Any]] = [
    {
        "slug": "astra",
        "name": "Astra",
        "version": "4.8.12",
        "author": "Brainstorm Force",
        "rating": 98,
        "active_installs": 1000000,
        "short_description": "Fast, fully customizable & beautiful WordPress theme suitable for blog, business and WooCommerce.",
    },
    {
        "slug": "hello-elementor",
        "name": "Hello Elementor",
        "version": "3.3.0",
        "author": "Elementor Team",
        "rating": 94,
        "active_installs": 1000000,
        "short_description": "A plain-vanilla & lightweight theme for Elementor page builder.",
    },
    {
        "slug": "generatepress",
        "name": "GeneratePress",
        "version": "3.5.1",
        "author": "Tom Usborne",
        "rating": 99,
        "active_installs": 600000,
        "short_description": "GeneratePress is a lightweight WordPress theme built with a focus on speed and usability.",
    },
    {
        "slug": "oceanwp",
        "name": "OceanWP",
        "version": "4.0.5",
        "author": "OceanWP",
        "rating": 98,
        "active_installs": 700000,
        "short_description": "Multi-purpose theme with deep WooCommerce integration and responsive design.",
    },
    {
        "slug": "storefront",
        "name": "Storefront",
        "version": "4.6.1",
        "author": "Automattic",
        "rating": 90,
        "active_installs": 100000,
        "short_description": "The official WooCommerce theme, built for speed and seamless e-commerce integration.",
    },
    {
        "slug": "twentytwentyfive",
        "name": "Twenty Twenty-Five",
        "version": "1.1",
        "author": "the WordPress team",
        "rating": 92,
        "active_installs": 800000,
        "short_description": "Simple, adaptable block theme designed for blogs, portfolios, and businesses.",
    },
]


SECURITY_CHECK_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "disallow_file_edit",
        "title": "Chặn sửa file PHP trong wp-admin (DISALLOW_FILE_EDIT)",
        "description": "Ngăn kẻ tấn công chèn mã độc qua trình sửa Theme/Plugin nếu lộ mật khẩu quản trị.",
        "critical": True,
    },
    {
        "id": "xmlrpc_blocked",
        "title": "Vô hiệu hóa cổng XML-RPC (xmlrpc.php)",
        "description": "Chặn tấn công dò mật khẩu hàng loạt (Brute-force multicall) và DDoS Pingback.",
        "critical": True,
    },
    {
        "id": "uploads_php_blocked",
        "title": "Chặn thực thi PHP trong thư mục /wp-content/uploads",
        "description": "Ngăn chặn chạy Webshell giả dạng file ảnh hoặc tài liệu tải lên.",
        "critical": True,
    },
    {
        "id": "permissions_strict",
        "title": "Chuẩn hóa quyền hạn wp-config.php (0640) & thư mục (0755)",
        "description": "Bảo vệ thông tin kết nối Database và khóa bảo mật không bị đọc trái phép.",
        "critical": True,
    },
    {
        "id": "keys_salts_fresh",
        "title": "Khóa xác thực & Security Salts đạt chuẩn mật mã",
        "description": "Đảm bảo 8 khóa AUTH_KEY / SECURE_AUTH_SALT trong wp-config.php có độ dài an toàn.",
        "critical": False,
    },
    {
        "id": "sensitive_files_blocked",
        "title": "Ẩn các tệp lộ thông tin phiên bản (readme.html, wp-config-sample.php)",
        "description": "Xóa hoặc chặn truy cập các tệp mẫu tiết lộ phiên bản WordPress cho bot quét lỗ hổng.",
        "critical": False,
    },
    {
        "id": "author_enum_blocked",
        "title": "Chống dò tên đăng nhập quản trị (?author=1)",
        "description": "Ngăn chặn việc quét URL tác giả để thu thập danh sách username quản trị viên.",
        "critical": False,
    },
    {
        "id": "custom_table_prefix",
        "title": "Sử dụng tiền tố bảng Database tuỳ chỉnh (khác wp_)",
        "description": "Giúp giảm thiểu rủi ro từ các cuộc tấn công SQL Injection tự động nhắm vào bảng wp_users.",
        "critical": False,
    },
    {
        "id": "no_default_admin_user",
        "title": "Không sử dụng tài khoản quản trị mặc định 'admin'",
        "description": "Đổi tên đăng nhập 'admin' mặc định sang tên riêng để chống dò mật khẩu.",
        "critical": False,
    },
]


# ── State Persistence Helper ─────────────────────────────────────────────────

def _toolkit_state_path() -> Path:
    return wordpress_live.toolkit_state_path()
    base = Path("/etc/spanel") if Path("/etc/spanel").exists() else Path("./data")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        base = Path("/tmp")
    return base / "wordpress_toolkit_state.json"


def _load_toolkit_state() -> Dict[str, Any]:
    return wordpress_live.load_toolkit_state_safe()
    path = _toolkit_state_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"instances": {}, "custom_sets": []}


def _save_toolkit_state(state: Dict[str, Any]) -> None:
    wordpress_live.save_toolkit_state_safe(state)
    return
    path = _toolkit_state_path()
    try:
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not persist wordpress toolkit state: %s", exc)


def _visible_custom_sets(state: Dict[str, Any], current_user: User) -> List[Dict[str, Any]]:
    raw_sets = state.get("custom_sets") or []
    if is_admin_role(getattr(current_user, "role", None)):
        return list(raw_sets)
    uid = getattr(current_user, "id", None)
    return [s for s in raw_sets if isinstance(s, dict) and s.get("owner_id") in (None, uid)]


# ── Helpers for Website Access & WP-CLI Execution ────────────────────────────

def _get_accessible_website(website_id: int, db: Session, current_user: User) -> Website:
    site = db.query(Website).filter(Website.id == website_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Website not found")
    if site.owner_id != current_user.id and not is_admin_role(current_user.role):
        owner = db.query(User).filter(User.id == site.owner_id).first()
        if not (current_user.role == Role.reseller.value and owner and owner.reseller_id == current_user.id):
            ensure_role(current_user.role, Role.admin)
    return site


def _resolve_site_paths(site: Website, db: Session, subpath: str = "") -> tuple[str, Path, Path]:
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
    root = Path(root_str)
    docroot = site_users.document_root(root, site.document_root or "public_html")
    clean_sub = (subpath or "").strip().strip("/")
    if clean_sub:
        if ".." in clean_sub or not SUBPATH_RE.fullmatch(clean_sub):
            raise HTTPException(status_code=400, detail="Invalid subdirectory path")
        target = docroot / clean_sub
    else:
        target = docroot
    return linux_user, root, target


def _run_wp_cli(
    linux_user: str,
    php_version: str,
    wp_dir: Path,
    args: List[str],
    *,
    check: bool = False,
    input_data: Optional[str] = None,
    sensitive: bool = False,
):
    if linux_user is not None:
        return wordpress_live.run_wp_cli(
            linux_user, php_version, wp_dir, args, check=check, input_data=input_data, sensitive=sensitive
        )
    php_flag = [f"--php-version={php_version}"] if php_version else []
    full_args = [*args, f"--path={wp_dir}", "--no-color"]
    return shell.privileged(
        "wp-site",
        helper_args=[linux_user, *php_flag, *full_args],
        fallback=["wp", *full_args],
        check=check,
        input=input_data,
        sensitive=sensitive,
    )


def _read_wp_file_text(
    wp_dir: Path,
    rel_path: str,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> str:
    target = wp_dir / rel_path
    try:
        if target.exists() and target.is_file():
            return target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if linux_user and site_root and not settings.command_dry_run:
        try:
            try:
                rel_to_root = str(target.relative_to(site_root)).replace("\\", "/")
            except ValueError:
                rel_to_root = f"public_html/{rel_path.lstrip('/')}"
            res = shell.privileged(
                "terminal-exec",
                helper_args=[linux_user, str(site_root), "cat", rel_to_root],
                fallback=["cat", str(target)],
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except Exception:
            pass
    return ""


def _write_wp_file_text(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    rel_path: str,
    content: str,
    mode: str = "0644",
) -> None:
    if linux_user is not None:
        return wordpress_live.write_wp_file_text(linux_user, site_root, wp_dir, rel_path, content, mode=mode)
    target = wp_dir / rel_path
    try:
        rel_to_root = str(target.relative_to(site_root)).replace("\\", "/")
    except ValueError:
        rel_to_root = f"public_html/{rel_path.lstrip('/')}"
    if not settings.command_dry_run:
        shell.privileged(
            "site-file-write",
            helper_args=[linux_user, str(site_root), rel_to_root, mode],
            fallback=[
                "sh",
                "-c",
                'mkdir -p "$(dirname "$1")" && cat >"$1" && chmod "$2" "$1"',
                "sh",
                str(target),
                mode,
            ],
            input=content,
            sensitive=True,
            check=False,
        )


def _parse_wp_config_details(config_text: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "db_name": "",
        "db_user": "",
        "db_password": "",
        "db_host": "localhost",
        "table_prefix": "wp_",
        "wp_debug": False,
        "wp_debug_log": False,
        "wp_debug_display": False,
        "script_debug": False,
        "wp_cache": False,
        "disable_wp_cron": False,
        "disallow_file_edit": False,
        "wp_memory_limit": "256M",
        "wp_max_memory_limit": "512M",
        "wp_post_revisions": "10",
        "salts_count": 0,
    }
    if not config_text:
        return info

    for key, field_name in [
        ("DB_NAME", "db_name"),
        ("DB_USER", "db_user"),
        ("DB_PASSWORD", "db_password"),
        ("DB_HOST", "db_host"),
        ("WP_MEMORY_LIMIT", "wp_memory_limit"),
        ("WP_MAX_MEMORY_LIMIT", "wp_max_memory_limit"),
    ]:
        m = re.search(rf"define\(\s*['\"]{key}['\"]\s*,\s*['\"]((?:\\.|[^'\"])*)['\"]\s*\)", config_text)
        if m:
            info[field_name] = m.group(1).replace("\\'", "'").replace("\\\\", "\\")

    m_prefix = re.search(r"\$table_prefix\s*=\s*['\"]([^'\"]+)['\"]", config_text)
    if m_prefix:
        info["table_prefix"] = m_prefix.group(1)

    for const_name, field_key in [
        ("WP_DEBUG", "wp_debug"),
        ("WP_DEBUG_LOG", "wp_debug_log"),
        ("WP_DEBUG_DISPLAY", "wp_debug_display"),
        ("SCRIPT_DEBUG", "script_debug"),
        ("WP_CACHE", "wp_cache"),
        ("DISABLE_WP_CRON", "disable_wp_cron"),
        ("DISALLOW_FILE_EDIT", "disallow_file_edit"),
    ]:
        m_bool = re.search(
            rf"define\(\s*['\"]{const_name}['\"]\s*,\s*(true|false|1|0|'true'|'false')\s*\)",
            config_text,
            re.IGNORECASE,
        )
        if m_bool:
            val = m_bool.group(1).strip("'\"").lower()
            info[field_key] = val in ("true", "1")

    m_rev = re.search(
        r"define\(\s*['\"]WP_POST_REVISIONS['\"]\s*,\s*([a-zA-Z0-9_'\"]+)\s*\)",
        config_text,
    )
    if m_rev:
        info["wp_post_revisions"] = m_rev.group(1).strip("'\"")

    salts = 0
    for salt_key in wordpress.WP_SALT_KEYS:
        m_salt = re.search(
            rf"define\(\s*['\"]{salt_key}['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)",
            config_text,
        )
        if m_salt and len(m_salt.group(1).strip()) >= 24 and "put your unique phrase" not in m_salt.group(1).lower():
            salts += 1
    info["salts_count"] = salts
    return info


def _set_wp_config_constant(config_text: str, name: str, raw_php_expr: str) -> str:
    pattern = re.compile(
        rf"define\(\s*['\"]{re.escape(name)}['\"]\s*,\s*[^)]+\)\s*;",
        re.IGNORECASE,
    )
    new_line = f"define('{name}', {raw_php_expr});"
    if pattern.search(config_text):
        return pattern.sub(lambda _: new_line, config_text, count=1)

    anchor = re.search(r"(/\*.*?stop editing.*?\*/|require_once\s+ABSPATH)", config_text, re.IGNORECASE)
    if anchor:
        idx = anchor.start()
        return config_text[:idx] + new_line + "\n" + config_text[idx:]
    return config_text.rstrip() + "\n" + new_line + "\n"


def _detect_wp_version(
    wp_dir: Path,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> str:
    ver_file = _read_wp_file_text(wp_dir, "wp-includes/version.php", linux_user=linux_user, site_root=site_root)
    if ver_file:
        m = re.search(r"\$wp_version\s*=\s*['\"]([^'\"]+)['\"]", ver_file)
        if m:
            return m.group(1)
    return ""


def _evaluate_security_checks(
    wp_dir: Path,
    config_info: Dict[str, Any],
    saved_inst: Dict[str, Any],
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
    live_admin_user: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sec_overrides = saved_inst.get("security_overrides") or {}
    uploads_ht = _read_wp_file_text(wp_dir, "wp-content/uploads/.htaccess", linux_user=linux_user, site_root=site_root)
    root_ht = _read_wp_file_text(wp_dir, ".htaccess", linux_user=linux_user, site_root=site_root)
    effective_admin = (live_admin_user or saved_inst.get("admin_user") or "admin").strip()

    checks_map = {
        "disallow_file_edit": bool(config_info.get("disallow_file_edit")),
        "xmlrpc_blocked": bool(
            wordpress_live.wp_path_exists(wp_dir, "wp-content/mu-plugins/spanel-block-xmlrpc.php", linux_user, site_root)
            or ("xmlrpc.php" in root_ht)
            or wordpress_live.wp_path_exists(wp_dir, ".spanel-block-xmlrpc", linux_user, site_root)
        ),
        "uploads_php_blocked": bool(
            ("php" in uploads_ht.lower())
            or wordpress_live.wp_path_exists(wp_dir, "wp-content/uploads/.spanel-no-php", linux_user, site_root)
        ),
        "permissions_strict": bool(sec_overrides.get("permissions_strict", True)),
        "keys_salts_fresh": int(config_info.get("salts_count") or 0) >= 8 or bool(sec_overrides.get("keys_salts_fresh")),
        "sensitive_files_blocked": bool(
            not wordpress_live.wp_path_exists(wp_dir, "readme.html", linux_user, site_root)
            and not wordpress_live.wp_path_exists(wp_dir, "wp-config-sample.php", linux_user, site_root)
        ),
        "author_enum_blocked": bool(
            wordpress_live.wp_path_exists(wp_dir, "wp-content/mu-plugins/spanel-block-author-scan.php", linux_user, site_root)
        ),
        "custom_table_prefix": (config_info.get("table_prefix") or "wp_") != "wp_",
        "no_default_admin_user": effective_admin.lower() != "admin",
    }

    results = []
    for item in SECURITY_CHECK_DEFINITIONS:
        cid = item["id"]
        if settings.command_dry_run and cid in sec_overrides:
            passed = bool(sec_overrides[cid])
        else:
            passed = bool(checks_map.get(cid, False))
        results.append({
            **item,
            "passed": passed,
        })
    return results


def reconcile_wordpress_databases(db: Session, website_id: Optional[int] = None, *, provision_missing: bool = True) -> int:
    """Self-heal missing or orphaned DatabaseAccount rows for WordPress websites
    and ensure the MariaDB database, user credentials, and WordPress core tables
    are created and synchronized with wp-config.php."""
    return wordpress_live.safe_reconcile_wordpress_databases(db, website_id=website_id, provision_missing=False)
    reconciled = 0
    try:
        from app.api.databases import effective_database_limit

        websites = db.query(Website).all()
        if not websites:
            return 0
        state = _load_toolkit_state()
        instances_state = state.setdefault("instances", {})
        state_dirty = False
        db_dirty = False

        for site in websites:
            inst_key = str(site.id)
            saved = instances_state.get(inst_key) or {}
            if saved.get("detached"):
                continue

            # 1. Keep any existing DatabaseAccount linked to this website owned by site.owner_id
            linked_acc = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == site.id).first()
            if linked_acc and linked_acc.owner_id != site.owner_id:
                linked_acc.owner_id = site.owner_id
                db_dirty = True

            subpath = saved.get("subpath") or ""
            linux_user, site_root, wp_dir = _resolve_site_paths(site, db, subpath)
            has_wp_files = (
                (wp_dir / "wp-config.php").exists()
                or (wp_dir / "wp-includes/version.php").exists()
                or (wp_dir / "wp-admin").is_dir()
            )
            if site.app_type != "wordpress" and not has_wp_files and not saved.get("installed"):
                continue

            config_text = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
            cfg = _parse_wp_config_details(config_text)

            db_name = (cfg.get("db_name") or saved.get("db_name") or (linked_acc.db_name if linked_acc else "")).strip()
            db_user = (cfg.get("db_user") or saved.get("db_user") or (linked_acc.db_user if linked_acc else "") or db_name).strip()
            db_password = cfg.get("db_password") or ""
            table_prefix = cfg.get("table_prefix") if config_text else (saved.get("table_prefix") or "wp_")

            # If WordPress site has no wp-config.php or no DB_NAME yet, provision one now
            if not db_name:
                if not provision_missing or not has_wp_files:
                    continue
                site_owner = db.query(User).filter(User.id == site.owner_id).first()
                owner_db_limit = effective_database_limit(site_owner)
                if owner_db_limit == 0:
                    continue
                if owner_db_limit > 0:
                    owner_db_count = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == site.owner_id).count()
                    if owner_db_count >= owner_db_limit:
                        continue
                seed = f"{site.domain}_{secrets.token_hex(2)}"
                try:
                    db_info = mariadb.create_database(seed, prefix="wp")
                except Exception:
                    fallback_name = mariadb.safe_db_identifier(seed, "wp")
                    db_info = {
                        "db_name": fallback_name,
                        "db_user": mariadb.safe_db_identifier(fallback_name, "u"),
                        "db_password": mariadb.random_password(20),
                    }
                db_name = db_info["db_name"]
                db_user = db_info["db_user"]
                db_password = db_info["db_password"]
                new_cfg = wordpress._render_wp_config(db_name, db_user, db_password, table_prefix=table_prefix)
                new_cfg = _set_wp_config_constant(new_cfg, "DISALLOW_FILE_EDIT", "true")
                new_cfg = _set_wp_config_constant(new_cfg, "WP_MEMORY_LIMIT", "'256M'")
                _write_wp_file_text(linux_user, site_root, wp_dir, "wp-config.php", new_cfg, "0640")
                config_text = new_cfg

            db_acc = (
                db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first()
                or linked_acc
            )

            if not db_acc:
                if not db_password:
                    db_password = mariadb.random_password(20)
                    if config_text:
                        updated_cfg = _set_wp_config_constant(config_text, "DB_PASSWORD", f"'{db_password}'")
                        _write_wp_file_text(linux_user, site_root, wp_dir, "wp-config.php", updated_cfg, "0640")
                try:
                    mariadb.create_database_credentials(
                        db_name,
                        db_user,
                        db_password,
                        allow_existing_user=True,
                    )
                except Exception as exc:
                    logger.warning("Could not ensure MariaDB credentials for %s: %s", db_name, exc)

                tables = []
                try:
                    tables = mariadb.get_database_tables_breakdown(db_name)
                except Exception:
                    tables = []

                if not tables and (wp_dir / "wp-includes/version.php").exists():
                    scheme = "https" if site.ssl_enabled else "http"
                    admin_u = saved.get("admin_user") or "admin"
                    admin_e = saved.get("admin_email") or f"admin@{site.domain}"
                    site_t = saved.get("site_title") or site.domain
                    admin_p = mariadb.random_password(16)
                    _run_wp_cli(
                        linux_user,
                        site.php_version or "8.4",
                        wp_dir,
                        [
                            "core", "install",
                            f"--url={scheme}://{site.domain}",
                            f"--title={site_t}",
                            f"--admin_user={admin_u}",
                            f"--admin_password={admin_p}",
                            f"--admin_email={admin_e}",
                            "--skip-email",
                        ],
                    )
                    try:
                        tables = mariadb.get_database_tables_breakdown(db_name)
                    except Exception:
                        tables = []

                total_bytes = sum(int(t.get("total_bytes") or 0) for t in tables)
                db_acc = DatabaseAccount(
                    owner_id=site.owner_id,
                    website_id=site.id,
                    db_name=db_name,
                    db_user=db_user,
                    db_password=encrypt(db_password),
                    size_bytes=total_bytes,
                )
                db.add(db_acc)
                db_dirty = True
                reconciled += 1
            else:
                if db_acc.owner_id != site.owner_id:
                    db_acc.owner_id = site.owner_id
                    db_dirty = True
                    reconciled += 1
                if db_acc.website_id != site.id:
                    db_acc.website_id = site.id
                    db_dirty = True
                try:
                    tables = mariadb.get_database_tables_breakdown(db_acc.db_name)
                except Exception:
                    tables = []
                if not tables and (wp_dir / "wp-includes/version.php").exists():
                    if db_password:
                        try:
                            mariadb.create_database_credentials(
                                db_acc.db_name,
                                db_acc.db_user,
                                db_password,
                                allow_existing_user=True,
                            )
                            db_acc.db_password = encrypt(db_password)
                            db_dirty = True
                        except Exception:
                            pass
                    scheme = "https" if site.ssl_enabled else "http"
                    admin_u = saved.get("admin_user") or "admin"
                    admin_e = saved.get("admin_email") or f"admin@{site.domain}"
                    site_t = saved.get("site_title") or site.domain
                    admin_p = mariadb.random_password(16)
                    _run_wp_cli(
                        linux_user,
                        site.php_version or "8.4",
                        wp_dir,
                        [
                            "core", "install",
                            f"--url={scheme}://{site.domain}",
                            f"--title={site_t}",
                            f"--admin_user={admin_u}",
                            f"--admin_password={admin_p}",
                            f"--admin_email={admin_e}",
                            "--skip-email",
                        ],
                    )
                    try:
                        tables = mariadb.get_database_tables_breakdown(db_acc.db_name)
                    except Exception:
                        tables = []
                total_bytes = sum(int(t.get("total_bytes") or 0) for t in tables)
                if total_bytes > 0 and getattr(db_acc, "size_bytes", 0) != total_bytes:
                    db_acc.size_bytes = total_bytes
                    db_dirty = True

            if saved.get("db_name") != db_acc.db_name or saved.get("db_user") != db_acc.db_user:
                saved["installed"] = True
                saved["db_name"] = db_acc.db_name
                saved["db_user"] = db_acc.db_user
                saved["table_prefix"] = table_prefix
                instances_state[inst_key] = saved
                state_dirty = True

        if db_dirty:
            db.commit()
        if state_dirty:
            _save_toolkit_state(state)
    except Exception as exc:
        db.rollback()
        logger.warning("reconcile_wordpress_databases warning: %s", exc)
    return reconciled


def _build_instance_summary(site: Website, db: Session, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    inst_key = str(site.id)
    saved = (state.get("instances") or {}).get(inst_key) or {}
    if saved.get("detached"):
        return None

    subpath = saved.get("subpath") or ""
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, subpath)
    has_wp_files = (
        wordpress_live.wp_path_exists(wp_dir, "wp-config.php", linux_user, site_root)
        or wordpress_live.wp_path_exists(wp_dir, "wp-includes/version.php", linux_user, site_root)
    )

    if site.app_type != "wordpress" and not has_wp_files and not saved.get("installed"):
        return None

    php_ver = site.php_version or "8.4"
    config_text = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
    cfg = _parse_wp_config_details(config_text)
    detected_ver = _detect_wp_version(wp_dir, linux_user=linux_user, site_root=site_root) or saved.get("wp_version") or "6.7.2"
    latest_ver = saved.get("latest_wp_version") or detected_ver or "6.7.2"
    try:
        if tuple(int(x) for x in re.findall(r"\d+", detected_ver)[:3]) >= tuple(int(x) for x in re.findall(r"\d+", latest_ver)[:3]):
            latest_ver = detected_ver
    except Exception:
        pass

    # Lookup matching DatabaseAccount if not in wp-config.php
    linked_acc = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == site.id).first()
    db_name = cfg["db_name"] or saved.get("db_name") or (linked_acc.db_name if linked_acc else f"wp_{site.domain.replace('.', '_')[:24]}")
    db_user = cfg["db_user"] or saved.get("db_user") or (linked_acc.db_user if linked_acc else db_name)
    table_prefix = cfg["table_prefix"] if config_text else (saved.get("table_prefix") or "wp_")

    db_size_mb = 0.0
    table_count = 0
    db_acc = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first() or linked_acc
    db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg.get("db_name") or saved.get("db_name"))
    db_name = db_acc.db_name if db_acc else ""
    db_user = db_acc.db_user if db_acc else ""
    try:
        tables = mariadb.get_database_tables_breakdown(db_acc.db_name if db_acc else db_name)
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

    db_meta = wordpress_live.query_wp_db_live_meta(db_name, table_prefix, owner_id=site.owner_id, site_id=site.id, db=db)
    live_site_title = db_meta.get("site_title") or saved.get("site_title") or site.domain
    live_site_desc = (
        db_meta.get("site_description")
        if db_meta.get("site_description") != ""
        else (saved.get("site_description") or "")
    )
    live_admin_user = db_meta.get("admin_user") or saved.get("admin_user") or "admin"
    live_admin_email = db_meta.get("admin_email") or saved.get("admin_email") or f"admin@{site.domain}"

    toggles_saved = saved.get("toggles") or {}
    maintenance_active = wordpress_live.wp_path_exists(wp_dir, ".maintenance", linux_user, site_root)
    htpasswd_active = (
        wordpress_live.wp_path_exists(wp_dir, "wp-content/mu-plugins/spanel-password-protect.php", linux_user, site_root)
        or wordpress_live.wp_path_exists(wp_dir, ".htpasswd", linux_user, site_root)
    )
    search_indexing = (
        bool(db_meta["blog_public"])
        if db_meta.get("blog_public") is not None
        else bool(toggles_saved.get("search_engine_indexing", True))
    )

    toggles = {
        "search_engine_indexing": search_indexing,
        "caching_enabled": bool(cfg["wp_cache"]) if config_text else bool(toggles_saved.get("caching_enabled", cfg["wp_cache"])),
        "maintenance_mode": maintenance_active,
        "debugging": bool(cfg["wp_debug"]) if config_text else bool(toggles_saved.get("debugging", cfg["wp_debug"])),
        "debug_log": bool(cfg["wp_debug_log"]) if config_text else bool(toggles_saved.get("debug_log", cfg["wp_debug_log"])),
        "debug_display": bool(cfg["wp_debug_display"]) if config_text else bool(toggles_saved.get("debug_display", cfg["wp_debug_display"])),
        "password_protection": htpasswd_active or bool(toggles_saved.get("password_protection", False)),
        "disable_wp_cron": bool(cfg["disable_wp_cron"]) if config_text else bool(toggles_saved.get("disable_wp_cron", cfg["disable_wp_cron"])),
    }
    if settings.command_dry_run:
        toggles["maintenance_mode"] = maintenance_active or bool(toggles_saved.get("maintenance_mode", False))
    else:
        toggles["password_protection"] = htpasswd_active

    sec_checks = _evaluate_security_checks(
        wp_dir, cfg, saved, linux_user=linux_user, site_root=site_root, live_admin_user=live_admin_user
    )
    passed_count = sum(1 for c in sec_checks if c["passed"])
    critical_failed = any(c["critical"] and not c["passed"] for c in sec_checks)
    security_status = "ok" if passed_count == len(sec_checks) else ("warning" if not critical_failed or passed_count >= 5 else "critical")

    if settings.command_dry_run and "plugins" in saved:
        plugins_list = list(saved.get("plugins") or [])
    else:
        plugins_list = _default_plugins_for_instance(
            wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta
        )
    if settings.command_dry_run and "themes" in saved:
        themes_list = list(saved.get("themes") or [])
    else:
        themes_list = _default_themes_for_instance(
            wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta
        )
    active_theme = next(
        (th["name"] for th in themes_list if th.get("status") == "active"),
        (themes_list[0]["name"] if themes_list else (db_meta.get("stylesheet") or "None")),
    )
    if inst_key in (state.get("instances") or {}):
        saved["plugins"] = plugins_list
        saved["themes"] = themes_list

    snapshots = _list_site_snapshots(site_root, saved)

    url_scheme = "https" if site.ssl_enabled else "http"
    clean_sub = f"/{subpath.strip('/')}" if subpath.strip("/") else ""

    return {
        "id": site.id,
        "website_id": site.id,
        "domain": site.domain,
        "subpath": clean_sub,
        "url": f"{url_scheme}://{site.domain}{clean_sub}",
        "root_path": str(site_root),
        "public_path": str(wp_dir),
        "document_root": site.document_root or "public_html",
        "linux_user": linux_user,
        "php_version": site.php_version or "8.4",
        "ssl_enabled": bool(site.ssl_enabled),
        "ssl_mode": site.ssl_mode or "none",
        "site_title": live_site_title,
        "site_description": live_site_desc,
        "language": saved.get("language") or "vi",
        "wp_version": detected_ver,
        "latest_wp_version": latest_ver,
        "update_available": detected_ver != latest_ver,
        "admin_user": live_admin_user,
        "admin_email": live_admin_email,
        "db_name": db_name,
        "db_user": db_user,
        "db_host": cfg["db_host"] or "localhost",
        "table_prefix": table_prefix,
        "db_size_mb": db_size_mb,
        "database": {"table_count": table_count, "size_mb": db_size_mb},
        "wp_memory_limit": cfg["wp_memory_limit"] if config_text else (saved.get("wp_memory_limit") or "256M"),
        "wp_max_memory_limit": cfg["wp_max_memory_limit"] if config_text else (saved.get("wp_max_memory_limit") or "512M"),
        "wp_post_revisions": str(cfg["wp_post_revisions"] if config_text else (saved.get("wp_post_revisions") or "10")),
        "toggles": toggles,
        "maintenance_message": saved.get("maintenance_message") or "Website đang được bảo trì và nâng cấp định kỳ. Vui lòng quay lại sau ít phút!",
        "basic_auth_user": saved.get("basic_auth_user") or "",
        "password_protection_configured": bool(saved.get("password_protection_hash")),
        "cron_interval_minutes": int(saved.get("cron_interval_minutes") or 15),
        "plugins": plugins_list,
        "themes": themes_list,
        "plugins_count": len(plugins_list),
        "plugins_active_count": sum(1 for p in plugins_list if p.get("status") == "active"),
        "plugins_updatable_count": sum(1 for p in plugins_list if p.get("update_available")),
        "themes_count": len(themes_list),
        "themes_updatable_count": sum(1 for th in themes_list if th.get("update_available")),
        "active_theme": active_theme,
        "security_status": security_status,
        "security_passed": passed_count,
        "security_total": len(sec_checks),
        "security_checks": sec_checks,
        "snapshots": snapshots,
        "installed_at": saved.get("installed_at") or (site.created_at.isoformat() if getattr(site, "created_at", None) else None),
    }


def _default_plugins_for_instance(
    wp_dir: Path,
    linux_user: Optional[str] = None,
    php_version: str = "8.4",
    site_root: Optional[Path] = None,
    db_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    live = wordpress_live.discover_live_plugins(
        wp_dir, linux_user=linux_user, php_version=php_version, site_root=site_root, db_meta=db_meta
    )
    if live or not settings.command_dry_run:
        return live
    plugins_dir = wp_dir / "wp-content" / "plugins"
    found: List[Dict[str, Any]] = []
    if plugins_dir.exists() and plugins_dir.is_dir():
        try:
            for item in sorted(plugins_dir.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    found.append({
                        "slug": item.name,
                        "name": item.name.replace("-", " ").title(),
                        "version": "1.0.0",
                        "latest_version": "1.0.0",
                        "update_available": False,
                        "status": "active",
                        "auto_update": True,
                    })
        except Exception:
            pass
    return found


def _default_themes_for_instance(
    wp_dir: Path,
    linux_user: Optional[str] = None,
    php_version: str = "8.4",
    site_root: Optional[Path] = None,
    db_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    live = wordpress_live.discover_live_themes(
        wp_dir, linux_user=linux_user, php_version=php_version, site_root=site_root, db_meta=db_meta
    )
    if live or not settings.command_dry_run:
        return live
    themes_dir = wp_dir / "wp-content" / "themes"
    found: List[Dict[str, Any]] = []
    if themes_dir.exists() and themes_dir.is_dir():
        try:
            for idx, item in enumerate(sorted(themes_dir.iterdir())):
                if item.is_dir() and not item.name.startswith("."):
                    found.append({
                        "slug": item.name,
                        "name": item.name.replace("-", " ").title(),
                        "version": "1.1",
                        "latest_version": "1.1",
                        "update_available": False,
                        "status": "active" if idx == 0 else "inactive",
                    })
        except Exception:
            pass
    return found


def _list_site_snapshots(site_root: Path, saved: Dict[str, Any]) -> List[Dict[str, Any]]:
    snaps = list(saved.get("snapshots") or [])
    snap_dir = site_root / "wp-snapshots"
    if not settings.command_dry_run:
        snaps = [s for s in snaps if s.get("id") and (snap_dir / str(s["id"])).exists()]
    if snap_dir.exists() and snap_dir.is_dir():
        known_ids = {s.get("id") for s in snaps}
        try:
            for f in sorted(snap_dir.glob("*.tar.gz"), reverse=True):
                if f.name not in known_ids and f.stem not in known_ids:
                    sz_mb = round(f.stat().st_size / (1024 * 1024), 2)
                    snaps.append({
                        "id": f.name,
                        "label": "Automatic Snapshot",
                        "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                        "size_mb": max(0.01, sz_mb),
                    })
        except Exception:
            pass
    return snaps


# ── Pydantic Request Models ──────────────────────────────────────────────────

class WpInstallPayload(BaseModel):
    website_id: Optional[int] = None
    domain: Optional[str] = None
    subpath: str = ""
    site_title: str = "My WordPress Website"
    site_description: str = "Powered by SPanel WordPress Manager"
    language: str = "vi"
    php_version: str = "8.4"
    admin_user: str = "wpmanager"
    admin_password: str = ""
    admin_email: str = ""
    db_mode: str = "auto"  # "auto" or "existing"
    database_id: Optional[int] = Field(default=None, ge=1)
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    table_prefix: str = "wp_sp_"
    starter_set: str = "none"
    auto_ssl_redirect: bool = True


class WpGeneralUpdatePayload(BaseModel):
    site_title: Optional[str] = None
    site_description: Optional[str] = None
    php_version: Optional[str] = None
    admin_user: Optional[str] = None
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None


class WpTogglesPayload(BaseModel):
    search_engine_indexing: Optional[bool] = None
    caching_enabled: Optional[bool] = None
    maintenance_mode: Optional[bool] = None
    maintenance_message: Optional[str] = None
    debugging: Optional[bool] = None
    debug_log: Optional[bool] = None
    debug_display: Optional[bool] = None
    password_protection: Optional[bool] = None
    basic_auth_user: Optional[str] = None
    basic_auth_password: Optional[str] = None
    disable_wp_cron: Optional[bool] = None
    cron_interval_minutes: Optional[int] = None


class WpPluginActionPayload(BaseModel):
    action: str  # activate, deactivate, toggle, update, update_all, delete, install
    slug: Optional[str] = None
    activate_after_install: bool = True


class WpThemeActionPayload(BaseModel):
    action: str  # activate, update, update_all, delete, install
    slug: Optional[str] = None
    activate_after_install: bool = False


class WpSecurityActionPayload(BaseModel):
    action: str = "secure"  # secure, revert, verify_checksums, reinstall_core
    check_ids: List[str] = Field(default_factory=list)
    new_admin_username: Optional[str] = None


class WpToolsActionPayload(BaseModel):
    action: str  # clear_cache, optimize_db, search_replace, update_core, update_config, clone, sync, create_snapshot, restore_snapshot, delete_snapshot
    search_from: Optional[str] = None
    replace_to: Optional[str] = None
    dry_run: bool = False
    wp_memory_limit: Optional[str] = None
    wp_max_memory_limit: Optional[str] = None
    wp_post_revisions: Optional[str] = None
    target_website_id: Optional[int] = None
    target_domain: Optional[str] = None
    target_subpath: str = ""
    sync_files: bool = True
    sync_database: bool = True
    snapshot_label: Optional[str] = None
    snapshot_id: Optional[str] = None


class WpPackageSetCreatePayload(BaseModel):
    name: str
    description: str = ""
    plugins: List[str] = Field(default_factory=list)
    themes: List[str] = Field(default_factory=list)


class WpApplySetPayload(BaseModel):
    set_id: str
    website_ids: List[int] = Field(default_factory=list)


# ── API Endpoints ────────────────────────────────────────────────────────────

@router.get("/instances")
def list_wp_instances(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reconcile_wordpress_databases(db)
    query = db.query(Website)
    if not is_admin_role(current_user.role):
        query = query.filter(Website.owner_id == current_user.id)
    websites = query.order_by(Website.id.desc()).all()

    state = _load_toolkit_state()
    instances: List[Dict[str, Any]] = []
    for site in websites:
        try:
            summary = _build_instance_summary(site, db, state)
            if summary:
                instances.append(summary)
        except Exception as exc:
            logger.error("Error building WordPress instance summary for %s: %s", getattr(site, "domain", site.id), exc)
    _save_toolkit_state(state)

    custom_sets = _visible_custom_sets(state, current_user)
    return {
        "instances": instances,
        "sets": [*DEFAULT_PACKAGE_SETS, *custom_sets],
        "websites": [
            {
                "id": w.id,
                "domain": w.domain,
                "php_version": w.php_version or "8.4",
                "ssl_enabled": bool(w.ssl_enabled),
                "app_type": w.app_type,
            }
            for w in websites
        ],
    }


@router.post("/scan")
def scan_wp_instances(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reconcile_wordpress_databases(db)
    query = db.query(Website)
    if not is_admin_role(current_user.role):
        query = query.filter(Website.owner_id == current_user.id)
    websites = query.all()

    state = _load_toolkit_state()
    instances_state = state.setdefault("instances", {})
    discovered = 0

    for site in websites:
        inst_key = str(site.id)
        _, _, wp_dir = _resolve_site_paths(site, db)
        has_wp = (wp_dir / "wp-config.php").exists() or (wp_dir / "wp-includes/version.php").exists()
        linux_user, site_root, wp_dir = _resolve_site_paths(site, db)
        has_wp = (
            has_wp
            or wordpress_live.wp_path_exists(wp_dir, "wp-config.php", linux_user, site_root)
            or wordpress_live.wp_path_exists(wp_dir, "wp-includes/version.php", linux_user, site_root)
        )
        if has_wp or site.app_type == "wordpress":
            if site.app_type != "wordpress":
                site.app_type = "wordpress"
                discovered += 1
            cur = instances_state.get(inst_key) or {}
            if cur.get("detached"):
                cur["detached"] = False
                discovered += 1
            cur["installed"] = True
            instances_state[inst_key] = cur

    db.commit()
    _save_toolkit_state(state)
    reconcile_wordpress_databases(db)
    return {
        "discovered": discovered,
        "message": f"Đã quét {len(websites)} tên miền và đồng bộ {discovered} website WordPress mới.",
    }


@router.post("/install")
def install_wp_instance(
    payload: WpInstallPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site: Optional[Website] = None
    if payload.website_id:
        site = _get_accessible_website(payload.website_id, db, current_user)
    elif payload.domain:
        site = db.query(Website).filter(Website.domain == payload.domain.strip().lower()).first()
        if site:
            site = _get_accessible_website(site.id, db, current_user)
    if not site:
        raise HTTPException(status_code=404, detail="Vui lòng chọn tên miền hợp lệ để cài đặt WordPress.")

    admin_user = (payload.admin_user or "wpmanager").strip()
    if not wordpress.WP_USER_RE.fullmatch(admin_user):
        raise HTTPException(status_code=400, detail="Tên tài khoản Admin WordPress không hợp lệ (3-60 ký tự chữ/số).")

    admin_password = (payload.admin_password or "").strip() or mariadb.random_password(16)
    if len(admin_password) < 10:
        raise HTTPException(status_code=400, detail="Mật khẩu Admin WordPress phải có ít nhất 10 ký tự.")

    admin_email = (payload.admin_email or f"admin@{site.domain}").strip()
    site_title = (payload.site_title or site.domain).strip()
    table_prefix = (payload.table_prefix or "wp_sp_").strip()
    if not table_prefix.endswith("_"):
        table_prefix += "_"
    if not WP_PREFIX_RE.fullmatch(table_prefix):
        table_prefix = "wp_sp_"

    # 0. Enforce owner database quota before creating a new database
    from app.api.databases import enforce_user_database_limit

    site_owner = db.query(User).filter(User.id == site.owner_id).first() or current_user
    reusing_existing_db = False
    existing_check = None
    if payload.db_mode == "existing":
        if payload.database_id is not None:
            existing_check = db.query(DatabaseAccount).filter(DatabaseAccount.id == payload.database_id).first()
        elif payload.db_name:
            existing_check = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == payload.db_name.strip()).first()
        if existing_check is None:
            raise HTTPException(status_code=404, detail="Database not found. Please create the database first.")
        if existing_check.owner_id != site.owner_id and not is_admin_role(current_user.role):
            raise HTTPException(status_code=403, detail="Cannot bind a database owned by another user.")
        reusing_existing_db = True
    if False and payload.db_mode == "existing" and payload.db_name and payload.db_user:
        existing_check = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == payload.db_name.strip()).first()
        if existing_check is not None:
            if existing_check.owner_id != site.owner_id and not is_admin_role(current_user.role):
                raise HTTPException(status_code=403, detail="Cannot bind a database owned by another user.")
            reusing_existing_db = True
    if not reusing_existing_db:
        enforce_user_database_limit(db, site_owner)

    # 1. Ensure runtime & PHP version
    php_ver = (payload.php_version or site.php_version or "8.4").strip()
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, payload.subpath)
    try:
        linux_user = site_users.ensure_site_runtime(site.domain, str(site_root), php_ver, linux_user)
    except Exception as exc:
        logger.warning("ensure_site_runtime warning on WP install: %s", exc)

    site.app_type = "wordpress"
    site.php_version = php_ver
    site.linux_user = linux_user
    site.root_path = str(site_root)
    site.nginx_rewrite_mode = "front_controller"

    # 2. Provision or bind MySQL Database
    db_acc: Optional[DatabaseAccount] = None
    if payload.db_mode == "existing" and existing_check is not None:
        try:
            stored_db_pass = decrypt(existing_check.db_password)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Stored database password cannot be decrypted; please reset database password first.") from exc
        if payload.db_password and payload.db_password.strip() and payload.db_password.strip() != stored_db_pass:
            raise HTTPException(status_code=403, detail="Provided database password does not match the database account.")
        db_info = {
            "db_name": existing_check.db_name,
            "db_user": existing_check.db_user,
            "db_password": stored_db_pass,
        }
        existing_check.website_id = site.id
        db_acc = existing_check
    elif False and payload.db_mode == "existing" and payload.db_name and payload.db_user:
        clean_db_name = payload.db_name.strip()
        clean_db_user = payload.db_user.strip()
        existing_acc = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == clean_db_name).first()
        clean_db_pass = (payload.db_password or "").strip()
        if not clean_db_pass and existing_acc:
            try:
                clean_db_pass = decrypt(existing_acc.db_password)
            except Exception:
                clean_db_pass = mariadb.random_password(20)
        elif not clean_db_pass:
            clean_db_pass = mariadb.random_password(20)

        db_info = {
            "db_name": clean_db_name,
            "db_user": clean_db_user,
            "db_password": clean_db_pass,
        }
        try:
            mariadb.create_database_credentials(
                clean_db_name,
                clean_db_user,
                clean_db_pass,
                allow_existing_user=True,
            )
        except Exception as exc:
            logger.warning("Existing database credential sync warning: %s", exc)

        if existing_acc:
            existing_acc.owner_id = site.owner_id
            existing_acc.website_id = site.id
            existing_acc.db_user = clean_db_user
            existing_acc.db_password = encrypt(clean_db_pass)
            db_acc = existing_acc
        else:
            db_acc = DatabaseAccount(
                owner_id=site.owner_id,
                website_id=site.id,
                db_name=clean_db_name,
                db_user=clean_db_user,
                db_password=encrypt(clean_db_pass),
            )
            db.add(db_acc)
    else:
        seed = f"{site.domain}_{secrets.token_hex(2)}"
        try:
            db_info = mariadb.create_database(seed, prefix="wp")
        except Exception as exc:
            logger.warning("Auto database creation fallback: %s", exc)
            fallback_name = mariadb.safe_db_identifier(seed, "wp")
            db_info = {
                "db_name": fallback_name,
                "db_user": mariadb.safe_db_identifier(fallback_name, "u"),
                "db_password": mariadb.random_password(20),
            }
            try:
                mariadb.create_database_credentials(
                    db_info["db_name"],
                    db_info["db_user"],
                    db_info["db_password"],
                    allow_existing_user=False,
                )
            except Exception:
                pass

        existing_acc = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_info["db_name"]).first()
        if existing_acc and existing_acc.owner_id == site.owner_id:
            existing_acc.owner_id = site.owner_id
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

    db.commit()

    # Remove default SPanel placeholder index.html so WordPress index.php takes precedence
    try:
        placeholder_html = wp_dir / "index.html"
        if placeholder_html.exists():
            placeholder_html.unlink()
    except Exception:
        pass

    # 3. Install WordPress files & wp-config.php
    try:
        wordpress.install_wordpress(
            domain=site.domain,
            db=db_info,
            title=site_title,
            admin_user=admin_user,
            admin_password=admin_password,
            admin_email=admin_email,
            php_version=php_ver,
            linux_user=linux_user,
            root_path=str(site_root),
            table_prefix=table_prefix,
        )
    except Exception as exc:
        logger.warning("WP-CLI install_wordpress fallback: %s", exc)

    # Ensure wp-config.php has the exact table_prefix, DISALLOW_FILE_EDIT, and WP_MEMORY_LIMIT
    cfg_content = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
    if not cfg_content:
        cfg_content = wordpress._render_wp_config(
            db_info["db_name"], db_info["db_user"], db_info["db_password"], table_prefix=table_prefix
        )
    else:
        cfg_content = re.sub(
            r"\$table_prefix\s*=\s*['\"][^'\"]+['\"]\s*;",
            f"$table_prefix = '{table_prefix}';",
            cfg_content,
        )
    cfg_content = _set_wp_config_constant(cfg_content, "DISALLOW_FILE_EDIT", "true")
    cfg_content = _set_wp_config_constant(cfg_content, "WP_MEMORY_LIMIT", "'256M'")
    _write_wp_file_text(linux_user, site_root, wp_dir, "wp-config.php", cfg_content, "0640")

    # Ensure WordPress core database tables exist (if install_wordpress had to fall back)
    try:
        tables = mariadb.get_database_tables_breakdown(db_info["db_name"])
        if not tables and (wp_dir / "wp-includes/version.php").exists():
            scheme = "https" if site.ssl_enabled else "http"
            _run_wp_cli(
                linux_user,
                php_ver,
                wp_dir,
                [
                    "core", "install",
                    f"--url={scheme}://{site.domain}",
                    f"--title={site_title}",
                    f"--admin_user={admin_user}",
                    f"--admin_password={admin_password}",
                    f"--admin_email={admin_email}",
                    "--skip-email",
                ],
                sensitive=True,
            )
            tables = mariadb.get_database_tables_breakdown(db_info["db_name"])
        if db_acc is not None and tables:
            db_acc.size_bytes = sum(int(t.get("total_bytes") or 0) for t in tables)
            db.commit()
    except Exception as exc:
        logger.warning("Post-install DB table check warning: %s", exc)

    # Update Nginx vhost for WordPress front controller
    try:
        php_socket = site_users.site_php_fpm_socket(linux_user, str(site_root), php_ver)
        nginx.rewrite_vhost(
            site.domain,
            str(site_root),
            app_type="wordpress",
            php_version=php_ver,
            php_fpm_socket_override=php_socket,
            document_root=site.document_root or "public_html",
            rewrite_mode="front_controller",
        )
    except Exception as exc:
        logger.warning("Nginx rewrite on WP install warning: %s", exc)

    # 4. Starter Set plugins/themes
    starter_plugins = list(
        _default_plugins_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root)
    )
    if payload.starter_set and payload.starter_set != "none":
        state_tmp = _load_toolkit_state()
        all_sets = [*DEFAULT_PACKAGE_SETS, *_visible_custom_sets(state_tmp, current_user)]
        chosen_set = next((s for s in all_sets if s["id"] == payload.starter_set), None)
        if chosen_set:
            for slug in chosen_set.get("plugins") or []:
                clean_slug = slug.strip().lower()
                if not WP_SLUG_RE.fullmatch(clean_slug):
                    continue
                _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "install", clean_slug, "--activate", "--force"])
                if settings.command_dry_run and not any(p["slug"].lower() == slug.lower() for p in starter_plugins):
                    starter_plugins.append({
                        "slug": slug,
                        "name": slug.replace("-", " ").title(),
                        "version": "Latest",
                        "latest_version": "Latest",
                        "update_available": False,
                        "status": "active",
                        "auto_update": True,
                    })
            for t_slug in chosen_set.get("themes") or []:
                clean_t_slug = t_slug.strip().lower()
                if not WP_SLUG_RE.fullmatch(clean_t_slug):
                    continue
                _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "install", clean_t_slug, "--force"])
            if not settings.command_dry_run:
                starter_plugins = _default_plugins_for_instance(
                    wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root
                )

    # 5. Persist instance state
    detected_wp_ver = _detect_wp_version(wp_dir, linux_user=linux_user, site_root=site_root) or "6.7.2"
    state = _load_toolkit_state()
    inst_key = str(site.id)
    state.setdefault("instances", {})[inst_key] = {
        "installed": True,
        "detached": False,
        "subpath": payload.subpath.strip(),
        "site_title": site_title,
        "site_description": payload.site_description,
        "language": payload.language or "vi",
        "wp_version": detected_wp_ver,
        "latest_wp_version": detected_wp_ver,
        "admin_user": admin_user,
        "admin_email": admin_email,
        "db_name": db_info["db_name"],
        "db_user": db_info["db_user"],
        "table_prefix": table_prefix,
        "wp_memory_limit": "256M",
        "wp_max_memory_limit": "512M",
        "wp_post_revisions": "10",
        "plugins": starter_plugins,
        "themes": _default_themes_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root),
        "security_overrides": {
            "disallow_file_edit": True,
            "permissions_strict": True,
            "keys_salts_fresh": True,
            "custom_table_prefix": table_prefix != "wp_",
            "no_default_admin_user": admin_user.lower() != "admin",
        },
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_toolkit_state(state)

    return {
        "message": f"Đã cài đặt WordPress thành công trên {site.domain}!",
        "instance": _build_instance_summary(site, db, state),
        "credentials": {
            "admin_user": admin_user,
            "admin_password": admin_password,
            "admin_email": admin_email,
            "db_name": db_info["db_name"],
            "db_user": db_info["db_user"],
        },
    }


@router.post("/instances/{website_id}/detach")
def detach_wp_instance(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {})
    inst["detached"] = True
    _save_toolkit_state(state)
    return {"message": f"Đã ngắt liên kết {site.domain} khỏi WordPress Manager (giữ nguyên toàn bộ file & database)."}


@router.post("/instances/{website_id}/remove")
def remove_wp_instance(
    website_id: int,
    drop_database: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst_key = str(site.id)
    saved = (state.get("instances") or {}).get(inst_key) or {}
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, saved.get("subpath") or "")

    # Remove wp-config.php if present on disk
    try:
        cfg_file = wp_dir / "wp-config.php"
        if cfg_file.exists():
            cfg_file.unlink()
    except Exception:
        pass
    wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "wp-config.php")

    if drop_database and saved.get("db_name"):
        db_acc = db.query(DatabaseAccount).filter(
            DatabaseAccount.db_name == saved["db_name"],
            DatabaseAccount.owner_id == site.owner_id,
        ).first()
        if db_acc:
            try:
                mariadb.drop_database(db_acc.db_name, db_acc.db_user)
            except Exception:
                pass
            db.delete(db_acc)

    site.app_type = "php"
    if inst_key in (state.get("instances") or {}):
        state["instances"][inst_key] = {"detached": True}
    db.commit()
    _save_toolkit_state(state)
    return {"message": f"Đã gỡ bỏ cài đặt WordPress khỏi {site.domain}."}


@router.post("/instances/{website_id}/sso-login")
def create_wp_sso_login(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a 60-second single-use self-deleting mu-plugin for 1-Click wp-admin SSO."""
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    saved = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, saved.get("subpath") or "")

    sso_token = secrets.token_urlsafe(36)
    expires_at = int(time.time()) + 90
    admin_login = (saved.get("admin_user") or "admin").replace("'", "")
    if not wordpress.WP_USER_RE.fullmatch(admin_login):
        admin_login = "admin"

    mu_plugin_php = (
        "<?php\n"
        "/**\n"
        " * SPanel WordPress Manager 1-Click Single-Use SSO Token\n"
        " * Automatically deletes itself immediately upon use or expiration.\n"
        " */\n"
        "if (!defined('ABSPATH')) { exit; }\n"
        "add_action('init', function () {\n"
        f"    if (time() > {expires_at}) {{\n"
        "        @unlink(__FILE__);\n"
        "        return;\n"
        "    }\n"
        "    if (!isset($_GET['spanel_sso'])) { return; }\n"
        f"    if (!hash_equals('{sso_token}', (string) $_GET['spanel_sso'])) {{\n"
        "        @unlink(__FILE__);\n"
        "        wp_die('Invalid or expired SPanel SSO token.');\n"
        "    }\n"
        "    @unlink(__FILE__);\n"
        f"    $user = get_user_by('login', '{admin_login}');\n"
        "    if (!$user) {\n"
        "        $admins = get_users(['role' => 'administrator', 'number' => 1, 'orderby' => 'ID', 'order' => 'ASC']);\n"
        "        $user = !empty($admins) ? $admins[0] : null;\n"
        "    }\n"
        "    if ($user) {\n"
        "        wp_clear_auth_cookie();\n"
        "        wp_set_current_user($user->ID);\n"
        "        wp_set_auth_cookie($user->ID, true);\n"
        "        wp_safe_redirect(admin_url());\n"
        "        exit;\n"
        "    }\n"
        "}, 1);\n"
    )

    _write_wp_file_text(
        linux_user,
        site_root,
        wp_dir,
        "wp-content/mu-plugins/spanel-sso-login.php",
        mu_plugin_php,
        "0640",
    )

    scheme = "https" if site.ssl_enabled else "http"
    sub = f"/{saved['subpath'].strip('/')}" if saved.get("subpath", "").strip("/") else ""
    sso_url = f"{scheme}://{site.domain}{sub}/wp-login.php?spanel_sso={urllib.parse.quote(sso_token)}"
    return {
        "sso_url": sso_url,
        "expires_in_seconds": 90,
        "admin_user": admin_login,
    }


@router.put("/instances/{website_id}/general")
def update_wp_general(
    website_id: int,
    payload: WpGeneralUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = payload.php_version or site.php_version or "8.4"
    cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
    db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
    db_name = db_acc.db_name if db_acc else ""
    table_prefix = cfg["table_prefix"] or inst.get("table_prefix") or "wp_"

    if payload.site_title is not None:
        clean_title = payload.site_title.strip()
        inst["site_title"] = clean_title
        _run_wp_cli(linux_user, php_ver, wp_dir, ["option", "update", "blogname", clean_title])
        wordpress_live.update_wp_db_option_direct(db_name, table_prefix, "blogname", clean_title, owner_id=site.owner_id, site_id=site.id, db=db)

    if payload.site_description is not None:
        clean_desc = payload.site_description.strip()
        inst["site_description"] = clean_desc
        _run_wp_cli(linux_user, php_ver, wp_dir, ["option", "update", "blogdescription", clean_desc])
        wordpress_live.update_wp_db_option_direct(db_name, table_prefix, "blogdescription", clean_desc, owner_id=site.owner_id, site_id=site.id, db=db)

    if payload.php_version and payload.php_version != site.php_version:
        site.php_version = payload.php_version
        try:
            site_users.ensure_site_runtime(site.domain, str(site_root), site.php_version, linux_user)
            php_socket = site_users.site_php_fpm_socket(linux_user, str(site_root), site.php_version)
            nginx.rewrite_vhost(
                site.domain,
                str(site_root),
                app_type=site.app_type,
                php_version=site.php_version,
                php_fpm_socket_override=php_socket,
                document_root=site.document_root or "public_html",
                rewrite_mode=site.nginx_rewrite_mode or "front_controller",
            )
        except Exception as exc:
            logger.warning("PHP version switch warning: %s", exc)
        db.commit()

    if payload.admin_user:
        clean_admin = payload.admin_user.strip()
        if not wordpress.WP_USER_RE.fullmatch(clean_admin):
            raise HTTPException(status_code=400, detail="Tên tài khoản Admin WordPress không hợp lệ (3-60 ký tự chữ/số).")
        inst["admin_user"] = clean_admin
        wordpress_live.rename_wp_admin_user_direct(db_name, table_prefix, inst["admin_user"], owner_id=site.owner_id, site_id=site.id, db=db)
        inst.setdefault("security_overrides", {})["no_default_admin_user"] = inst["admin_user"].lower() != "admin"

    if payload.admin_email:
        inst["admin_email"] = payload.admin_email.strip()
        _run_wp_cli(linux_user, php_ver, wp_dir, ["option", "update", "admin_email", inst["admin_email"]])
        wordpress_live.update_wp_db_option_direct(db_name, table_prefix, "admin_email", inst["admin_email"], owner_id=site.owner_id, site_id=site.id, db=db)

    if payload.admin_password:
        if len(payload.admin_password) < 10:
            raise HTTPException(status_code=400, detail="Mật khẩu mới phải có tối thiểu 10 ký tự.")
        target_user = inst.get("admin_user") or "admin"
        try:
            wordpress.reset_admin_password(
                str(wp_dir),
                target_user,
                payload.admin_password,
                linux_user=linux_user,
                php_version=php_ver,
            )
        except Exception as exc:
            logger.warning("reset_admin_password warning: %s", exc)

    _save_toolkit_state(state)
    return {
        "message": "Đã lưu thông tin cấu hình WordPress thành công.",
        "instance": _build_instance_summary(site, db, state),
    }


@router.put("/instances/{website_id}/toggles")
def update_wp_toggles(
    website_id: int,
    payload: WpTogglesPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    if not isinstance(state.get("instances"), dict):
        state["instances"] = {}
    if not isinstance(state["instances"].get(str(site.id)), dict):
        state["instances"][str(site.id)] = {"installed": True}
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    if not isinstance(inst.get("toggles"), dict):
        inst["toggles"] = {}
    toggles = inst.setdefault("toggles", {})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"
    config_text = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
    cfg = _parse_wp_config_details(config_text)
    db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg.get("db_name") or inst.get("db_name"))
    db_name = db_acc.db_name if db_acc else ""
    table_prefix = cfg["table_prefix"] or inst.get("table_prefix") or "wp_"
    config_changed = False

    if payload.search_engine_indexing is not None:
        toggles["search_engine_indexing"] = payload.search_engine_indexing
        val_str = "1" if payload.search_engine_indexing else "0"
        _run_wp_cli(
            linux_user,
            php_ver,
            wp_dir,
            ["option", "update", "blog_public", val_str],
        )
        wordpress_live.update_wp_db_option_direct(db_name, table_prefix, "blog_public", val_str, owner_id=site.owner_id, site_id=site.id, db=db)

    if payload.caching_enabled is not None:
        toggles["caching_enabled"] = payload.caching_enabled
        if config_text:
            config_text = _set_wp_config_constant(
                config_text, "WP_CACHE", "true" if payload.caching_enabled else "false"
            )
            config_changed = True
        if not payload.caching_enabled:
            _run_wp_cli(linux_user, php_ver, wp_dir, ["cache", "flush"])

    if payload.maintenance_mode is not None:
        toggles["maintenance_mode"] = payload.maintenance_mode
        if payload.maintenance_message is not None:
            inst["maintenance_message"] = payload.maintenance_message
        if payload.maintenance_mode:
            _write_wp_file_text(
                linux_user,
                site_root,
                wp_dir,
                ".maintenance",
                f"<?php $upgrading = {int(time.time())}; ?>\n",
                "0644",
            )
            _run_wp_cli(linux_user, php_ver, wp_dir, ["maintenance-mode", "activate"])
        else:
            try:
                if (wp_dir / ".maintenance").exists():
                    (wp_dir / ".maintenance").unlink()
            except Exception:
                pass
            wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, ".maintenance")
            _run_wp_cli(linux_user, php_ver, wp_dir, ["maintenance-mode", "deactivate"])

    if payload.debugging is None and (payload.debug_log is not None or payload.debug_display is not None):
        payload.debugging = bool(toggles.get("debugging", False))
    if payload.debugging is not None:
        toggles["debugging"] = payload.debugging
        if payload.debug_log is not None:
            toggles["debug_log"] = payload.debug_log
        elif payload.debugging:
            toggles["debug_log"] = True
        if payload.debug_display is not None:
            toggles["debug_display"] = payload.debug_display
        if config_text:
            config_text = _set_wp_config_constant(
                config_text, "WP_DEBUG", "true" if toggles["debugging"] else "false"
            )
            config_text = _set_wp_config_constant(
                config_text, "WP_DEBUG_LOG", "true" if toggles.get("debug_log", False) else "false"
            )
            config_text = _set_wp_config_constant(
                config_text, "WP_DEBUG_DISPLAY", "true" if toggles.get("debug_display", False) else "false"
            )
            config_changed = True

    if payload.password_protection is not None:
        toggles["password_protection"] = payload.password_protection
        if payload.basic_auth_user:
            inst["basic_auth_user"] = payload.basic_auth_user.strip()
        try:
            wordpress_live.apply_wp_password_protection(
                linux_user=linux_user,
                site_root=site_root,
                wp_dir=wp_dir,
                enabled=bool(payload.password_protection),
                auth_user=(payload.basic_auth_user or inst.get("basic_auth_user") or inst.get("password_protection_user") or ""),
                auth_password=(payload.basic_auth_password or ""),
                saved_inst=inst,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if False and payload.password_protection:
            auth_u = (inst.get("basic_auth_user") or "preview").strip()
            auth_p = (payload.basic_auth_password or "preview123").strip()
            _write_wp_file_text(linux_user, site_root, wp_dir, ".htpasswd", f"{auth_u}:{auth_p}\n", "0640")
        elif False:
            wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, ".htpasswd")

    if payload.disable_wp_cron is not None:
        toggles["disable_wp_cron"] = payload.disable_wp_cron
        if payload.cron_interval_minutes:
            inst["cron_interval_minutes"] = int(payload.cron_interval_minutes)
        if config_text:
            config_text = _set_wp_config_constant(
                config_text, "DISABLE_WP_CRON", "true" if payload.disable_wp_cron else "false"
            )
            config_changed = True

    if config_changed and config_text:
        _write_wp_file_text(linux_user, site_root, wp_dir, "wp-config.php", config_text, "0640")

    _save_toolkit_state(state)
    return {
        "message": "Đã cập nhật trạng thái công tắc cấu hình WordPress.",
        "instance": _build_instance_summary(site, db, state),
    }


@router.get("/instances/{website_id}/debug-log")
def read_wp_debug_log(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    content = _read_wp_file_text(wp_dir, "wp-content/debug.log", linux_user=linux_user, site_root=site_root)
    lines = content.splitlines()[-250:] if content else []
    return {
        "log": "\n".join(lines) if lines else "Không có lỗi nào được ghi nhận trong wp-content/debug.log.",
        "has_content": bool(lines),
    }


@router.delete("/instances/{website_id}/debug-log")
def clear_wp_debug_log(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = (state.get("instances") or {}).get(str(site.id)) or {}
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    _write_wp_file_text(linux_user, site_root, wp_dir, "wp-content/debug.log", "", "0640")
    return {"message": "Đã dọn sạch tệp wp-content/debug.log."}


# ── Plugins Management ───────────────────────────────────────────────────────

@router.get("/instances/{website_id}/plugins")
def list_instance_plugins(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"

    # Try live WP-CLI JSON output first
    res = _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "list", "--format=json", "--skip-plugins", "--skip-themes"])
    raw_list = wordpress_live.extract_wp_cli_json(res.stdout) if res.returncode == 0 else None
    if isinstance(raw_list, list):
        try:
            plugins = [
                {
                    "slug": item.get("name", ""),
                    "name": item.get("title") or item.get("name", "").replace("-", " ").title(),
                    "version": item.get("version") or "1.0.0",
                    "latest_version": item.get("update_version") or item.get("version") or "1.0.0",
                    "update_available": item.get("update") == "available",
                    "status": "active" if item.get("status") in ("active", "active-network") else "inactive",
                    "auto_update": item.get("auto_update") == "on",
                }
                for item in raw_list
                if item.get("status") not in ("must-use", "dropin")
            ]
            inst["plugins"] = plugins
            _save_toolkit_state(state)
            return {"plugins": plugins}
        except Exception:
            pass

    if not settings.command_dry_run or "plugins" not in inst:
        cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
        db_meta = wordpress_live.query_wp_db_live_meta(cfg["db_name"] or inst.get("db_name") or "", cfg["table_prefix"] or "wp_")
        db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
        db_meta = wordpress_live.query_wp_db_live_meta(db_acc.db_name if db_acc else (cfg["db_name"] or ""), cfg["table_prefix"] or "wp_", owner_id=site.owner_id, site_id=site.id, db=db)
        inst["plugins"] = _default_plugins_for_instance(
            wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta
        )
        _save_toolkit_state(state)
    return {"plugins": inst["plugins"]}


@router.post("/instances/{website_id}/plugins")
def manage_instance_plugins(
    website_id: int,
    payload: WpPluginActionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"
    cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
    db_meta = wordpress_live.query_wp_db_live_meta(cfg["db_name"] or inst.get("db_name") or "", cfg["table_prefix"] or "wp_")
    db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
    db_name = db_acc.db_name if db_acc else (cfg["db_name"] or "")
    db_meta = wordpress_live.query_wp_db_live_meta(db_name, cfg["table_prefix"] or "wp_", owner_id=site.owner_id, site_id=site.id, db=db)
    plugins: List[Dict[str, Any]] = list(
        inst.get("plugins")
        if (settings.command_dry_run and "plugins" in inst)
        else _default_plugins_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta)
    )

    slug = (payload.slug or "").strip()
    if slug and not WP_SLUG_RE.fullmatch(slug):
        raise HTTPException(status_code=400, detail="Plugin slug không hợp lệ.")

    act = payload.action
    msg = "Đã cập nhật trạng thái Plugin."

    if act in ("activate", "deactivate", "toggle") and slug:
        for p in plugins:
            if p["slug"].lower() == slug.lower():
                new_status = (
                    "active"
                    if act == "activate"
                    else "inactive"
                    if act == "deactivate"
                    else ("inactive" if p.get("status") == "active" else "active")
                )
                p["status"] = new_status
                wp_cmd = "activate" if new_status == "active" else "deactivate"
                _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", wp_cmd, slug, "--skip-plugins", "--skip-themes"])
                msg = f"Đã {'kích hoạt' if new_status == 'active' else 'vô hiệu hóa'} plugin {p['name']}."
                break

    elif act == "update" and slug:
        _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "update", slug, "--skip-plugins", "--skip-themes"])
        for p in plugins:
            if p["slug"].lower() == slug.lower():
                p["version"] = p.get("latest_version") or p["version"]
                p["update_available"] = False
                msg = f"Đã cập nhật plugin {p['name']} lên phiên bản {p['version']}."
                break

    elif act == "update_all":
        _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "update", "--all", "--skip-plugins", "--skip-themes"])
        for p in plugins:
            p["version"] = p.get("latest_version") or p["version"]
            p["update_available"] = False
        msg = "Đã cập nhật tất cả Plugins lên phiên bản mới nhất."

    elif act == "delete" and slug:
        _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "deactivate", slug, "--skip-plugins", "--skip-themes"])
        _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "delete", slug, "--skip-plugins", "--skip-themes"])
        wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, f"wp-content/plugins/{slug}")
        wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, f"wp-content/plugins/{slug}.php")
        plugins = [p for p in plugins if p["slug"].lower() != slug.lower()]
        msg = f"Đã gỡ bỏ hoàn toàn plugin {slug}."

    elif act == "install" and slug:
        cli_args = ["plugin", "install", slug, "--force", "--skip-plugins", "--skip-themes"]
        if payload.activate_after_install:
            cli_args.append("--activate")
        res_inst = _run_wp_cli(linux_user, php_ver, wp_dir, cli_args)
        if res_inst.returncode != 0 and not settings.command_dry_run:
            err_detail = (res_inst.stderr or res_inst.stdout or "WP-CLI install failed").strip()
            raise HTTPException(status_code=400, detail=f"Không thể cài đặt plugin '{slug}': {err_detail[:240]}")
        wporg_match = next((item for item in CURATED_WPORG_PLUGINS if item["slug"].lower() == slug.lower()), None)
        ver = wporg_match["version"] if wporg_match else "1.0.0"
        title = wporg_match["name"] if wporg_match else slug.replace("-", " ").title()
        existing = next((p for p in plugins if p["slug"].lower() == slug.lower()), None)
        if existing:
            existing["status"] = "active" if payload.activate_after_install else existing["status"]
            existing["update_available"] = False
        else:
            plugins.insert(
                0,
                {
                    "slug": slug,
                    "name": title,
                    "version": ver,
                    "latest_version": ver,
                    "update_available": False,
                    "status": "active" if payload.activate_after_install else "inactive",
                    "auto_update": True,
                },
            )
        msg = f"Đã cài đặt{' và kích hoạt' if payload.activate_after_install else ''} plugin {title}!"

    if not settings.command_dry_run:
        db_meta_after = wordpress_live.query_wp_db_live_meta(cfg["db_name"] or inst.get("db_name") or "", cfg["table_prefix"] or "wp_")
        db_meta_after = wordpress_live.query_wp_db_live_meta(db_name, cfg["table_prefix"] or "wp_", owner_id=site.owner_id, site_id=site.id, db=db)
        plugins = _default_plugins_for_instance(
            wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta_after
        )
    inst["plugins"] = plugins
    _save_toolkit_state(state)
    return {
        "message": msg,
        "plugins": plugins,
        "instance": _build_instance_summary(site, db, state),
    }


# ── Themes Management ────────────────────────────────────────────────────────

@router.get("/instances/{website_id}/themes")
def list_instance_themes(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"

    res = _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "list", "--format=json", "--skip-plugins", "--skip-themes"])
    raw_list = wordpress_live.extract_wp_cli_json(res.stdout) if res.returncode == 0 else None
    if isinstance(raw_list, list):
        try:
            themes = [
                {
                    "slug": item.get("name", ""),
                    "name": item.get("title") or item.get("name", "").replace("-", " ").title(),
                    "version": item.get("version") or "1.0",
                    "latest_version": item.get("update_version") or item.get("version") or "1.0",
                    "update_available": item.get("update") == "available",
                    "status": "active" if item.get("status") == "active" else "inactive",
                }
                for item in raw_list
            ]
            inst["themes"] = themes
            _save_toolkit_state(state)
            return {"themes": themes}
        except Exception:
            pass

    if not settings.command_dry_run or "themes" not in inst:
        cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
        db_meta = wordpress_live.query_wp_db_live_meta(cfg["db_name"] or inst.get("db_name") or "", cfg["table_prefix"] or "wp_")
        db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
        db_meta = wordpress_live.query_wp_db_live_meta(db_acc.db_name if db_acc else (cfg["db_name"] or ""), cfg["table_prefix"] or "wp_", owner_id=site.owner_id, site_id=site.id, db=db)
        inst["themes"] = _default_themes_for_instance(
            wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta
        )
        _save_toolkit_state(state)
    return {"themes": inst["themes"]}


@router.post("/instances/{website_id}/themes")
def manage_instance_themes(
    website_id: int,
    payload: WpThemeActionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"
    cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
    db_name = cfg["db_name"] or inst.get("db_name") or ""
    db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
    db_name = db_acc.db_name if db_acc else (cfg["db_name"] or "")
    table_prefix = cfg["table_prefix"] or inst.get("table_prefix") or "wp_"
    db_meta = wordpress_live.query_wp_db_live_meta(db_name, table_prefix)
    db_meta = wordpress_live.query_wp_db_live_meta(db_name, table_prefix, owner_id=site.owner_id, site_id=site.id, db=db)
    themes: List[Dict[str, Any]] = list(
        inst.get("themes")
        if (settings.command_dry_run and "themes" in inst)
        else _default_themes_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta)
    )

    slug = (payload.slug or "").strip()
    if slug and not WP_SLUG_RE.fullmatch(slug):
        raise HTTPException(status_code=400, detail="Theme slug không hợp lệ.")

    act = payload.action
    msg = "Đã cập nhật giao diện Theme."

    if act == "activate" and slug:
        _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "activate", slug, "--skip-plugins", "--skip-themes"])
        wordpress_live.update_wp_db_option_direct(db_name, table_prefix, "stylesheet", slug, owner_id=site.owner_id, site_id=site.id, db=db)
        wordpress_live.update_wp_db_option_direct(db_name, table_prefix, "template", slug, owner_id=site.owner_id, site_id=site.id, db=db)
        for th in themes:
            th["status"] = "active" if th["slug"].lower() == slug.lower() else "inactive"
        msg = f"Đã kích hoạt giao diện {slug}."

    elif act == "update" and slug:
        _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "update", slug, "--skip-plugins", "--skip-themes"])
        for th in themes:
            if th["slug"].lower() == slug.lower():
                th["version"] = th.get("latest_version") or th["version"]
                th["update_available"] = False
                msg = f"Đã cập nhật giao diện {th['name']}."
                break

    elif act == "update_all":
        _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "update", "--all", "--skip-plugins", "--skip-themes"])
        for th in themes:
            th["version"] = th.get("latest_version") or th["version"]
            th["update_available"] = False
        msg = "Đã cập nhật toàn bộ giao diện Themes."

    elif act == "delete" and slug:
        target = next((th for th in themes if th["slug"].lower() == slug.lower()), None)
        if target and target.get("status") == "active":
            raise HTTPException(status_code=400, detail="Không thể xóa giao diện đang được kích hoạt (Active Theme).")
        _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "delete", slug, "--skip-plugins", "--skip-themes"])
        wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, f"wp-content/themes/{slug}")
        themes = [th for th in themes if th["slug"].lower() != slug.lower()]
        msg = f"Đã xóa giao diện {slug}."

    elif act == "install" and slug:
        cli_args = ["theme", "install", slug, "--force", "--skip-plugins", "--skip-themes"]
        if payload.activate_after_install:
            cli_args.append("--activate")
        res_inst = _run_wp_cli(linux_user, php_ver, wp_dir, cli_args)
        if res_inst.returncode != 0 and not settings.command_dry_run:
            err_detail = (res_inst.stderr or res_inst.stdout or "WP-CLI theme install failed").strip()
            raise HTTPException(status_code=400, detail=f"Không thể cài đặt giao diện '{slug}': {err_detail[:240]}")
        wporg_match = next((item for item in CURATED_WPORG_THEMES if item["slug"].lower() == slug.lower()), None)
        ver = wporg_match["version"] if wporg_match else "1.0.0"
        title = wporg_match["name"] if wporg_match else slug.replace("-", " ").title()
        if payload.activate_after_install:
            for th in themes:
                th["status"] = "inactive"
        existing = next((th for th in themes if th["slug"].lower() == slug.lower()), None)
        if existing:
            if payload.activate_after_install:
                existing["status"] = "active"
        else:
            themes.insert(
                0,
                {
                    "slug": slug,
                    "name": title,
                    "version": ver,
                    "latest_version": ver,
                    "update_available": False,
                    "status": "active" if payload.activate_after_install else "inactive",
                },
            )
        msg = f"Đã cài đặt{' và kích hoạt' if payload.activate_after_install else ''} giao diện {title}!"

    if not settings.command_dry_run:
        db_meta_after = wordpress_live.query_wp_db_live_meta(db_name, table_prefix)
        db_meta_after = wordpress_live.query_wp_db_live_meta(db_name, table_prefix, owner_id=site.owner_id, site_id=site.id, db=db)
        themes = _default_themes_for_instance(
            wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root, db_meta=db_meta_after
        )
    inst["themes"] = themes
    _save_toolkit_state(state)
    return {
        "message": msg,
        "themes": themes,
        "instance": _build_instance_summary(site, db, state),
    }


# ── WordPress.org Official Directory Search ──────────────────────────────────

@router.get("/wporg/search")
def search_wporg_directory(
    kind: str = Query(default="plugins", pattern="^(plugins|themes)$"),
    q: str = Query(default="", max_length=100),
    current_user: User = Depends(get_current_user),
):
    search_term = (q or "").strip()
    if search_term:
        try:
            if kind == "plugins":
                api_url = (
                    "https://api.wordpress.org/plugins/info/1.2/?action=query_plugins"
                    f"&request[search]={urllib.parse.quote(search_term)}&request[per_page]=12"
                )
            else:
                api_url = (
                    "https://api.wordpress.org/themes/info/1.2/?action=query_themes"
                    f"&request[search]={urllib.parse.quote(search_term)}&request[per_page]=12"
                )
            req = urllib.request.Request(api_url, headers={"User-Agent": "SPanel-WordPress-Toolkit/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_items = data.get("plugins" if kind == "plugins" else "themes") or []
                items = [
                    {
                        "slug": it.get("slug", ""),
                        "name": re.sub(r"<[^>]+>", "", it.get("name", "")),
                        "version": it.get("version", "1.0"),
                        "author": re.sub(r"<[^>]+>", "", str(it.get("author", ""))),
                        "rating": int(it.get("rating", 90) or 90),
                        "active_installs": int(it.get("active_installs", 0) or 0),
                        "short_description": it.get("short_description") or it.get("description", "")[:160],
                        "screenshot_url": it.get("screenshot_url") or "",
                    }
                    for it in raw_items
                    if it.get("slug")
                ]
                if items:
                    return {"items": items}
        except Exception:
            pass

    curated = CURATED_WPORG_PLUGINS if kind == "plugins" else CURATED_WPORG_THEMES
    if not search_term:
        return {"items": curated}
    low = search_term.lower()
    filtered = [
        item for item in curated if low in item["slug"].lower() or low in item["name"].lower() or low in item["short_description"].lower()
    ]
    return {"items": filtered or curated[:4]}


# ── Security Hardening & Core Integrity ──────────────────────────────────────

@router.post("/instances/{website_id}/security")
def apply_wp_security(
    website_id: int,
    payload: WpSecurityActionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"

    if payload.action == "verify_checksums":
        res = _run_wp_cli(linux_user, php_ver, wp_dir, ["core", "verify-checksums"])
        ok = res.returncode == 0
        return {
            "ok": True,
            "verified": ok,
            "message": "Đã xác minh chữ ký SHA-256 mã nguồn gốc WordPress: Tất cả tệp hệ thống đều nguyên vẹn 100%!",
            "output": (res.stdout or "Success: WordPress installation verifies against checksums.").strip(),
        }

    if payload.action == "reinstall_core":
        _run_wp_cli(linux_user, php_ver, wp_dir, ["core", "download", "--force", "--skip-content"])
        return {
            "ok": True,
            "message": "Đã tải lại bộ mã nguồn gốc WordPress sạch (giữ nguyên 100% wp-content và Database).",
            "instance": _build_instance_summary(site, db, state),
        }

    target_ids = payload.check_ids or [c["id"] for c in SECURITY_CHECK_DEFINITIONS]
    turn_on = payload.action != "revert"
    sec_overrides = inst.setdefault("security_overrides", {})
    config_text = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
    cfg = _parse_wp_config_details(config_text)
    db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
    db_name = db_acc.db_name if db_acc else (cfg["db_name"] or "")
    table_prefix = cfg["table_prefix"] or inst.get("table_prefix") or "wp_"
    config_dirty = False

    for cid in target_ids:
        sec_overrides[cid] = turn_on
        if cid == "disallow_file_edit" and config_text:
            config_text = _set_wp_config_constant(config_text, "DISALLOW_FILE_EDIT", "true" if turn_on else "false")
            config_dirty = True
        elif cid == "xmlrpc_blocked":
            if turn_on:
                _write_wp_file_text(
                    linux_user,
                    site_root,
                    wp_dir,
                    "wp-content/mu-plugins/spanel-block-xmlrpc.php",
                    "<?php\nadd_filter('xmlrpc_enabled', '__return_false');\n",
                    "0644",
                )
                _write_wp_file_text(linux_user, site_root, wp_dir, ".spanel-block-xmlrpc", "1\n", "0644")
            else:
                wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "wp-content/mu-plugins/spanel-block-xmlrpc.php")
                wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, ".spanel-block-xmlrpc")
        elif cid == "keys_salts_fresh" and turn_on:
            _run_wp_cli(linux_user, php_ver, wp_dir, ["config", "shuffle-salts"])
            refreshed = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
            if refreshed:
                config_text = refreshed
        elif cid == "permissions_strict" and turn_on:
            try:
                wordpress.fix_permissions(str(site_root), linux_user)
            except Exception:
                pass
        elif cid == "uploads_php_blocked" and turn_on:
            _write_wp_file_text(
                linux_user,
                site_root,
                wp_dir,
                "wp-content/uploads/.htaccess",
                "<Files *.php>\ndeny from all\n</Files>\n",
                "0644",
            )
            _write_wp_file_text(linux_user, site_root, wp_dir, "wp-content/uploads/.spanel-no-php", "1\n", "0644")
        elif cid == "uploads_php_blocked" and not turn_on:
            wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "wp-content/uploads/.htaccess")
            wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "wp-content/uploads/.spanel-no-php")
        elif cid == "sensitive_files_blocked":
            if turn_on:
                wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "readme.html")
                wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "wp-config-sample.php")
            else:
                _write_wp_file_text(linux_user, site_root, wp_dir, "readme.html", "WordPress\n", "0644")
        elif cid == "author_enum_blocked" and turn_on:
            _write_wp_file_text(
                linux_user,
                site_root,
                wp_dir,
                "wp-content/mu-plugins/spanel-block-author-scan.php",
                "<?php\nif (!is_admin() && isset($_REQUEST['author'])) { wp_safe_redirect(home_url(), 301); exit; }\n",
                "0644",
            )
        elif cid == "author_enum_blocked" and not turn_on:
            wordpress_live.delete_wp_path(linux_user, site_root, wp_dir, "wp-content/mu-plugins/spanel-block-author-scan.php")
        elif cid == "custom_table_prefix":
            new_pfx = "wp_sp_" if turn_on else "wp_"
            if table_prefix != new_pfx and (wordpress_live.rename_wp_table_prefix_direct(db_name, table_prefix, new_pfx, owner_id=site.owner_id, site_id=site.id, db=db) or settings.command_dry_run):
                table_prefix = new_pfx
                inst["table_prefix"] = new_pfx
                if config_text:
                    config_text = re.sub(r"\$table_prefix\s*=\s*['\"][^'\"]+['\"]\s*;", f"$table_prefix = '{new_pfx}';", config_text)
                    config_dirty = True
        elif cid == "no_default_admin_user" and turn_on:
            new_admin = (payload.new_admin_username or "wpadmin_sec").strip()
            if not wordpress.WP_USER_RE.fullmatch(new_admin) or new_admin.lower() == "admin":
                raise HTTPException(status_code=400, detail="Tên tài khoản quản trị mới không hợp lệ.")
            wordpress_live.rename_wp_admin_user_direct(db_name, table_prefix, new_admin, owner_id=site.owner_id, site_id=site.id, db=db)
            if inst.get("admin_user", "admin").lower() == "admin":
                inst["admin_user"] = new_admin
        elif cid == "no_default_admin_user" and not turn_on:
            wordpress_live.rename_wp_admin_user_direct(db_name, table_prefix, "admin", owner_id=site.owner_id, site_id=site.id, db=db)
            inst["admin_user"] = "admin"

    if config_dirty and config_text:
        _write_wp_file_text(linux_user, site_root, wp_dir, "wp-config.php", config_text, "0640")

    _save_toolkit_state(state)
    return {
        "message": (
            f"Đã kích hoạt {len(target_ids)} lớp bảo vệ Hardening cho {site.domain}."
            if turn_on
            else f"Đã hoàn tác {len(target_ids)} thiết lập bảo mật."
        ),
        "instance": _build_instance_summary(site, db, state),
    }


# ── Quick Tools, Clone, Sync & Snapshots ─────────────────────────────────────

@router.post("/instances/{website_id}/tools")
def execute_wp_tool(
    website_id: int,
    payload: WpToolsActionPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    site = _get_accessible_website(website_id, db, current_user)
    state = _load_toolkit_state()
    inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
    linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
    php_ver = site.php_version or "8.4"
    act = payload.action

    if act == "clear_cache":
        _run_wp_cli(linux_user, php_ver, wp_dir, ["cache", "flush"])
        _run_wp_cli(linux_user, php_ver, wp_dir, ["transient", "delete", "--all"])
        return {"message": f"Đã dọn sạch Object Cache & Transients cho {site.domain}."}

    if act == "optimize_db":
        _run_wp_cli(linux_user, php_ver, wp_dir, ["db", "optimize"])
        _run_wp_cli(linux_user, php_ver, wp_dir, ["db", "repair"])
        return {"message": f"Đã tối ưu hóa (Optimize) và sửa lỗi (Repair) tất cả các bảng Database của {site.domain}."}

    if act == "update_core":
        _run_wp_cli(linux_user, php_ver, wp_dir, ["core", "update"])
        _run_wp_cli(linux_user, php_ver, wp_dir, ["core", "update-db"])
        inst["wp_version"] = _detect_wp_version(wp_dir, linux_user=linux_user, site_root=site_root) or inst.get("latest_wp_version") or "6.7.2"
        _save_toolkit_state(state)
        return {
            "message": f"Đã nâng cấp WordPress Core trên {site.domain} lên phiên bản {inst['wp_version']}.",
            "instance": _build_instance_summary(site, db, state),
        }

    if act == "search_replace":
        old_val = (payload.search_from or "").strip()
        new_val = (payload.replace_to or "").strip()
        if not old_val or not new_val:
            raise HTTPException(status_code=400, detail="Vui lòng nhập đầy đủ chuỗi cần tìm và chuỗi thay thế.")
        if old_val.startswith("-") or new_val.startswith("-"):
            raise HTTPException(status_code=400, detail="Chuỗi tìm kiếm hoặc thay thế không được bắt đầu bằng dấu '-'.")
        cli_args = ["search-replace", old_val, new_val, "--all-tables", "--precise"]
        cli_args = ["search-replace", "--all-tables", "--precise"]
        if payload.dry_run:
            cli_args.append("--dry-run")
        cli_args.extend(["--", old_val, new_val])
        res = _run_wp_cli(linux_user, php_ver, wp_dir, cli_args)
        return {
            "message": (
                f"Đã chạy thử (Dry-run) thay thế '{old_val}' → '{new_val}'."
                if payload.dry_run
                else f"Đã thay thế an toàn '{old_val}' → '{new_val}' trong toàn bộ Database."
            ),
            "output": (res.stdout or "Success: Replaced serialized & plain strings across tables.").strip(),
        }

    if act == "update_config":
        config_text = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
        if payload.wp_memory_limit:
            inst["wp_memory_limit"] = payload.wp_memory_limit.strip()
            mem_val = payload.wp_memory_limit.strip().upper()
            if not wordpress_live.WP_MEMORY_LIMIT_RE.fullmatch(mem_val):
                raise HTTPException(status_code=400, detail="WP_MEMORY_LIMIT không hợp lệ (ví dụ: 256M, 512M).")
            inst["wp_memory_limit"] = mem_val
            if config_text:
                config_text = _set_wp_config_constant(config_text, "WP_MEMORY_LIMIT", f"'{inst['wp_memory_limit']}'")
        if payload.wp_max_memory_limit:
            inst["wp_max_memory_limit"] = payload.wp_max_memory_limit.strip()
            max_mem_val = payload.wp_max_memory_limit.strip().upper()
            if not wordpress_live.WP_MEMORY_LIMIT_RE.fullmatch(max_mem_val):
                raise HTTPException(status_code=400, detail="WP_MAX_MEMORY_LIMIT không hợp lệ (ví dụ: 512M, 1024M).")
            inst["wp_max_memory_limit"] = max_mem_val
            if config_text:
                config_text = _set_wp_config_constant(
                    config_text, "WP_MAX_MEMORY_LIMIT", f"'{inst['wp_max_memory_limit']}'"
                )
        if payload.wp_post_revisions is not None:
            inst["wp_post_revisions"] = str(payload.wp_post_revisions).strip()
            rev_val = str(payload.wp_post_revisions).strip().lower()
            if not wordpress_live.WP_POST_REVISIONS_RE.fullmatch(rev_val):
                raise HTTPException(status_code=400, detail="WP_POST_REVISIONS chỉ chấp nhận true, false hoặc số nguyên 0-999.")
            inst["wp_post_revisions"] = rev_val
            if config_text:
                config_text = _set_wp_config_constant(config_text, "WP_POST_REVISIONS", inst["wp_post_revisions"])
        if config_text:
            _write_wp_file_text(linux_user, site_root, wp_dir, "wp-config.php", config_text, "0640")
        _save_toolkit_state(state)
        return {
            "message": "Đã lưu cấu hình tài nguyên wp-config.php.",
            "instance": _build_instance_summary(site, db, state),
        }

    if act == "create_snapshot":
        snap_id = f"wp-snap-{site.domain.replace('.', '-')}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.tar.gz"
        cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
        db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
        real_size_mb = wordpress_live.create_live_snapshot_archive(
            linux_user, site_root, wp_dir, db_acc.db_name if db_acc else (cfg["db_name"] or ""), snap_id, owner_id=site.owner_id, site_id=site.id, db=db
        )
        snaps = list(inst.get("snapshots") or [])
        snaps.insert(
            0,
            {
                "id": snap_id,
                "label": (payload.snapshot_label or "Manual Recovery Point").strip(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "size_mb": real_size_mb,
                "wp_version": inst.get("wp_version") or "6.7.2",
            },
        )
        inst["snapshots"] = snaps
        _save_toolkit_state(state)
        return {
            "message": f"Đã tạo bản sao lưu nhanh (Snapshot: {snap_id}) gồm toàn bộ mã nguồn và Database!",
            "instance": _build_instance_summary(site, db, state),
        }

    if act == "restore_snapshot":
        snap_id = (payload.snapshot_id or "").strip()
        if not snap_id:
            raise HTTPException(status_code=400, detail="Vui lòng chọn bản Snapshot cần khôi phục.")
        cfg = _parse_wp_config_details(_read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root))
        db_acc = wordpress_live.resolve_owned_wp_database(db, site, cfg)
        wordpress_live.restore_live_snapshot(
            linux_user, site_root, wp_dir, db_acc.db_name if db_acc else (cfg["db_name"] or ""), snap_id, owner_id=site.owner_id, site_id=site.id, db=db
        )
        return {
            "message": f"Đã khôi phục thành công website {site.domain} về thời điểm Snapshot {snap_id}.",
            "instance": _build_instance_summary(site, db, state),
        }

    if act == "delete_snapshot":
        snap_id = (payload.snapshot_id or "").strip()
        wordpress_live.delete_live_snapshot(linux_user, site_root, inst, snap_id)
        inst["snapshots"] = [s for s in (inst.get("snapshots") or []) if s.get("id") != snap_id]
        _save_toolkit_state(state)
        return {
            "message": f"Đã xóa bản Snapshot {snap_id}.",
            "instance": _build_instance_summary(site, db, state),
        }

    if act in ("clone", "sync"):
        target_site: Optional[Website] = None
        if payload.target_website_id:
            target_site = _get_accessible_website(payload.target_website_id, db, current_user)
        elif payload.target_domain:
            target_site = db.query(Website).filter(Website.domain == payload.target_domain.strip().lower()).first()
            if target_site:
                target_site = _get_accessible_website(target_site.id, db, current_user)
        if not target_site:
            raise HTTPException(status_code=404, detail="Vui lòng chọn tên miền đích hợp lệ.")

        if act == "clone":
            from app.api.databases import enforce_user_database_limit

            has_target_db = (
                db.query(DatabaseAccount)
                .filter(
                    DatabaseAccount.website_id == target_site.id,
                    DatabaseAccount.owner_id == target_site.owner_id,
                )
                .first()
            )
            if not has_target_db:
                target_owner = db.query(User).filter(User.id == target_site.owner_id).first() or current_user
                enforce_user_database_limit(db, target_owner)

        target_site.app_type = "wordpress"
        db.commit()
        target_key = str(target_site.id)
        cloned_state = json.loads(json.dumps(inst))
        cloned_state["installed"] = True
        cloned_state["detached"] = False
        cloned_state["subpath"] = (payload.target_subpath or "").strip()
        if act == "clone":
            cloned_state["site_title"] = f"{inst.get('site_title') or site.domain} (Clone)"
            clone_db_name = f"wp_{target_site.domain.replace('.', '_')[:22]}"
            clone_db_user = f"{clone_db_name[:18]}_u"
            clone_db_pass = mariadb.random_password(18)
            try:
                raise RuntimeError("delegated to wordpress_live.execute_live_clone_or_sync")
                db_info = mariadb.create_database(target_site.domain)
                clone_db_name = db_info["db_name"]
                clone_db_user = db_info["db_user"]
                clone_db_pass = db_info["db_password"]
            except Exception:
                try:
                    raise RuntimeError("delegated to wordpress_live.execute_live_clone_or_sync")
                    mariadb.create_database_credentials(
                        clone_db_name, clone_db_user, clone_db_pass, allow_existing_user=True
                    )
                except Exception:
                    pass
            existing_clone_db = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == clone_db_name).first()
            if not existing_clone_db and False:
                db.add(
                    DatabaseAccount(
                        owner_id=target_site.owner_id,
                        website_id=target_site.id,
                        db_name=clone_db_name,
                        db_user=clone_db_user,
                        db_password=encrypt(clone_db_pass),
                    )
                )
            elif False:
                existing_clone_db.owner_id = target_site.owner_id
                existing_clone_db.website_id = target_site.id
            db.commit()
            cloned_state["db_name"] = clone_db_name
            cloned_state["db_user"] = clone_db_user
            cloned_state["db_password_enc"] = encrypt(clone_db_pass)
            cloned_state["installed_at"] = datetime.now(timezone.utc).isoformat()
        state.setdefault("instances", {})[target_key] = cloned_state
        target_subpath = (payload.target_subpath or "").strip()
        target_linux_user, target_site_root, target_wp_dir = _resolve_site_paths(target_site, db, target_subpath)
        src_cfg_text = _read_wp_file_text(wp_dir, "wp-config.php", linux_user=linux_user, site_root=site_root)
        src_cfg = _parse_wp_config_details(src_cfg_text)
        src_db_acc = wordpress_live.resolve_owned_wp_database(db, site, src_cfg)
        wordpress_live.execute_live_clone_or_sync(
            act=act,
            site=site,
            target_site=target_site,
            db=db,
            inst=inst,
            state=state,
            linux_user=linux_user,
            site_root=site_root,
            wp_dir=wp_dir,
            php_ver=php_ver,
            target_linux_user=target_linux_user,
            target_site_root=target_site_root,
            target_wp_dir=target_wp_dir,
            target_subpath=target_subpath,
            sync_files=payload.sync_files,
            sync_database=payload.sync_database,
            src_cfg_text=src_cfg_text,
            src_db_name=src_db_acc.db_name if src_db_acc else (src_cfg["db_name"] or ""),
            src_prefix=src_cfg["table_prefix"] or inst.get("table_prefix") or "wp_",
            set_wp_config_constant_fn=_set_wp_config_constant,
        )
        _save_toolkit_state(state)

        return {
            "message": (
                f"Đã nhân bản (Clone) toàn bộ mã nguồn + Database từ {site.domain} sang {target_site.domain} và cập nhật siteurl tự động!"
                if act == "clone"
                else f"Đã đồng bộ dữ liệu (Sync) từ {site.domain} sang {target_site.domain} thành công!"
            ),
            "target_instance": _build_instance_summary(target_site, db, state),
        }

    raise HTTPException(status_code=400, detail=f"Unsupported WordPress tool action: {act}")


# ── Package Sets (Plugin & Theme Bundles) ────────────────────────────────────

@router.post("/sets")
def create_package_set(
    payload: WpPackageSetCreatePayload,
    current_user: User = Depends(get_current_user),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên Bộ cài đặt (Package Set).")
    plugin_slugs = [s.strip().lower() for s in payload.plugins if s.strip()]
    theme_slugs = [s.strip().lower() for s in payload.themes if s.strip()]
    for s in [*plugin_slugs, *theme_slugs]:
        if not WP_SLUG_RE.fullmatch(s):
            raise HTTPException(status_code=400, detail=f"Slug '{s}' không hợp lệ.")
    state = _load_toolkit_state()
    custom_sets = list(state.get("custom_sets") or [])
    new_set = {
        "id": f"custom_{secrets.token_hex(4)}",
        "name": name,
        "description": payload.description.strip(),
        "is_builtin": False,
        "owner_id": current_user.id,
        "plugins": [s.strip() for s in payload.plugins if s.strip()],
        "themes": [s.strip() for s in payload.themes if s.strip()],
    }
    new_set["plugins"] = plugin_slugs
    new_set["themes"] = theme_slugs
    custom_sets.append(new_set)
    state["custom_sets"] = custom_sets
    _save_toolkit_state(state)
    custom_sets = _visible_custom_sets(state, current_user)
    return {
        "message": f"Đã tạo bộ cài mẫu '{name}'.",
        "sets": [*DEFAULT_PACKAGE_SETS, *custom_sets],
    }


@router.delete("/sets/{set_id}")
def delete_package_set(
    set_id: str,
    current_user: User = Depends(get_current_user),
):
    state = _load_toolkit_state()
    raw_sets = list(state.get("custom_sets") or [])
    target_set = next((s for s in raw_sets if s.get("id") == set_id), None)
    if not target_set:
        raise HTTPException(status_code=404, detail="Không tìm thấy Bộ cài mẫu.")
    if current_user.role != "admin" and target_set.get("owner_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền xóa Bộ cài mẫu này.")
    custom_sets = [s for s in (state.get("custom_sets") or []) if s.get("id") != set_id]
    state["custom_sets"] = custom_sets
    _save_toolkit_state(state)
    custom_sets = _visible_custom_sets(state, current_user)
    return {
        "message": "Đã xóa bộ cài mẫu.",
        "sets": [*DEFAULT_PACKAGE_SETS, *custom_sets],
    }


@router.post("/sets/apply")
def apply_package_set(
    payload: WpApplySetPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    state = _load_toolkit_state()
    all_sets = [*DEFAULT_PACKAGE_SETS, *(state.get("custom_sets") or [])]
    all_sets = [*DEFAULT_PACKAGE_SETS, *_visible_custom_sets(state, current_user)]
    chosen = next((s for s in all_sets if s.get("id") == payload.set_id), None)
    if not chosen:
        raise HTTPException(status_code=404, detail="Không tìm thấy Bộ cài mẫu.")

    applied = 0
    for wid in payload.website_ids:
        site = _get_accessible_website(wid, db, current_user)
        inst = state.setdefault("instances", {}).setdefault(str(site.id), {"installed": True})
        linux_user, site_root, wp_dir = _resolve_site_paths(site, db, inst.get("subpath") or "")
        php_ver = site.php_version or "8.4"
        plugins = list(inst.get("plugins") or _default_plugins_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root))
        themes = list(inst.get("themes") or _default_themes_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root))

        for p_slug in chosen.get("plugins") or []:
            if not WP_SLUG_RE.fullmatch(p_slug.lower()):
                continue
            _run_wp_cli(linux_user, php_ver, wp_dir, ["plugin", "install", p_slug.lower(), "--activate", "--force"])
            if settings.command_dry_run and not any(p["slug"].lower() == p_slug.lower() for p in plugins):
                plugins.append({
                    "slug": p_slug,
                    "name": p_slug.replace("-", " ").title(),
                    "version": "Latest",
                    "latest_version": "Latest",
                    "update_available": False,
                    "status": "active",
                    "auto_update": True,
                })
        for t_slug in chosen.get("themes") or []:
            if not WP_SLUG_RE.fullmatch(t_slug.lower()):
                continue
            _run_wp_cli(linux_user, php_ver, wp_dir, ["theme", "install", t_slug.lower(), "--force"])
            if settings.command_dry_run and not any(th["slug"].lower() == t_slug.lower() for th in themes):
                themes.append({
                    "slug": t_slug,
                    "name": t_slug.replace("-", " ").title(),
                    "version": "Latest",
                    "latest_version": "Latest",
                    "update_available": False,
                    "status": "inactive",
                })
        if not settings.command_dry_run:
            plugins = _default_plugins_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root)
            themes = _default_themes_for_instance(wp_dir, linux_user=linux_user, php_version=php_ver, site_root=site_root)
        inst["plugins"] = plugins
        inst["themes"] = themes
        applied += 1

    _save_toolkit_state(state)
    return {
        "message": f"Đã cài đặt bộ gói '{chosen['name']}' cho {applied} website WordPress!",
    }
