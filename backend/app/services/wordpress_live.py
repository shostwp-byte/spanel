import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, Website
from app.services import mariadb, nginx, site_users, wordpress
from app.services.shell import CommandResult, shell

logger = logging.getLogger("spanel.wordpress_live")

WP_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
WP_PREFIX_RE = re.compile(r"^[a-zA-Z0-9_]{2,24}$")
DB_IDENT_RE = re.compile(r"^[a-z0-9_]{1,64}$")
WP_MEMORY_LIMIT_RE = re.compile(r"^\d{1,5}[MG]$")
WP_POST_REVISIONS_RE = re.compile(r"^(true|false|\d{1,4})$")
_TOOLKIT_STATE_LOCK = threading.RLock()


def toolkit_state_path() -> Path:
    """Return a panel-owned state file path (never in /tmp)."""
    candidates = [
        Path("/var/lib/spanel/wordpress_toolkit_state.json"),
        Path(__file__).resolve().parents[2] / "data" / "wordpress_toolkit_state.json",
    ]
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if os.access(p.parent, os.W_OK):
                return p
        except Exception:
            continue
    fallback = Path(__file__).resolve().parents[2] / "wordpress_toolkit_state.json"
    return fallback


def _ensure_toolkit_state_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS wordpress_toolkit_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "payload TEXT NOT NULL, "
                "updated_at TEXT"
                ")"
            )
        )


def load_toolkit_state_safe() -> Dict[str, Any]:
    with _TOOLKIT_STATE_LOCK:
        try:
            _ensure_toolkit_state_table()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT payload FROM wordpress_toolkit_state WHERE id = 1")
                ).fetchone()
                if row and row[0]:
                    data = json.loads(row[0])
                    if isinstance(data, dict):
                        data.setdefault("instances", {})
                        data.setdefault("custom_sets", [])
                        return data
        except Exception as exc:
            logger.debug("load_toolkit_state_safe db read warning: %s", exc)

        path = toolkit_state_path()
        if path.exists() and not path.is_symlink():
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    data.setdefault("instances", {})
                    data.setdefault("custom_sets", [])
                    save_toolkit_state_safe(data)
                    return data
            except Exception as exc:
                logger.warning("Failed to load WP toolkit state file: %s", exc)
        return {"instances": {}, "custom_sets": []}


def save_toolkit_state_safe(state: Dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _TOOLKIT_STATE_LOCK:
        try:
            _ensure_toolkit_state_table()
            with engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT 1 FROM wordpress_toolkit_state WHERE id = 1")
                ).fetchone()
                if existing:
                    conn.execute(
                        text(
                            "UPDATE wordpress_toolkit_state SET payload = :payload, updated_at = :updated_at WHERE id = 1"
                        ),
                        {"payload": payload, "updated_at": now_iso},
                    )
                else:
                    conn.execute(
                        text(
                            "INSERT INTO wordpress_toolkit_state (id, payload, updated_at) VALUES (1, :payload, :updated_at)"
                        ),
                        {"payload": payload, "updated_at": now_iso},
                    )
        except Exception as exc:
            logger.warning("Failed to save WP toolkit state to DB: %s", exc)

        path = toolkit_state_path()
        try:
            if path.is_symlink():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".wp_toolkit_state.", dir=str(path.parent), text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
        except Exception as exc:
            logger.debug("save_toolkit_state_safe file mirror warning: %s", exc)


def is_authorized_wp_database(
    db_name: Optional[str],
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> bool:
    """Verify db_name is a valid, non-reserved customer database registered in DatabaseAccount
    and owned by owner_id (when provided). Never allow panel-level SQL on unregistered or
    other-tenant databases named in wp-config.php."""
    clean_db = (db_name or "").strip()
    if not clean_db or not DB_IDENT_RE.fullmatch(clean_db):
        return False
    if clean_db.lower() in mariadb.RESERVED_DATABASES:
        return False
    close_session = False
    session = db
    try:
        if session is None:
            session = SessionLocal()
            close_session = True
        acc = (
            session.query(DatabaseAccount)
            .filter(DatabaseAccount.db_name == clean_db)
            .first()
        )
        if acc is None:
            return False
        if owner_id is not None and acc.owner_id != owner_id:
            return False
        if site_id is not None and acc.website_id is not None and acc.website_id != site_id:
            if owner_id is None:
                site = session.query(Website).filter(Website.id == site_id).first()
                if site is not None and acc.owner_id != site.owner_id:
                    return False
        return True
    except Exception as exc:
        logger.debug("is_authorized_wp_database check failed for %s: %s", clean_db, exc)
        return False
    finally:
        if close_session and session is not None:
            try:
                session.close()
            except Exception:
                pass


def resolve_owned_wp_database(
    db: Session,
    site: Website,
    candidate_db_or_cfg: Any = None,
) -> Optional[DatabaseAccount]:
    """Return DatabaseAccount ONLY if owned by site.owner_id.
    Never returns a database owned by another user or an unregistered system database."""
    db_acc = (
        db.query(DatabaseAccount)
        .filter(
            DatabaseAccount.website_id == site.id,
            DatabaseAccount.owner_id == site.owner_id,
        )
        .first()
    )
    if db_acc is not None and db_acc.db_name.lower() not in mariadb.RESERVED_DATABASES:
        return db_acc

    if isinstance(candidate_db_or_cfg, dict):
        clean_candidate = str(candidate_db_or_cfg.get("db_name") or "").strip()
    else:
        clean_candidate = str(candidate_db_or_cfg or "").strip()
    if (
        clean_candidate
        and DB_IDENT_RE.fullmatch(clean_candidate)
        and clean_candidate.lower() not in mariadb.RESERVED_DATABASES
    ):
        cand_acc = (
            db.query(DatabaseAccount)
            .filter(
                DatabaseAccount.db_name == clean_candidate,
                DatabaseAccount.owner_id == site.owner_id,
            )
            .first()
        )
        if cand_acc is not None:
            if cand_acc.website_id is None:
                cand_acc.website_id = site.id
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            return cand_acc

    return None


def safe_reconcile_wordpress_databases(
    db: Session,
    *,
    website_id: Optional[int] = None,
    provision_missing: bool = False,
) -> int:
    """Safely link already-owned DatabaseAccount records (owner_id == site.owner_id,
    website_id IS NULL) to their Website if recorded in toolkit state.
    Never reads wp-config.php to create/alter MariaDB credentials, never changes
    DatabaseAccount.owner_id, and never runs wp core install."""
    _ = provision_missing
    reconciled = 0
    try:
        state = load_toolkit_state_safe()
        instances = state.get("instances", {})
        q = db.query(Website)
        if website_id is not None:
            q = q.filter(Website.id == website_id)
        sites = q.all()
        for site in sites:
            inst = instances.get(str(site.id))
            if not isinstance(inst, dict):
                continue
            cand_db = (inst.get("db_name") or "").strip()
            if not cand_db or not DB_IDENT_RE.fullmatch(cand_db) or cand_db.lower() in mariadb.RESERVED_DATABASES:
                continue
            existing = (
                db.query(DatabaseAccount)
                .filter(
                    DatabaseAccount.db_name == cand_db,
                    DatabaseAccount.owner_id == site.owner_id,
                )
                .first()
            )
            if existing is not None and existing.website_id is None:
                existing.website_id = site.id
                reconciled += 1
        if reconciled > 0:
            db.commit()
    except Exception as exc:
        logger.debug("safe_reconcile_wordpress_databases warning: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    return reconciled


def run_wp_cli(
    linux_user: str,
    php_version: str,
    wp_dir: Path,
    args: List[str],
    *,
    check: bool = False,
    input_data: Optional[str] = None,
    sensitive: bool = False,
) -> CommandResult:
    php_flag = [f"--php-version={php_version}"] if php_version else []
    full_args = [*args, f"--path={wp_dir}", "--no-color"]
    try:
        return shell.privileged(
            "wp-site",
            helper_args=[linux_user, *php_flag, *full_args],
            fallback=["wp", *full_args],
            check=check,
            input=input_data,
            sensitive=sensitive,
        )
    except Exception as exc:
        if check:
            raise
        logger.debug("run_wp_cli fallback error: %s", exc)
        return CommandResult(command="wp", returncode=1, stdout="", stderr=str(exc))


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
    safe_mode = "0640" if mode == "0640" else "0644"
    try:
        rel_to_root = str(target.relative_to(site_root)).replace("\\", "/")
    except ValueError:
        rel_to_root = f"public_html/{rel_path.lstrip('/')}"
    if not settings.command_dry_run:
        try:
            res = shell.privileged(
                "site-file-write",
                helper_args=[linux_user, str(site_root), rel_to_root, safe_mode],
                fallback=[
                    "sh",
                    "-c",
                    'mkdir -p "$(dirname "$1")" && cat >"$1" && chmod "$2" "$1"',
                    "sh",
                    str(target),
                    safe_mode,
                ],
                input=content,
                sensitive=True,
                check=False,
            )
            if res.returncode == 0:
                return
        except Exception as exc:
            logger.debug("write_wp_file_text helper warning: %s", exc)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception:
        pass


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
    if not settings.command_dry_run:
        try:
            shell.privileged(
                "rm-site",
                helper_args=[linux_user, str(site_root), str(target)],
                fallback=["rm", "-rf", "--", str(target)],
                check=False,
            )
        except Exception as exc:
            logger.debug("delete_wp_path warning: %s", exc)


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
                helper_args=[linux_user, str(site_root), "stat", rel_to_root],
                fallback=["stat", str(target)],
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


def _run_mariadb_sql(sql: str, batch: bool = True) -> CommandResult:
    extra = ["-N", "-B"] if batch else []
    try:
        return shell.run(
            mariadb._mysql_args(extra),
            check=False,
            input=sql,
            sensitive=True,
        )
    except Exception as exc:
        logger.debug("_run_mariadb_sql warning: %s", exc)
        return CommandResult(command="mysql", returncode=1, stdout="", stderr=str(exc))


def query_wp_db_live_meta(
    db_name: str,
    table_prefix: str = "wp_",
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
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
        "auto_update_plugin_slugs": [],
    }
    try:
        clean_db = (db_name or "").strip()
        clean_prefix = (table_prefix or "wp_").strip()
        if not clean_db or settings.command_dry_run:
            return meta
        if not DB_IDENT_RE.fullmatch(clean_db) or not WP_PREFIX_RE.fullmatch(clean_prefix):
            return meta
        if not is_authorized_wp_database(clean_db, owner_id=owner_id, site_id=site_id, db=db):
            return meta

        options_table = f"{clean_prefix}options"
        users_table = f"{clean_prefix}users"
        sql = (
            f"USE `{clean_db}`; "
            f"SELECT 'OPT', option_name, REPLACE(REPLACE(option_value, '\\r', ' '), '\\n', ' ') "
            f"FROM `{options_table}` "
            f"WHERE option_name IN ('blogname','blogdescription','admin_email','blog_public','stylesheet','template','active_plugins','auto_update_plugins'); "
            f"SELECT 'USR', user_login, user_email FROM `{users_table}` ORDER BY ID ASC LIMIT 5;\n"
        )
        res = _run_mariadb_sql(sql, batch=True)
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
                    slugs: List[str] = []
                    for item in re.findall(r's:\d+:"([^"]+)";', val):
                        slug_part = item.split("/")[0].strip()
                        if slug_part.endswith(".php"):
                            slug_part = slug_part[:-4]
                        if slug_part and slug_part.lower() not in slugs:
                            slugs.append(slug_part.lower())
                    meta["active_plugin_slugs"] = slugs
                elif key == "auto_update_plugins":
                    auto_slugs: List[str] = []
                    for item in re.findall(r's:\d+:"([^"]+)";', val):
                        slug_part = item.split("/")[0].strip()
                        if slug_part.endswith(".php"):
                            slug_part = slug_part[:-4]
                        if slug_part and slug_part.lower() not in auto_slugs:
                            auto_slugs.append(slug_part.lower())
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


def update_wp_db_option_direct(
    db_name: str,
    table_prefix: str,
    option_name: str,
    option_value: str,
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> None:
    try:
        clean_db = (db_name or "").strip()
        clean_prefix = (table_prefix or "wp_").strip()
        if not clean_db or settings.command_dry_run:
            return
        if not DB_IDENT_RE.fullmatch(clean_db) or not WP_PREFIX_RE.fullmatch(clean_prefix):
            return
        if not is_authorized_wp_database(clean_db, owner_id=owner_id, site_id=site_id, db=db):
            return
        safe_opt = option_name.replace("'", "''")
        safe_val = option_value.replace("\\", "\\\\").replace("'", "''")
        sql = (
            f"USE `{clean_db}`; "
            f"UPDATE `{clean_prefix}options` SET option_value = '{safe_val}' WHERE option_name = '{safe_opt}';\n"
        )
        _run_mariadb_sql(sql, batch=False)
    except Exception as exc:
        logger.debug("update_wp_db_option_direct warning: %s", exc)


def rename_wp_admin_user_direct(
    db_name: str,
    table_prefix: str,
    new_login: str,
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> None:
    try:
        clean_db = (db_name or "").strip()
        clean_prefix = (table_prefix or "wp_").strip()
        if not clean_db or not new_login or settings.command_dry_run:
            return
        if not DB_IDENT_RE.fullmatch(clean_db) or not WP_PREFIX_RE.fullmatch(clean_prefix):
            return
        if not wordpress.WP_USER_RE.fullmatch(new_login):
            return
        if not is_authorized_wp_database(clean_db, owner_id=owner_id, site_id=site_id, db=db):
            return
        safe_login = new_login.replace("'", "''")
        sql = (
            f"USE `{clean_db}`; "
            f"UPDATE `{clean_prefix}users` SET user_login = '{safe_login}', user_nicename = '{safe_login.lower()}' "
            f"WHERE user_login = 'admin' OR ID = 1 LIMIT 1;\n"
        )
        _run_mariadb_sql(sql, batch=False)
    except Exception as exc:
        logger.debug("rename_wp_admin_user_direct warning: %s", exc)


def rename_wp_table_prefix_direct(
    db_name: str,
    old_prefix: str,
    new_prefix: str,
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> bool:
    try:
        clean_db = (db_name or "").strip()
        if not clean_db or old_prefix == new_prefix or settings.command_dry_run:
            return False
        if not DB_IDENT_RE.fullmatch(clean_db):
            return False
        if not WP_PREFIX_RE.fullmatch(old_prefix) or not WP_PREFIX_RE.fullmatch(new_prefix):
            return False
        if not is_authorized_wp_database(clean_db, owner_id=owner_id, site_id=site_id, db=db):
            return False
        tables = mariadb.get_database_tables_breakdown(clean_db)
        matching = [
            t["table_name"]
            for t in tables
            if isinstance(t.get("table_name"), str) and t["table_name"].startswith(old_prefix)
        ]
        if not matching:
            return False
        rename_pairs = [f"`{tbl}` TO `{new_prefix}{tbl[len(old_prefix):]}`" for tbl in matching]
        sql = (
            f"USE `{clean_db}`; "
            f"RENAME TABLE {', '.join(rename_pairs)}; "
            f"UPDATE `{new_prefix}options` SET option_name = '{new_prefix}user_roles' "
            f"WHERE option_name = '{old_prefix}user_roles'; "
            f"UPDATE `{new_prefix}usermeta` SET meta_key = CONCAT('{new_prefix}', SUBSTRING(meta_key, {len(old_prefix) + 1})) "
            f"WHERE meta_key LIKE '{old_prefix}%';\n"
        )
        res = _run_mariadb_sql(sql, batch=False)
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
    try:
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
        listed_ok = False
        try:
            if plugins_dir.exists() and plugins_dir.is_dir():
                for item in sorted(plugins_dir.iterdir()):
                    if item.name.startswith(".") or item.name.lower() == "index.php":
                        continue
                    if item.is_dir():
                        entries.append((item.name, True))
                    elif item.is_file() and item.name.endswith(".php"):
                        entries.append((item.stem, False))
                listed_ok = True
        except Exception:
            listed_ok = False

        if not listed_ok and linux_user and site_root and not settings.command_dry_run:
            try:
                try:
                    rel_plugins = str(plugins_dir.relative_to(site_root)).replace("\\", "/")
                except ValueError:
                    rel_plugins = "public_html/wp-content/plugins"
                res_ls = shell.privileged(
                    "terminal-exec",
                    helper_args=[linux_user, str(site_root), "ls", "-1F", rel_plugins],
                    fallback=["ls", "-1F", str(plugins_dir)],
                    check=False,
                )
                if res_ls.returncode == 0 and res_ls.stdout:
                    for raw_entry in res_ls.stdout.splitlines():
                        fname = raw_entry.strip()
                        if not fname or fname.startswith(".") or fname.lower() in ("index.php", "index.php*"):
                            continue
                        if fname.endswith("/"):
                            entries.append((fname[:-1], True))
                        elif fname.endswith(".php") or fname.endswith(".php*"):
                            clean_f = fname.rstrip("*")
                            entries.append((clean_f[:-4], False))
            except Exception:
                pass

        active_slugs = set((db_meta or {}).get("active_plugin_slugs") or [])
        auto_slugs = set((db_meta or {}).get("auto_update_plugin_slugs") or [])
        found: List[Dict[str, Any]] = []
        for slug, is_dir in entries:
            p_name, p_ver = _parse_plugin_header_metadata(
                wp_dir, slug, is_dir, linux_user=linux_user, site_root=site_root
            )
            is_active = slug.lower() in active_slugs
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
    except Exception as exc:
        logger.warning("discover_live_plugins warning: %s", exc)
        return []


def discover_live_themes(
    wp_dir: Path,
    linux_user: Optional[str] = None,
    php_version: str = "8.4",
    site_root: Optional[Path] = None,
    db_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return the exact live themes installed on this WordPress site. Never returns fake/mock themes."""
    try:
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
        listed_ok = False
        try:
            if themes_dir.exists() and themes_dir.is_dir():
                for item in sorted(themes_dir.iterdir()):
                    if item.is_dir() and not item.name.startswith("."):
                        slugs.append(item.name)
                listed_ok = True
        except Exception:
            listed_ok = False

        if not listed_ok and linux_user and site_root and not settings.command_dry_run:
            try:
                try:
                    rel_themes = str(themes_dir.relative_to(site_root)).replace("\\", "/")
                except ValueError:
                    rel_themes = "public_html/wp-content/themes"
                res_ls = shell.privileged(
                    "terminal-exec",
                    helper_args=[linux_user, str(site_root), "ls", "-1F", rel_themes],
                    fallback=["ls", "-1F", str(themes_dir)],
                    check=False,
                )
                if res_ls.returncode == 0 and res_ls.stdout:
                    for raw_entry in res_ls.stdout.splitlines():
                        fname = raw_entry.strip()
                        if fname.endswith("/") and not fname.startswith("."):
                            slugs.append(fname[:-1])
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
    except Exception as exc:
        logger.warning("discover_live_themes warning: %s", exc)
        return []


def create_live_snapshot_archive(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    db_name: str,
    snap_id: str,
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    php_ver: str = "8.4",
    db: Optional[Session] = None,
) -> float:
    snap_id = Path((snap_id or "").strip()).name
    snap_dir = site_root / "wp-snapshots"
    snap_file = snap_dir / snap_id
    real_size_mb = 0.1
    if settings.command_dry_run or not snap_id:
        return real_size_mb
    try:
        shell.privileged(
            "terminal-exec",
            helper_args=[linux_user, str(site_root), "mkdir", "-p", "wp-snapshots"],
            fallback=["mkdir", "-p", str(snap_dir)],
            check=False,
        )
    except Exception:
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    if db_name and is_authorized_wp_database(db_name, owner_id=owner_id, site_id=site_id, db=db):
        rel_sql = f"wp-snapshots/{snap_id}.sql"
        try:
            res = run_wp_cli(
                linux_user,
                php_ver,
                wp_dir,
                ["db", "export", str(site_root / rel_sql), "--add-drop-table"],
                check=False,
                sensitive=True,
            )
            if res.returncode != 0:
                with tempfile.TemporaryDirectory(prefix="spanel-wpsnap-") as tmp_dir:
                    tmp_sql = Path(tmp_dir) / "dump.sql"
                    mariadb.export_database(db_name, str(tmp_sql))
                    if tmp_sql.exists():
                        shell.privileged(
                            "site-file-install",
                            helper_args=[linux_user, str(site_root), rel_sql, str(tmp_sql)],
                            fallback=["cp", "--", str(tmp_sql), str(site_root / rel_sql)],
                            check=False,
                        )
        except Exception as exc:
            logger.warning("Snapshot DB dump warning: %s", exc)

    try:
        rel_wp = str(wp_dir.relative_to(site_root)).replace("\\", "/")
    except ValueError:
        rel_wp = "public_html"
    try:
        shell.privileged(
            "terminal-exec",
            helper_args=[linux_user, str(site_root), "tar", "-czf", f"wp-snapshots/{snap_id}", "-C", rel_wp, "."],
            fallback=["tar", "-czf", str(snap_file), "-C", str(wp_dir), "."],
            check=False,
        )
    except Exception as exc:
        logger.warning("Snapshot tar warning: %s", exc)
    try:
        if snap_file.exists() and not snap_file.is_symlink():
            real_size_mb = max(0.01, round(snap_file.stat().st_size / (1024 * 1024), 2))
    except Exception:
        pass
    return real_size_mb


def restore_live_snapshot(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    db_name: str,
    snapshot_id: Optional[str],
    *,
    owner_id: Optional[int] = None,
    site_id: Optional[int] = None,
    php_ver: str = "8.4",
    db: Optional[Session] = None,
) -> str:
    snap_id = Path((snapshot_id or "").strip()).name
    if not snap_id:
        raise HTTPException(status_code=400, detail="Vui lòng chọn bản Snapshot cần khôi phục.")
    snap_file = site_root / "wp-snapshots" / snap_id
    if not settings.command_dry_run:
        if not wp_path_exists(site_root, f"wp-snapshots/{snap_id}", linux_user, site_root):
            raise HTTPException(status_code=404, detail=f"Không tìm thấy tệp Snapshot {snap_id} trên ổ đĩa.")
        try:
            rel_wp = str(wp_dir.relative_to(site_root)).replace("\\", "/")
        except ValueError:
            rel_wp = "public_html"
        shell.privileged(
            "terminal-exec",
            helper_args=[linux_user, str(site_root), "tar", "-xzf", f"wp-snapshots/{snap_id}", "-C", rel_wp],
            fallback=["tar", "-xzf", str(snap_file), "-C", str(wp_dir)],
            check=False,
        )
        # Ensure any legacy in-docroot SQL dump extracted from older archives is removed immediately
        delete_wp_path(linux_user, site_root, wp_dir, ".spanel-db-snapshot.sql")
        sql_dump = site_root / "wp-snapshots" / f"{snap_id}.sql"
        if (
            db_name
            and is_authorized_wp_database(db_name, owner_id=owner_id, site_id=site_id, db=db)
            and wp_path_exists(site_root, f"wp-snapshots/{snap_id}.sql", linux_user, site_root)
        ):
            try:
                res = run_wp_cli(
                    linux_user,
                    php_ver,
                    wp_dir,
                    ["db", "import", str(sql_dump)],
                    check=False,
                    sensitive=True,
                )
                if res.returncode != 0 and sql_dump.exists() and not sql_dump.is_symlink():
                    mariadb.import_database(db_name, str(sql_dump))
            except Exception as exc:
                logger.warning("Snapshot DB restore warning: %s", exc)
    return snap_id


def delete_live_snapshot(
    linux_user: str,
    site_root: Path,
    inst: Dict[str, Any],
    snapshot_id: Optional[str],
) -> str:
    snap_id = Path((snapshot_id or "").strip()).name
    if snap_id and not settings.command_dry_run:
        for fname in (snap_id, f"{snap_id}.sql"):
            snap_file = site_root / "wp-snapshots" / fname
            try:
                if snap_file.exists() and not snap_file.is_symlink():
                    snap_file.unlink()
            except Exception:
                pass
            try:
                shell.privileged(
                    "rm-site",
                    helper_args=[linux_user, str(site_root), str(snap_file)],
                    fallback=["rm", "-f", "--", str(snap_file)],
                    check=False,
                )
            except Exception:
                pass
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

    target_db_acc = (
        db.query(DatabaseAccount)
        .filter(
            DatabaseAccount.website_id == target_site.id,
            DatabaseAccount.owner_id == target_site.owner_id,
        )
        .first()
    )
    if not target_db_acc:
        from app.api.databases import enforce_user_database_limit
        from app.models.entities import User

        target_owner = db.query(User).filter(User.id == target_site.owner_id).first()
        if target_owner is not None:
            enforce_user_database_limit(db, target_owner, action_label="nhân bản/đồng bộ WordPress")
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
                    clone_db_name, clone_db_user, clone_db_pass, allow_existing_user=False
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
        elif existing_clone_db.owner_id == target_site.owner_id:
            existing_clone_db.website_id = target_site.id
            existing_clone_db.db_user = clone_db_user
            existing_clone_db.db_password = encrypt(clone_db_pass)
            target_db_acc = existing_clone_db
        else:
            raise HTTPException(status_code=409, detail="Xung đột tên cơ sở dữ liệu khi nhân bản.")
        db.commit()
    else:
        clone_db_name = target_db_acc.db_name
        clone_db_user = target_db_acc.db_user
        try:
            clone_db_pass = decrypt(target_db_acc.db_password)
        except Exception:
            clone_db_pass = mariadb.random_password(20)

    if sync_files and not settings.command_dry_run and str(wp_dir) != str(target_wp_dir):
        delete_wp_path(target_linux_user, target_site_root, target_wp_dir, "index.html")
        src_stage_rel = f"wp-snapshots/.clone-src-{secrets.token_hex(6)}.tar.gz"
        src_stage_abs = site_root / src_stage_rel
        rel_stage = ".spanel-wp-clone-stage.tar.gz"
        stage_dest = target_site_root / rel_stage
        try:
            shell.privileged(
                "terminal-exec",
                helper_args=[linux_user, str(site_root), "mkdir", "-p", "wp-snapshots"],
                fallback=["mkdir", "-p", str(site_root / "wp-snapshots")],
                check=False,
            )
            try:
                rel_src_wp = str(wp_dir.relative_to(site_root)).replace("\\", "/")
            except ValueError:
                rel_src_wp = "public_html"
            shell.privileged(
                "terminal-exec",
                helper_args=[linux_user, str(site_root), "tar", "-czf", src_stage_rel, "-C", rel_src_wp, "."],
                fallback=["tar", "-czf", str(src_stage_abs), "-C", str(wp_dir), "."],
                check=False,
            )
            if src_stage_abs.exists() and not src_stage_abs.is_symlink():
                try:
                    shell.privileged(
                        "site-file-install",
                        helper_args=[target_linux_user, str(target_site_root), rel_stage, str(src_stage_abs)],
                        fallback=["cp", "--", str(src_stage_abs), str(stage_dest)],
                        check=False,
                    )
                except Exception:
                    pass
                try:
                    rel_dst_wp = str(target_wp_dir.relative_to(target_site_root)).replace("\\", "/")
                except ValueError:
                    rel_dst_wp = "public_html"
                shell.privileged(
                    "terminal-exec",
                    helper_args=[
                        target_linux_user,
                        str(target_site_root),
                        "tar",
                        "-xzf",
                        rel_stage,
                        "-C",
                        rel_dst_wp,
                    ],
                    fallback=["tar", "-xzf", str(stage_dest), "-C", str(target_wp_dir)],
                    check=False,
                )
                delete_wp_path(target_linux_user, target_site_root, target_site_root, rel_stage)
            wordpress.fix_permissions(str(target_site_root), target_linux_user)
        except Exception as exc:
            logger.warning("Clone/Sync file copy warning: %s", exc)
        finally:
            delete_wp_path(linux_user, site_root, site_root, src_stage_rel)

    if (
        sync_database
        and src_db_name
        and clone_db_name
        and not settings.command_dry_run
        and is_authorized_wp_database(src_db_name, owner_id=site.owner_id, db=db)
        and is_authorized_wp_database(clone_db_name, owner_id=target_site.owner_id, db=db)
    ):
        try:
            with tempfile.TemporaryDirectory(prefix="spanel-wpdb-") as tmp_dir:
                tmp_sql = Path(tmp_dir) / "clone.sql"
                mariadb.export_database(src_db_name, str(tmp_sql))
                mariadb.import_database(clone_db_name, str(tmp_sql))
        except Exception as exc:
            logger.warning("Clone/Sync database copy warning: %s", exc)

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

    update_wp_db_option_direct(
        clone_db_name, src_prefix, "siteurl", dst_url, owner_id=target_site.owner_id, db=db
    )
    update_wp_db_option_direct(
        clone_db_name, src_prefix, "home", dst_url, owner_id=target_site.owner_id, db=db
    )
    if src_url != dst_url:
        run_wp_cli(
            target_linux_user,
            target_php_ver,
            target_wp_dir,
            ["search-replace", "--all-tables", "--precise", "--skip-plugins", "--skip-themes", "--", src_url, dst_url],
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


def apply_wp_password_protection(
    linux_user: str,
    site_root: Path,
    wp_dir: Path,
    inst: Dict[str, Any],
    *,
    enabled: Optional[bool] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """Enforce HTTP Basic Auth via an MU-plugin with PBKDF2-HMAC-SHA256 instead of
    writing plaintext .htpasswd into public_html."""
    if enabled is not None:
        inst["password_protection_enabled"] = bool(enabled)
    if username is not None and username.strip():
        clean_u = re.sub(r"[^a-zA-Z0-9._@-]", "", username.strip())[:64]
        if clean_u:
            inst["password_protection_user"] = clean_u
    if password is not None and password.strip():
        raw_pw = password.strip()
        if len(raw_pw) < 6:
            raise HTTPException(
                status_code=400,
                detail="Mật khẩu bảo vệ trang phải có ít nhất 6 ký tự.",
            )
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", raw_pw.encode("utf-8"), salt.encode("ascii"), 100_000).hex()
        inst["password_protection_hash"] = f"pbkdf2_sha256$100000${salt}${digest}"
        inst.pop("password_protection_password", None)

    # Always remove any legacy plaintext .htpasswd from docroot
    delete_wp_path(linux_user, site_root, wp_dir, ".htpasswd")

    if inst.get("password_protection_enabled"):
        pw_hash = inst.get("password_protection_hash") or ""
        if not pw_hash.startswith("pbkdf2_sha256$"):
            raise HTTPException(
                status_code=400,
                detail="Vui lòng thiết lập mật khẩu bảo vệ trang (tối thiểu 6 ký tự) trước khi bật khóa HTTP Basic Auth.",
            )
        _, iter_str, salt, digest = pw_hash.split("$", 3)
        user_str = re.sub(r"[^a-zA-Z0-9._@-]", "", (inst.get("password_protection_user") or "preview")) or "preview"
        mu_php = (
            "<?php\n"
            "/**\n"
            " * Plugin Name: SPanel HTTP Basic Auth Guard\n"
            " * Description: Enforces HTTP Basic Auth across Nginx + PHP-FPM without exposing .htpasswd.\n"
            " */\n"
            "if (defined('WP_CLI') && WP_CLI) { return; }\n"
            "if (defined('DOING_CRON') && DOING_CRON) { return; }\n"
            "add_action('init', function() {\n"
            f"    $expected_user = '{user_str}';\n"
            f"    $salt = '{salt}';\n"
            f"    $expected_digest = '{digest}';\n"
            "    $u = isset($_SERVER['PHP_AUTH_USER']) ? (string)$_SERVER['PHP_AUTH_USER'] : '';\n"
            "    $p = isset($_SERVER['PHP_AUTH_PW']) ? (string)$_SERVER['PHP_AUTH_PW'] : '';\n"
            f"    $calc = hash_pbkdf2('sha256', $p, $salt, {int(iter_str)}, 64, false);\n"
            "    if (!hash_equals($expected_user, $u) || !hash_equals($expected_digest, $calc)) {\n"
            "        header('WWW-Authenticate: Basic realm=\"Protected WordPress Site\"');\n"
            "        header('HTTP/1.0 401 Unauthorized');\n"
            "        exit('401 Unauthorized - Protected by SPanel');\n"
            "    }\n"
            "}, 1);\n"
        )
        write_wp_file_text(
            linux_user,
            site_root,
            wp_dir,
            "wp-content/mu-plugins/spanel-password-protect.php",
            mu_php,
            "0640",
        )
    else:
        delete_wp_path(
            linux_user,
            site_root,
            wp_dir,
            "wp-content/mu-plugins/spanel-password-protect.php",
        )
