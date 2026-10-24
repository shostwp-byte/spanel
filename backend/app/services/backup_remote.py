"""One way to talk to a saved backup destination, whatever kind it is.

Uploading already worked for S3 and SFTP, each its own way. Retention,
verification and the panel self-backup need more than an upload - list what
is in one folder, fetch a file, delete one - and need it for every kind, FTP
and FTPS included. This is that small interface:

    list_files(target, subpath)          what is in <base>/<subpath>, newest first
    download(target, key, destination)   one file to a local path
    delete(target, key)                  one file
    upload(target, local, name, subpath) one file, returns where it landed

`key` is whatever list_files returned for the file: an object key for S3,
a path for SFTP and FTP. An SFTP server's key is checked against the pin
before any credential is sent (restore_sources._sftp).
"""
from __future__ import annotations

import ftplib
import posixpath
from pathlib import Path
from typing import Optional

from app.core.secrets import decrypt
from app.services import backup, backup_s3, restore_sources

FTP_KINDS = ("ftp", "ftps")
KINDS = ("sftp", "s3", *FTP_KINDS)


class RemoteError(RuntimeError):
    """The destination refused or could not be reached; phrased for a person."""


def kind_of(target) -> str:
    kind = (getattr(target, "kind", None) or "sftp").lower()
    if kind not in KINDS:
        raise RemoteError(f"Unknown destination kind: {kind}")
    return kind


def _check_key(key: str) -> str:
    if not key or ".." in key.split("/") or any(ord(ch) < 32 for ch in key):
        raise RemoteError("Not a valid file on the destination")
    return key


def folder_for(target, subpath: Optional[str]) -> str:
    """The folder (SFTP/FTP) or key prefix (S3) holding <subpath>."""
    clean = (subpath or "").strip().strip("/")
    if kind_of(target) == "s3":
        return backup_s3.object_key(target.prefix, "", subpath=clean) if (target.prefix or clean) else ""
    base = restore_sources.remote_folder(target.remote_path)
    return posixpath.join(base, clean) if clean else base


def ftp_source(target) -> dict:
    """A saved FTP(S) destination in the shape restore_sources' FTP code takes."""
    return {
        "protocol": kind_of(target),
        "host": target.host,
        "port": int(target.port or restore_sources.DEFAULT_PORTS[kind_of(target)]),
        "username": target.username,
        "password": decrypt(target.password) if target.password else "",
    }


def _ftp(target) -> ftplib.FTP:
    try:
        return restore_sources._ftp_open(ftp_source(target))
    except restore_sources.RestoreSourceError as exc:
        raise RemoteError(str(exc)) from None


def _ftp_makedirs(ftp: ftplib.FTP, folder: str) -> None:
    current = "/" if folder.startswith("/") else ""
    for part in [p for p in folder.split("/") if p]:
        current = posixpath.join(current, part) if current else part
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass  # already there, or not ours to create - STOR will say which


# --- list ------------------------------------------------------------------------

def list_files(target, subpath: Optional[str] = None, *, suffixes: tuple[str, ...] = (".tar.gz",)) -> list[dict]:
    """Files directly in <base>/<subpath> whose name ends in one of `suffixes`,
    newest first: [{name, key, size, modified}]."""
    kind = kind_of(target)
    folder = folder_for(target, subpath)
    rows: list[dict] = []
    if kind == "s3":
        client = backup_s3._client(target)
        prefix = (folder.rstrip("/") + "/") if folder else None
        try:
            for item in client.list_objects(target.bucket, prefix=prefix, recursive=False):
                name = posixpath.basename(item.object_name or "")
                if getattr(item, "is_dir", False) or not name.endswith(suffixes):
                    continue
                rows.append({"name": name, "key": item.object_name, "size": int(item.size or 0),
                             "modified": item.last_modified.isoformat() if item.last_modified else ""})
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(str(backup_s3._wrap(exc))) from None
    elif kind == "sftp":
        try:
            with restore_sources._target_sftp(target) as (client, _seen):
                try:
                    entries = client.listdir_attr(folder)
                except FileNotFoundError:
                    entries = []
                import stat as statmode

                for entry in entries:
                    if statmode.S_ISREG(entry.st_mode or 0) and entry.filename.endswith(suffixes):
                        rows.append({"name": entry.filename, "key": posixpath.join(folder, entry.filename),
                                     "size": int(entry.st_size or 0), "modified": restore_sources._iso(entry.st_mtime)})
        except (restore_sources.RestoreSourceError, backup.SftpHostKeyMismatch) as exc:
            raise RemoteError(str(exc)) from None
    else:
        ftp = _ftp(target)
        try:
            try:
                found = restore_sources._ftp_list(ftp, folder)
            except restore_sources.RestoreSourceError:
                found = []  # no such folder yet: nothing there
            rows = [{"name": r["name"], "key": r["key"], "size": r["size"], "modified": r["modified"]}
                    for r in found if r["name"].endswith(suffixes)]
        finally:
            restore_sources._ftp_close(ftp)
    rows.sort(key=lambda row: row["modified"], reverse=True)
    return rows


# --- delete / download ----------------------------------------------------------

def delete(target, key: str) -> None:
    key = _check_key(key)
    kind = kind_of(target)
    if kind == "s3":
        try:
            backup_s3.remove(target, key)
        except backup_s3.S3Error as exc:
            raise RemoteError(str(exc)) from None
        return
    if kind == "sftp":
        try:
            with restore_sources._target_sftp(target) as (client, _seen):
                client.remove(key)
        except FileNotFoundError:
            return
        except (OSError, restore_sources.RestoreSourceError, backup.SftpHostKeyMismatch) as exc:
            raise RemoteError(f"Could not delete {posixpath.basename(key)} ({exc})") from None
        return
    ftp = _ftp(target)
    try:
        ftp.delete(key)
    except ftplib.error_perm as exc:
        if not str(exc).startswith("550"):
            raise RemoteError(f"Could not delete {posixpath.basename(key)} ({exc})") from None
    except (ftplib.Error, OSError, EOFError) as exc:
        raise RemoteError(f"FTP: the delete failed ({exc})") from None
    finally:
        restore_sources._ftp_close(ftp)


def download(target, key: str, destination: str) -> str:
    key = _check_key(key)
    kind = kind_of(target)
    out = Path(destination)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "s3":
            backup_s3.download(target, key, str(out))
        elif kind == "sftp":
            with restore_sources._target_sftp(target) as (client, _seen):
                client.get(key, str(out))
        else:
            ftp = _ftp(target)
            try:
                with open(out, "wb") as handle:
                    ftp.retrbinary(f"RETR {key}", handle.write, blocksize=1024 * 1024)
            finally:
                restore_sources._ftp_close(ftp)
    except RemoteError:
        out.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - one message whatever the transport
        out.unlink(missing_ok=True)
        raise RemoteError(f"Could not download {posixpath.basename(key)} ({exc})") from None
    return str(out)


# --- upload ------------------------------------------------------------------------

def upload(target, local_file: str, remote_name: str, subpath: Optional[str] = None, *, db=None) -> str:
    """Put one file on the destination. Returns '<target name>:<where>'.
    For SFTP the host key is pinned on first contact (and saved when `db` is given)."""
    path = Path(local_file)
    if not path.is_file():
        raise FileNotFoundError("Local file not found")
    if "/" in remote_name or "\\" in remote_name or ".." in remote_name:
        raise RemoteError("Not a valid remote file name")
    kind = kind_of(target)
    if kind == "s3":
        try:
            result = backup_s3.upload(target, str(path), remote_name=remote_name, subpath=subpath)
        except backup_s3.S3Error as exc:
            raise RemoteError(str(exc)) from None
        return f"{target.name}:{result['remote_file']}"
    if kind == "sftp":
        result = backup.upload_to_sftp(
            str(path), remote_name=remote_name, subpath=subpath, host=target.host, port=target.port,
            username=target.username,
            password=decrypt(target.password) if target.password else None,
            private_key=decrypt(target.private_key) if target.private_key else None,
            remote_path=target.remote_path,
            expected_host_key_type=target.host_key_type,
            expected_host_key_fingerprint=target.host_key_fingerprint,
        )
        if not target.host_key_fingerprint and result.get("host_key_fingerprint") and db is not None:
            target.host_key_type = result["host_key_type"]
            target.host_key_fingerprint = result["host_key_fingerprint"]
            db.commit()
        return f"{target.name}:{result['remote_file']}"
    folder = folder_for(target, subpath)
    remote = posixpath.join(folder, remote_name)
    ftp = _ftp(target)
    try:
        _ftp_makedirs(ftp, folder)
        # Upload under a temporary name and rename, so a listing never shows
        # half a file as a backup.
        partial = remote + ".part"
        with open(path, "rb") as handle:
            ftp.storbinary(f"STOR {partial}", handle, blocksize=1024 * 1024)
        try:
            ftp.delete(remote)
        except ftplib.error_perm:
            pass
        ftp.rename(partial, remote)
    except ftplib.error_perm as exc:
        raise RemoteError(f"The FTP server refused the file in {folder} ({exc})") from None
    except (ftplib.Error, OSError, EOFError) as exc:
        raise RemoteError(f"FTP: the upload failed ({exc or type(exc).__name__})") from None
    finally:
        restore_sources._ftp_close(ftp)
    return f"{target.name}:{remote}"


def check(target) -> dict:
    """Prove a destination answers before a schedule depends on it."""
    kind = kind_of(target)
    if kind == "s3":
        return backup_s3.check(target)
    list_files(target, None)
    return {"ok": True}
