import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, Website
from app.services import mariadb, nginx, site_users, wordpress
from app.services.shell import shell

logger = logging.getLogger("spanel.wordpress_live")

WP_SLUG_RE = re.compile(r"^[a-zA-Z0-9._-]{1,120}$")
WP_PREFIX_RE = re.compile(r"^[a-zA-Z0-9_]{2,24}$")


def run_wp_cli(
    linux_user: str,
    php_version: str,
    wp_dir: Path,
    args: List[str],
    *,
    check: bool = False,
    input_data: Optional[str] = None,
    sensitive: bool = False,
):
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


def read_wp_file_text(
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


def write_wp_file_text(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    rel_path: str,
    content: str,
    mode: str = "0644",
) -> None:
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


def delete_wp_path(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    rel_path: str,
) -> None:
    target = wp_dir / rel_path
    try:
        if target.exists() and target.is_file():
            target.unlink()
            return
    except Exception:
        pass
    try:
        rel_to_root = str(target.relative_to(site_root)).replace("\\", "/")
    except ValueError:
        rel_to_root = f"public_html/{rel_path.lstrip('/')}"
    if not settings.command_dry_run:
        shell.privileged(
            "site-file-delete",
            helper_args=[linux_user, str(site_root), rel_to_root],
            fallback=["rm", "-rf", str(target)],
            check=False,
        )


def wp_path_exists(
    wp_dir: Path,
    rel_path: str,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> bool:
    target = wp_dir / rel_path
    try:
        if target.exists():
            return True
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
                helper_args=[linux_user, str(site_root), "test", "-e", rel_to_root],
                fallback=["test", "-e", str(target)],
                check=False,
            )
            return res.returncode == 0
        except Exception:
            pass
    return False


def extract_wp_cli_json(raw_stdout: Optional[str]) -> Optional[Any]:
    if not raw_stdout:
        return None
    text = raw_stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    for start_char, end_char in (("[", "]"), ("{", "}")):
        start_idx = text.find(start_char)
        end_idx = text.rfind(end_char)
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            candidate = text[start_idx : end_idx + 1]
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None


def query_wp_db_live_meta(db_name: str, table_prefix: str = "wp_") -> Dict[str, Any]:
    """Query live WordPress options and primary administrator directly from MariaDB."""
    meta: Dict[str, Any] = {
        "site_title": "",
        "site_description": "",
        "admin_email": "",
        "admin_user": "",
        "blog_public": None,
        "stylesheet": "",
        "template": "",
        "active_plugin_slugs": None,
        "auto_update_plugin_slugs": set(),
    }
    if not db_name or settings.command_dry_run:
        return meta
    if not mariadb.IDENT_RE.fullmatch(db_name) or not WP_PREFIX_RE.fullmatch(table_prefix):
        return meta

    options_table = f"{table_prefix}options"
    users_table = f"{table_prefix}users"
    sql = (
        f"USE `{db_name}`; "
        f"SELECT 'OPT', option_name, REPLACE(REPLACE(option_value, '\\r', ' '), '\\n', ' ') "
        f"FROM `{options_table}` "
        f"WHERE option_name IN ('blogname','blogdescription','admin_email','blog_public','stylesheet','template','active_plugins','auto_update_plugins'); "
        f"SELECT 'USR', user_login, user_email FROM `{users_table}` ORDER BY ID ASC LIMIT 5;"
    )
    try:
        res = shell.privileged(
            "mariadb-exec",
            fallback=["mysql", "-u", "root", "-N", "-B"],
            input=sql,
            check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return meta
        admin_candidates: List[tuple[str, str]] = []
        for raw_line in res.stdout.splitlines():
            parts = raw_line.split("\t")
            if len(parts) < 3:
                continue
            kind = parts[0].strip()
            key = parts[1].strip()
            val = "\t".join(parts[2:]).strip()
            if kind == "OPT":
                if key == "blogname":
                    meta["site_title"] = val
                elif key == "blogdescription":
                    meta["site_description"] = val
                elif key == "admin_email":
                    meta["admin_email"] = val
                elif key == "blog_public":
                    meta["blog_public"] = val != "0"
                elif key == "stylesheet":
                    meta["stylesheet"] = val
                elif key == "template":
                    meta["template"] = val
                elif key == "active_plugins":
                    slugs = set()
                    for item in re.findall(r's:\d+:"([^"]+)";', val):
                        slug_part = item.split("/")[0].strip()
                        if slug_part.endswith(".php"):
                            slug_part = slug_part[:-4]
                        if slug_part:
                            slugs.add(slug_part.lower())
                    meta["active_plugin_slugs"] = slugs
                elif key == "auto_update_plugins":
                    auto_slugs = set()
                    for item in re.findall(r's:\d+:"([^"]+)";', val):
                        slug_part = item.split("/")[0].strip()
                        if slug_part.endswith(".php"):
                            slug_part = slug_part[:-4]
                        if slug_part:
                            auto_slugs.add(slug_part.lower())
                    meta["auto_update_plugin_slugs"] = auto_slugs
            elif kind == "USR" and key:
                admin_candidates.append((key, val))

        if admin_candidates:
            chosen_login = admin_candidates[0][0]
            chosen_email = admin_candidates[0][1]
            for u_login, u_email in admin_candidates:
                if meta["admin_email"] and u_email.lower() == meta["admin_email"].lower():
                    chosen_login = u_login
                    chosen_email = u_email
                    break
            meta["admin_user"] = chosen_login
            if not meta["admin_email"] and chosen_email:
                meta["admin_email"] = chosen_email
    except Exception as exc:
        logger.debug("query_wp_db_live_meta warning for %s: %s", db_name, exc)
    return meta


def update_wp_db_option_direct(db_name: str, table_prefix: str, option_name: str, option_value: str) -> None:
    if not db_name or settings.command_dry_run:
        return
    if not mariadb.IDENT_RE.fullmatch(db_name) or not WP_PREFIX_RE.fullmatch(table_prefix):
        return
    safe_opt = option_name.replace("'", "''")
    safe_val = option_value.replace("\\", "\\\\").replace("'", "''")
    sql = (
        f"USE `{db_name}`; "
        f"UPDATE `{table_prefix}options` SET option_value = '{safe_val}' WHERE option_name = '{safe_opt}';"
    )
    shell.privileged(
        "mariadb-exec",
        fallback=["mysql", "-u", "root"],
        input=sql,
        check=False,
    )


def rename_wp_admin_user_direct(db_name: str, table_prefix: str, new_login: str) -> None:
    if not db_name or not new_login or settings.command_dry_run:
        return
    if not mariadb.IDENT_RE.fullmatch(db_name) or not WP_PREFIX_RE.fullmatch(table_prefix):
        return
    if not wordpress.WP_USER_RE.fullmatch(new_login):
        return
    safe_login = new_login.replace("'", "''")
    sql = (
        f"USE `{db_name}`; "
        f"UPDATE `{table_prefix}users` SET user_login = '{safe_login}', user_nicename = '{safe_login.lower()}' "
        f"WHERE user_login = 'admin' OR ID = 1 LIMIT 1;"
    )
    shell.privileged(
        "mariadb-exec",
        fallback=["mysql", "-u", "root"],
        input=sql,
        check=False,
    )


def rename_wp_table_prefix_direct(db_name: str, old_prefix: str, new_prefix: str) -> bool:
    if not db_name or old_prefix == new_prefix or settings.command_dry_run:
        return False
    if not mariadb.IDENT_RE.fullmatch(db_name):
        return False
    if not WP_PREFIX_RE.fullmatch(old_prefix) or not WP_PREFIX_RE.fullmatch(new_prefix):
        return False
    try:
        tables = mariadb.get_database_tables_breakdown(db_name)
        matching = [
            t["table_name"]
            for t in tables
            if isinstance(t.get("table_name"), str) and t["table_name"].startswith(old_prefix)
        ]
        if not matching:
            return False
        rename_pairs = [f"`{tbl}` TO `{new_prefix}{tbl[len(old_prefix):]}`" for tbl in matching]
        sql = (
            f"USE `{db_name}`; "
            f"RENAME TABLE {', '.join(rename_pairs)}; "
            f"UPDATE `{new_prefix}options` SET option_name = '{new_prefix}user_roles' "
            f"WHERE option_name = '{old_prefix}user_roles'; "
            f"UPDATE `{new_prefix}usermeta` SET meta_key = CONCAT('{new_prefix}', SUBSTRING(meta_key, {len(old_prefix) + 1})) "
            f"WHERE meta_key LIKE '{old_prefix}%';"
        )
        res = shell.privileged(
            "mariadb-exec",
            fallback=["mysql", "-u", "root"],
            input=sql,
            check=False,
        )
        return res.returncode == 0
    except Exception as exc:
        logger.warning("rename_wp_table_prefix_direct warning: %s", exc)
        return False


def _parse_plugin_header_metadata(
    wp_dir: Path,
    slug: str,
    is_dir: bool,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> tuple[str, str]:
    default_name = slug.replace("-", " ").replace("_", " ").title()
    default_ver = "1.0.0"
    candidates: List[str] = []
    if is_dir:
        candidates.append(f"wp-content/plugins/{slug}/{slug}.php")
        candidates.append(f"wp-content/plugins/{slug}/plugin.php")
        candidates.append(f"wp-content/plugins/{slug}/index.php")
        p_dir = wp_dir / "wp-content" / "plugins" / slug
        try:
            if p_dir.exists() and p_dir.is_dir():
                for php_f in sorted(p_dir.glob("*.php"))[:6]:
                    rel = f"wp-content/plugins/{slug}/{php_f.name}"
                    if rel not in candidates:
                        candidates.append(rel)
        except Exception:
            pass
    else:
        candidates.append(f"wp-content/plugins/{slug}.php")

    for rel_path in candidates:
        txt = read_wp_file_text(wp_dir, rel_path, linux_user=linux_user, site_root=site_root)
        if not txt or "Plugin Name:" not in txt:
            continue
        head = txt[:8192]
        m_name = re.search(r"^\s*\*?\s*Plugin Name:\s*(.+)$", head, re.MULTILINE | re.IGNORECASE)
        m_ver = re.search(r"^\s*\*?\s*Version:\s*([0-9A-Za-z._-]+)", head, re.MULTILINE | re.IGNORECASE)
        name = m_name.group(1).strip() if m_name else default_name
        ver = m_ver.group(1).strip() if m_ver else default_ver
        return name, ver
    return default_name, default_ver


def _parse_theme_header_metadata(
    wp_dir: Path,
    slug: str,
    linux_user: Optional[str] = None,
    site_root: Optional[Path] = None,
) -> tuple[str, str]:
    default_name = slug.replace("-", " ").replace("_", " ").title()
    default_ver = "1.0"
    css = read_wp_file_text(wp_dir, f"wp-content/themes/{slug}/style.css", linux_user=linux_user, site_root=site_root)
    if not css:
        return default_name, default_ver
    head = css[:8192]
    m_name = re.search(r"^\s*\*?\s*Theme Name:\s*(.+)$", head, re.MULTILINE | re.IGNORECASE)
    m_ver = re.search(r"^\s*\*?\s*Version:\s*([0-9A-Za-z._-]+)", head, re.MULTILINE | re.IGNORECASE)
    name = m_name.group(1).strip() if m_name else default_name
    ver = m_ver.group(1).strip() if m_ver else default_ver
    return name, ver


def discover_live_plugins(
    wp_dir: Path,
    linux_user: Optional[str] = None,
    php_version: str = "8.4",
    site_root: Optional[Path] = None,
    db_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return the exact live plugins installed on this WordPress site. Never returns fake/mock plugins."""
    if linux_user and not settings.command_dry_run:
        res = run_wp_cli(
            linux_user,
            php_version or "8.4",
            wp_dir,
            ["plugin", "list", "--format=json", "--skip-plugins", "--skip-themes"],
        )
        parsed = extract_wp_cli_json(res.stdout) if res.returncode == 0 else None
        if isinstance(parsed, list):
            return [
                {
                    "slug": str(item.get("name") or "").strip(),
                    "name": (item.get("title") or str(item.get("name") or "").replace("-", " ").title()).strip(),
                    "version": str(item.get("version") or "1.0.0").strip(),
                    "latest_version": str(item.get("update_version") or item.get("version") or "1.0.0").strip(),
                    "update_available": item.get("update") == "available",
                    "status": "active" if item.get("status") in ("active", "active-network") else "inactive",
                    "auto_update": item.get("auto_update") == "on",
                }
                for item in parsed
                if isinstance(item, dict)
                and item.get("name")
                and item.get("status") not in ("must-use", "dropin")
            ]

    plugins_dir = wp_dir / "wp-content" / "plugins"
    entries: List[tuple[str, bool]] = []
    if plugins_dir.exists() and plugins_dir.is_dir():
        try:
            for item in sorted(plugins_dir.iterdir()):
                if item.name.startswith(".") or item.name.lower() == "index.php":
                    continue
                if item.is_dir():
                    entries.append((item.name, True))
                elif item.is_file() and item.name.endswith(".php"):
                    entries.append((item.stem, False))
        except Exception:
            pass
    elif linux_user and site_root and not settings.command_dry_run:
        try:
            try:
                rel_plugins = str(plugins_dir.relative_to(site_root)).replace("\\", "/")
            except ValueError:
                rel_plugins = "public_html/wp-content/plugins"
            res_ls = shell.privileged(
                "site-file-list",
                helper_args=[linux_user, str(site_root), rel_plugins],
                check=False,
            )
            if res_ls.returncode == 0 and res_ls.stdout:
                for line in res_ls.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        ftype, fname = parts[0], parts[3].strip()
                        if fname.startswith(".") or fname.lower() == "index.php":
                            continue
                        if ftype == "directory":
                            entries.append((fname, True))
                        elif ftype == "file" and fname.endswith(".php"):
                            entries.append((fname[:-4], False))
        except Exception:
            pass

    active_slugs = (db_meta or {}).get("active_plugin_slugs")
    auto_slugs = (db_meta or {}).get("auto_update_plugin_slugs") or set()
    found: List[Dict[str, Any]] = []
    for slug, is_dir in entries:
        p_name, p_ver = _parse_plugin_header_metadata(
            wp_dir, slug, is_dir, linux_user=linux_user, site_root=site_root
        )
        is_active = (slug.lower() in active_slugs) if isinstance(active_slugs, set) else False
        found.append({
            "slug": slug,
            "name": p_name,
            "version": p_ver,
            "latest_version": p_ver,
            "update_available": False,
            "status": "active" if is_active else "inactive",
            "auto_update": slug.lower() in auto_slugs,
        })
    return found


def discover_live_themes(
    wp_dir: Path,
    linux_user: Optional[str] = None,
    php_version: str = "8.4",
    site_root: Optional[Path] = None,
    db_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return the exact live themes installed on this WordPress site. Never returns fake/mock themes."""
    if linux_user and not settings.command_dry_run:
        res = run_wp_cli(
            linux_user,
            php_version or "8.4",
            wp_dir,
            ["theme", "list", "--format=json", "--skip-plugins", "--skip-themes"],
        )
        parsed = extract_wp_cli_json(res.stdout) if res.returncode == 0 else None
        if isinstance(parsed, list):
            return [
                {
                    "slug": str(item.get("name") or "").strip(),
                    "name": (item.get("title") or str(item.get("name") or "").replace("-", " ").title()).strip(),
                    "version": str(item.get("version") or "1.0").strip(),
                    "latest_version": str(item.get("update_version") or item.get("version") or "1.0").strip(),
                    "update_available": item.get("update") == "available",
                    "status": "active" if item.get("status") == "active" else "inactive",
                }
                for item in parsed
                if isinstance(item, dict) and item.get("name")
            ]

    themes_dir = wp_dir / "wp-content" / "themes"
    slugs: List[str] = []
    if themes_dir.exists() and themes_dir.is_dir():
        try:
            for item in sorted(themes_dir.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    slugs.append(item.name)
        except Exception:
            pass
    elif linux_user and site_root and not settings.command_dry_run:
        try:
            try:
                rel_themes = str(themes_dir.relative_to(site_root)).replace("\\", "/")
            except ValueError:
                rel_themes = "public_html/wp-content/themes"
            res_ls = shell.privileged(
                "site-file-list",
                helper_args=[linux_user, str(site_root), rel_themes],
                check=False,
            )
            if res_ls.returncode == 0 and res_ls.stdout:
                for line in res_ls.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 4 and parts[0] == "directory":
                        fname = parts[3].strip()
                        if not fname.startswith("."):
                            slugs.append(fname)
        except Exception:
            pass

    active_stylesheet = ((db_meta or {}).get("stylesheet") or "").strip().lower()
    found: List[Dict[str, Any]] = []
    for idx, slug in enumerate(slugs):
        t_name, t_ver = _parse_theme_header_metadata(wp_dir, slug, linux_user=linux_user, site_root=site_root)
        is_active = (slug.lower() == active_stylesheet) if active_stylesheet else (idx == 0)
        found.append({
            "slug": slug,
            "name": t_name,
            "version": t_ver,
            "latest_version": t_ver,
            "update_available": False,
            "status": "active" if is_active else "inactive",
        })
    return found


def create_live_snapshot_archive(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    db_name: str,
    snap_id: str,
) -> float:
    snap_dir = site_root / "wp-snapshots"
    snap_file = snap_dir / snap_id
    real_size_mb = 0.1
    if settings.command_dry_run:
        return real_size_mb
    try:
        snap_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        shell.privileged(
            "terminal-exec",
            helper_args=[linux_user, str(site_root), "mkdir", "-p", "wp-snapshots"],
            fallback=["mkdir", "-p", str(snap_dir)],
            check=False,
        )
    sql_dump = wp_dir / ".spanel-db-snapshot.sql"
    if db_name:
        try:
            mariadb.export_database(db_name, str(sql_dump))
        except Exception as exc:
            logger.warning("Snapshot DB dump warning: %s", exc)
    shell.privileged(
        "terminal-exec",
        helper_args=[linux_user, str(site_root), "tar", "-czf", f"wp-snapshots/{snap_id}", "-C", str(wp_dir), "."],
        fallback=["tar", "-czf", str(snap_file), "-C", str(wp_dir), "."],
        check=False,
    )
    delete_wp_path(linux_user, site_root, wp_dir, ".spanel-db-snapshot.sql")
    try:
        if snap_file.exists():
            real_size_mb = max(0.01, round(snap_file.stat().st_size / (1024 * 1024), 2))
    except Exception:
        pass
    return real_size_mb


def create_live_snapshot(
    site: Website,
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    db_name: str,
    inst: Dict[str, Any],
    snapshot_label: Optional[str],
    detected_wp_ver: str,
) -> str:
    snap_id = f"wp-snap-{site.domain.replace('.', '-')}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.tar.gz"
    real_size_mb = create_live_snapshot_archive(linux_user, site_root, wp_dir, db_name, snap_id)

    snaps = list(inst.get("snapshots") or [])
    snaps.insert(
        0,
        {
            "id": snap_id,
            "label": (snapshot_label or "Manual Recovery Point").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size_mb": real_size_mb,
            "wp_version": detected_wp_ver or inst.get("wp_version") or "6.7.2",
        },
    )
    inst["snapshots"] = snaps
    return snap_id


def restore_live_snapshot(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    db_name: str,
    snapshot_id: Optional[str],
) -> str:
    snap_id = Path((snapshot_id or "").strip()).name
    if not snap_id:
        raise HTTPException(status_code=400, detail="Vui lòng chọn bản Snapshot cần khôi phục.")
    snap_file = site_root / "wp-snapshots" / snap_id
    if not settings.command_dry_run:
        if not snap_file.exists():
            raise HTTPException(status_code=404, detail=f"Không tìm thấy tệp Snapshot {snap_id} trên ổ đĩa.")
        shell.privileged(
            "terminal-exec",
            helper_args=[linux_user, str(site_root), "tar", "-xzf", f"wp-snapshots/{snap_id}", "-C", str(wp_dir)],
            fallback=["tar", "-xzf", str(snap_file), "-C", str(wp_dir)],
            check=False,
        )
        sql_dump = wp_dir / ".spanel-db-snapshot.sql"
        if db_name and wp_path_exists(wp_dir, ".spanel-db-snapshot.sql", linux_user, site_root):
            try:
                mariadb.import_database(db_name, str(sql_dump))
            except Exception as exc:
                logger.warning("Snapshot DB restore warning: %s", exc)
            delete_wp_path(linux_user, site_root, wp_dir, ".spanel-db-snapshot.sql")
    return snap_id


def delete_live_snapshot(
    linux_user: str,
    site_root: Path,
    inst: Dict[str, Any],
    snapshot_id: Optional[str],
) -> str:
    snap_id = Path((snapshot_id or "").strip()).name
    if snap_id and not settings.command_dry_run:
        snap_file = site_root / "wp-snapshots" / snap_id
        try:
            if snap_file.exists():
                snap_file.unlink()
        except Exception:
            pass
        shell.privileged(
            "site-file-delete",
            helper_args=[linux_user, str(site_root), f"wp-snapshots/{snap_id}"],
            fallback=["rm", "-f", str(snap_file)],
            check=False,
        )
    inst["snapshots"] = [s for s in (inst.get("snapshots") or []) if s.get("id") != snap_id]
    return snap_id


def execute_live_clone_or_sync(
    *,
    act: str,
    site: Website,
    target_site: Website,
    db: Session,
    inst: Dict[str, Any],
    state: Dict[str, Any],
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    php_ver: str,
    target_linux_user: str,
    target_site_root: Path,
    target_wp_dir: Path,
    target_subpath: str,
    sync_files: bool,
    sync_database: bool,
    src_cfg_text: str,
    src_db_name: str,
    src_prefix: str,
    set_wp_config_constant_fn: Callable[[str, str, str], str],
) -> None:
    target_php_ver = target_site.php_version or php_ver or "8.4"
    try:
        target_linux_user = site_users.ensure_site_runtime(
            target_site.domain, str(target_site_root), target_php_ver, target_linux_user
        )
    except Exception:
        pass

    target_site.app_type = "wordpress"
    target_site.php_version = target_php_ver
    target_site.linux_user = target_linux_user
    target_site.root_path = str(target_site_root)
    target_site.nginx_rewrite_mode = "front_controller"
    db.commit()

    target_key = str(target_site.id)
    cloned_state = json.loads(json.dumps(inst))
    cloned_state["installed"] = True
    cloned_state["detached"] = False
    cloned_state["subpath"] = target_subpath

    target_db_acc = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == target_site.id).first()
    if not target_db_acc:
        if act == "clone":
            cloned_state["site_title"] = f"{inst.get('site_title') or site.domain} (Clone)"
        seed = f"{target_site.domain}_{secrets.token_hex(2)}"
        try:
            db_info = mariadb.create_database(seed, prefix="wp")
            clone_db_name = db_info["db_name"]
            clone_db_user = db_info["db_user"]
            clone_db_pass = db_info["db_password"]
        except Exception:
            clone_db_name = mariadb.safe_db_identifier(seed, "wp")
            clone_db_user = mariadb.safe_db_identifier(clone_db_name, "u")
            clone_db_pass = mariadb.random_password(20)
            try:
                mariadb.create_database_credentials(
                    clone_db_name, clone_db_user, clone_db_pass, allow_existing_user=True
                )
            except Exception:
                pass
        existing_clone_db = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == clone_db_name).first()
        if not existing_clone_db:
            target_db_acc = DatabaseAccount(
                owner_id=target_site.owner_id,
                website_id=target_site.id,
                db_name=clone_db_name,
                db_user=clone_db_user,
                db_password=encrypt(clone_db_pass),
            )
            db.add(target_db_acc)
        else:
            existing_clone_db.owner_id = target_site.owner_id
            existing_clone_db.website_id = target_site.id
            existing_clone_db.db_user = clone_db_user
            existing_clone_db.db_password = encrypt(clone_db_pass)
            target_db_acc = existing_clone_db
        db.commit()
    else:
        clone_db_name = target_db_acc.db_name
        clone_db_user = target_db_acc.db_user
        try:
            clone_db_pass = decrypt(target_db_acc.db_password)
        except Exception:
            clone_db_pass = mariadb.random_password(20)

    if sync_files and not settings.command_dry_run and str(wp_dir) != str(target_wp_dir):
        try:
            target_wp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        delete_wp_path(target_linux_user, target_site_root, target_wp_dir, "index.html")
        tmp_tar = Path(f"/tmp/spanel-wp-clone-{secrets.token_hex(6)}.tar.gz")
        try:
            shell.run(["tar", "-czf", str(tmp_tar), "-C", str(wp_dir), "."], check=False)
            shell.run(["tar", "-xzf", str(tmp_tar), "-C", str(target_wp_dir)], check=False)
            wordpress.fix_permissions(str(target_site_root), target_linux_user)
        finally:
            try:
                if tmp_tar.exists():
                    tmp_tar.unlink()
            except Exception:
                pass

    if sync_database and src_db_name and clone_db_name and not settings.command_dry_run:
        tmp_sql = Path(f"/tmp/spanel-wp-db-{secrets.token_hex(6)}.sql")
        try:
            mariadb.export_database(src_db_name, str(tmp_sql))
            mariadb.import_database(clone_db_name, str(tmp_sql))
        except Exception as exc:
            logger.warning("Clone/Sync database copy warning: %s", exc)
        finally:
            try:
                if tmp_sql.exists():
                    tmp_sql.unlink()
            except Exception:
                pass

    target_cfg = src_cfg_text or wordpress._render_wp_config(
        clone_db_name, clone_db_user, clone_db_pass, table_prefix=src_prefix
    )
    target_cfg = set_wp_config_constant_fn(target_cfg, "DB_NAME", f"'{clone_db_name}'")
    target_cfg = set_wp_config_constant_fn(target_cfg, "DB_USER", f"'{clone_db_user}'")
    target_cfg = set_wp_config_constant_fn(target_cfg, "DB_PASSWORD", f"'{clone_db_pass}'")
    write_wp_file_text(target_linux_user, target_site_root, target_wp_dir, "wp-config.php", target_cfg, "0640")

    src_scheme = "https" if site.ssl_enabled else "http"
    dst_scheme = "https" if target_site.ssl_enabled else "http"
    src_sub = f"/{inst['subpath'].strip('/')}" if inst.get("subpath", "").strip("/") else ""
    dst_sub = f"/{target_subpath.strip('/')}" if target_subpath.strip("/") else ""
    src_url = f"{src_scheme}://{site.domain}{src_sub}"
    dst_url = f"{dst_scheme}://{target_site.domain}{dst_sub}"

    update_wp_db_option_direct(clone_db_name, src_prefix, "siteurl", dst_url)
    update_wp_db_option_direct(clone_db_name, src_prefix, "home", dst_url)
    if src_url != dst_url:
        run_wp_cli(
            target_linux_user,
            target_php_ver,
            target_wp_dir,
            ["search-replace", src_url, dst_url, "--all-tables", "--precise", "--skip-plugins", "--skip-themes"],
        )

    try:
        php_socket = site_users.site_php_fpm_socket(target_linux_user, str(target_site_root), target_php_ver)
        nginx.rewrite_vhost(
            target_site.domain,
            str(target_site_root),
            app_type="wordpress",
            php_version=target_php_ver,
            php_fpm_socket_override=php_socket,
            document_root=target_site.document_root or "public_html",
            rewrite_mode="front_controller",
        )
    except Exception:
        pass

    cloned_state["db_name"] = clone_db_name
    cloned_state["db_user"] = clone_db_user
    cloned_state["table_prefix"] = src_prefix
    cloned_state["installed_at"] = datetime.now(timezone.utc).isoformat()
    state.setdefault("instances", {})[target_key] = cloned_state
