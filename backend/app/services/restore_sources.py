"""Where a restore comes from, asked the way DirectAdmin asks: pick a source,
fill in what that source needs, tick the accounts, restore (operator,
2026-09-27).

Three kinds of source:

- local: the panel's own folder, <backup_root>/users - the scheduled and
  manual full-user backups in users/<account>/, plus uploads and copies
  fetched earlier. The page before this one listed only users/restore, so the
  scheduled backups sitting on the server could not be restored from it;
- target: a destination saved under Backup Destination, S3 or SFTP;
- remote: another server over SFTP, FTP or FTPS, with details typed in for
  this restore and never stored.

A listing never opens an archive. Reading the manifest of an archive written
before the manifest moved to the first member is a full decompress, and
OPanel measured 159 s for 17 archives. The account comes from the folder or
the file name; the restore reads the manifest and trusts only that.

An SFTP server's key is checked before the password is sent: a saved
destination against the key pinned on its first upload, another server
against the key seen when it was listed.
"""
from __future__ import annotations

import ftplib
import posixpath
import re
import shutil
import socket
import ssl
import stat as statmode
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import paramiko

from app.core.config import settings
from app.core.secrets import decrypt
from app.models.entities import SftpBackupTarget
from app.services import backup, backup_s3

DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21}
TIMEOUT = 30
LIST_LIMIT = 1000
# Folders under users/ that hold archives of any account, not one account.
SHARED_FOLDERS = {"restore", "uploads"}
# Room left on the disk after a download, so a restore has space to unpack.
DISK_MARGIN = 1024 ** 3
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RestoreSourceError(ValueError):
    """Something about the source the operator can act on: a refused login,
    a folder that is not there, a destination that was deleted."""


def _iso(timestamp: float | int | None) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp else ""


def _row(name: str, key: str, size, modified: str, username: str = "") -> dict:
    return {"name": name, "key": key, "size": int(size or 0), "modified": modified or "",
            "username": username or backup.username_from_archive_name(name)}


def _newest_first(rows: list[dict]) -> list[dict]:
    rows.sort(key=lambda row: row["modified"], reverse=True)
    return rows[:LIST_LIMIT]


def users_dir() -> Path:
    return Path(settings.backup_root) / "users"


def remote_folder(path: str) -> str:
    folder = posixpath.normpath((path or "").strip() or "/")
    if _CONTROL.search(folder):
        raise RestoreSourceError("The folder path contains a control character")
    return folder


# --- this server --------------------------------------------------------------

def list_local() -> list[dict]:
    root = users_dir()
    if settings.command_dry_run or not root.is_dir():
        return []
    rows = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink()):
        owner = "" if folder.name in SHARED_FOLDERS else folder.name
        for path in folder.glob("*.tar.gz"):
            if path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
            rows.append(_row(path.name, f"{folder.name}/{path.name}", info.st_size, _iso(info.st_mtime), owner))
    return _newest_first(rows)


def local_path(key: str) -> str:
    """The archive a local key names: <folder>/<file>.tar.gz under users/,
    nothing else."""
    parts = (key or "").split("/")
    if (len(parts) != 2 or any(part in {"", ".", ".."} or _CONTROL.search(part) for part in parts)
            or not parts[1].endswith(".tar.gz")):
        raise RestoreSourceError("Not a backup in the panel's backup folder")
    path = users_dir() / parts[0] / parts[1]
    if path.is_symlink():
        raise RestoreSourceError("Not a backup in the panel's backup folder")
    try:
        return str(backup.user_backup_path(str(path)))
    except FileNotFoundError as exc:
        raise RestoreSourceError(f"{parts[1]} is no longer in the backup folder") from exc


# --- SFTP -----------------------------------------------------------------------

@contextmanager
def _sftp(host: str, port: int, username: str, *, password: str = "", private_key: str = "",
          pinned: str = "") -> Iterator[tuple[paramiko.SFTPClient, dict]]:
    """An SFTP session. The server's key is read and compared with `pinned`
    before any credential leaves this machine."""
    try:
        sock = socket.create_connection((host, port), timeout=TIMEOUT)
    except OSError as exc:
        raise RestoreSourceError(f"Cannot reach {host}:{port} ({exc.strerror or type(exc).__name__})") from None
    transport = paramiko.Transport(sock)
    try:
        transport.banner_timeout = TIMEOUT
        try:
            transport.start_client(timeout=TIMEOUT)
        except paramiko.SSHException as exc:
            raise RestoreSourceError(f"{host}:{port} is not an SFTP server ({exc})") from None
        key = transport.get_remote_server_key()
        seen = {"type": key.get_name(), "fingerprint": backup._fingerprint(key)}
        if pinned and pinned != seen["fingerprint"]:
            raise backup.SftpHostKeyMismatch(
                f"The server at {host} presented a different host key: expected {pinned}, "
                f"got {seen['fingerprint']}. Nothing was sent to it.")
        try:
            if private_key:
                transport.auth_publickey(username, backup._load_private_key(private_key, password or None))
            else:
                transport.auth_password(username, password)
        except paramiko.AuthenticationException:
            raise RestoreSourceError("The server refused the username or password") from None
        try:
            client = paramiko.SFTPClient.from_transport(transport)
        except (paramiko.SSHException, paramiko.SFTPError, EOFError, OSError) as exc:
            # Typically an account whose shell prints a message (nologin) or
            # has no SFTP subsystem: the login worked, SFTP did not start.
            raise RestoreSourceError(f"Signed in, but the server did not start SFTP for this account ({exc})") from None
        if client is None:
            raise RestoreSourceError("The server did not open an SFTP session")
        client.get_channel().settimeout(TIMEOUT * 4)
        try:
            yield client, seen
        except (paramiko.SSHException, paramiko.SFTPError, EOFError, socket.timeout) as exc:
            raise RestoreSourceError(f"SFTP: the connection failed partway ({exc or type(exc).__name__})") from None
    finally:
        transport.close()


def _sftp_list(client: paramiko.SFTPClient, folder: str) -> list[dict]:
    try:
        entries = client.listdir_attr(folder)
    except OSError:
        raise RestoreSourceError(f"No folder {folder} on the server, or it cannot be read") from None
    return [_row(entry.filename, posixpath.join(folder, entry.filename), entry.st_size, _iso(entry.st_mtime))
            for entry in entries
            if entry.filename.endswith(".tar.gz") and statmode.S_ISREG(entry.st_mode or 0)]


def _target_sftp(target: SftpBackupTarget):
    return _sftp(target.host, int(target.port or 22), target.username,
                 password=decrypt(target.password) if target.password else "",
                 private_key=decrypt(target.private_key) if target.private_key else "",
                 pinned=target.host_key_fingerprint or "")


# --- FTP --------------------------------------------------------------------------

def _ftp_open(source: dict) -> ftplib.FTP:
    if source["protocol"] == "ftps":
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        ftp: ftplib.FTP = ftplib.FTP_TLS(context=context, timeout=TIMEOUT)
    else:
        ftp = ftplib.FTP(timeout=TIMEOUT)
    host, port = source["host"], int(source.get("port") or DEFAULT_PORTS[source["protocol"]])
    try:
        ftp.connect(host, port)
    except (OSError, EOFError, ftplib.Error) as exc:
        raise RestoreSourceError(f"Cannot reach {host}:{port} ({exc})") from None
    try:
        ftp.login(source["username"], source["password"])
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
    except ssl.SSLError as exc:
        ftp.close()
        raise RestoreSourceError(f"FTPS: the server's certificate could not be verified ({exc.reason})") from None
    except (OSError, EOFError, ftplib.Error) as exc:
        ftp.close()
        raise RestoreSourceError(f"The server refused the login ({exc})") from None
    return ftp


def _ftp_close(ftp: ftplib.FTP) -> None:
    try:
        ftp.quit()
    except (OSError, EOFError, ftplib.Error):
        ftp.close()


def _mlsd_time(value: str | None) -> str:
    try:
        return datetime.strptime((value or "")[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return ""


def _ftp_list(ftp: ftplib.FTP, folder: str) -> list[dict]:
    try:
        return [_row(name, posixpath.join(folder, name), facts.get("size"), _mlsd_time(facts.get("modify")))
                for name, facts in ftp.mlsd(folder, facts=["type", "size", "modify"])
                if facts.get("type") == "file" and name.endswith(".tar.gz")]
    except ftplib.error_perm as exc:
        if not str(exc).startswith(("500", "502")):  # anything but "no MLSD here"
            raise RestoreSourceError(f"No folder {folder} on the server, or it cannot be read ({exc})") from None
    # An old server without MLSD: names first, then a size and a date each.
    try:
        names = [posixpath.basename(name) for name in ftp.nlst(folder)]
    except ftplib.error_perm as exc:
        raise RestoreSourceError(f"No folder {folder} on the server, or it cannot be read ({exc})") from None
    rows = []
    for name in names:
        if not name.endswith(".tar.gz"):
            continue
        path = posixpath.join(folder, name)
        try:
            size = ftp.size(path) or 0
        except ftplib.error_perm:
            continue  # a folder whose name ends in .tar.gz
        try:
            modified = _mlsd_time(ftp.sendcmd(f"MDTM {path}").split()[-1])
        except (ftplib.error_perm, IndexError):
            modified = ""
        rows.append(_row(name, path, size, modified))
    return rows


# --- one entry point for the page --------------------------------------------

def _target(db, target_id) -> SftpBackupTarget:
    target = db.query(SftpBackupTarget).filter(
        SftpBackupTarget.id == target_id, SftpBackupTarget.is_active == True,  # noqa: E712
    ).first()
    if not target:
        raise RestoreSourceError("That backup destination no longer exists")
    return target


def describe(source: dict, db=None) -> str:
    """The source in a line for the audit log - never a password."""
    if source["kind"] == "local":
        return "this server"
    if source["kind"] == "target":
        target = db.get(SftpBackupTarget, source.get("target_id")) if db is not None else None
        return f"destination {target.name if target else source.get('target_id')}"
    return f"{source['protocol']}://{source['username']}@{source['host']}{remote_folder(source.get('path'))}"


def list_source(source: dict, db) -> dict:
    """What can be restored from this source, newest first, plus where that
    is and - for another SFTP server - the key it presented."""
    kind = source["kind"]
    if kind == "local":
        return {"items": list_local(), "location": str(users_dir()), "host_key": None}
    if kind == "target":
        target = _target(db, source.get("target_id"))
        if (target.kind or "sftp") == "s3":
            try:
                rows = [_row(row["name"], row["key"], row["size"], row["modified"]) for row in backup_s3.listing(target)]
            except backup_s3.S3Error as exc:
                raise RestoreSourceError(str(exc)) from None
            prefix = (target.prefix or "").strip("/")
            return {"items": _newest_first(rows), "location": f"s3://{target.bucket}/{prefix}".rstrip("/"),
                    "host_key": None}
        folder = remote_folder(target.remote_path)
        with _target_sftp(target) as (client, seen):
            rows = _sftp_list(client, folder)
        if not target.host_key_fingerprint:
            # The first connection pins the key, as the first upload would.
            target.host_key_type, target.host_key_fingerprint = seen["type"], seen["fingerprint"]
            db.commit()
        return {"items": _newest_first(rows), "location": f"sftp://{target.host}{folder}", "host_key": None}
    folder = remote_folder(source.get("path"))
    port = int(source.get("port") or DEFAULT_PORTS[source["protocol"]])
    if source["protocol"] == "sftp":
        with _sftp(source["host"], port, source["username"], password=source["password"]) as (client, seen):
            rows = _sftp_list(client, folder)
        return {"items": _newest_first(rows), "location": f"sftp://{source['host']}{folder}", "host_key": seen}
    ftp = _ftp_open(source)
    try:
        rows = _ftp_list(ftp, folder)
    except (ftplib.Error, OSError, EOFError) as exc:
        raise RestoreSourceError(f"FTP: the listing failed ({exc or type(exc).__name__})") from None
    finally:
        _ftp_close(ftp)
    return {"items": _newest_first(rows), "location": f"{source['protocol']}://{source['host']}{folder}",
            "host_key": None}


def push(source: dict, local_file: str, db) -> str:
    """Put an uploaded archive in a destination or on another server, so it
    is listed there (the page's Upload backup, for a source that is not this
    server). Returns where it landed."""
    name = Path(local_file).name
    if source["kind"] == "target":
        target = _target(db, source.get("target_id"))
        if (target.kind or "sftp") == "s3":
            try:
                return backup_s3.upload(target, local_file, remote_name=name)["remote_file"]
            except backup_s3.S3Error as exc:
                raise RestoreSourceError(str(exc)) from None
        folder = remote_folder(target.remote_path)
        with _target_sftp(target) as (client, seen):
            _sftp_put(client, local_file, folder, name)
        if not target.host_key_fingerprint:
            target.host_key_type, target.host_key_fingerprint = seen["type"], seen["fingerprint"]
            db.commit()
        return posixpath.join(folder, name)
    if source["kind"] != "remote":
        raise RestoreSourceError("Nothing to send: the archive is already on this server")
    folder = remote_folder(source.get("path"))
    if source["protocol"] == "sftp":
        port = int(source.get("port") or DEFAULT_PORTS["sftp"])
        with _sftp(source["host"], port, source["username"], password=source["password"],
                   pinned=source.get("host_key") or "") as (client, _seen):
            _sftp_put(client, local_file, folder, name)
        return posixpath.join(folder, name)
    ftp = _ftp_open(source)
    try:
        with open(local_file, "rb") as handle:
            ftp.storbinary(f"STOR {posixpath.join(folder, name)}", handle, blocksize=1024 * 1024)
    except ftplib.error_perm as exc:
        raise RestoreSourceError(f"The server refused the file in {folder} ({exc})") from None
    except (ftplib.Error, OSError, EOFError) as exc:
        raise RestoreSourceError(f"FTP: the upload failed ({exc or type(exc).__name__})") from None
    finally:
        _ftp_close(ftp)
    return posixpath.join(folder, name)


def _sftp_put(client: paramiko.SFTPClient, local_file: str, folder: str, name: str) -> None:
    try:
        client.put(local_file, posixpath.join(folder, name))
    except OSError as exc:
        raise RestoreSourceError(f"Could not write {name} in {folder} on the server ({exc})") from None


def check_key(source: dict, key: str) -> str:
    """Refuse a key this source could not have listed; return its file name."""
    if _CONTROL.search(key or ""):
        raise RestoreSourceError("Not a backup file")
    if source["kind"] == "local":
        return Path(local_path(key)).name
    name = posixpath.basename(key)
    if not name.endswith(".tar.gz") or name != name.strip():
        raise RestoreSourceError("Not a backup file")
    if source["kind"] == "remote" and posixpath.dirname(key) != remote_folder(source.get("path")):
        raise RestoreSourceError("That file is not in the folder that was listed")
    return name


def _room_for(size: int) -> None:
    folder = users_dir()
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if size and free < size + DISK_MARGIN:
        raise RestoreSourceError(
            f"Not enough disk space to download it: {size // 1024 ** 2} MB needed plus 1 GB to unpack, "
            f"{free // 1024 ** 2} MB free")


def fetch(source: dict, key: str, size: int, db) -> tuple[str, bool]:
    """A local archive to restore from, and whether it is a downloaded copy
    that should go once the restore has succeeded."""
    name = check_key(source, key)
    if source["kind"] == "local":
        return local_path(key), False
    _room_for(size)
    if source["kind"] == "target":
        target = _target(db, source.get("target_id"))
        if (target.kind or "sftp") == "s3":
            def pull(destination: str) -> None:
                backup_s3.download(target, key, destination)
            staged = backup.stage_remote_backup(pull, name)
        else:
            with _target_sftp(target) as (client, _seen):
                staged = backup.stage_remote_backup(lambda destination: client.get(key, destination), name)
    elif source["protocol"] == "sftp":
        if not source.get("host_key"):
            raise RestoreSourceError("List the server first, so its key can be checked")
        port = int(source.get("port") or DEFAULT_PORTS["sftp"])
        with _sftp(source["host"], port, source["username"], password=source["password"],
                   pinned=source["host_key"]) as (client, _seen):
            staged = backup.stage_remote_backup(lambda destination: client.get(key, destination), name)
    else:
        ftp = _ftp_open(source)
        try:
            def pull(destination: str) -> None:
                with open(destination, "wb") as handle:
                    ftp.retrbinary(f"RETR {key}", handle.write, blocksize=1024 * 1024)
            staged = backup.stage_remote_backup(pull, name)
        finally:
            _ftp_close(ftp)
    return str(Path(backup.user_restore_dir()) / staged), True
