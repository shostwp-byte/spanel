import os
import stat
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role
from app.models.entities import SiteApp, User, Website


BYTES_PER_MB = 1024 * 1024
STATIC_SITE_ESTIMATE_BYTES = 1 * BYTES_PER_MB
WORDPRESS_SITE_ESTIMATE_BYTES = 100 * BYTES_PER_MB

# Container volumes sit outside the customer's home, so measuring them means
# asking the helper, which means a `du` as root. The dashboard asks for usage on
# every load, so the answer is held briefly rather than recomputed each time.
VOLUME_USAGE_TTL_SECONDS = 60
_volume_usage_cache: dict[str, tuple[float, int]] = {}
_volume_usage_lock = threading.Lock()

# Measuring a user's storage walks every file under every website they own -
# tens of thousands of them for a WordPress site. The user list renders that
# figure for every row, so it is cached: a few minutes of staleness on a disk
# number is fine, and any write that changes it calls forget_user_storage().
USER_USAGE_TTL_SECONDS = 300
_user_usage_cache: dict[int, tuple[float, int]] = {}
_user_usage_lock = threading.Lock()


def forget_user_storage(user_id: int | None) -> None:
    if user_id is None:
        return
    with _user_usage_lock:
        _user_usage_cache.pop(int(user_id), None)


class StorageQuotaExceeded(ValueError):
    pass


def user_storage_limit_bytes(user: User) -> int | None:
    if is_admin_role(user.role):
        return None
    return max(0, int(user.storage_limit_mb or 0)) * BYTES_PER_MB


def path_usage_bytes(path: str | Path) -> int:
    # os.scandir carries each entry's stat from the dirent on Linux, so a tree
    # walk is one syscall per entry instead of two - it matters on a WordPress
    # tree of tens of thousands of files, walked once per user on the user list.
    root = os.fspath(path)
    total = 0
    try:
        total += os.lstat(root).st_size
    except OSError:
        return 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISLNK(entry_stat.st_mode):
                        continue
                    total += entry_stat.st_size
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except OSError:
            continue
    return total


def website_storage_used_bytes(website: Website) -> int:
    if not website.root_path:
        return 0
    return path_usage_bytes(website.root_path)


def volume_usage_bytes(linux_user: str, use_cache: bool = True) -> int:
    """How much disk a customer's container volumes take.

    Zero when Docker is not installed or the helper cannot answer: a number the
    panel cannot measure must not be allowed to look like a full disk.
    """
    if not linux_user:
        return 0
    now = time.monotonic()
    if use_cache:
        with _volume_usage_lock:
            cached = _volume_usage_cache.get(linux_user)
        if cached and now - cached[0] < VOLUME_USAGE_TTL_SECONDS:
            return cached[1]

    from app.services.shell import shell

    result = shell.privileged(
        "site-app-volume-usage",
        helper_args=[linux_user],
        check=False,
        timeout=120,
        fallback=["bash", "-lc", "echo 0"],
    )
    text = (result.stdout or "").strip().splitlines()
    total = 0
    if result.returncode == 0 and text and text[-1].isdigit():
        total = int(text[-1])
    with _volume_usage_lock:
        _volume_usage_cache[linux_user] = (now, total)
    return total


def forget_volume_usage(linux_user: str) -> None:
    """Drop the cached figure after something changed it."""
    with _volume_usage_lock:
        _volume_usage_cache.pop(linux_user or "", None)


def app_storage_used_bytes(db: Session, user: User) -> int:
    """An application's own directory plus the volumes its containers write to.

    Both sat outside what the quota measured: the directory because it is not a
    website root, the volumes because they are not even under /home.
    """
    from app.services import addons

    if not addons.is_installed(addons.APPLICATION):
        return 0
    from app.services import site_apps

    apps = db.query(SiteApp).filter(SiteApp.owner_id == user.id).all()
    if not apps:
        return 0
    total = 0
    linux_users = set()
    for app in apps:
        try:
            linux_user = site_apps.owner_linux_user(app)
            total += path_usage_bytes(site_apps.app_directory(linux_user, app.name))
        except (ValueError, AttributeError):
            continue
        linux_users.add(linux_user)
    for linux_user in linux_users:
        total += volume_usage_bytes(linux_user)
    return total


def mail_storage_used_bytes(user: User) -> int:
    """The Email addon keeps a customer's mailboxes in their home. The panel
    reads them through group spanel; nothing there means no mail."""
    from app.services import site_users

    try:
        linux_user = site_users.linux_user_for_panel_username(user.username)
    except ValueError:
        return 0
    return path_usage_bytes(f"/home/{linux_user}/mail")


def mysql_storage_used_bytes(db: Session, user: User) -> int:
    """Total physical size of all MariaDB/MySQL databases owned by this user."""
    from app.models.entities import DatabaseAccount
    from app.services import resource_monitor

    cached = resource_monitor.cached_user_mysql_bytes(user.id)
    if cached > 0:
        return cached
    dbs = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).all()
    if not dbs:
        return 0
    return sum(max(0, int(getattr(d, "size_bytes", 0) or resource_monitor.cached_db_size_bytes(d.db_name))) for d in dbs)


def user_storage_breakdown(db: Session, user: User, *, use_cache: bool = True) -> dict[str, int]:
    websites = db.query(Website).filter(Website.owner_id == user.id).all()
    web_bytes = sum(website_storage_used_bytes(website) for website in websites)
    app_bytes = app_storage_used_bytes(db, user)
    mail_bytes = mail_storage_used_bytes(user)
    mysql_bytes = mysql_storage_used_bytes(db, user)
    total = web_bytes + app_bytes + mail_bytes + mysql_bytes
    if use_cache:
        with _user_usage_lock:
            _user_usage_cache[user.id] = (time.monotonic(), total)
    return {
        "web_used_bytes": web_bytes,
        "mysql_used_bytes": mysql_bytes,
        "mail_used_bytes": mail_bytes,
        "app_used_bytes": app_bytes,
        "storage_used_bytes": total,
    }


def user_storage_used_bytes(db: Session, user: User, *, use_cache: bool = False) -> int:
    if use_cache:
        with _user_usage_lock:
            cached = _user_usage_cache.get(user.id)
        if cached and time.monotonic() - cached[0] < USER_USAGE_TTL_SECONDS:
            return cached[1]
    websites = db.query(Website).filter(Website.owner_id == user.id).all()
    total = (sum(website_storage_used_bytes(website) for website in websites)
             + app_storage_used_bytes(db, user)
             + mail_storage_used_bytes(user)
             + mysql_storage_used_bytes(db, user))
    if use_cache:
        with _user_usage_lock:
            _user_usage_cache[user.id] = (time.monotonic(), total)
    return total


def cached_storage_used_bytes(user_id: int) -> int | None:
    """What the cache holds for this user, or None. Never walks the disk.

    The user list needs to answer "who are the accounts" without waiting on a
    figure that takes a filesystem walk per account. On a cold cache that was
    3.8 seconds for fifteen users before a single row appeared.
    """
    with _user_usage_lock:
        entry = _user_usage_cache.get(int(user_id))
    if not entry:
        return None
    when, value = entry
    return value if time.time() - when < USER_USAGE_TTL_SECONDS else None


def storage_usage_summary(db: Session, user: User, *, use_cache: bool = False) -> dict:
    used_bytes = user_storage_used_bytes(db, user, use_cache=use_cache)
    limit_bytes = user_storage_limit_bytes(user)
    percent = 0.0
    if limit_bytes and limit_bytes > 0:
        percent = min(999.0, round((used_bytes / limit_bytes) * 100, 2))
    return {
        "storage_used_bytes": used_bytes,
        "storage_limit_bytes": limit_bytes,
        "storage_percent": percent,
    }


def enforce_user_storage_quota(
    db: Session,
    user: User,
    *,
    incoming_bytes: int = 0,
    replaced_bytes: int = 0,
) -> None:
    limit_bytes = user_storage_limit_bytes(user)
    if limit_bytes is None:
        return
    used_bytes = user_storage_used_bytes(db, user)
    projected_bytes = max(0, used_bytes - max(0, replaced_bytes)) + max(0, incoming_bytes)
    if projected_bytes > limit_bytes:
        raise StorageQuotaExceeded(
            f"Storage quota exceeded: {projected_bytes // BYTES_PER_MB} MB used/projected, "
            f"limit {limit_bytes // BYTES_PER_MB} MB"
        )


def source_file_size(source_file) -> int | None:
    try:
        position = source_file.tell()
        source_file.seek(0, os.SEEK_END)
        size = source_file.tell()
        source_file.seek(position)
        return max(0, int(size - position))
    except (AttributeError, OSError, ValueError):
        return None
