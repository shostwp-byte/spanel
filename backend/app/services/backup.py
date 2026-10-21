from datetime import datetime
from io import StringIO
import hashlib
import json
import logging
import os
from pathlib import Path
import posixpath
import re
import secrets
import tarfile
import tempfile
from typing import List, Optional

import paramiko
from paramiko.pkey import PKey
from paramiko.ssh_exception import SSHException

from app.core.config import settings
from app.core.secrets import decrypt, encrypt
from app.core.security import hash_password
from app.core.permissions import Role
from app.models.entities import DatabaseAccount, MailAccount, SiteApp, User, Website, WebsiteAlias
from app.services import mariadb, nginx, site_users, waf, wordpress
from app.services.shell import shell


logger = logging.getLogger("spanel.backup")

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
BACKUP_MANIFEST = "manifest.json"
PANEL_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")

# Full-user archives spanel will restore. spanel writes "spanel_user"; "opanel_user"
# is the same archive layout (manifest.json, sites/<domain>/site, databases/<domain>.sql)
# written by opanel, a sibling panel, so accept it too and move accounts between the
# two. opanel archives carry no "applications" section - only domains, files and
# databases come across.
RESTORABLE_BACKUP_KINDS = ("spanel_user", "opanel_user")
_PLACEHOLDER_EMAIL_SUFFIXES = (
    "@users.spanel.test", "@users.spanel.vn",
    "@users.opanel.test", "@users.opanel.vn",
)


def _hostname_conflicts(db, domain: str, exclude_website_id: int | None = None) -> bool:
    safe = (domain or "").strip().lower()
    if not safe:
        return True
    reserved: set[str] = set()
    for website in db.query(Website).all():
        if exclude_website_id is not None and website.id == exclude_website_id:
            continue
        hostname = (website.domain or "").strip().lower()
        if hostname:
            reserved.add(hostname)
            reserved.add(f"www.{hostname}")
    for alias in db.query(WebsiteAlias).all():
        if exclude_website_id is not None and alias.website_id == exclude_website_id:
            continue
        alias_host = (alias.domain or "").strip().lower()
        if alias_host:
            reserved.add(alias_host)
    return safe in reserved or f"www.{safe}" in reserved


def create_backup(website: Website, db_name: Optional[str] = None) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_dir = Path(settings.backup_root) / website.domain
    archive = backup_dir / f"{website.domain}-{stamp}.tar.gz"
    sql_file = backup_dir / f"{website.domain}-{stamp}.sql"
    shell.run(["mkdir", "-p", str(backup_dir)])
    backup_dir.mkdir(parents=True, exist_ok=True)
    if db_name:
        mariadb.export_database(db_name, str(sql_file))
        if settings.command_dry_run and not sql_file.exists():
            sql_file.write_text(f"-- DRY RUN database dump for {db_name}\n", encoding="utf-8")
    try:
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(website.root_path, arcname="site")
            if sql_file.exists():
                tar.add(sql_file, arcname=f"database/{sql_file.name}")
    finally:
        # The dump is inside the archive, and restore reads it from there.
        # Left beside it, every website backup kept a second, uncompressed
        # copy of the database that nothing ever removed.
        sql_file.unlink(missing_ok=True)
    return str(archive)


def _user_backup_dir(username: str) -> Path:
    if not PANEL_USERNAME_RE.fullmatch(username or ""):
        raise ValueError("Invalid panel username")
    return Path(settings.backup_root) / "users" / username


def _user_restore_dir() -> Path:
    return Path(settings.backup_root) / "users" / "restore"


def user_restore_dir() -> str:
    return str(_user_restore_dir())


def _ensure_user_dir(path: Path) -> Path:
    """Create a directory under <backup_root>/users, past a root-owned parent.

    The panel runs as spanel and owns <backup_root>, but nothing ever created
    <backup_root>/users, so whichever process reached it first became its
    owner. Where that was a root one, every scheduled backup since has died on

        [Errno 13] Permission denied: '/var/backups/spanel/users/<account>'

    reported as a failed job with no hint that a directory mode was the whole
    of it. The helper repairs the parent and creates the leaf as spanel, which
    is the one thing the panel cannot do for itself.

    The retry is outside the except block on purpose: if the helper could not
    fix it either, the caller should see the same PermissionError it would
    have seen anyway, not a second error about the helper.

    0750 explicitly, so a directory the panel makes matches one the helper
    makes. Default umask gave 0755 and left account archives world-readable
    to anyone who could reach them - nothing can today, because the backup
    root above is 0750, but that is one chmod away from not being true.
    """
    try:
        path.mkdir(mode=0o750, parents=True, exist_ok=True)
        return path
    except PermissionError:
        shell.privileged(
            "user-backup-dir-ensure",
            helper_args=[path.name],
            check=False,
            fallback=["install", "-d", "-m", "0750", str(path)],
        )
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    return path


def create_user_backup(user: User, db) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_dir = _ensure_user_dir(_user_backup_dir(user.username))
    archive = backup_dir / f"user-{user.username}-{stamp}.tar.gz"
    websites = db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.asc()).all()

    with tempfile.TemporaryDirectory(prefix="spanel-user-backup-", dir=str(backup_dir)) as tmp:
        tmp_dir = Path(tmp)
        manifest = {
            "kind": "spanel_user",
            "version": 1,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "user": {
                "username": user.username,
                "email": user.email,
                "hashed_password": user.hashed_password,
                "role": user.role,
                "is_active": user.is_active,
                "website_limit": user.website_limit,
                "storage_limit_mb": user.storage_limit_mb,
            },
            "websites": [],
            "applications": [],
        }
        # Email addon: the account's mail domains, mailboxes (password hashes)
        # and forwarders; the mail itself is under mail/<domain>/<name>.
        from app.services import mail

        manifest["mail"] = mail.backup_manifest(db, user)
        mail_boxes: list[tuple[str, Path]] = []
        linux_user = site_users.linux_user_for_panel_username(user.username)
        for account in (db.query(MailAccount).filter(MailAccount.owner_id == user.id)
                        .order_by(MailAccount.domain, MailAccount.local_part).all()):
            mail_boxes.append((f"mail/{account.domain}/{account.local_part}",
                               Path(f"/home/{linux_user}/mail/{account.domain}/{account.local_part}")))

        # Applications are not websites and their data is not under a website
        # root, so without this a restore brought back the sites and quietly
        # dropped every container's workflows and database.
        app_payloads: dict[str, Path] = {}
        for app_entry, payload in _collect_applications(user, db, tmp_dir):
            manifest["applications"].append(app_entry)
            if payload:
                app_payloads[app_entry["name"]] = payload

        sql_files: dict[str, Path] = {}
        for website in websites:
            site_entry = {
                "domain": website.domain,
                "php_version": website.php_version,
                "app_type": website.app_type or "wordpress",
                "status": website.status or "active",
                "document_root": getattr(website, "document_root", "public_html") or "public_html",
                "nginx_custom": website.nginx_custom or "",
                "nginx_config_mode": "managed",
                "nginx_rewrite_mode": getattr(website, "nginx_rewrite_mode", "none") or "none",
                "waf_enabled": bool(website.waf_enabled),
                "waf_default_rules": website.waf_default_rules or "",
                "waf_custom_rules": website.waf_custom_rules or "",
                "http_flood_enabled": bool(getattr(website, "http_flood_enabled", False)),
                "http_flood_config": getattr(website, "http_flood_config", "") or "",
                "aliases": [alias.domain for alias in getattr(website, "aliases", []) or [] if getattr(alias, "mode", "alias") == "alias"],
                "database": None,
            }
            db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
            if db_item:
                sql_name = f"{website.domain}.sql"
                sql_path = tmp_dir / sql_name
                mariadb.export_database(db_item.db_name, str(sql_path))
                if settings.command_dry_run and not sql_path.exists():
                    sql_path.write_text(f"-- DRY RUN database dump for {db_item.db_name}\n", encoding="utf-8")
                sql_files[website.domain] = sql_path
                try:
                    db_password = decrypt(db_item.db_password)
                except RuntimeError:
                    db_password = ""
                site_entry["database"] = {
                    "db_name": db_item.db_name,
                    "db_user": db_item.db_user,
                    "db_password": db_password,
                    "sql_member": f"databases/{sql_name}",
                }
            manifest["websites"].append(site_entry)

        manifest_path = tmp_dir / BACKUP_MANIFEST
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(manifest_path, arcname=BACKUP_MANIFEST)
            for website in websites:
                root = Path(website.root_path)
                if root.exists():
                    tar.add(root, arcname=f"sites/{website.domain}/site")
                sql_path = sql_files.get(website.domain)
                if sql_path and sql_path.exists():
                    tar.add(sql_path, arcname=f"databases/{website.domain}.sql")
            for name, payload in app_payloads.items():
                if payload.exists():
                    tar.add(payload, arcname=f"applications/{name}.tar")
            for arcname, box in mail_boxes:
                _add_readable_tree(tar, box, arcname)
    return str(archive)


def _add_readable_tree(tar: tarfile.TarFile, root: Path, arcname: str) -> None:
    """A directory tree, skipping what cannot be read instead of failing.

    A mailbox is being written to while the backup runs: Dovecot renames
    files between new/ and cur/ and removes its temporary ones. One that
    vanished between listing and reading is not a reason to lose the whole
    account's backup.
    """
    if not root.is_dir():
        return
    tar.add(root, arcname=arcname, recursive=False)
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(directory)
        relative = base.relative_to(root)
        for name in dirnames:
            try:
                tar.add(base / name, arcname=f"{arcname}/{(relative / name).as_posix()}", recursive=False)
            except OSError:
                continue
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                continue
            try:
                tar.add(path, arcname=f"{arcname}/{(relative / name).as_posix()}", recursive=False)
            except OSError as exc:
                logger.info("Skipped %s in a mail backup: %s", path, exc)


def _collect_applications(user: User, db, tmp_dir: Path) -> list[tuple[dict, Optional[Path]]]:
    """Each of a user's applications: what it is, and a tar of what it holds.

    An application whose data cannot be read is still recorded — coming back with
    the settings and an empty directory beats not coming back at all — and the
    manifest says so, so a restore can tell the customer which ones need their
    data putting back by hand.
    """
    from app.services import addons, site_apps

    if not addons.is_installed(addons.APPLICATION):
        return []
    apps = db.query(SiteApp).filter(SiteApp.owner_id == user.id).order_by(SiteApp.id.asc()).all()
    collected: list[tuple[dict, Optional[Path]]] = []
    for app in apps:
        entry = {
            "name": app.name,
            "kind": app.kind,
            "port": app.port,
            "memory_limit_mb": app.memory_limit_mb,
            "cpu_limit": app.cpu_limit or "1",
            "autostart": bool(app.autostart),
            "env": app.env or "",
            "start_kind": app.start_kind,
            "start_arg": app.start_arg,
            "node_major": app.node_major,
            "image": app.image,
            "container_port": app.container_port,
            "compose_source": app.compose_source or "",
            "web_service": app.web_service,
            "websites": [website.domain for website in app.websites],
            "payload_member": None,
            "payload_error": "",
        }
        payload: Optional[Path] = None
        try:
            target = tmp_dir / f"app-{app.name}.tar"
            site_apps.export_payload(app, str(target))
            if target.exists():
                payload = target
                entry["payload_member"] = f"applications/{app.name}.tar"
        except (RuntimeError, ValueError) as exc:
            entry["payload_error"] = str(exc)[-500:]
            logger.warning("Could not export application %s: %s", app.name, exc)
        collected.append((entry, payload))
    return collected


def list_user_backups(username: str) -> List[str]:
    backup_dir = _user_backup_dir(username)
    if settings.command_dry_run or not backup_dir.exists():
        return []
    return [str(path) for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True)]


def list_uploaded_user_backups(username: Optional[str] = None) -> List[str]:
    backup_dirs = [_user_restore_dir(), Path(settings.backup_root) / "users" / "uploads"]
    if settings.command_dry_run:
        return []
    items = []
    seen = set()
    for backup_dir in backup_dirs:
        if not backup_dir.exists():
            continue
        for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True):
            if path in seen:
                continue
            if username:
                try:
                    manifest = read_backup_manifest(str(path))
                except Exception:
                    continue
                if (manifest.get("user") or {}).get("username") != username:
                    continue
            seen.add(path)
            items.append(str(path))
    return items


def describe_user_backup(backup_file: str) -> dict:
    path = user_backup_path(backup_file)
    item = {
        "backup_file": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
        "username": "",
        "generated_at": "",
        "websites": 0,
        "applications": 0,
        "valid": False,
        "source": "spanel",
        "error": "",
    }
    try:
        manifest = read_backup_manifest(str(path))
        kind = manifest.get("kind")
        item["valid"] = kind in RESTORABLE_BACKUP_KINDS
        item["source"] = "opanel" if kind == "opanel_user" else "spanel"
        item["username"] = (manifest.get("user") or {}).get("username") or ""
        item["generated_at"] = manifest.get("generated_at") or ""
        item["websites"] = len(manifest.get("websites") or [])
        item["applications"] = len(manifest.get("applications") or [])
        if not item["valid"]:
            item["error"] = "This is not a spanel or opanel user backup"
    except Exception as exc:
        item["error"] = str(exc)
    if not item["username"]:
        item["username"] = username_from_archive_name(path.name)
    return item


def list_all_user_backups(username: Optional[str] = None) -> list[dict]:
    if settings.command_dry_run:
        return []
    users_root = Path(settings.backup_root) / "users"
    if not users_root.exists():
        return []
    paths: list[Path] = []
    seen: set[Path] = set()
    if username:
        for p_str in list_user_backups(username):
            p = Path(p_str).resolve()
            if p not in seen and p.is_file():
                seen.add(p)
                paths.append(p)
    else:
        for sub in sorted(users_root.iterdir()):
            if not sub.is_dir():
                continue
            for p in sorted(sub.glob("*.tar.gz"), reverse=True):
                rp = p.resolve()
                if rp not in seen and rp.is_file():
                    seen.add(rp)
                    paths.append(rp)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    restore_dir_resolved = _user_restore_dir().resolve()
    for p in paths:
        info = describe_user_backup(str(p))
        info["location"] = "restore" if restore_dir_resolved in p.parents else "user"
        results.append(info)
    return results


def username_from_archive_name(name: str) -> str:
    """Best-effort account name from a file name, for a listing only.

    Archives are named user-<account>-<something>.tar.gz, where <something> is
    a timestamp or one of the rotating suffixes. This guesses the account so a
    remote listing can be grouped without downloading every file to read its
    manifest; the restore itself still reads the manifest and trusts that.
    """
    base = Path(name or "").name
    if not base.startswith("user-") or not base.endswith(".tar.gz"):
        return ""
    stem = base[len("user-"):-len(".tar.gz")]
    # Strip a trailing timestamp, weekday, week number or date if present.
    stem = re.sub(r"-(\d{14}|Mon|Tue|Wed|Thu|Fri|Sat|Sun|W[1-5]|\d{4}-\d{2}-\d{2})$", "", stem)
    return stem


def stage_remote_backup(fetch, name: str) -> str:
    """Put a remote archive where restore can find it, and return its name.

    `fetch` is handed a destination path and is responsible for filling it.
    The file lands in the same directory an uploaded backup would, under a
    name derived from the remote one, so everything downstream - validation,
    manifest reading, restore - is the path already in use rather than a
    second one written for remote files.
    """
    # Reject a name that carries a path, rather than quietly reducing it to
    # its basename. Path(...).name was applied FIRST here, which stripped the
    # traversal before the checks looked for it: "/" in safe and ".." in safe
    # could never be true, and "../escape.tar.gz" sailed through as
    # "escape.tar.gz". The caller passes the file name from a bucket listing
    # and the object key separately, so a name with a separator in it means
    # something is wrong upstream and should say so, not be rewritten.
    safe = (name or "").strip()
    if (not safe.endswith(".tar.gz")
            or "/" in safe or "\\" in safe
            or ".." in safe
            or safe != Path(safe).name):
        raise ValueError("Not a backup file name")

    destination = _ensure_user_dir(_user_restore_dir())
    stem = safe[: -len(".tar.gz")]
    # Never overwrite something already staged: two targets can hold a file of
    # the same name, and a half-finished download must not clobber a good one.
    local = destination / f"{stem}-{secrets.token_hex(3)}.tar.gz"
    try:
        fetch(str(local))
        if not local.is_file() or local.stat().st_size == 0:
            raise ValueError("The download produced no file")
    except BaseException:
        # A download cut short must not stay behind looking like a backup.
        local.unlink(missing_ok=True)
        raise
    return local.name


def list_user_restore_backups() -> list[dict]:
    backup_dir = _user_restore_dir()
    if settings.command_dry_run or not backup_dir.exists():
        return []
    return [describe_user_backup(str(path)) for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True)]


def user_backup_path(backup_file: str) -> Path:
    backup_root = Path(settings.backup_root).resolve()
    path = Path(backup_file).resolve()
    if backup_root != path and backup_root not in path.parents:
        raise FileNotFoundError("Backup not found")
    if not path.exists() or not path.is_file() or path.suffixes[-2:] != [".tar", ".gz"]:
        raise FileNotFoundError("Backup not found")
    return path


def delete_user_backup(backup_file: str) -> str:
    path = user_backup_path(backup_file)
    path.unlink()
    return str(path)


def delete_user_restore_backup(backup_file: str) -> str:
    path = user_backup_path(backup_file)
    restore_dir = _user_restore_dir().resolve()
    if restore_dir not in path.parents:
        raise FileNotFoundError("Backup not found")
    path.unlink()
    return str(path)


def discard_local_copy(archive: str) -> None:
    """Remove an archive that has just been uploaded off-server.

    A backup sent to S3 or SFTP used to stay on this disk as well, so a
    schedule with a destination kept a week of full-account archives in both
    places - on .88, 32 archives and 6 GB on the very disk the destination was
    meant to spare (operator, 2026-09-27). Call this only once the upload has
    succeeded: when it fails, the local archive is the one copy there is, and
    it stays. Never reaches outside the backup folder.
    """
    try:
        user_backup_path(archive).unlink()
    except FileNotFoundError:
        pass


def prune_user_backups(username: str, keep: int) -> None:
    keep = max(int(keep or 1), 1)
    for old_backup in list_user_backups(username)[keep:]:
        Path(old_backup).unlink(missing_ok=True)


def read_backup_manifest(backup_file: str) -> dict:
    archive = user_backup_path(backup_file)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(BACKUP_MANIFEST)
        except KeyError as exc:
            raise ValueError("Backup manifest not found") from exc
        if member.size > 2 * 1024 * 1024:
            raise ValueError("Backup manifest is too large")
        source = tar.extractfile(member)
        if source is None:
            raise ValueError("Backup manifest cannot be read")
        return json.loads(source.read().decode("utf-8"))


def _safe_extract_prefix(archive: Path, prefix: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    prefix = prefix.strip("/")
    with tarfile.open(archive, "r:gz") as tar:
        for original in tar.getmembers():
            if original.name == prefix:
                continue
            if not original.name.startswith(prefix + "/"):
                continue
            if original.islnk():
                continue
            original.name = original.name[len(prefix) + 1:]
            if not original.name:
                continue

            def safe_filter(member: tarfile.TarInfo, dest_path: str):
                if member.islnk():
                    return None
                return tarfile.data_filter(member, dest_path)

            try:
                tar.extract(original, path=str(destination), filter=safe_filter)
            except TypeError:
                member_path = (destination / original.name).resolve()
                if destination != member_path and destination not in member_path.parents:
                    raise ValueError("Backup archive contains unsafe paths")
                if original.issym():
                    link_path = (member_path.parent / original.linkname).resolve()
                    if destination != link_path and destination not in link_path.parents:
                        raise ValueError("Backup archive contains unsafe links")
                tar.extract(original, str(destination))


def _extract_member_to_file(archive: Path, member_name: str, output_dir: Path) -> Optional[Path]:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(member_name)
        except KeyError:
            return None
        if not member.isfile() or member.size > MAX_UPLOAD_BYTES:
            raise ValueError("Invalid SQL backup member")
        source = tar.extractfile(member)
        if source is None:
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / Path(member_name).name
        with target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        return target


def restore_user_backup(backup_file: str, db) -> dict:
    archive = user_backup_path(backup_file)
    manifest = read_backup_manifest(str(archive))
    if manifest.get("kind") not in RESTORABLE_BACKUP_KINDS:
        raise ValueError("This is not a spanel or opanel user backup")
    user_info = manifest.get("user") or {}
    username = user_info.get("username") or ""
    if not PANEL_USERNAME_RE.fullmatch(username):
        raise ValueError("Invalid user in backup")

    user = db.query(User).filter(User.username == username).first()
    created_user = False
    generated_password = ""
    if user is None:
        email = user_info.get("email") or f"{username}@users.spanel.invalid"
        if email.endswith(_PLACEHOLDER_EMAIL_SUFFIXES):
            email = f"{username}@users.spanel.invalid"
        if db.query(User).filter(User.email == email).first():
            email = f"{username}-{secrets.token_hex(4)}@users.spanel.invalid"
        # An archive is untrusted input: the manifest is written by whoever
        # produced the .tar.gz, and RESTORABLE_BACKUP_KINDS deliberately accepts
        # archives from another panel. Taking the role from it let a file mint a
        # panel admin, and taking hashed_password let it choose that admin's
        # password. The panel admin is the principal the root sudo helper obeys,
        # so that was a file deciding who gets the server.
        #
        # Restore always creates an end_user with a credential generated here.
        # An operator who genuinely wants to promote the account does it through
        # PATCH /api/users/{id}, which is audited and bumps token_version.
        # da_import._ensure_panel_user_record has always done it this way.
        generated_password = secrets.token_urlsafe(18)
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(generated_password),
            role=Role.end_user.value,
            is_active=bool(user_info.get("is_active", True)),
            website_limit=int(user_info.get("website_limit") or 5),
            storage_limit_mb=int(user_info.get("storage_limit_mb") or 1024),
            # Its own SFTP secret, like every other creation path. Leaving this
            # NULL would claim the Linux password is the panel password, which
            # is not true here - and NULL is what tells the panel an account is
            # still sharing one secret.
            sftp_password_set_at=datetime.utcnow(),
        )
        db.add(user)
        db.flush()
        created_user = True
        site_users.ensure_panel_user(user.username, site_users.generate_login_password())
    else:
        site_users.ensure_panel_user(user.username)

    restored_websites = []
    # Stage under the panel-owned import area: the helper only copies site files
    # into place from there (the site dir belongs to its Linux user, not spanel).
    site_users.IMPORT_STAGE_BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="spanel-user-restore-", dir=str(site_users.IMPORT_STAGE_BASE)
    ) as tmp:
        tmp_dir = Path(tmp)
        for site_info in manifest.get("websites") or []:
            domain = (site_info.get("domain") or "").strip().lower()
            if not site_users.DOMAIN_RE.fullmatch(domain):
                raise ValueError(f"Invalid domain in backup: {domain}")
            php_version = site_info.get("php_version") or settings.default_php_version
            app_type = site_info.get("app_type") or "wordpress"
            if app_type not in {"wordpress", "php", "static"}:
                app_type = "wordpress"
            nginx_rewrite_mode = site_info.get("nginx_rewrite_mode")
            if nginx_rewrite_mode not in {"none", "front_controller", "laravel", "codeigniter", "seohburl"}:
                nginx_rewrite_mode = "front_controller" if app_type in {"wordpress", "php"} else "none"
            document_root = site_users.validate_document_root(site_info.get("document_root") or "public_html")
            backup_aliases = [
                (alias or "").strip().lower()
                for alias in (site_info.get("aliases") or [])
                if (alias or "").strip()
            ]
            backup_aliases = sorted({alias for alias in backup_aliases if alias != domain})
            linux_user = site_users.linux_user_for_panel_username(user.username)
            root_path = site_users.site_root_for_panel_user(user.username, domain)
            runtime_php_version = php_version if app_type in {"wordpress", "php"} else None
            site_users.ensure_site_runtime(domain, root_path, runtime_php_version, linux_user)
            site_stage = tmp_dir / "site" / domain
            _safe_extract_prefix(archive, f"sites/{domain}/site", site_stage)
            site_users.import_site_files(root_path, linux_user, str(site_stage))
            # Backups from older spanel releases may contain public/. Normalize
            # the document root after extraction before rewriting the vhost.
            site_users.ensure_site_runtime(domain, root_path, runtime_php_version, linux_user)
            site_users.ensure_document_root(root_path, document_root, linux_user)

            website = db.query(Website).filter(Website.domain == domain).first()
            created_site = False
            if website is None:
                website = Website(
                    domain=domain,
                    owner_id=user.id,
                    root_path=root_path,
                    document_root=document_root,
                    linux_user=linux_user,
                    php_version=php_version,
                    app_type=app_type,
                    ssl_enabled=False,
                    status=site_info.get("status") or "active",
                    nginx_custom=site_info.get("nginx_custom") or "",
                    nginx_config_mode="managed",
                    nginx_rewrite_mode=nginx_rewrite_mode,
                    waf_enabled=bool(site_info.get("waf_enabled", True)),
                    waf_default_rules=site_info.get("waf_default_rules") or "",
                    waf_custom_rules=site_info.get("waf_custom_rules") or "",
                    http_flood_enabled=bool(site_info.get("http_flood_enabled", False)),
                    http_flood_config=site_info.get("http_flood_config") or "",
                )
                db.add(website)
                db.flush()
                created_site = True
            else:
                # The alias loop below already refuses a domain that belongs to
                # someone else (_hostname_conflicts, a few lines down), but the
                # primary domain never got the same check: restoring an archive
                # that names a live domain silently moved that Website row to the
                # archive's user and rewrote the vhost to serve their files.
                if website.owner_id != user.id:
                    raise ValueError(f"Domain already belongs to another user: {domain}")
                website.root_path = root_path
                website.document_root = document_root
                website.linux_user = linux_user
                website.php_version = php_version
                website.app_type = app_type
                website.status = site_info.get("status") or "active"
                website.nginx_custom = site_info.get("nginx_custom") or ""
                website.nginx_config_mode = "managed"
                website.nginx_rewrite_mode = nginx_rewrite_mode
                website.waf_enabled = bool(site_info.get("waf_enabled", True))
                website.waf_default_rules = site_info.get("waf_default_rules") or ""
                website.waf_custom_rules = site_info.get("waf_custom_rules") or ""
                website.http_flood_enabled = bool(site_info.get("http_flood_enabled", False))
                website.http_flood_config = site_info.get("http_flood_config") or ""
                db.flush()

            existing_aliases = {
                alias.domain: alias
                for alias in db.query(WebsiteAlias).filter(WebsiteAlias.website_id == website.id).all()
            }
            for alias_domain in backup_aliases:
                if _hostname_conflicts(db, alias_domain, exclude_website_id=website.id):
                    raise ValueError(f"Alias domain already belongs to another website: {alias_domain}")
                if alias_domain not in existing_aliases:
                    db.add(WebsiteAlias(website_id=website.id, domain=alias_domain, mode="alias"))
            for alias_domain, alias_obj in existing_aliases.items():
                if alias_domain not in backup_aliases:
                    db.delete(alias_obj)

            db_info = site_info.get("database") or None
            if db_info:
                db_name = db_info.get("db_name")
                db_user = db_info.get("db_user")
                db_password = db_info.get("db_password") or mariadb.random_password()
                conflict = db.query(DatabaseAccount).filter(
                    DatabaseAccount.db_name == db_name,
                    DatabaseAccount.website_id != website.id,
                ).first()
                if conflict:
                    raise ValueError(f"Database name already belongs to another website: {db_name}")
                # A restore recreates the account this archive already owned.
                mariadb.create_database_credentials(
                    db_name, db_user, db_password, allow_existing_user=True
                )
                sql_member = db_info.get("sql_member") or f"databases/{domain}.sql"
                sql_path = _extract_member_to_file(archive, sql_member, tmp_dir)
                if sql_path:
                    mariadb.import_database(db_name, str(sql_path))
                db_account = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
                if db_account is None:
                    db.add(DatabaseAccount(
                        owner_id=website.owner_id,
                        website_id=website.id,
                        db_name=db_name,
                        db_user=db_user,
                        db_password=encrypt(db_password),
                    ))
                else:
                    db_account.owner_id = website.owner_id
                    db_account.db_name = db_name
                    db_account.db_user = db_user
                    db_account.db_password = encrypt(db_password)

            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Could not write WAF rules").strip())
            if website.http_flood_enabled:
                result = nginx.sync_http_flood_zones(db.query(Website).all())
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "Could not write HTTP flood zones").strip())
            nginx.rewrite_vhost(
                domain,
                root_path,
                app_type=app_type,
                php_version=php_version,
                custom_directives=website.nginx_custom or "",
                php_fpm_socket_override=site_users.site_php_fpm_socket(linux_user, root_path, runtime_php_version),
                waf_enabled=website.waf_enabled,
                http_flood_enabled=website.http_flood_enabled,
                http_flood_config=website.http_flood_config or "",
                document_root=document_root,
                rewrite_mode=nginx_rewrite_mode,
                aliases=backup_aliases,
            )
            if not website.http_flood_enabled:
                result = nginx.sync_http_flood_zones(db.query(Website).all())
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "Could not write HTTP flood zones").strip())
            wordpress.fix_permissions(root_path, linux_user)
            restored_websites.append({"domain": domain, "created": created_site})

        restored_apps = _restore_applications(manifest, user, db, archive, tmp_dir)

        from app.services import mail

        def stage_mail(domain: str, local: str) -> Optional[Path]:
            stage = tmp_dir / "mail" / domain / local
            _safe_extract_prefix(archive, f"mail/{domain}/{local}", stage)
            return stage if any(stage.iterdir()) else None

        restored_mail = mail.restore_manifest(db, user, manifest.get("mail") or {}, stage_mail)

    db.commit()
    # DNS Manager: the restored domains get their zones, owned by the account.
    from app.services import dns

    dns.sync_quietly(db)
    # Email: the restored mailboxes reach the mail server, and their DKIM the zones.
    mail.sync_quietly(db)
    return {
        "created_user": created_user,
        "username": username,
        # Only set when restore created the account. The archive no longer
        # chooses the password, so the operator needs this to hand it over.
        "generated_password": generated_password,
        "role": user.role,
        "websites": restored_websites,
        "applications": restored_apps,
        "mailboxes": restored_mail,
    }


def _restore_applications(manifest: dict, user: User, db, archive: Path, tmp_dir: Path) -> list[dict]:
    """Bring back a user's applications, stopped.

    Deliberately not started: the images may not be pulled yet, and a customer
    should look at what came back before it starts answering on their domain. The
    panel's Deploy button does the rest.
    """
    entries = manifest.get("applications") or []
    if not entries:
        return []
    from app.services import addons, site_apps

    if not addons.is_installed(addons.APPLICATION):
        # Recorded rather than dropped, so the operator knows what this backup
        # holds and can install the addon and restore again.
        return [{"name": entry.get("name"), "skipped": "The Application addon is not installed"} for entry in entries]

    results: list[dict] = []
    for entry in entries:
        name = (entry.get("name") or "").strip()
        try:
            name = site_apps.validate_name(name)
            kind = entry.get("kind") if entry.get("kind") in site_apps.APP_KINDS else "node"
        except ValueError as exc:
            results.append({"name": name, "error": str(exc)})
            continue

        app = db.query(SiteApp).filter(SiteApp.owner_id == user.id, SiteApp.name == name).first()
        created = app is None
        if created:
            try:
                port = site_apps.allocate_port(db, entry.get("port"))
            except ValueError:
                port = site_apps.allocate_port(db, None)
            app = SiteApp(owner_id=user.id, name=name, kind=kind, port=port, status="stopped")
            db.add(app)
        app.kind = kind
        app.start_kind = entry.get("start_kind")
        app.start_arg = entry.get("start_arg")
        app.node_major = entry.get("node_major")
        app.image = entry.get("image")
        app.container_port = int(entry.get("container_port") or 3000)
        app.cpu_limit = entry.get("cpu_limit") or "1"
        app.env = entry.get("env") or ""
        app.compose_source = entry.get("compose_source") or ""
        app.web_service = entry.get("web_service")
        app.memory_limit_mb = int(entry.get("memory_limit_mb") or 512)
        app.autostart = bool(entry.get("autostart", True))
        app.status = "stopped"
        db.flush()

        record = {"name": name, "created": created, "data_restored": False}
        member = entry.get("payload_member")
        if member:
            payload = _extract_member_to_file(archive, member, tmp_dir)
            if payload:
                # The helper only reads from the backup tree, so the extracted
                # payload has to live there rather than in /tmp — and inside the
                # user's own directory, the one level of it the panel may write.
                staging_dir = _user_backup_dir(user.username)
                staged = staging_dir / f".restore-{name}.tar"
                try:
                    _ensure_user_dir(staging_dir)
                    staged.write_bytes(payload.read_bytes())
                    site_apps.import_payload(app, str(staged))
                    record["data_restored"] = True
                except (OSError, RuntimeError, ValueError) as exc:
                    record["error"] = str(exc)[-500:]
                    logger.warning("Could not restore application %s: %s", name, exc)
                finally:
                    staged.unlink(missing_ok=True)
        elif entry.get("payload_error"):
            record["error"] = f"This backup carries no data: {entry['payload_error']}"
        results.append(record)
    return results


def save_uploaded_backup(domain: str, filename: str, source_file) -> str:
    backup_dir = (Path(settings.backup_root).resolve() / domain).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if not safe_name.endswith(".tar.gz"):
        raise ValueError("Only .tar.gz backup files are supported")
    target = (backup_dir / safe_name).resolve()
    if backup_dir not in target.parents:
        raise ValueError("Invalid backup filename")
    written = 0
    with target.open("wb") as buffer:
        while chunk := source_file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                target.unlink(missing_ok=True)
                raise ValueError("Backup file is too large")
            buffer.write(chunk)
    return str(target)


def save_uploaded_user_backup(filename: str, source_file) -> str:
    backup_dir = _ensure_user_dir(_user_restore_dir()).resolve()
    safe_name = Path(filename).name
    if not safe_name.endswith(".tar.gz"):
        raise ValueError("Only .tar.gz backup files are supported")
    target = (backup_dir / safe_name).resolve()
    if backup_dir not in target.parents:
        raise ValueError("Invalid backup filename")
    if target.exists():
        stem = safe_name[:-7]
        suffix = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        target = (backup_dir / f"{stem}-{suffix}-{secrets.token_hex(3)}.tar.gz").resolve()
    written = 0
    with target.open("wb") as buffer:
        while chunk := source_file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                target.unlink(missing_ok=True)
                raise ValueError("Backup file is too large")
            buffer.write(chunk)
    return str(target)


def restore_backup(website: Website, backup_file: str) -> str:
    archive = backup_path(website.domain, backup_file)
    destination = Path(website.root_path).resolve()

    # Single-pass extraction with PEP 706 data filter (Python 3.12+).
    # The data filter rejects path traversal, absolute paths, and unsafe
    # symlinks at the tarfile layer itself.
    with tarfile.open(archive, "r:gz") as tar:
        members = list(tar.getmembers())
        has_site_prefix = any(m.name == "site" or m.name.startswith("site/") for m in members)

        def safe_filter(member: tarfile.TarInfo, dest_path: str):
            # Hard-links inside backups are uncommon and risky; refuse outright.
            if member.islnk():
                return None
            if member.name.startswith("database/"):
                return None
            if has_site_prefix:
                if member.name == "site":
                    return None
                if not member.name.startswith("site/"):
                    return None
                member.name = member.name[len("site/"):]
            return tarfile.data_filter(member, dest_path)

        try:
            tar.extractall(path=str(destination), filter=safe_filter)
        except TypeError:
            # Older Python (<3.12) without the filter parameter — fall back to
            # manual extraction with the existing safety check.
            _ensure_safe_tar(archive, destination)
            for member in members:
                if member.name.startswith("database/"):
                    continue
                if has_site_prefix:
                    if member.name == "site":
                        continue
                    if not member.name.startswith("site/"):
                        continue
                    member.name = member.name[len("site/"):]
                tar.extract(member, str(destination))
    return str(destination)


def backup_path(domain: str, backup_file: str) -> Path:
    backup_root = (Path(settings.backup_root).resolve() / domain).resolve()
    path = Path(backup_file).resolve()
    if backup_root not in path.parents or not path.exists() or path.suffixes[-2:] != [".tar", ".gz"] or not path.is_file():
        raise FileNotFoundError("Backup not found")
    return path


def _ensure_safe_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = (destination / member.name).resolve()
            if destination != member_path and destination not in member_path.parents:
                raise ValueError("Backup archive contains unsafe paths")
            if member.issym() or member.islnk():
                link_path = (member_path.parent / member.linkname).resolve()
                if destination != link_path and destination not in link_path.parents:
                    raise ValueError("Backup archive contains unsafe links")


def delete_backup(domain: str, backup_file: str) -> str:
    path = backup_path(domain, backup_file)
    path.unlink()
    return str(path)


def list_backups(domain: str) -> List[str]:
    backup_dir = Path(settings.backup_root) / domain
    if settings.command_dry_run:
        return []
    if not backup_dir.exists():
        return []
    return [str(path) for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True)]


def _load_private_key(private_key: str, password: Optional[str] = None):
    key_stream = StringIO(private_key)
    key_classes = (
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.Ed25519Key,
        paramiko.DSSKey,
    )
    last_error = None
    for key_class in key_classes:
        key_stream.seek(0)
        try:
            return key_class.from_private_key(key_stream, password=password or None)
        except Exception as exc:  # pragma: no cover - depends on key type
            last_error = exc
    raise ValueError(f"Cannot load SFTP private key: {last_error}")


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    remote_dir = posixpath.normpath(remote_dir or ".")
    if remote_dir in {".", "/"}:
        return
    parts = [part for part in remote_dir.split("/") if part]
    current = "/" if remote_dir.startswith("/") else "."
    for part in parts:
        current = posixpath.join(current, part) if current != "/" else f"/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


class SftpHostKeyMismatch(RuntimeError):
    """Raised when a pinned host key no longer matches the server."""


def _fingerprint(key: PKey) -> str:
    """SHA256:base64 fingerprint, identical to OpenSSH ``ssh-keygen -lf``."""
    digest = hashlib.sha256(key.asbytes()).digest()
    import base64 as _b64

    return "SHA256:" + _b64.b64encode(digest).rstrip(b"=").decode("ascii")


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Paramiko policy that captures the server host key on first connect.

    When ``expected`` is ``None`` (TOFU bootstrap) the policy accepts the key
    and stores it on ``self.captured`` for the caller to persist. When
    ``expected`` is set the policy refuses to fall back here at all because
    the verification has already happened in the caller; this is purely a
    safety net to surface a clear error if logic upstream changes.
    """

    def __init__(self, expected_type: Optional[str], expected_fingerprint: Optional[str]):
        self.expected_type = expected_type
        self.expected_fingerprint = expected_fingerprint
        self.captured_type: Optional[str] = None
        self.captured_fingerprint: Optional[str] = None

    def missing_host_key(self, client, hostname, key):  # type: ignore[override]
        captured = _fingerprint(key)
        if self.expected_fingerprint:
            # Verification path: a key was pinned but Paramiko did not find a
            # matching entry. Refuse the connection.
            raise SftpHostKeyMismatch(
                f"SFTP host key mismatch for {hostname}: "
                f"expected {self.expected_type or '?'} {self.expected_fingerprint}, "
                f"got {key.get_name()} {captured}"
            )
        # TOFU bootstrap.
        self.captured_type = key.get_name()
        self.captured_fingerprint = captured
        logger.warning(
            "SFTP TOFU bootstrap: pinning %s host key %s %s",
            hostname,
            self.captured_type,
            self.captured_fingerprint,
        )


def upload_to_sftp(
    local_file: str,
    *,
    host: str,
    port: int,
    username: str,
    remote_path: str,
    remote_name: Optional[str] = None,
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    expected_host_key_type: Optional[str] = None,
    expected_host_key_fingerprint: Optional[str] = None,
) -> dict:
    """Upload ``local_file`` to ``host:port`` via SFTP.

    Host key handling:
      * If a fingerprint is pinned, the server's key is compared against it
        before any auth bytes are sent. A mismatch raises
        :class:`SftpHostKeyMismatch`.
      * If no fingerprint is pinned yet (TOFU bootstrap), the server's key is
        captured and returned. The caller must persist it.

    Returns a dict ``{"remote_file": ..., "host_key_type": ...,
    "host_key_fingerprint": ...}``.
    """
    local_path = Path(local_file).resolve()
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError("Local backup file not found")
    if not password and not private_key:
        raise ValueError("SFTP password or private key is required")

    pkey = _load_private_key(private_key, password=password) if private_key else None
    remote_dir = posixpath.normpath(remote_path.strip() or ".")
    # The caller may want it stored under a rotating name - see
    # backup_s3.stored_name, which both destinations share.
    remote_file = posixpath.join(remote_dir, remote_name or local_path.name)

    captured_type: Optional[str] = expected_host_key_type
    captured_fp: Optional[str] = expected_host_key_fingerprint

    client = paramiko.SSHClient()
    # Note: we deliberately do NOT call load_system_host_keys() because the
        # daemon runs as the spanel service user with no interactive shell
        # history; trusting its known_hosts blindly would defeat the pinning model.
    if expected_host_key_fingerprint:
        # Strict: the only acceptable key is the one pinned in the DB.
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            host_keys = client.get_host_keys()
            host_key_obj = _decode_pinned_key(
                expected_host_key_type or "", expected_host_key_fingerprint
            )
            if host_key_obj is not None:
                host_keys.add(host, expected_host_key_type or host_key_obj.get_name(), host_key_obj)
        except Exception:  # pragma: no cover - decoding fallback
            pass
        # Even if we could not pre-load the key (e.g. only fingerprint stored),
        # the missing_host_key policy below performs the comparison itself.
        client.set_missing_host_key_policy(
            _PinnedHostKeyPolicy(expected_host_key_type, expected_host_key_fingerprint)
        )
    else:
        # TOFU bootstrap path: capture the server key for the caller.
        client.set_missing_host_key_policy(
            _PinnedHostKeyPolicy(None, None)
        )

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password if not pkey else None,
            pkey=pkey,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
        # Verify the server key we actually negotiated against the pin. The
        # policy catches the "missing" case; this catches the case where the
        # key was already in the local known_hosts and the policy was not
        # called at all.
        transport = client.get_transport()
        if transport is None:
            raise SSHException("SFTP transport unavailable")
        server_key = transport.get_remote_server_key()
        server_fp = _fingerprint(server_key)
        server_type = server_key.get_name()
        if expected_host_key_fingerprint:
            if server_fp != expected_host_key_fingerprint:
                raise SftpHostKeyMismatch(
                    f"SFTP host key mismatch for {host}: "
                    f"expected {expected_host_key_type or '?'} {expected_host_key_fingerprint}, "
                    f"got {server_type} {server_fp}"
                )
            captured_type = expected_host_key_type or server_type
            captured_fp = expected_host_key_fingerprint
        else:
            captured_type = server_type
            captured_fp = server_fp
        with client.open_sftp() as sftp:
            _ensure_remote_dir(sftp, remote_dir)
            sftp.put(str(local_path), remote_file)
    finally:
        client.close()
    return {
        "remote_file": remote_file,
        "host_key_type": captured_type,
        "host_key_fingerprint": captured_fp,
    }


def _decode_pinned_key(key_type: str, fingerprint: str) -> Optional[PKey]:
    """We store only the fingerprint, so reconstructing a PKey is not always
    possible. Returns ``None`` to indicate the caller should rely on the
    in-policy fingerprint comparison instead."""
    return None
