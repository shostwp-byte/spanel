"""Import DirectAdmin backup archives into spanel via the web panel.

Uploads are saved to ``DA_BACKUP_DIR`` (default ``/home/admin/spanel_backups/da``).
An admin can then *scan* a backup to preview users/domains/databases, and
*import* it to create spanel users, websites, nginx vhosts, MariaDB databases,
and optionally SSL certificates.
"""

from __future__ import annotations

import bz2
import datetime as _dt
import gzip
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import DatabaseAccount, User, Website, WebsiteAlias
from app.services import mariadb, nginx, site_users, teardown, waf
from app.services.shell import shell

logger = logging.getLogger("spanel.da_import")

DA_BACKUP_DIR = Path(os.environ.get("spanel_DA_BACKUP_DIR", "/home/admin/spanel_backups/da"))
STAGE_BASE = Path(os.environ.get("DA_IMPORT_STAGE_BASE", "/var/lib/spanel/da-import"))
DEFAULT_PHP_VERSION = os.environ.get("DA_IMPORT_PHP", "8.3")

DEFAULT_STORAGE_MB = int(os.environ.get("DA_IMPORT_STORAGE_MB", "102400"))

DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
DB_RE = re.compile(r"^[a-z0-9_]{1,64}$")

RESERVED_USERS = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "_apt",
    "nobody", "spanel", "spanel-sites", "spanel-sftp", "mysql", "redis", "nginx",
    "admin",
}

ARCHIVE_SUFFIXES = (
    ".tar.zst", ".tzst", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar", ".zip",
)
SQL_SUFFIXES = (".sql", ".sql.gz", ".sql.bz2", ".sql.zst")

# Directories that never hold an application's database config; skipped when
# walking a site tree looking for wp-config.php / .env / configuration.php.
APP_CONFIG_SKIP_DIRS = {
    "wp-content", "wp-includes", "wp-admin", "node_modules", "vendor", "cache",
    "storage", "uploads", "images", "img", "assets", "media", "backup", "backups",
    ".git", ".well-known", "logs", "log", "tmp", "temp",
}
APP_CONFIG_SCAN_DEPTH = 2

# mysql_native_password stores "*" followed by 40 hex characters. DirectAdmin's
# <db>.conf keeps the original hash; reusing it lets the imported site keep its
# existing config untouched.
DA_PASSWORD_HASH_RE = re.compile(r"^\*[0-9A-Fa-f]{40}$")
_SUBDOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

SQL_DATABASE_DIRECTIVE_RE = re.compile(
    r"^\s*(?:/\*![0-9]{5}\s*)?(?:CREATE\s+DATABASE|DROP\s+DATABASE|USE)\b",
    re.IGNORECASE,
)
SQL_DEFINER_RE = re.compile(
    r"DEFINER\s*=\s*(?:`[^`]*`|'[^']*'|\"[^\"]*\"|[^\s*/]+)\s*@\s*(?:`[^`]*`|'[^']*'|\"[^\"]*\"|[^\s*/]+)",
    re.IGNORECASE,
)


# The default lives under /home/admin, which is root-owned and not writable by
# the spanel user the API runs as. Creating it has to go through the helper.
DA_BACKUP_DIR_IS_DEFAULT = "spanel_DA_BACKUP_DIR" not in os.environ


def ensure_backup_dir() -> None:
    """Make sure the DA upload directory exists and the panel can write to it.

    Both the upload endpoint and the listing behind the DirectAdmin import page
    used to call DA_BACKUP_DIR.mkdir() directly. On a server where the directory
    did not already exist that raised PermissionError - /home/admin is
    root-owned - which nothing caught, so the page and the upload both answered
    500 and the uploaded file went nowhere.

    Never raises: a caller that cannot create it should still be able to report
    an empty list rather than fail the whole request.
    """
    if DA_BACKUP_DIR.is_dir() and os.access(DA_BACKUP_DIR, os.W_OK):
        return
    if DA_BACKUP_DIR_IS_DEFAULT:
        try:
            shell.privileged(
                "da-backup-dir-ensure",
                check=False,
                fallback=["bash", "-lc", f"install -d -m 0770 {DA_BACKUP_DIR}"],
            )
        except Exception:  # pragma: no cover - helper failure
            logger.warning("could not create %s through the helper", DA_BACKUP_DIR)
    try:
        DA_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("DA backup directory %s is unusable: %s", DA_BACKUP_DIR, exc)


def start_detached_import(archive_path: str, force: bool) -> str:
    """Hand the import to systemd and return the unit's invocation id.

    The import then outlives spanel-api. Before this it ran in a thread inside
    the API process, so an update or a crash killed it mid-archive and left a
    half-created account behind with no record of what had happened.
    """
    result = shell.privileged(
        "da-import-start",
        helper_args=[str(archive_path), "force" if force else "noforce"],
        check=False,
        fallback=["bash", "-lc", "echo 'da-import-start needs the spanel helper'; exit 1"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not start the import").strip())
    return (result.stdout or "").strip()


def detached_import_status() -> dict:
    """What systemd says about the import unit, plus its recent output.

    This is deliberately not backed by anything in the API's memory: the whole
    point is that it still answers after spanel-api has restarted.
    """
    result = shell.privileged(
        "da-import-status",
        check=False,
        fallback=["bash", "-lc", "echo active=unknown"],
    )
    info = {"active": "unknown", "result": "", "exit": "", "invocation": "", "log": []}
    body, _, log = (result.stdout or "").partition("---log---")
    for line in body.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in info:
            info[key.strip()] = value.strip()
    info["log"] = [line for line in log.splitlines() if line.strip()]

    if info["active"] == "active":
        status = "running"
    elif info["active"] in {"inactive", "failed"} and info["invocation"]:
        ok = info["result"] == "success" and info["exit"] in {"0", ""}
        status = "completed" if ok else "failed"
    else:
        status = "unknown"
    info["status"] = status
    return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    logger.info(msg)
    print(msg, flush=True)


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def _is_archive(path: Path) -> bool:
    name = path.name.lower()
    return path.is_file() and any(name.endswith(s) for s in ARCHIVE_SUFFIXES)


def safe_upload_name(filename: str) -> str:
    """Return a filename that can only ever land inside ``DA_BACKUP_DIR``.

    The uploaded name comes straight from the browser, so strip every
    directory component before it is joined onto the backup directory.
    """
    name = Path((filename or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or name.startswith("."):
        raise ValueError("Invalid backup filename")
    if not any(name.lower().endswith(suffix) for suffix in ARCHIVE_SUFFIXES):
        raise ValueError(
            "Unsupported archive type. Expected one of: " + ", ".join(ARCHIVE_SUFFIXES)
        )
    return name


ALLOWED_MIGRATION_DIRS = (
    Path("/var/backups/spanel/migrations"),
    Path(tempfile.gettempdir()) / "spanel-migrations",
)


def resolve_backup_path(archive_path: str) -> Path:
    """Resolve a caller-supplied archive path inside ``DA_BACKUP_DIR`` or migration staging dirs.

    Scan, import and delete all take a path from the request body; confining it
    to the upload/migration directories keeps the endpoints from reading or deleting
    arbitrary files on the host.
    """
    raw = (archive_path or "").strip()
    if not raw:
        raise ValueError("archive_path is required")
    root = DA_BACKUP_DIR.resolve()
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    allowed_roots = [root] + [d.resolve() for d in ALLOWED_MIGRATION_DIRS]
    for allowed in allowed_roots:
        try:
            resolved.relative_to(allowed)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"Backup must be inside {root}")


def _strip_archive_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def _archive_username(name: str) -> str:
    base = _strip_archive_suffix(name)
    pieces = [p for p in re.split(r"[._-]+", base) if p]
    if len(pieces) >= 3 and pieces[0] in {"user", "reseller"}:
        return pieces[-1]
    for piece in reversed(pieces):
        if piece.lower() not in {"backup", "user", "admin", "reseller"}:
            return piece
    return base


def _normalize_username(raw: str, archive_name: str = "") -> str:
    value = (raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "_", value).strip("_-")
    if not value or not re.match(r"^[a-z_]", value):
        value = f"da_{value}" if value else "da_user"
    value = value[:32]
    if len(value) < 3:
        value = f"{value}_da"[:32]
    if value in RESERVED_USERS or not USER_RE.fullmatch(value):
        digest = hashlib.sha1((raw + archive_name).encode("utf-8")).hexdigest()[:8]
        stem = re.sub(r"[^a-z0-9_]+", "_", value).strip("_") or "user"
        value = f"da_{stem[:18]}_{digest}"[:32]
    return value


def _normalize_domain(value: str) -> Optional[str]:
    domain = (value or "").strip().lower().rstrip(".")
    return domain if DOMAIN_RE.fullmatch(domain) else None


def _normalize_db_identifier(raw: str, fallback: str, existing: set[str]) -> str:
    value = (raw or fallback).strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
    if not value:
        value = fallback
    if not re.match(r"^[a-z0-9_]+$", value):
        value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = value[:64]
    if value not in existing and DB_RE.fullmatch(value):
        return value
    digest = hashlib.sha1((raw + fallback).encode("utf-8")).hexdigest()[:6]
    stem = (value[:55].strip("_") or fallback[:55].strip("_") or "db")
    candidate = f"{stem}_{digest}"[:64]
    counter = 2
    while candidate in existing or not DB_RE.fullmatch(candidate):
        suffix = f"_{counter}"
        candidate = f"{stem[:64 - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def _read_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists() or not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        values[key.strip().lower()] = value.strip().strip("'\"")
    return values


def _server_ip_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        result = subprocess.run(
            ["ip", "-j", "address", "show", "scope", "global"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for interface in json.loads(result.stdout or "[]"):
                for address in interface.get("addr_info", []):
                    local = address.get("local")
                    if local:
                        try:
                            addresses.add(str(ipaddress.ip_address(local)))
                        except ValueError:
                            pass
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired, ValueError):
        pass
    return addresses


def _domain_ip_addresses(domain: str) -> set[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM):
            value = item[4][0].split("%", 1)[0]
            try:
                addresses.add(str(ipaddress.ip_address(value)))
            except ValueError:
                pass
    except socket.gaierror:
        pass
    return addresses


# ---------------------------------------------------------------------------
# Backup discovery
# ---------------------------------------------------------------------------

def _find_backup_root(extracted: Path) -> Path:
    if (extracted / "backup").exists() or (extracted / "domains").exists() or (extracted / "mysql").exists():
        return extracted
    candidates = []
    for path in extracted.rglob("backup"):
        if not path.is_dir():
            continue
        parent = path.parent
        score = 1
        if (parent / "domains").exists():
            score += 3
        if (parent / "mysql").exists():
            score += 2
        if (path / "user.conf").exists():
            score += 3
        candidates.append((score, parent))
    if candidates:
        return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
    return extracted


def _discover_username(root: Path, archive_name: str) -> tuple[str, str]:
    user_conf = _read_key_values(root / "backup" / "user.conf")
    raw = (
        user_conf.get("username")
        or user_conf.get("user")
        # DirectAdmin's own key for the account name (its "account" key is the
        # on/off flag, not a name).
        or user_conf.get("name")
        or _archive_username(archive_name)
    )
    email = user_conf.get("email") or user_conf.get("emailaddress") or ""
    return _normalize_username(raw, archive_name), email


def _discover_domains(root: Path) -> list[str]:
    found: list[str] = []
    domains_list = root / "backup" / "domains.list"
    if domains_list.exists():
        for line in domains_list.read_text(encoding="utf-8", errors="ignore").splitlines():
            domain = _normalize_domain(line)
            if domain and domain not in found:
                found.append(domain)
    backup_dir = root / "backup"
    if backup_dir.exists():
        for item in backup_dir.iterdir():
            if item.is_file() and item.name.endswith(".conf"):
                domain = _normalize_domain(item.name[:-5])
                if domain and domain not in found:
                    found.append(domain)
    domains_dir = root / "domains"
    if domains_dir.exists():
        for item in domains_dir.iterdir():
            if item.is_dir():
                domain = _normalize_domain(item.name)
                if domain and domain not in found:
                    found.append(domain)
    return found


def _discover_domain_pointers(root: Path, domain: str) -> list[tuple[str, str]]:
    """Return (domain, mode) pairs for a DirectAdmin domain's pointers.

    DirectAdmin writes pointers either as ``<domain>.pointers`` under
    ``backup/`` or as ``domain.pointers`` inside the domain directory. Lines
    are ``pointer.tld=alias`` / ``pointer.tld=redirect``; very old backups list
    one bare domain per line, which DirectAdmin treats as an alias.
    """
    candidates = [
        root / "backup" / f"{domain}.pointers",
        root / "domains" / domain / "domain.pointers",
        root / "domains" / domain / "pointers",
    ]
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name, _, mode = line.partition("=")
            pointer = _normalize_domain(name)
            if not pointer or pointer == domain or pointer in seen:
                continue
            seen.add(pointer)
            found.append((pointer, "redirect" if mode.strip().lower() == "redirect" else "alias"))
    return found


def _sync_website_aliases(db, website, pointers: list[tuple[str, str]], item_summary: dict) -> list[tuple[str, str]]:
    """Persist discovered pointers as WebsiteAlias rows.

    Alias domains are globally unique, so one already owned by another website
    is reported as a warning instead of failing the whole import.
    """
    applied: list[tuple[str, str]] = []
    for pointer, mode in pointers:
        existing = db.query(WebsiteAlias).filter(WebsiteAlias.domain == pointer).first()
        if existing and existing.website_id != website.id:
            item_summary["warnings"].append(
                f"Alias {pointer} skipped: already used by another website"
            )
            continue
        if existing:
            existing.mode = mode
        else:
            db.add(WebsiteAlias(website_id=website.id, domain=pointer, mode=mode))
        applied.append((pointer, mode))
    if applied:
        db.commit()
    return applied


def _extract_nested_domain_archives(root: Path) -> None:
    """Extract per-domain archives some DirectAdmin backups ship.

    Newer / compressed DA backups store a domain's files as
    ``backup/example.com.tar.gz`` (or ``.tar.zst``) instead of an extracted
    ``domains/example.com/`` tree. Unpack each one into ``domains/`` so the rest
    of the importer sees the layout it expects.
    """
    backup_dir = root / "backup"
    if not backup_dir.is_dir():
        return
    domains_dir = root / "domains"
    for item in sorted(backup_dir.iterdir()):
        if not item.is_file() or not _is_archive(item):
            continue
        domain = _normalize_domain(_strip_archive_suffix(item.name))
        if not domain:
            continue
        target_dir = domains_dir / domain
        if target_dir.exists():
            continue
        _log(f"  Extracting nested domain archive: {item.name}")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            _safe_extract_tar(item, target_dir)
            # DA sometimes wraps the whole domain in one subdirectory
            # (<domain>/public_html/...). Unwrap that, but leave an archive
            # whose own root is public_html/ alone.
            children = list(target_dir.iterdir())
            if (
                len(children) == 1
                and children[0].is_dir()
                and children[0].name not in {"public_html", "private_html"}
                and ((children[0] / "public_html").exists() or (children[0] / "private_html").exists())
            ):
                inner = children[0]
                for child in sorted(inner.iterdir(), reverse=True):
                    shutil.move(str(child), str(target_dir / child.name))
                inner.rmdir()
        except Exception as exc:  # noqa: BLE001 - one bad archive must not stop the import
            _log(f"  WARNING: could not extract {item.name}: {exc}")
            shutil.rmtree(target_dir, ignore_errors=True)


def _source_for_domain(root: Path, domain: str) -> Optional[Path]:
    candidates = [
        root / "domains" / domain / "public_html",
        root / "domains" / domain / "private_html",
        root / "domains" / domain,
        root / domain / "public_html",
        root / domain,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            if candidate.name == domain and (candidate / "public_html").exists():
                return candidate / "public_html"
            return candidate
    for path in root.rglob("public_html"):
        if path.is_dir() and domain in {part.lower() for part in path.parts}:
            return path
    return None


def _discover_subdomains(root: Path, parent_domain: str) -> list[str]:
    """DirectAdmin subdomain labels declared for a parent domain.

    DA records them one label per line, in one of a few places depending on the
    version. Each label's document root lives at
    ``domains/<domain>/public_html/<label>``.
    """
    labels: list[str] = []
    candidates = [
        root / "backup" / parent_domain / "subdomain.list",
        root / "backup" / f"{parent_domain}.subdomains",
        root / "backup" / "domains" / f"{parent_domain}.subdomains",
        root / "backup" / "domains" / parent_domain / "subdomain.list",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            label = raw.strip().lower()
            if not label or label.startswith("#"):
                continue
            label = label.split(":", 1)[0].split("=", 1)[0].strip()
            if _SUBDOMAIN_LABEL_RE.fullmatch(label) and label not in labels:
                labels.append(label)
    return labels


def _relocate_da_subdomain_sources(root: Path, parent_domains: list[str]) -> "dict[str, Optional[Path]]":
    """Move DirectAdmin subdomain docroots out of their parent's public_html.

    DA nests ``sub.example.com`` inside ``example.com``'s public_html
    (``.../public_html/sub``). spanel has no parent/child website concept, so
    each DA subdomain becomes its own website with its own root. Moving each
    docroot into a staging sibling keeps the parent import from copying the same
    files again and gives the subdomain import a clean source. The move is a
    rename inside the spanel-owned staging tree, so it is cheap and touches no
    live files.

    Returns subdomain FQDN -> staged source dir (or ``None`` when the label is
    declared but ships no files).
    """
    holding = root / "_spanel_subdomains"
    sources: "dict[str, Optional[Path]]" = {}
    for parent in parent_domains:
        labels = _discover_subdomains(root, parent)
        if not labels:
            continue
        parent_public = _source_for_domain(root, parent)
        _log(f"  {parent}: {len(labels)} DirectAdmin subdomain(s) declared")
        for label in labels:
            fqdn = _normalize_domain(f"{label}.{parent}")
            if not fqdn or fqdn in sources:
                continue
            src = (parent_public / label) if parent_public else None
            if src and src.is_dir() and not src.is_symlink():
                dest = holding / fqdn
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.move(str(src), str(dest))
                sources[fqdn] = dest
                _log(f"    staged subdomain {fqdn} from {parent}/public_html/{label}")
            else:
                sources[fqdn] = None
                _log(f"    subdomain {fqdn}: no files under public_html/{label}, importing empty")
    return sources


def _has_php_files(path: Path) -> bool:
    return any(item.is_file() and item.suffix.lower() in {".php", ".phtml"} for item in path.rglob("*"))


def _candidate_config_dirs(path: Path) -> list[Path]:
    """Directories that may hold the app's database config, closest first.

    Looking only at the top of the document root misses two very common
    layouts: WordPress allows wp-config.php one level above the document root,
    and plenty of accounts keep the site itself in a subfolder. Both hold the
    credentials the imported database has to match.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    def add(candidate: Path) -> None:
        try:
            if candidate.is_dir() and candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
        except OSError:
            return

    add(path)
    add(path.parent)
    level = [path]
    for _depth in range(APP_CONFIG_SCAN_DEPTH):
        children: list[Path] = []
        for parent in level:
            try:
                entries = sorted(parent.iterdir())
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name.lower() in APP_CONFIG_SKIP_DIRS:
                    continue
                add(entry)
                children.append(entry)
        level = children
    return found


def _detect_app_type(source: Optional[Path]) -> str:
    if not source:
        return "php"
    for directory in _candidate_config_dirs(source):
        if (directory / "wp-config.php").exists() or (directory / "wp-config-sample.php").exists():
            return "wordpress"
    return "php" if _has_php_files(source) else "static"


def _sql_base_name(path: Path) -> str:
    name = path.name
    for suffix in SQL_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _discover_sql_files(root: Path) -> dict[str, Path]:
    preferred = [root / "mysql", root / "backup"]
    files: list[Path] = []
    for directory in preferred:
        if directory.exists():
            files.extend(
                path for path in directory.rglob("*")
                if path.is_file() and any(path.name.lower().endswith(s) for s in SQL_SUFFIXES)
            )
    if not files:
        for path in root.rglob("*"):
            if path.is_file() and any(path.name.lower().endswith(s) for s in SQL_SUFFIXES):
                if "domains" not in {part.lower() for part in path.parts}:
                    files.append(path)
    result: dict[str, Path] = {}
    for path in files:
        result.setdefault(_sql_base_name(path).lower(), path)
    return result


def _temporary_sql_file(sql_file: Path) -> Path:
    lower = sql_file.name.lower()
    tmp = Path(tempfile.mkstemp(prefix="spanel-da-", suffix=".sql")[1])
    if lower.endswith(".sql"):
        opener = open
    elif lower.endswith(".gz"):
        opener = gzip.open
    elif lower.endswith(".bz2"):
        opener = bz2.open
    elif lower.endswith(".zst"):
        zstd = shutil.which("zstdcat") or shutil.which("zstd")
        if not zstd:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("zstd is required to import .sql.zst files. Install package: zstd")
        args = [zstd, str(sql_file)] if Path(zstd).name == "zstdcat" else [zstd, "-dc", str(sql_file)]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        try:
            with tmp.open("w", encoding="utf-8") as target:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if SQL_DATABASE_DIRECTIVE_RE.match(line):
                        continue
                    target.write(SQL_DEFINER_RE.sub("", line))
            _, stderr = proc.communicate(timeout=30)
            if proc.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(stderr.strip() or "zstd SQL decompression failed")
            return tmp
        finally:
            if proc.poll() is None:
                proc.kill()
    else:
        raise RuntimeError(f"Unsupported SQL compression: {sql_file}")
    with opener(sql_file, "rt", encoding="utf-8", errors="replace") as source, tmp.open("w", encoding="utf-8") as target:
        for line in source:
            if SQL_DATABASE_DIRECTIVE_RE.match(line):
                continue
            target.write(SQL_DEFINER_RE.sub("", line))
    return tmp


# ---------------------------------------------------------------------------
# App config parsing
# ---------------------------------------------------------------------------

def _parse_wp_config(path: Path) -> dict[str, str]:
    config = path / "wp-config.php"
    if not config.exists():
        return {}
    text = config.read_text(encoding="utf-8", errors="ignore")
    found: dict[str, str] = {}
    for key in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST"):
        match = re.search(r"define\s*\(\s*['\"]" + key + r"['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)", text)
        if match:
            found[key] = match.group(1)
    return found


def _parse_dotenv_config(path: Path) -> dict[str, str]:
    env_file = path / ".env"
    if not env_file.exists():
        return {}
    values = _read_key_values(env_file)
    result: dict[str, str] = {}
    if values.get("db_database"):
        result["DB_NAME"] = values["db_database"]
    if values.get("db_username"):
        result["DB_USER"] = values["db_username"]
    if values.get("db_password"):
        result["DB_PASSWORD"] = values["db_password"]
    if values.get("db_host"):
        result["DB_HOST"] = values["db_host"]
    return result


def _parse_php_variable_config(path: Path) -> dict[str, str]:
    config = path / "configuration.php"
    if not config.exists():
        return {}
    text = config.read_text(encoding="utf-8", errors="ignore")
    mappings = {
        "DB_NAME": ("db", "db_name", "database", "db_database"),
        "DB_USER": ("user", "dbuser", "db_user", "db_username", "username"),
        "DB_PASSWORD": ("password", "dbpass", "db_password"),
        "DB_HOST": ("host", "db_host"),
    }
    result: dict[str, str] = {}
    for key, names in mappings.items():
        for name in names:
            match = re.search(r"(?:public\s+)?\$" + re.escape(name) + r"\s*=\s*['\"]([^'\"]*)['\"]\s*;", text)
            if match:
                result[key] = match.group(1)
                break
    return result


def _locate_app_db_config(path: Path) -> tuple[dict[str, str], Optional[Path]]:
    """(parsed DB settings, the directory they were found in), closest first."""
    for directory in _candidate_config_dirs(path):
        for parser in (_parse_wp_config, _parse_dotenv_config, _parse_php_variable_config):
            values = parser(directory)
            if values.get("DB_NAME"):
                return values, directory
    return {}, None


def _parse_app_db_config(path: Path) -> dict[str, str]:
    values, _directory = _locate_app_db_config(path)
    return values


def _replace_define(text: str, key: str, value: str) -> str:
    pattern = re.compile(r"(define\s*\(\s*['\"]" + key + r"['\"]\s*,\s*)['\"][^'\"]*['\"](\s*\)\s*;)")
    if pattern.search(text):
        return pattern.sub(lambda m: f"{m.group(1)}'{value}'{m.group(2)}", text, count=1)
    return text + f"\ndefine('{key}', '{value}');\n"


def _update_app_db_config(public: Path, db_name: str, db_user: str, db_password: str) -> None:
    # A site's config is not always at the top of the document root - WordPress
    # allows wp-config.php one level up, and subfolder installs keep everything
    # deeper. Rewrite every recognised config file across the same directories
    # the credentials were discovered in.
    for directory in _candidate_config_dirs(public):
        _update_config_dir(directory, db_name, db_user, db_password)


def _update_config_dir(public: Path, db_name: str, db_user: str, db_password: str) -> None:
    wp = public / "wp-config.php"
    if wp.exists():
        text = wp.read_text(encoding="utf-8", errors="ignore")
        text = _replace_define(text, "DB_NAME", db_name)
        text = _replace_define(text, "DB_USER", db_user)
        text = _replace_define(text, "DB_PASSWORD", db_password)
        text = _replace_define(text, "DB_HOST", "localhost")
        wp.write_text(text, encoding="utf-8")

    env_file = public / ".env"
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        replacements = {
            "DB_DATABASE": db_name,
            "DB_USERNAME": db_user,
            "DB_PASSWORD": db_password,
            "DB_HOST": "127.0.0.1",
        }
        out = []
        seen = set()
        for line in lines:
            stripped = line.strip()
            matched = False
            for env_key, env_val in replacements.items():
                if stripped.lower().startswith(f"{env_key.lower()}=") or stripped.lower().startswith(f"{env_key.lower()} ="):
                    out.append(f"{env_key}={env_val}")
                    seen.add(env_key)
                    matched = True
                    break
            if not matched:
                out.append(line)
        for env_key, env_val in replacements.items():
            if env_key not in seen:
                out.append(f"{env_key}={env_val}")
        env_file.write_text("\n".join(out) + "\n", encoding="utf-8")

    config_php = public / "configuration.php"
    if config_php.exists():
        text = config_php.read_text(encoding="utf-8", errors="ignore")
        for key, value in [
            ("db", db_name), ("db_name", db_name),
            ("user", db_user), ("dbuser", db_user), ("db_username", db_user),
            ("password", db_password), ("dbpass", db_password), ("db_password", db_password),
            ("host", "localhost"), ("db_host", "localhost"),
        ]:
            text = re.sub(
                r"(public\s+\$?" + re.escape(key) + r"\s*=\s*)['\"][^'\"]*['\"](\s*;)",
                rf"\1'{value}'\2", text, count=1,
            )
        config_php.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def _copy_site_files(source: Optional[Path], public: Path) -> None:
    public.mkdir(parents=True, exist_ok=True)
    if not source or not source.exists():
        return
    for root_dir, dirs, files in os.walk(source):
        root_path = Path(root_dir)
        rel_root = root_path.relative_to(source)
        target_root = public / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        dirs[:] = [name for name in dirs if not (root_path / name).is_symlink()]
        for name in files:
            src = root_path / name
            if src.is_symlink() or not src.is_file():
                continue
            dst = target_root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def _checked_members(tar: tarfile.TarFile, destination: Path, archive_name: str):
    """Yield archive members after rejecting anything that escapes *destination*.

    This must stay a generator consumed by a single ``extractall`` call: a
    ``.tar.zst`` archive is decompressed through a pipe, so the tar stream is
    not seekable and can only be walked once. Validating in a separate pass
    first would leave nothing for the extraction to read.
    """
    base_resolved = destination.resolve()
    for member in tar:
        member_path = (destination / member.name).resolve()
        try:
            member_path.relative_to(base_resolved)
        except ValueError:
            raise RuntimeError(f"Unsafe path in {archive_name}: {member.name}")
        if member.issym() or member.islnk():
            # DirectAdmin backups contain no links we need; skipping keeps the
            # extracted tree free of anything pointing outside the stage dir.
            continue
        if not (member.isfile() or member.isdir()):
            continue
        yield member


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    proc = None
    if archive.name.lower().endswith((".tar.zst", ".tzst")):
        zstd = shutil.which("zstdcat") or shutil.which("zstd")
        if not zstd:
            raise RuntimeError("zstd is required to extract .tar.zst backups. Install package: zstd")
        args = [zstd, str(archive)] if Path(zstd).name == "zstdcat" else [zstd, "-dc", str(archive)]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        tar = tarfile.open(fileobj=proc.stdout, mode="r|")
    else:
        tar = tarfile.open(archive, "r:*")
    try:
        tar.extractall(
            path=str(destination),
            members=_checked_members(tar, destination, archive.name),
            filter="data",
        )
    finally:
        tar.close()
        if proc is not None:
            stderr = b""
            if proc.stderr is not None:
                stderr = proc.stderr.read()
                proc.stderr.close()
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait()
            if proc.returncode not in (0, None):
                raise RuntimeError(stderr.decode("utf-8", errors="replace").strip() or "zstd extraction failed")


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def _existing_identifiers(db) -> tuple[set[str], set[str]]:
    rows = db.query(DatabaseAccount).all()
    return {row.db_name for row in rows}, {row.db_user for row in rows}


def _da_db_conf_candidates(sql_file: Path, root: Path, base: str) -> list[Path]:
    """Every file that could be the DirectAdmin conf for this dump, closest first.

    DA has moved these between versions - beside the dump, under ``backup/``,
    under ``mysql/`` - and the compression suffix on the dump is not always part
    of the conf name, so the stem is matched case-insensitively.
    """
    candidates = [sql_file.with_name(f"{base}.conf"), root / "backup" / f"{base}.conf"]
    seen: set[Path] = set(candidates)
    for directory in (sql_file.parent, root / "backup", root / "mysql"):
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.rglob("*.conf"))
        except OSError:
            continue
        for entry in entries:
            if entry in seen:
                continue
            if entry.stem.lower() == base.lower():
                seen.add(entry)
                candidates.append(entry)
    return candidates


def _decode_da_conf_password(text: str, db_name: str) -> dict[str, str]:
    """Pull the MySQL password out of a DirectAdmin database .conf file.

    Two shapes seen in the wild:
      - flat ``passwd=...`` / ``password=...`` one per line;
      - the whole grant packed into one line as
        ``<db>=accesshosts=localhost&passwd=%2A<hash>&plugin=mysql_native_password``
        (an '&'-delimited, URL-encoded query string).
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {"passwd", "password", "pass", "db_password", "dbpass"}:
            decoded = urllib.parse.unquote(value).strip("'\"")
            if decoded:
                return {"password": "", "password_hash": decoded} if DA_PASSWORD_HASH_RE.match(decoded) \
                    else {"password": decoded, "password_hash": ""}
        if "&" in value and ("passwd=" in value.lower() or "password=" in value.lower()):
            fields = urllib.parse.parse_qs(value, keep_blank_values=True)
            for name in ("passwd", "password", "pass"):
                got = (fields.get(name) or [""])[0].strip()
                if not got:
                    continue
                if DA_PASSWORD_HASH_RE.match(got):
                    return {"password": "", "password_hash": got}
                return {"password": got, "password_hash": ""}
    return {"password": "", "password_hash": ""}


def _da_db_credentials(sql_file: Path, root: Path) -> dict[str, str]:
    """Recover the original MySQL password (or its hash) for a DA dump."""
    base = _sql_base_name(sql_file)
    for candidate in _da_db_conf_candidates(sql_file, root, base):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found = _decode_da_conf_password(text, base)
        if found["password"] or found["password_hash"]:
            return found
    return {"password": "", "password_hash": ""}


def _import_db_password(app_config: dict[str, str], da_credentials: dict[str, str]) -> tuple[str, str, bool]:
    """Choose the password for an imported database user.

    Returns ``(password, password_hash, reused)``. Reusing what the site already
    carries is what keeps it online: the panel rewrites the configs it knows,
    but a custom include or a second copy outside the document root would still
    point at the old secret. Prefer the app's own config value, then the hash
    from DirectAdmin's <db>.conf, and only generate a fresh password when the
    backup carries neither.
    """
    from_config = (app_config or {}).get("DB_PASSWORD") or ""
    if from_config:
        return from_config, "", True
    hashed = (da_credentials or {}).get("password_hash") or ""
    if hashed:
        return "", hashed, True
    clear = (da_credentials or {}).get("password") or ""
    if clear:
        return clear, "", True
    return secrets.token_urlsafe(18), "", False


def _matched_sql_for_config(
    app_config: dict[str, str],
    sql_files: dict[str, Path],
    single_site: bool,
) -> tuple[str, Optional[Path]]:
    if app_config.get("DB_NAME"):
        key = app_config["DB_NAME"].lower()
        if key in sql_files:
            return key, sql_files[key]
    if single_site and len(sql_files) == 1:
        return next(iter(sql_files.items()))
    return "", None


def _unique_email(db, username: str, preferred: str, domains: list[str]) -> str:
    domain = domains[0] if domains else "import.local"
    base = preferred if preferred and "@" in preferred else f"{username}@{domain}"
    local, _, host = base.partition("@")
    local = re.sub(r"[^A-Za-z0-9_.+-]+", ".", local).strip(".") or username
    host = host or domain
    candidate = f"{local}@{host}"
    counter = 2
    while db.query(User).filter(User.email == candidate).first():
        candidate = f"{local}+{counter}@{host}"
        counter += 1
    return candidate


def _delete_database_record(db, db_item) -> None:
    # One implementation, shared with api/users.py delete_user and
    # provisioning.terminate_account. This path had it right all along; the
    # other two swept by website_id and missed every standalone database.
    teardown.drop_database_record(db, db_item)


def _delete_website_record(db, website) -> None:
    for db_item in db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).all():
        _delete_database_record(db, db_item)
    try:
        nginx.delete_wordpress_vhost(website.domain)
    finally:
        try:
            from app.services import wordpress
            wordpress.delete_wordpress(website.root_path)
        except Exception:
            pass
        db.delete(website)
        db.flush()


def _delete_existing_domain(db, domain: str) -> bool:
    website = db.query(Website).filter(Website.domain == domain).first()
    if not website:
        return False
    _log(f"  deleting existing domain before import: {domain}")
    _delete_website_record(db, website)
    db.commit()
    return True


def _delete_existing_user(db, username: str) -> bool:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return False
    _log(f"  deleting existing user before import: {username}")
    for website in db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.asc()).all():
        _delete_website_record(db, website)
    for db_item in db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).all():
        _delete_database_record(db, db_item)
    site_users.delete_panel_user(username)
    db.delete(user)
    db.commit()
    return True


def _ensure_panel_user_record(db, username: str, email: str, domains: list[str], credentials: list[str]):
    password = secrets.token_urlsafe(18)
    # Two separate secrets from the start. The panel password never reaches the
    # Linux account, because sshd offers password authentication to it on port
    # 22 and a panel password is root through the sudo helper.
    sftp_password = site_users.generate_login_password()
    site_users.ensure_panel_user(username, sftp_password)
    user = User(
        username=username,
        email=_unique_email(db, username, email, domains),
        hashed_password=hash_password(password),
        role="end_user",
        website_limit=max(5, len(domains) + 5),
        storage_limit_mb=DEFAULT_STORAGE_MB,
        is_active=True,
        sftp_password_set_at=_dt.datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    credentials.append(f"panel_user username={username} password={password}")
    # The operator running the import is the only one who can hand this on.
    credentials.append(f"sftp_user username={username} password={sftp_password}")
    return user, True


def _create_panel_database(
    db, owner, website, old_db: str, old_user: Optional[str],
    sql_file: Optional[Path], credentials: list[str],
    *, app_config: Optional[dict[str, str]] = None, da_credentials: Optional[dict[str, str]] = None,
) -> tuple[str, str, str, bool]:
    used_names, used_users = _existing_identifiers(db)
    fallback = mariadb.safe_db_identifier(
        website.domain if website else owner.username, "da"
    )
    db_name = _normalize_db_identifier(old_db, fallback, used_names)
    db_user = _normalize_db_identifier(old_user or db_name, f"u_{db_name}"[:64], used_users)

    db_password, db_password_hash, reused = _import_db_password(app_config or {}, da_credentials or {})
    # A reused secret only helps if the site's config still points at this
    # database and user. If normalisation renamed either, fall back to a fresh
    # password and let the caller rewrite the config.
    renamed = (
        (old_db and db_name != (old_db or "").strip().lower())
        or (old_user and db_user != (old_user or "").strip().lower())
    )
    if renamed and reused:
        db_password, db_password_hash, reused = mariadb.random_password(), "", False

    # An import recreates the account the DirectAdmin backup already owned.
    import_pw = db_password or mariadb.random_password()
    use_temp_pw = bool(sql_file is not None and not db_password and db_password_hash)
    mariadb.create_database_credentials(
        db_name,
        db_user,
        import_pw,
        password_hash=None if use_temp_pw else (db_password_hash or None),
        allow_existing_user=True,
    )
    temp_sql: Optional[Path] = None
    try:
        if sql_file is not None:
            temp_sql = _temporary_sql_file(sql_file)
            mariadb.import_database_as_tenant(
                db_name,
                str(temp_sql),
                db_user=db_user,
                db_password=import_pw,
            )
            if use_temp_pw:
                mariadb.create_database_credentials(
                    db_name,
                    db_user,
                    "",
                    password_hash=db_password_hash,
                    allow_existing_user=True,
                )
    except Exception:
        mariadb.drop_database(db_name, db_user)
        raise
    finally:
        if temp_sql is not None:
            temp_sql.unlink(missing_ok=True)

    from app.core.secrets import encrypt
    # The panel cannot recover the plaintext behind a reused mysql_native hash,
    # so it stores what it can: an empty secret the operator resets if needed.
    item = DatabaseAccount(
        owner_id=owner.id,
        website_id=website.id if website else None,
        db_name=db_name,
        db_user=db_user,
        db_password=encrypt(db_password) if db_password else "",
    )
    db.add(item)
    db.commit()
    target = website.domain if website else owner.username
    if db_password:
        credentials.append(
            f"database target={target} db_name={db_name} db_user={db_user} db_password={db_password}"
        )
    else:
        credentials.append(
            f"database target={target} db_name={db_name} db_user={db_user} "
            f"db_password=<kept DirectAdmin password hash; unchanged>"
        )
    return db_name, db_user, db_password, reused


def _import_da_certificate(db, website, root: Path, item_summary: dict) -> bool:
    """Reuse the certificate DirectAdmin was serving, when there is a usable one.

    A migration cannot get a Let's Encrypt certificate for a domain until DNS
    points at the new server - and DNS is the last thing you move, by which
    point visitors are already arriving. The archive carries the certificate the
    old host was serving, in backup/<domain>/domain.cert with its key and chain,
    so the site can answer HTTPS from the moment it is imported.

    DirectAdmin also writes a self-signed placeholder when a domain has no real
    certificate; validate_manual_ssl rejects those along with anything expired
    or whose key does not match, so a placeholder simply leaves ssl off.

    Returns True when a certificate was installed.
    """
    from app.services import ssl as ssl_service

    base = root / "backup" / website.domain
    cert_path, key_path, ca_path = base / "domain.cert", base / "domain.key", base / "domain.cacert"
    if not (cert_path.is_file() and key_path.is_file()):
        return False
    try:
        cert = cert_path.read_bytes()
        key = key_path.read_bytes()
        ca = ca_path.read_bytes() if ca_path.is_file() else b""
    except OSError:
        return False
    if not cert.strip() or not key.strip():
        return False

    aliases = [alias.domain for alias in (website.aliases or [])]
    try:
        written = ssl_service.install_manual_ssl(website.domain, cert, key, ca, aliases=aliases)
    except (ValueError, RuntimeError) as exc:
        # Expired, self-signed, mismatched key, or covering another name: not a
        # failure of the import, just nothing worth installing.
        _log(f"  {website.domain}: no usable certificate in the backup ({exc})")
        return False

    website.ssl_enabled = True
    website.ssl_mode = "manual"
    website.ssl_cert_path = written["cert"]
    website.ssl_key_path = written["key"]
    website.ssl_ca_path = written["ca"]
    website.ssl_updated_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    # Writing the files is not enough: nginx keeps serving whatever the vhost
    # names, so the vhost has to be rewritten to point at them.
    try:
        nginx.rewrite_vhost(
            website.domain,
            website.root_path,
            app_type=website.app_type or "wordpress",
            php_version=website.php_version,
            custom_directives=website.nginx_custom or "",
            waf_enabled=bool(website.waf_enabled),
            document_root=getattr(website, "document_root", "public_html") or "public_html",
            rewrite_mode=getattr(website, "nginx_rewrite_mode", "none") or "none",
            ssl_cert_path=written["cert"],
            ssl_key_path=written["key"],
            ssl_ca_path=written["ca"],
            preserve_existing_ssl=False,
            aliases=[a.domain for a in (website.aliases or []) if a.mode == "alias"],
            redirects=[a.domain for a in (website.aliases or []) if a.mode == "redirect"],
        )
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        _log(f"  {website.domain}: certificate installed but the vhost rewrite failed ({exc})")
        return False

    item_summary.setdefault("ssl_imported_domains", []).append(website.domain)
    _log(f"  {website.domain}: installed the certificate from the backup")
    return True


def _enable_ssl_when_dns_matches(db, website, item_summary: dict) -> None:
    from app.services import ssl as ssl_service
    try:
        domain_ips = _domain_ip_addresses(website.domain)
    except Exception as exc:
        item_summary["warnings"].append(f"SSL skipped for {website.domain}: DNS lookup failed ({exc})")
        return
    server_ips = _server_ip_addresses()
    matching_ips = domain_ips & server_ips
    if not matching_ips:
        return
    # _sync_website_aliases() already ran and persisted every pointer domain
    # DirectAdmin had for this site (alias and redirect) - a redirect domain
    # needs its own valid certificate too, since the browser has to complete
    # TLS with *something* before it ever sees the 301 that sends it on.
    # Asking for the bare domain alone here left every pointer domain (and,
    # before issue_ssl() started adding it itself, www.<domain> too) with no
    # SSL coverage: nginx already serves them, so a visitor got a hard
    # certificate mismatch instead of the site. --allow-subset-of-names
    # (added to certbot-issue) means one pointer with bad DNS no longer
    # blocks the certificate for the ones that are fine.
    ssl_domains = [alias.domain for alias in (website.aliases or [])]
    _log(f"  DNS matches ({', '.join(sorted(matching_ips))}); installing SSL for {website.domain} ...")
    result = ssl_service.issue_ssl(website.domain, ssl_domains)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "certbot failed").strip().replace("\n", " ")
        item_summary["warnings"].append(f"SSL failed for {website.domain}: {detail[:500]}")
        return
    website.ssl_enabled = True
    website.ssl_mode = "letsencrypt"
    website.ssl_updated_at = _dt.datetime.utcnow()
    db.commit()
    db.refresh(website)
    item_summary["ssl_enabled_domains"].append(website.domain)
    _log(f"  SSL installed for {website.domain}")
    # --allow-subset-of-names can have dropped a pointer whose DNS is not
    # here (or not yet) - report that plainly instead of a blanket "SSL
    # installed" covering every pointer, and record it per-alias the same
    # way the panel's own "Add domain" does (WebsiteAlias.ssl_enabled).
    sans = ssl_service.cert_info(website.domain).get("sans") or []
    uncovered = []
    for alias in website.aliases or []:
        alias.ssl_enabled = bool(sans) and ssl_service.cert_covers(sans, alias.domain)
        if not alias.ssl_enabled:
            uncovered.append(alias.domain)
    if uncovered:
        db.commit()
        item_summary["warnings"].append(
            f"SSL not obtained for pointer(s) of {website.domain}: {', '.join(sorted(uncovered))} "
            "(DNS for these does not point here yet - re-run SSL from the panel once it does)"
        )


def _discover_da_emails(root: Path, domains: list[str]) -> dict[str, list[dict]]:
    """Discover email accounts and forwarders per domain in a DirectAdmin backup."""
    found: dict[str, list[dict]] = {}
    for domain in domains:
        mailboxes: dict[str, dict] = {}
        # 1. Read backup/<domain>/email/passwd
        passwd_candidates = [
            root / "backup" / domain / "email" / "passwd",
            root / "domains" / domain / ".htpasswd",
        ]
        quota_map: dict[str, int] = {}
        quota_file = root / "backup" / domain / "email" / "quota"
        if quota_file.is_file():
            for raw in quota_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if ":" in line and not line.startswith("#"):
                    local, _, q_raw = line.partition(":")
                    try:
                        q_bytes = int(q_raw.strip())
                        quota_map[local.strip().lower()] = max(0, q_bytes // (1024 * 1024)) if q_bytes > 0 else 1024
                    except ValueError:
                        pass

        for pfile in passwd_candidates:
            if not pfile.is_file():
                continue
            for raw in pfile.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                parts = line.split(":")
                local = parts[0].strip().lower()
                pw_hash = parts[1].strip() if len(parts) > 1 else ""
                if not local:
                    continue
                mailboxes[local] = {
                    "local_part": local,
                    "domain": domain,
                    "address": f"{local}@{domain}",
                    "password_hash": pw_hash,
                    "quota_mb": quota_map.get(local, 1024),
                    "maildir": None,
                }

        # 2. Check imap/<domain>/<local>/Maildir or backup/<domain>/email/data/imap/<local>/Maildir
        imap_dirs = [
            root / "imap" / domain,
            root / "backup" / domain / "email" / "data" / "imap",
        ]
        for imap_base in imap_dirs:
            if not imap_base.is_dir():
                continue
            for entry in sorted(imap_base.iterdir()):
                if not entry.is_dir():
                    continue
                local = entry.name.strip().lower()
                maildir = entry / "Maildir" if (entry / "Maildir").is_dir() else entry
                if local not in mailboxes:
                    mailboxes[local] = {
                        "local_part": local,
                        "domain": domain,
                        "address": f"{local}@{domain}",
                        "password_hash": "",
                        "quota_mb": quota_map.get(local, 1024),
                        "maildir": maildir,
                    }
                elif mailboxes[local]["maildir"] is None:
                    mailboxes[local]["maildir"] = maildir

        if mailboxes:
            found[domain] = list(mailboxes.values())
    return found


def _import_da_emails(
    db,
    user: User,
    root: Path,
    domains: list[str],
    credentials: list[str],
    item_summary: dict,
) -> list[str]:
    """Import discovered DirectAdmin mailboxes and Maildir data into SPanel mail."""
    from passlib.hash import sha512_crypt
    from app.services import mail as mail_service

    discovered = _discover_da_emails(root, domains)
    if not discovered:
        return []

    mail_section: dict = {"domains": [], "mailboxes": [], "forwarders": []}
    maildir_lookup: dict[tuple[str, str], Optional[Path]] = {}

    for domain, boxes in discovered.items():
        mail_section["domains"].append({"domain": domain, "catch_all": "", "relay": ""})
        for box in boxes:
            local = box["local_part"]
            raw_hash = str(box.get("password_hash") or "").strip()
            if raw_hash.startswith("{SHA512-CRYPT}$6$"):
                final_hash = raw_hash
            elif raw_hash.startswith("$6$"):
                final_hash = f"{{SHA512-CRYPT}}{raw_hash}"
            else:
                gen_pass = secrets.token_urlsafe(14)
                final_hash = "{SHA512-CRYPT}" + sha512_crypt.using(rounds=5000).hash(gen_pass)
                credentials.append(f"mailbox address={local}@{domain} password={gen_pass}")

            if not mail_service.SHA512_RE.fullmatch(final_hash):
                gen_pass = secrets.token_urlsafe(14)
                final_hash = "{SHA512-CRYPT}" + sha512_crypt.using(rounds=5000).hash(gen_pass)
                credentials.append(f"mailbox address={local}@{domain} password={gen_pass}")

            mail_section["mailboxes"].append({
                "local_part": local,
                "domain": domain,
                "password_hash": final_hash,
                "quota_mb": int(box.get("quota_mb") or 1024),
                "enabled": True,
            })
            maildir_lookup[(domain, local)] = box.get("maildir")

        # Forwarders from backup/<domain>/email/aliases
        aliases_file = root / "backup" / domain / "email" / "aliases"
        if aliases_file.is_file():
            for raw in aliases_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                local, _, dest_raw = line.partition(":")
                local = local.strip().lower()
                dests = [d.strip() for d in dest_raw.split(",") if "@" in d]
                if local and dests:
                    mail_section["forwarders"].append({
                        "local_part": local,
                        "domain": domain,
                        "destinations": dests,
                    })

    def stage_mail(domain: str, local: str) -> Optional[Path]:
        mdir = maildir_lookup.get((domain, local))
        if mdir and mdir.is_dir():
            return mdir
        return None

    try:
        restored = mail_service.restore_manifest(db, user, mail_section, stage_mail)
        mail_service.sync_quietly(db)
        item_summary.setdefault("mailboxes", []).extend(restored)
        return restored
    except Exception as exc:
        item_summary["warnings"].append(f"Email import warning: {exc}")
        return []


# ---------------------------------------------------------------------------
# Public API — Scan
# ---------------------------------------------------------------------------

def list_da_backups() -> list[dict]:
    """Return metadata for every DA backup archive in the upload directory."""
    ensure_backup_dir()
    if not DA_BACKUP_DIR.is_dir():
        return []
    items = []
    for path in sorted(DA_BACKUP_DIR.iterdir()):
        if _is_archive(path):
            items.append({
                "filename": path.name,
                "path": str(path),
                "size": path.stat().st_size,
            })
    return items


def scan_da_backup(archive_path: str) -> dict:
    """Extract a DA backup into a staging dir, discover users/domains/databases/emails."""
    path = resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not _is_archive(path):
        raise ValueError("Not a supported archive format")

    result: dict = {
        "filename": path.name,
        "size": path.stat().st_size,
        "archive_path": str(path),
        "panel_type": "directadmin",
        "users": [],
        "errors": [],
    }

    # Scan under the same staging root as the import. /tmp is frequently a
    # small tmpfs and a DirectAdmin account backup can be tens of gigabytes.
    STAGE_BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spanel-da-scan-", dir=str(STAGE_BASE)) as tmp:
        extracted = Path(tmp) / "extracted"
        try:
            _safe_extract_tar(path, extracted)
        except Exception as exc:
            result["errors"].append(f"Extraction failed: {exc}")
            return result

        root = _find_backup_root(extracted)
        _extract_nested_domain_archives(root)
        username, email = _discover_username(root, path.name)
        domains = _discover_domains(root)
        sql_files = _discover_sql_files(root)
        discovered_emails = _discover_da_emails(root, domains)

        # DirectAdmin subdomains (sub.example.com nested under example.com) come
        # in as their own websites; list them alongside the parent domains.
        subdomains: list[tuple[str, Optional[Path]]] = []
        for parent in list(domains):
            parent_public = _source_for_domain(root, parent)
            for label in _discover_subdomains(root, parent):
                fqdn = _normalize_domain(f"{label}.{parent}")
                if not fqdn or fqdn in domains or any(fqdn == s for s, _ in subdomains):
                    continue
                src = (parent_public / label) if parent_public else None
                subdomains.append((fqdn, src if src and src.is_dir() else None))

        all_mailboxes = [box["address"] for boxes in discovered_emails.values() for box in boxes]

        user_entry: dict = {
            "username": username,
            "email": email,
            "domains": [],
            "databases": [],
            "mailboxes": all_mailboxes,
        }

        scan_targets = [(d, _source_for_domain(root, d)) for d in domains] + subdomains
        total_sites = len(scan_targets)
        for domain, source in scan_targets:
            app_type = _detect_app_type(source)
            app_config = _parse_app_db_config(source) if source else {}
            matched_key, matched_sql = _matched_sql_for_config(app_config, sql_files, total_sites == 1)

            domain_entry: dict = {
                "domain": domain,
                "app_type": app_type,
                "has_files": source is not None,
                "db_name": app_config.get("DB_NAME") or matched_key or "",
                "db_user": app_config.get("DB_USER") or "",
                "has_sql_dump": matched_sql is not None,
                "email_count": len(discovered_emails.get(domain, [])),
                "aliases": [
                    {"domain": name, "mode": mode}
                    for name, mode in _discover_domain_pointers(root, domain)
                ],
            }
            user_entry["domains"].append(domain_entry)

        # Unassigned databases
        assigned_keys = set()
        for d in user_entry["domains"]:
            if d["db_name"]:
                assigned_keys.add(d["db_name"].lower())
        for key, sql_path in sql_files.items():
            if key not in assigned_keys:
                user_entry["databases"].append({
                    "db_name": key,
                    "sql_file": str(sql_path),
                    "has_sql_dump": True,
                })

        result["users"].append(user_entry)

    return result


# ---------------------------------------------------------------------------
# Public API — Import
# ---------------------------------------------------------------------------

def import_da_backup(
    archive_path: str,
    force: bool = False,
    *,
    import_websites: bool = True,
    import_databases: bool = True,
    import_emails: bool = True,
) -> dict:
    """Import a DA backup archive into spanel.

    Creates panel users, websites, nginx vhosts, MariaDB databases, emails, and
    optionally SSL certificates.

    ``force`` is required to overwrite anything that already exists: without
    it, an import that collides with a live panel user or domain stops instead
    of deleting the running site.

    Returns a summary dict with credentials (passwords are generated).
    """
    path = resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not _is_archive(path):
        raise ValueError("Not a supported archive format")

    stamp = _utc_stamp()
    STAGE_BASE.mkdir(parents=True, exist_ok=True)
    stage_dir = STAGE_BASE / f"web-{stamp}-{secrets.token_hex(4)}"
    stage_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    credentials: list[str] = [
        "# spanel DirectAdmin import credentials",
        f"# Web import: {stamp}",
        f"# Archive: {path.name}",
        "# Keep this file private. It contains generated panel and database passwords.",
    ]

    try:
        extracted = stage_dir / "extracted"
        _log(f"Extracting {path.name} ...")
        _safe_extract_tar(path, extracted)

        root = _find_backup_root(extracted)
        _extract_nested_domain_archives(root)
        domains = _discover_domains(root)
        username, email = _discover_username(root, path.name)
        subdomain_sources = _relocate_da_subdomain_sources(root, domains)
        all_domains = domains + [d for d in subdomain_sources if d not in domains]

        item_summary: dict = {
            "archive": path.name,
            "username": username,
            "domains": all_domains,
            "imported_domains": [],
            "databases": [],
            "mailboxes": [],
            "aliases": [],
            "ssl_enabled_domains": [],
            "ssl_imported_domains": [],
            "warnings": [],
        }

        if not all_domains and not _discover_sql_files(root):
            item_summary["warnings"].append("No domains or databases found")
            summary.append(item_summary)
            return {"summary": summary, "credentials": credentials, "errors": ["No domains found"]}

        db = SessionLocal()
        try:
            # Importing is destructive: it replaces the panel user, its
            # websites, files and databases. Refuse unless the caller asked
            # for it, so a re-run of a finished import cannot wipe a live site.
            if not force:
                conflicts = []
                if db.query(User).filter(User.username == username).first():
                    conflicts.append(f"panel user '{username}'")
                if import_websites:
                    for domain in all_domains:
                        if db.query(Website).filter(Website.domain == domain).first():
                            conflicts.append(f"website '{domain}'")
                if conflicts:
                    message = (
                        "Already exists: " + ", ".join(conflicts)
                        + ". Re-run with force to replace (this deletes the existing files and databases)."
                    )
                    item_summary["warnings"].append(message)
                    summary.append(item_summary)
                    return {"summary": summary, "credentials": credentials, "errors": [message]}

            if force:
                if import_websites:
                    for domain in all_domains:
                        _delete_existing_domain(db, domain)
                _delete_existing_user(db, username)

            existing_user = db.query(User).filter(User.username == username).first()
            if existing_user:
                user = existing_user
            else:
                user, _created = _ensure_panel_user_record(db, username, email, all_domains, credentials)

            sql_files = _discover_sql_files(root)
            imported_sql_keys: set[str] = set()
            websites = []

            import_targets = [(d, _source_for_domain(root, d)) for d in domains]
            import_targets += [(fqdn, src) for fqdn, src in subdomain_sources.items()]
            if import_websites:
                for domain, source in import_targets:
                    app_type = _detect_app_type(source)
                    app_config = _parse_app_db_config(source) if source else {}

                    _log(f"Importing domain {domain} for user {username} ...")

                    root_path = site_users.site_root_for_panel_user(user.username, domain)
                    linux_user = site_users.ensure_site_runtime(
                        domain, root_path,
                        DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                        user.username,
                    )
                    public = site_users.document_root(root_path)
                    if source is not None:
                        staged = stage_dir / "payload" / domain
                        _copy_site_files(source, staged / "public_html")
                        site_users.import_site_files(root_path, linux_user, str(staged))
                    else:
                        _log(f"  {domain}: backup carried no files, importing empty")

                    selected_rules = [rule["id"] for rule in waf.DEFAULT_RULES]
                    waf_enabled = True
                    waf_result = waf.sync_site_rules(domain, selected_rules, "", crs_mode="off")
                    if waf_result.returncode != 0:
                        waf_enabled = False
                        _log(f"  warning: WAF rules failed for {domain}")

                    php_socket = site_users.site_php_fpm_socket(
                        linux_user, root_path,
                        DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                    )
                    rewrite_mode = "front_controller" if app_type == "wordpress" else "none"
                    pointers = _discover_domain_pointers(root, domain)
                    try:
                        nginx.write_vhost(
                            domain, root_path,
                            app_type=app_type,
                            php_version=DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                            php_fpm_socket_override=php_socket,
                            waf_enabled=waf_enabled,
                            rewrite_mode=rewrite_mode,
                        )
                    except RuntimeError:
                        if not waf_enabled:
                            raise
                        waf_enabled = False
                        _log(f"  warning: nginx rejected WAF for {domain}; retrying with WAF disabled")
                        nginx.write_vhost(
                            domain, root_path,
                            app_type=app_type,
                            php_version=DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                            php_fpm_socket_override=php_socket,
                            waf_enabled=False,
                            rewrite_mode=rewrite_mode,
                        )

                    website = db.query(Website).filter(Website.domain == domain).first()
                    if website is None:
                        website = Website(
                            domain=domain,
                            owner_id=user.id,
                            root_path=root_path,
                            linux_user=linux_user,
                            php_version=DEFAULT_PHP_VERSION,
                            app_type=app_type,
                            status="active",
                            waf_enabled=waf_enabled,
                            nginx_rewrite_mode=rewrite_mode,
                        )
                        db.add(website)
                    else:
                        website.owner_id = user.id
                        website.root_path = root_path
                        website.linux_user = linux_user
                        website.php_version = DEFAULT_PHP_VERSION
                        website.app_type = app_type
                        website.waf_enabled = waf_enabled
                        website.nginx_rewrite_mode = rewrite_mode
                    db.commit()
                    db.refresh(website)

                    applied_pointers = _sync_website_aliases(db, website, pointers, item_summary)
                    if applied_pointers:
                        alias_domains = [name for name, mode in applied_pointers if mode == "alias"]
                        redirect_domains = [name for name, mode in applied_pointers if mode == "redirect"]
                        try:
                            nginx.rewrite_vhost(
                                domain, root_path,
                                app_type=app_type,
                                php_version=DEFAULT_PHP_VERSION if app_type in {"wordpress", "php"} else None,
                                php_fpm_socket_override=php_socket,
                                waf_enabled=waf_enabled,
                                rewrite_mode=rewrite_mode,
                                aliases=alias_domains,
                                redirects=redirect_domains,
                            )
                            item_summary.setdefault("aliases", []).extend(
                                f"{name} ({mode})" for name, mode in applied_pointers
                            )
                            _log(f"  imported {len(applied_pointers)} pointer(s) for {domain}")
                        except RuntimeError as exc:
                            item_summary["warnings"].append(
                                f"Pointers for {domain} not applied to Nginx: {exc}"
                            )

                    site_users.fix_site_permissions(root_path, linux_user)
                    websites.append(website)
                    item_summary["imported_domains"].append(domain)

                    # Database import paired with website
                    if import_databases:
                        public = site_users.document_root(website.root_path)
                        app_config = app_config or _parse_app_db_config(public)
                        matched_key, matched_sql = _matched_sql_for_config(
                            app_config, sql_files, len(import_targets) == 1
                        )
                        if matched_sql:
                            da_creds = _da_db_credentials(matched_sql, root)
                            db_name, db_user, db_password, reused = _create_panel_database(
                                db, user, website,
                                app_config.get("DB_NAME") or matched_key,
                                app_config.get("DB_USER"),
                                matched_sql, credentials,
                                app_config=app_config, da_credentials=da_creds,
                            )
                            imported_sql_keys.add(matched_key)
                            if not reused:
                                _update_app_db_config(public, db_name, db_user, db_password)
                            site_users.fix_site_permissions(website.root_path, website.linux_user)
                            item_summary["databases"].append({
                                "domain": domain, "source": str(matched_sql), "db_name": db_name,
                            })

            # Import unassigned databases (or all databases when import_websites=False)
            if import_databases:
                for key, sql_path in sql_files.items():
                    if key in imported_sql_keys:
                        continue
                    if db.query(DatabaseAccount).filter(DatabaseAccount.db_name == key).first() and not force:
                        continue
                    _log(f"Importing database {key} for user {username} ...")
                    db_name, db_user, _db_password, _reused = _create_panel_database(
                        db, user, None, key, key, sql_path, credentials,
                        da_credentials=_da_db_credentials(sql_path, root),
                    )
                    item_summary["databases"].append({
                        "domain": None, "source": str(sql_path), "db_name": db_name, "db_user": db_user,
                    })

            # Import emails
            if import_emails:
                _import_da_emails(db, user, root, domains, credentials, item_summary)

            # SSL
            for website in websites:
                _enable_ssl_when_dns_matches(db, website, item_summary)
                if not website.ssl_enabled:
                    _import_da_certificate(db, website, root, item_summary)

            summary.append(item_summary)
        finally:
            db.close()

    finally:
        # Cleanup staging
        shutil.rmtree(stage_dir, ignore_errors=True)

    # DNS Manager: the imported domains get their zones.
    from app.services import dns

    with SessionLocal() as dns_db:
        dns.sync_quietly(dns_db)

    errors = [w for item in summary for w in item.get("warnings", []) if "failed" in w.lower() or "error" in w.lower()]
    return {
        "summary": summary,
        "credentials": credentials,
        "errors": errors,
    }


def delete_da_backup(archive_path: str) -> str:
    """Delete an uploaded DA backup archive."""
    path = resolve_backup_path(archive_path)
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path.name}")
    if not _is_archive(path):
        raise ValueError("Not a supported archive format")
    name = path.name
    path.unlink(missing_ok=True)
    return name


def restore_to_user(
    archive_path: str,
    user_id: int,
    import_websites: bool = True,
    import_databases: bool = True,
    import_emails: bool = True,
    force: bool = True,
) -> dict:
    """Entry point used by Unified Migration to restore a DirectAdmin backup."""
    return import_da_backup(
        archive_path,
        force=force,
        import_websites=import_websites,
        import_databases=import_databases,
        import_emails=import_emails,
    )
