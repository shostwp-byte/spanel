import hashlib
import os
import re
import secrets
from pathlib import Path, PurePosixPath
from typing import Optional

from app.services.shell import shell


LINUX_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
PHP_VERSION_RE = re.compile(r"^(?:5\.6|7\.4|8\.[0-5])$")
HOME_ROOT = Path("/home")
PUBLIC_DIR = "public_html"
RESERVED_LINUX_USERS = {
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "_apt",
    "nobody", "spanel", "spanel-sites", "spanel-sftp", "spanel-sftp-site",
    "mysql", "redis", "nginx",
}
# Per-website SFTP sub-accounts own this prefix. A panel user may not take a
# name inside it: the helper proves a sub-account verb can never be aimed at a
# panel user by requiring the prefix, and that proof only holds while the two
# namespaces stay disjoint.
SFTP_SUB_ACCOUNT_PREFIX = "sftp_"


def linux_user_for_domain(domain: str) -> str:
    """Return a deterministic, Linux-safe username for a website domain."""
    normalized = (domain or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "site"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    username = f"bp_{slug[:18]}_{digest}"
    return username[:32]


def validate_linux_user(username: str) -> str:
    if not LINUX_USER_RE.fullmatch(username or "") or username in RESERVED_LINUX_USERS:
        raise ValueError("Invalid panel Linux user")
    if (username or "").startswith(SFTP_SUB_ACCOUNT_PREFIX):
        raise ValueError("Invalid panel Linux user")
    return username


def linux_user_for_panel_username(username: str) -> str:
    return validate_linux_user((username or "").strip().lower())


def validate_php_version(php_version: str) -> str:
    if not PHP_VERSION_RE.fullmatch(php_version or ""):
        raise ValueError("Invalid PHP version")
    return php_version


def php_fpm_pool_name(username: str, php_version: str, root_path: str | Path | None = None) -> str:
    safe_user = validate_linux_user(username)
    safe_version = validate_php_version(php_version).replace(".", "_")
    if root_path is not None:
        resolved_root = str(Path(root_path).resolve())
        site_hash = hashlib.sha256(resolved_root.encode("utf-8")).hexdigest()[:12]
        return f"spanel-{safe_user}-{site_hash}-{safe_version}"
    return f"spanel-{safe_user}-{safe_version}"


def php_fpm_socket(
    username: Optional[str],
    php_version: Optional[str] = None,
    root_path: str | Path | None = None,
) -> Optional[str]:
    if not username:
        return None
    if php_version:
        return f"/run/php/{php_fpm_pool_name(username, php_version, root_path)}.sock"
    safe_user = validate_linux_user(username)
    return f"/run/php/spanel-{safe_user}.sock"


def site_php_fpm_socket(username: Optional[str], root_path: str | Path, php_version: Optional[str]) -> Optional[str]:
    return php_fpm_socket(username, php_version, root_path) if php_version else None


def site_root_for_domain(domain: str) -> str:
    username = linux_user_for_domain(domain)
    return str(HOME_ROOT / username / domain.strip().lower())


def site_root_for_panel_user(username: str, domain: str) -> str:
    return str(HOME_ROOT / linux_user_for_panel_username(username) / domain.strip().lower())


def validate_document_root(value: str | None) -> str:
    cleaned = (value or PUBLIC_DIR).strip().replace("\\", "/")
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:/", cleaned):
        raise ValueError("document_root must be relative to the website root")
    cleaned = cleaned.strip("/")
    if not cleaned or len(cleaned) > 255:
        raise ValueError("document_root must be a relative path up to 255 characters")
    relative = PurePosixPath(cleaned)
    if relative.is_absolute():
        raise ValueError("document_root must be relative to the website root")
    parts = relative.parts
    if any(part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts):
        raise ValueError("document_root must be a safe relative path such as public_html/public")
    return "/".join(parts)


def document_root(root_path: str | Path, relative_path: str = PUBLIC_DIR) -> Path:
    root = Path(root_path).resolve()
    target = (root / validate_document_root(relative_path)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("document_root must stay inside the website root") from exc
    return target


def is_managed_site_path(path: str | Path) -> bool:
    resolved = Path(path).resolve()
    home_root = HOME_ROOT.resolve()
    try:
        relative = resolved.relative_to(home_root)
    except ValueError:
        return False
    parts = relative.parts
    return len(parts) >= 2 and bool(LINUX_USER_RE.fullmatch(parts[0])) and parts[0] not in RESERVED_LINUX_USERS and bool(DOMAIN_RE.fullmatch(parts[1]))


def is_site_root_for_domain(path: str | Path, domain: str) -> bool:
    resolved = Path(path).resolve()
    home_root = HOME_ROOT.resolve()
    try:
        relative = resolved.relative_to(home_root)
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) == 2
        and bool(LINUX_USER_RE.fullmatch(parts[0]))
        and parts[0] not in RESERVED_LINUX_USERS
        and parts[1] == domain.strip().lower()
    )


def ensure_panel_user(username: str, password: Optional[str] = None) -> str:
    linux_user = linux_user_for_panel_username(username)
    shell.privileged(
        "panel-user-ensure",
        helper_args=[linux_user],
        fallback=["mkdir", "-p", str(HOME_ROOT / linux_user)],
    )
    if password is not None:
        set_panel_user_password(linux_user, password)
    return linux_user


def set_panel_user_password(username: str, password: str) -> None:
    linux_user = linux_user_for_panel_username(username)
    shell.privileged(
        "panel-user-password",
        helper_args=[linux_user],
        input=f"{password}\n",
        sensitive=True,
        fallback=["true"],
    )


def generate_login_password() -> str:
    """A password for a Linux login the panel invents rather than accepts.

    token_urlsafe emits only letters, digits, '-' and '_', so it can never trip
    the ':' rule that chpasswd cares about - it reads `user:password` one line
    at a time, and a ':' in the value would let a password describe a second
    account.

    One implementation, shared with per-website sub-accounts, so the two cannot
    drift apart on strength or on the character set.
    """
    return secrets.token_urlsafe(18)


def rotate_panel_user_password(username: str) -> str:
    """Give this account's Linux login a fresh secret nobody has seen.

    Used when a panel password that was also the SFTP password is changed: the
    old value has to stop working, or a rotation the user believes killed the
    old secret quietly did not.
    """
    password = generate_login_password()
    set_panel_user_password(username, password)
    return password


def retire_shared_login_password(username: str, already_separate: bool) -> Optional[str]:
    """What has to happen to the SFTP password when the panel password changes.

    One implementation because three call sites change a panel password -
    api/users.py, api/panel_settings.py and api/provisioning.py - and the
    account-teardown bug in this codebase came from exactly that shape drifting
    apart.

    If the account already has its own SFTP password, a panel password change
    is none of its business: return None and leave it alone.

    If it does not, the Linux account is still carrying the *old* panel
    password. Leaving it there would mean a user who rotates a compromised
    password still has the compromised value opening SFTP on port 22 - the
    rotation would look complete and not be. So the Linux password is replaced
    with a fresh secret, which the caller must hand to the user: it is the only
    time anyone will see it.
    """
    if already_separate:
        return None
    return rotate_panel_user_password(username)


def delete_panel_user(username: str) -> None:
    linux_user = linux_user_for_panel_username(username)
    shell.privileged(
        "panel-user-delete",
        helper_args=[linux_user],
        check=False,
        fallback=["true"],
    )


def ensure_site_runtime(domain: str, root_path: str, php_version: Optional[str] = None, linux_user: Optional[str] = None) -> str:
    username = validate_linux_user(linux_user) if linux_user else linux_user_for_domain(domain)
    helper_args = [username, root_path, php_version or "none"]
    shell.privileged(
        "site-runtime-ensure",
        helper_args=helper_args,
        fallback=["mkdir", "-p", str(document_root(root_path))],
    )
    return username


def ensure_document_root(root_path: str, relative_path: str, linux_user: Optional[str]) -> Path:
    target = document_root(root_path, relative_path)
    if not linux_user:
        target.mkdir(parents=True, exist_ok=True)
        return target
    username = validate_linux_user(linux_user)
    shell.privileged(
        "site-document-root-ensure",
        helper_args=[username, root_path, validate_document_root(relative_path)],
        fallback=["mkdir", "-p", str(target)],
    )
    return target


def move_site_runtime(old_root_path: str, new_root_path: str, linux_user: str, php_version: Optional[str] = None) -> str:
    username = validate_linux_user(linux_user)
    shell.privileged(
        "site-runtime-move",
        helper_args=[username, old_root_path, new_root_path, php_version or "none"],
        fallback=["mv", old_root_path, new_root_path],
    )
    return new_root_path


def fix_site_permissions(root_path: str, linux_user: Optional[str]) -> None:
    if linux_user:
        shell.privileged(
            "fix-permissions",
            helper_args=[root_path, validate_linux_user(linux_user)],
            check=False,
            fallback=["chown", "-R", f"{linux_user}:{linux_user}", root_path],
        )
        return
    shell.privileged(
        "fix-permissions",
        helper_args=[root_path],
        check=False,
        fallback=["chown", "-R", "www-data:www-data", root_path],
    )


IMPORT_STAGE_BASE = Path(os.environ.get("spanel_IMPORT_STAGE_BASE", "/var/lib/spanel/import-stage"))


def import_site_files(root_path: str, linux_user: str, staged_source: str) -> None:
    """Replace a site's tree from a panel-staged copy.

    The site directory belongs to its Linux user, so the panel process cannot
    write into it; the helper does the copy as root and re-applies the site
    permission model. ``staged_source`` is a panel-owned directory (under the
    import staging area) laid out like the site root, i.e. with ``public_html/``
    at its top.
    """
    username = validate_linux_user(linux_user)
    shell.privileged(
        "site-populate",
        helper_args=[username, root_path, staged_source],
        fallback=["cp", "-aT", staged_source, str(root_path)],
    )


def fix_site_path(path: str, linux_user: Optional[str], check: bool = False) -> None:
    if not linux_user:
        return
    shell.privileged(
        "site-path-fix",
        helper_args=[path, validate_linux_user(linux_user)],
        check=check,
        fallback=["chown", "-R", f"{linux_user}:{linux_user}", path],
    )


def delete_site_runtime(root_path: str, linux_user: Optional[str]) -> None:
    if not linux_user:
        return
    shell.privileged(
        "site-runtime-delete",
        helper_args=[validate_linux_user(linux_user), root_path],
        check=False,
        fallback=["true"],
    )

def lock_linux_user(username: str) -> None:
    linux_user = validate_linux_user(username)
    shell.privileged(
        "panel-user-lock",
        helper_args=[linux_user],
        check=False,
        fallback=["usermod", "-L", linux_user],
    )

def unlock_linux_user(username: str) -> None:
    linux_user = validate_linux_user(username)
    shell.privileged(
        "panel-user-unlock",
        helper_args=[linux_user],
        check=False,
        fallback=["usermod", "-U", linux_user],
    )
