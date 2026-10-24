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

MAX_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
BACKUP_MANIFEST = "manifest.json"
PANEL_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


def get_server_hostname() -> str:
    """Resolve the canonical hostname for organizing multi-server backups (<hostname>/<user>/...)."""
    candidates: list[str] = []
    if getattr(settings, "panel_domain", None):
        candidates.append(str(settings.panel_domain).strip())
    if getattr(settings, "panel_url", None):
        try:
            from urllib.parse import urlparse
            parsed = (urlparse(str(settings.panel_url)).hostname or "").strip()
            if parsed:
                candidates.append(parsed)
        except Exception:
            pass
    try:
        import socket
        fqdn = (socket.getfqdn() or "").strip()
        host = (socket.gethostname() or "").strip()
        if fqdn and "." in fqdn:
            candidates.append(fqdn)
        if host:
            candidates.append(host)
        if fqdn:
            candidates.append(fqdn)
    except Exception:
        pass
    for raw in candidates:
        clean = re.sub(r"[^A-Za-z0-9._-]", "", raw.lower()).strip(".-")
        if clean and clean not in {"localhost", "localhost.localdomain", "127.0.0.1", "0.0.0.0"}:
            return clean
    return "spanel-server"

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


SITE_MANIFEST = "site_manifest.json"
VALID_RESTORE_MODES = ("full", "files_only", "database_only", "mail_only")
OPENSSL_SALT_MAGIC = b"Salted__"
BACKUP_HMAC_MAGIC = b"SPNLMAC1"
BACKUP_HMAC_TRAILER_LEN = len(BACKUP_HMAC_MAGIC) + 32
MIN_BACKUP_PASSPHRASE_LEN = 12
PBKDF2_ITERATIONS = 100_000


def is_encrypted_backup(archive_path: str | Path) -> bool:
    """Return True if the backup archive is encrypted with AES-256-CBC + HMAC-SHA256 (Salted__ format)."""
    path = Path(archive_path)
    if not path.is_file():
        return str(path).endswith(".enc")
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
        return head == OPENSSL_SALT_MAGIC
    except OSError:
        return str(path).endswith(".enc")


def encrypt_backup_archive(
    archive_path: str | Path,
    passphrase: str,
    *,
    output_path: Optional[str | Path] = None,
    replace_original: bool = True,
) -> Path:
    """Encrypt a backup archive using AES-256-CBC + Encrypt-then-MAC HMAC-SHA256 (PBKDF2 100,000 iterations)."""
    import hmac as hmac_mod
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    src = Path(archive_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Backup archive not found: {src}")
    secret = (passphrase or "").strip()
    if len(secret) < MIN_BACKUP_PASSPHRASE_LEN:
        raise ValueError(f"Encryption passphrase must be at least {MIN_BACKUP_PASSPHRASE_LEN} characters")
    if is_encrypted_backup(src):
        return src

    salt = secrets.token_bytes(8)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=80)
    key, iv, mac_key = dk[:32], dk[32:48], dk[48:80]

    tmp_enc = Path(output_path).resolve() if output_path else src.with_name(src.name + ".enc.tmp")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    padder = sym_padding.PKCS7(128).padder()
    mac = hmac_mod.new(mac_key, digestmod=hashlib.sha256)

    with src.open("rb") as fin, tmp_enc.open("wb") as fout:
        header = OPENSSL_SALT_MAGIC + salt
        fout.write(header)
        mac.update(header)
        while chunk := fin.read(64 * 1024):
            padded = padder.update(chunk)
            if padded:
                ct = encryptor.update(padded)
                if ct:
                    fout.write(ct)
                    mac.update(ct)
        final_padded = padder.finalize()
        final_ct = encryptor.update(final_padded) + encryptor.finalize()
        if final_ct:
            fout.write(final_ct)
            mac.update(final_ct)
        fout.write(BACKUP_HMAC_MAGIC + mac.digest())

    if output_path:
        final_dest = tmp_enc
    elif replace_original:
        tmp_enc.replace(src)
        final_dest = src
    else:
        final_dest = src.with_name(src.name + ".enc")
        tmp_enc.replace(final_dest)

    _write_sha256_sidecar(final_dest)
    return final_dest


def decrypt_backup_archive(
    encrypted_path: str | Path,
    passphrase: str,
    *,
    output_path: Optional[str | Path] = None,
    replace_original: bool = False,
) -> Path:
    """Verify Encrypt-then-MAC HMAC-SHA256 and decrypt an AES-256-CBC backup archive."""
    import hmac as hmac_mod
    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    src = Path(encrypted_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Encrypted backup archive not found: {src}")
    secret = (passphrase or "").strip()
    if not secret:
        raise ValueError("Decryption passphrase is required for encrypted backup archive")

    file_size = src.stat().st_size
    if file_size < 32:
        raise ValueError("Encrypted archive is truncated or missing HMAC-SHA256 authentication tag")

    with src.open("rb") as fin:
        header = fin.read(16)
        if len(header) < 16 or header[:8] != OPENSSL_SALT_MAGIC:
            raise ValueError("Archive is not in AES-256-CBC Salted__ format")
        salt = header[8:16]
        dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=80)
        key, iv, mac_key = dk[:32], dk[32:48], dk[48:80]

        has_hmac_trailer = False
        if file_size >= 16 + BACKUP_HMAC_TRAILER_LEN:
            fin.seek(file_size - BACKUP_HMAC_TRAILER_LEN)
            trailer = fin.read(BACKUP_HMAC_TRAILER_LEN)
            if len(trailer) == BACKUP_HMAC_TRAILER_LEN and trailer[:8] == BACKUP_HMAC_MAGIC:
                has_hmac_trailer = True
                expected_mac = trailer[8:]

        if has_hmac_trailer:
            # Verify Encrypt-then-MAC over header + ciphertext before touching CBC decryption
            mac = hmac_mod.new(mac_key, digestmod=hashlib.sha256)
            fin.seek(0)
            remaining_auth = file_size - BACKUP_HMAC_TRAILER_LEN
            while remaining_auth > 0:
                chunk = fin.read(min(64 * 1024, remaining_auth))
                if not chunk:
                    break
                mac.update(chunk)
                remaining_auth -= len(chunk)
            if not hmac_mod.compare_digest(mac.digest(), expected_mac):
                raise ValueError("Invalid AES-256 decryption passphrase or HMAC-SHA256 authentication failed")
            ciphertext_len = file_size - 16 - BACKUP_HMAC_TRAILER_LEN
        else:
            # Backward-compatibility for archives encrypted by c7c7db7 (pre-SPNLMAC1):
            # only permitted when an intact .sha256 sidecar matches the file on disk.
            expected_sha = _read_sha256_sidecar(src)
            if not expected_sha or (file_size - 16) % 16 != 0:
                raise ValueError("Encrypted archive is missing required SPNLMAC1 HMAC-SHA256 authentication trailer")
            actual_sha = _compute_sha256(src)
            if not hmac_mod.compare_digest(actual_sha.lower(), expected_sha.lower()):
                raise ValueError("Encrypted archive failed SHA-256 integrity verification before decryption")
            logger.warning("Decrypting legacy pre-SPNLMAC1 backup archive %s (verified via .sha256 sidecar)", src.name)
            ciphertext_len = file_size - 16

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        unpadder = sym_padding.PKCS7(128).unpadder()

        if output_path:
            dest = Path(output_path).resolve()
        elif replace_original:
            dest = src.with_name(src.name + ".dec.tmp")
        else:
            clean_name = src.name[:-4] if src.name.endswith(".enc") else (src.name + ".decrypted.tar.gz")
            dest = src.with_name(clean_name)

        try:
            fin.seek(16)
            remaining_ct = ciphertext_len
            with dest.open("wb") as fout:
                while remaining_ct > 0:
                    chunk = fin.read(min(64 * 1024, remaining_ct))
                    if not chunk:
                        break
                    remaining_ct -= len(chunk)
                    dec = decryptor.update(chunk)
                    if dec:
                        unpadded = unpadder.update(dec)
                        if unpadded:
                            fout.write(unpadded)
                tail_dec = decryptor.finalize()
                tail_unpadded = unpadder.update(tail_dec) + unpadder.finalize()
                if tail_unpadded:
                    fout.write(tail_unpadded)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise ValueError("Invalid AES-256 decryption passphrase or corrupted encrypted backup") from exc

    if replace_original and not output_path:
        dest.replace(src)
        _write_sha256_sidecar(src)
        return src
    return dest


def _compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a backup file in 1 MB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_sidecar_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".sha256")


def _write_sha256_sidecar(archive: Path) -> str:
    """Compute and write a `<archive>.sha256` sidecar file next to a backup archive."""
    if not archive.is_file():
        return ""
    try:
        digest = _compute_sha256(archive)
        sidecar = _sha256_sidecar_path(archive)
        sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        return digest
    except OSError as exc:
        logger.warning("Could not write SHA-256 sidecar for %s: %s", archive, exc)
        return ""


def _read_sha256_sidecar(archive: Path, *, compute_if_missing: bool = True) -> str:
    """Read the SHA-256 digest from the `.sha256` sidecar, or compute & persist it if missing."""
    sidecar = _sha256_sidecar_path(archive)
    if sidecar.is_file():
        try:
            first_token = (sidecar.read_text(encoding="utf-8").strip().split() or [""])[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", first_token):
                return first_token
        except OSError:
            pass
    if compute_if_missing and archive.is_file():
        return _write_sha256_sidecar(archive)
    return ""


def verify_backup_integrity(archive_path: str | Path, passphrase: Optional[str] = None) -> dict:
    """Verify SHA-256 checksum and tar.gz (or AES-256-CBC encrypted) archive structure before or after restore."""
    path = Path(archive_path).resolve()
    if not path.is_file():
        raise FileNotFoundError("Backup archive not found")
    expected_sha = _read_sha256_sidecar(path, compute_if_missing=False)
    actual_sha = _compute_sha256(path)
    if not expected_sha:
        _write_sha256_sidecar(path)
        expected_sha = actual_sha
    sidecar_match = (actual_sha == expected_sha)
    encrypted = is_encrypted_backup(path)
    member_count = 0
    tar_ok = False
    error_detail = ""
    if encrypted:
        if passphrase:
            tmp_dec = path.with_name(f".verify-{secrets.token_hex(4)}.tar.gz")
            try:
                decrypt_backup_archive(path, passphrase, output_path=tmp_dec)
                with tarfile.open(tmp_dec, "r:gz") as tar:
                    members = tar.getnames()
                    member_count = len(members)
                    tar_ok = member_count > 0
            except Exception as exc:
                error_detail = str(exc)
            finally:
                tmp_dec.unlink(missing_ok=True)
        else:
            tar_ok = False
            error_detail = "Passphrase required to verify encrypted backup archive"
    else:
        try:
            with tarfile.open(path, "r:gz") as tar:
                members = tar.getnames()
                member_count = len(members)
                tar_ok = member_count > 0
        except (tarfile.TarError, OSError, EOFError) as exc:
            error_detail = f"Corrupted tar.gz archive: {exc}"
    if not sidecar_match and not error_detail:
        error_detail = f"SHA-256 checksum mismatch (expected {expected_sha[:12]}..., got {actual_sha[:12]}...)"
    valid = bool(sidecar_match and tar_ok and not error_detail)
    return {
        "valid": valid,
        "status": "passphrase_required" if (encrypted and not passphrase) else ("valid" if valid else "invalid"),
        "filename": path.name,
        "size": path.stat().st_size,
        "sha256": actual_sha,
        "expected_sha256": expected_sha,
        "sidecar_match": sidecar_match,
        "encrypted": encrypted,
        "cipher": "AES-256-CBC-PBKDF2-SHA256" if encrypted else "none",
        "mac": "HMAC-SHA256" if encrypted else "none",
        "member_count": member_count,
        "error": error_detail,
    }


def _scan_tree_files(root: Path, arc_prefix: str) -> dict[str, list[int]]:
    """Scan a directory tree and return {arc_path: [mtime_ms, size]} for incremental tracking."""
    files: dict[str, list[int]] = {}
    if not root.is_dir():
        return files
    clean_prefix = arc_prefix.strip("/")
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(directory)
        try:
            relative = base.relative_to(root)
        except ValueError:
            continue
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            rel_posix = (relative / name).as_posix()
            arc_path = f"{clean_prefix}/{rel_posix}" if clean_prefix else rel_posix
            files[arc_path] = [int(st.st_mtime_ns // 1_000_000), int(st.st_size)]
    return files


def _add_filtered_tree(
    tar: tarfile.TarFile,
    root: Path,
    arcname: str,
    allowed_files: Optional[set[str]] = None,
) -> None:
    """Add a directory tree to a tarfile, optionally including only files in allowed_files."""
    if not root.is_dir():
        return
    clean_arc = arcname.strip("/")
    try:
        tar.add(root, arcname=clean_arc, recursive=False)
    except OSError:
        return
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(directory)
        try:
            relative = base.relative_to(root)
        except ValueError:
            continue
        for name in dirnames:
            try:
                tar.add(base / name, arcname=f"{clean_arc}/{(relative / name).as_posix()}", recursive=False)
            except OSError:
                continue
        for name in sorted(filenames):
            path = base / name
            if path.is_symlink():
                continue
            arc_path = f"{clean_arc}/{(relative / name).as_posix()}"
            if allowed_files is not None and arc_path not in allowed_files:
                continue
            try:
                tar.add(path, arcname=arc_path, recursive=False)
            except OSError as exc:
                logger.info("Skipped %s in backup: %s", path, exc)


def _read_incremental_state(state_path: Path) -> Optional[dict]:
    if not state_path.is_file():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict) and isinstance(data.get("chain_archives"), list):
            return data
    except Exception:
        pass
    return None


def _write_incremental_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")
        tmp_path.replace(state_path)
    except Exception as exc:
        logger.warning("Could not write incremental state %s: %s", state_path, exc)


def create_backup(
    website: Website,
    db_name: Optional[str] = None,
    *,
    backup_mode: str = "full",
    target_id: Optional[int] = None,
    full_every_n: int = 7,
    passphrase: Optional[str] = None,
) -> str:
    stamp_dt = datetime.utcnow()
    stamp = stamp_dt.strftime("%Y%m%d%H%M%S")
    backup_dir = Path(settings.backup_root) / website.domain
    owner_username = getattr(getattr(website, "owner", None), "username", "") or ""
    prefix = f"{owner_username}-{website.domain}" if owner_username else website.domain
    shell.run(["mkdir", "-p", str(backup_dir)])
    backup_dir.mkdir(parents=True, exist_ok=True)

    scope = f"target-{target_id}" if target_id else "local"
    state_path = backup_dir / f".incremental-state-{scope}.json"
    prev_state = _read_incremental_state(state_path)
    requested_mode = "incremental" if (backup_mode or "").lower() == "incremental" else "full"
    effective_mode = requested_mode

    if effective_mode == "incremental":
        if not prev_state or not prev_state.get("chain_archives"):
            effective_mode = "full"
        elif len(prev_state["chain_archives"]) >= max(int(full_every_n or 7), 2):
            effective_mode = "full"
        elif scope == "local":
            for entry in prev_state["chain_archives"]:
                if not (backup_dir / (entry.get("local_name") or "")).is_file():
                    effective_mode = "full"
                    break

    suffix_tag = "-inc" if effective_mode == "incremental" else ""
    archive = backup_dir / f"{prefix}-{stamp}{suffix_tag}.tar.gz"
    sql_file = backup_dir / f"{website.domain}-{stamp}.sql"
    manifest_file = backup_dir / f".site-manifest-{stamp}.json"

    root_path = Path(website.root_path)
    current_files = _scan_tree_files(root_path, "site") if root_path.exists() else {}

    if effective_mode == "incremental" and prev_state:
        prev_files = prev_state.get("files") or {}
        changed_files = {k for k, v in current_files.items() if prev_files.get(k) != v}
        deleted_files = sorted(k for k in prev_files.keys() if k not in current_files)
        saved_bytes = sum(v[1] for k, v in current_files.items() if k not in changed_files)
        chain_id = prev_state.get("chain_id") or stamp
        base_backup = prev_state.get("base_backup") or archive.name
        chain_archives = list(prev_state.get("chain_archives") or [])
    else:
        changed_files = set(current_files.keys())
        deleted_files = []
        saved_bytes = 0
        chain_id = stamp
        base_backup = archive.name
        chain_archives = []

    current_chain_entry = {
        "local_name": archive.name,
        "remote_name": archive.name,
        "backup_type": effective_mode,
        "generated_at": stamp_dt.isoformat() + "Z",
    }
    chain_archives.append(current_chain_entry)

    site_manifest = {
        "kind": "spanel_website",
        "version": 1,
        "domain": website.domain,
        "hostname": get_server_hostname(),
        "backup_type": effective_mode,
        "chain_id": chain_id,
        "base_backup": base_backup,
        "chain_archives": chain_archives,
        "deleted_files": deleted_files,
        "stats": {
            "total_files": len(current_files),
            "included_files": len(changed_files),
            "skipped_files": len(current_files) - len(changed_files),
            "deleted_files": len(deleted_files),
            "saved_bytes": saved_bytes,
        },
        "generated_at": stamp_dt.isoformat() + "Z",
    }

    if db_name:
        mariadb.export_database(db_name, str(sql_file))
        if settings.command_dry_run and not sql_file.exists():
            sql_file.write_text(f"-- DRY RUN database dump for {db_name}\n", encoding="utf-8")
    try:
        manifest_file.write_text(json.dumps(site_manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(manifest_file, arcname=SITE_MANIFEST)
            if root_path.exists():
                _add_filtered_tree(
                    tar,
                    root_path,
                    "site",
                    allowed_files=changed_files if effective_mode == "incremental" else None,
                )
            if sql_file.exists():
                tar.add(sql_file, arcname=f"database/{sql_file.name}")
        _write_incremental_state(state_path, {
            "chain_id": chain_id,
            "base_backup": base_backup,
            "chain_archives": chain_archives,
            "updated_at": stamp_dt.isoformat() + "Z",
            "files": current_files,
        })
        if passphrase and passphrase.strip():
            encrypt_backup_archive(archive, passphrase.strip(), replace_original=True)
        else:
            _write_sha256_sidecar(archive)
    finally:
        sql_file.unlink(missing_ok=True)
        manifest_file.unlink(missing_ok=True)
    return str(archive)


def _user_backup_dir(username: str, use_hierarchy: bool = True) -> Path:
    if not PANEL_USERNAME_RE.fullmatch(username or ""):
        raise ValueError("Invalid panel username")
    if use_hierarchy:
        return Path(settings.backup_root) / "users" / get_server_hostname() / username
    return Path(settings.backup_root) / "users" / username


def _user_restore_dir() -> Path:
    return Path(settings.backup_root) / "users" / "restore"


def user_restore_dir() -> str:
    return str(_user_restore_dir())


def _ensure_user_dir(path: Path) -> Path:
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


def create_user_backup(
    user: User,
    db,
    use_hierarchy: bool = True,
    *,
    backup_mode: str = "full",
    target_id: Optional[int] = None,
    remote_name_mode: Optional[str] = None,
    full_every_n: int = 7,
    passphrase: Optional[str] = None,
) -> str:
    from app.services import backup_s3

    stamp_dt = datetime.utcnow()
    stamp = stamp_dt.strftime("%Y%m%d%H%M%S")
    backup_dir = _ensure_user_dir(_user_backup_dir(user.username, use_hierarchy=use_hierarchy))
    websites = db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.asc()).all()

    scope = f"target-{target_id}" if target_id else "local"
    state_path = backup_dir / f".incremental-state-{scope}.json"
    prev_state = _read_incremental_state(state_path)
    requested_mode = "incremental" if (backup_mode or "").lower() == "incremental" else "full"
    effective_mode = requested_mode

    if effective_mode == "incremental":
        if not prev_state or not prev_state.get("chain_archives"):
            effective_mode = "full"
        elif len(prev_state["chain_archives"]) >= max(int(full_every_n or 7), 2):
            effective_mode = "full"
        elif scope == "local":
            for entry in prev_state["chain_archives"]:
                if not (backup_dir / (entry.get("local_name") or "")).is_file():
                    effective_mode = "full"
                    break

    suffix_tag = "-inc" if effective_mode == "incremental" else ""
    archive = backup_dir / f"user-{user.username}-{stamp}{suffix_tag}.tar.gz"

    if remote_name_mode:
        computed_remote_name = backup_s3.stored_name(
            archive.name,
            remote_name_mode,
            when=stamp_dt,
            preserve_chain_base=(requested_mode == "incremental"),
        )
    else:
        computed_remote_name = archive.name

    with tempfile.TemporaryDirectory(prefix="spanel-user-backup-", dir=str(backup_dir)) as tmp:
        tmp_dir = Path(tmp)
        # Scan live files for incremental indexing & comparison
        from app.services import mail

        mail_boxes: list[tuple[str, Path]] = []
        linux_user = site_users.linux_user_for_panel_username(user.username)
        for account in (db.query(MailAccount).filter(MailAccount.owner_id == user.id)
                        .order_by(MailAccount.domain, MailAccount.local_part).all()):
            mail_boxes.append((f"mail/{account.domain}/{account.local_part}",
                               Path(f"/home/{linux_user}/mail/{account.domain}/{account.local_part}")))

        current_files: dict[str, list[int]] = {}
        for website in websites:
            root = Path(website.root_path)
            if root.exists():
                current_files.update(_scan_tree_files(root, f"sites/{website.domain}/site"))
        for arcname, box in mail_boxes:
            if box.is_dir():
                current_files.update(_scan_tree_files(box, arcname))

        if effective_mode == "incremental" and prev_state:
            prev_files = prev_state.get("files") or {}
            changed_files = {k for k, v in current_files.items() if prev_files.get(k) != v}
            deleted_files = sorted(k for k in prev_files.keys() if k not in current_files)
            saved_bytes = sum(v[1] for k, v in current_files.items() if k not in changed_files)
            chain_id = prev_state.get("chain_id") or stamp
            base_backup = prev_state.get("base_backup") or archive.name
            base_remote_name = prev_state.get("base_remote_name") or computed_remote_name
            chain_archives = list(prev_state.get("chain_archives") or [])
        else:
            changed_files = set(current_files.keys())
            deleted_files = []
            saved_bytes = 0
            chain_id = stamp
            base_backup = archive.name
            base_remote_name = computed_remote_name
            chain_archives = []

        current_chain_entry = {
            "local_name": archive.name,
            "remote_name": computed_remote_name,
            "backup_type": effective_mode,
            "generated_at": stamp_dt.isoformat() + "Z",
        }
        chain_archives.append(current_chain_entry)

        manifest = {
            "kind": "spanel_user",
            "version": 1,
            "hostname": get_server_hostname(),
            "backup_type": effective_mode,
            "chain_id": chain_id,
            "base_backup": base_backup,
            "base_remote_name": base_remote_name,
            "chain_archives": chain_archives,
            "deleted_files": deleted_files,
            "stats": {
                "total_files": len(current_files),
                "included_files": len(changed_files),
                "skipped_files": len(current_files) - len(changed_files),
                "deleted_files": len(deleted_files),
                "saved_bytes": saved_bytes,
            },
            "generated_at": stamp_dt.isoformat() + "Z",
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
            "databases": [],
            "applications": [],
        }
        manifest["mail"] = mail.backup_manifest(db, user)

        app_payloads: dict[str, Path] = {}
        for app_entry, payload in _collect_applications(user, db, tmp_dir):
            manifest["applications"].append(app_entry)
            if payload:
                app_payloads[app_entry["name"]] = payload

        sql_files: dict[str, Path] = {}
        extra_sql_files: dict[str, Path] = {}
        exported_db_ids: set[int] = set()
        website_domain_by_id: dict[int, str] = {w.id: w.domain for w in websites}
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
            db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).order_by(DatabaseAccount.id.asc()).first()
            if db_item:
                exported_db_ids.add(db_item.id)
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

        for db_item in db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == user.id).order_by(DatabaseAccount.id.asc()).all():
            if db_item.id in exported_db_ids:
                continue
            exported_db_ids.add(db_item.id)
            safe_db_name = re.sub(r"[^A-Za-z0-9._-]", "_", db_item.db_name)
            sql_name = f"extra-{safe_db_name}.sql"
            sql_path = tmp_dir / sql_name
            mariadb.export_database(db_item.db_name, str(sql_path))
            if settings.command_dry_run and not sql_path.exists():
                sql_path.write_text(f"-- DRY RUN database dump for {db_item.db_name}\n", encoding="utf-8")
            extra_sql_files[sql_name] = sql_path
            try:
                db_password = decrypt(db_item.db_password)
            except RuntimeError:
                db_password = ""
            manifest["databases"].append({
                "db_name": db_item.db_name,
                "db_user": db_item.db_user,
                "db_password": db_password,
                "website_domain": website_domain_by_id.get(db_item.website_id) if db_item.website_id else None,
                "sql_member": f"databases/{sql_name}",
            })

        manifest_path = tmp_dir / BACKUP_MANIFEST
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(manifest_path, arcname=BACKUP_MANIFEST)
            for website in websites:
                root = Path(website.root_path)
                if root.exists():
                    _add_filtered_tree(
                        tar,
                        root,
                        f"sites/{website.domain}/site",
                        allowed_files=changed_files if effective_mode == "incremental" else None,
                    )
                sql_path = sql_files.get(website.domain)
                if sql_path and sql_path.exists():
                    tar.add(sql_path, arcname=f"databases/{website.domain}.sql")
            for sql_name, sql_path in extra_sql_files.items():
                if sql_path and sql_path.exists():
                    tar.add(sql_path, arcname=f"databases/{sql_name}")
            for name, payload in app_payloads.items():
                if payload.exists():
                    tar.add(payload, arcname=f"applications/{name}.tar")
            for arcname, box in mail_boxes:
                _add_filtered_tree(
                    tar,
                    box,
                    arcname,
                    allowed_files=changed_files if effective_mode == "incremental" else None,
                )
        _write_incremental_state(state_path, {
            "chain_id": chain_id,
            "base_backup": base_backup,
            "base_remote_name": base_remote_name,
            "chain_archives": chain_archives,
            "updated_at": stamp_dt.isoformat() + "Z",
            "files": current_files,
        })
        effective_passphrase = (passphrase or os.environ.get("SPANEL_BACKUP_PASSPHRASE") or "").strip()
        if effective_passphrase:
            encrypt_backup_archive(archive, effective_passphrase, replace_original=True)
        else:
            _write_sha256_sidecar(archive)
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
    if settings.command_dry_run:
        return []
    dirs = [
        _user_backup_dir(username, use_hierarchy=True),
        _user_backup_dir(username, use_hierarchy=False),
    ]
    paths: list[Path] = []
    seen: set[Path] = set()
    for d in dirs:
        if d.exists():
            for p in d.glob("*.tar.gz"):
                rp = p.resolve()
                if rp not in seen and rp.is_file():
                    seen.add(rp)
                    paths.append(rp)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in paths]


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
    users_root = (Path(settings.backup_root) / "users").resolve()
    try:
        rel_path = str(path.relative_to(users_root)).replace("\\", "/")
    except ValueError:
        rel_path = path.name
    item = {
        "backup_file": str(path),
        "filename": path.name,
        "relative_path": rel_path,
        "hostname": get_server_hostname(),
        "size": path.stat().st_size,
        "sha256": _read_sha256_sidecar(path),
        "username": "",
        "generated_at": "",
        "backup_type": "incremental" if path.name.endswith("-inc.tar.gz") else "full",
        "base_backup": "",
        "chain_length": 1,
        "saved_bytes": 0,
        "included_files": 0,
        "deleted_files_count": 0,
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
        if manifest.get("hostname"):
            item["hostname"] = manifest["hostname"]
        if manifest.get("backup_type") in {"full", "incremental"}:
            item["backup_type"] = manifest["backup_type"]
        item["base_backup"] = manifest.get("base_backup") or ""
        chain_list = manifest.get("chain_archives")
        if isinstance(chain_list, list) and chain_list:
            item["chain_length"] = len(chain_list)
        stats = manifest.get("stats") or {}
        if isinstance(stats, dict):
            item["saved_bytes"] = int(stats.get("saved_bytes") or 0)
            item["included_files"] = int(stats.get("included_files") or 0)
        item["deleted_files_count"] = len(manifest.get("deleted_files") or [])
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


def list_all_user_backups(
    username: Optional[str] = None,
    allowed_usernames: Optional[set[str]] = None,
) -> list[dict]:
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
        for p in users_root.rglob("*.tar.gz"):
            if p.is_symlink() or not p.is_file():
                continue
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                paths.append(rp)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    restore_dir_resolved = _user_restore_dir().resolve()
    for p in paths:
        info = describe_user_backup(str(p))
        if allowed_usernames is not None and info.get("username") not in allowed_usernames:
            continue
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
    stem = re.sub(r"-(?:inc|full)$", "", stem)
    # Strip a trailing timestamp, weekday, week number or date if present.
    stem = re.sub(r"-(\d{14}|\d{4}-\d{2}-\d{2}_\d{6}|Mon|Tue|Wed|Thu|Fri|Sat|Sun|W[1-5]|\d{4}-\d{2}-\d{2})$", "", stem)
    stem = re.sub(r"-(?:inc|full)$", "", stem)
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
    _sha256_sidecar_path(path).unlink(missing_ok=True)
    path.unlink()
    return str(path)


def delete_user_restore_backup(backup_file: str) -> str:
    path = user_backup_path(backup_file)
    restore_dir = _user_restore_dir().resolve()
    if restore_dir not in path.parents:
        raise FileNotFoundError("Backup not found")
    _sha256_sidecar_path(path).unlink(missing_ok=True)
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
        p = user_backup_path(archive)
        _sha256_sidecar_path(p).unlink(missing_ok=True)
        p.unlink()
    except FileNotFoundError:
        pass


def prune_user_backups(username: str, keep: int) -> None:
    keep = max(int(keep or 1), 1)
    all_backups = list_user_backups(username)
    retained = all_backups[:keep]
    protected_names: set[str] = set()
    for kept_path in retained:
        protected_names.add(Path(kept_path).name)
        try:
            manifest = read_backup_manifest(kept_path)
            for entry in manifest.get("chain_archives") or []:
                if isinstance(entry, dict):
                    if entry.get("local_name"):
                        protected_names.add(str(entry["local_name"]))
                    if entry.get("remote_name"):
                        protected_names.add(str(entry["remote_name"]))
            if manifest.get("base_backup"):
                protected_names.add(str(manifest["base_backup"]))
        except Exception:
            continue
    for old_backup in all_backups[keep:]:
        p = Path(old_backup)
        if p.name not in protected_names:
            _sha256_sidecar_path(p).unlink(missing_ok=True)
            p.unlink(missing_ok=True)


def read_backup_manifest(backup_file: str) -> dict:
    archive = user_backup_path(backup_file)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(BACKUP_MANIFEST)
        except KeyError as exc:
            raise ValueError("Backup manifest not found") from exc
        if member.size > 16 * 1024 * 1024:
            raise ValueError("Backup manifest is too large")
        source = tar.extractfile(member)
        if source is None:
            raise ValueError("Backup manifest cannot be read")
        return json.loads(source.read().decode("utf-8"))


def _apply_deleted_files(stage_dir: Path, prefix: str, deleted_files: list[str]) -> None:
    """Remove files recorded in an incremental layer's deleted_files list."""
    if not deleted_files or not stage_dir.is_dir():
        return
    clean_prefix = prefix.strip("/") + "/"
    stage_resolved = stage_dir.resolve()
    for rel_item in deleted_files:
        if not isinstance(rel_item, str) or not rel_item.startswith(clean_prefix):
            continue
        sub_rel = rel_item[len(clean_prefix):].strip("/")
        if not sub_rel or ".." in sub_rel.split("/"):
            continue
        try:
            target = (stage_resolved / sub_rel).resolve()
            if stage_resolved not in target.parents:
                continue
            if target.is_file() or target.is_symlink():
                target.unlink(missing_ok=True)
        except OSError:
            continue


def _resolve_local_user_chain(archive: Path, manifest: dict, username: str) -> list[Path]:
    """Return the ordered list of archives [base_full, inc_1, ..., archive] to apply during restore."""
    if manifest.get("backup_type") != "incremental":
        return [archive]
    chain_entries = manifest.get("chain_archives")
    if not isinstance(chain_entries, list) or len(chain_entries) <= 1:
        return [archive]

    search_dirs: list[Path] = [archive.parent]
    if PANEL_USERNAME_RE.fullmatch(username or ""):
        for use_h in (True, False):
            d = _user_backup_dir(username, use_hierarchy=use_h)
            if d not in search_dirs:
                search_dirs.append(d)
    restore_d = _user_restore_dir()
    if restore_d not in search_dirs:
        search_dirs.append(restore_d)

    resolved: list[Path] = []
    for entry in chain_entries[:-1]:
        if not isinstance(entry, dict):
            continue
        candidates = [c for c in (entry.get("local_name"), entry.get("remote_name")) if c]
        if archive.name in candidates:
            resolved.append(archive)
            continue
        found_path: Optional[Path] = None
        for cand_name in candidates:
            safe_cand = Path(str(cand_name)).name
            for sdir in search_dirs:
                p = (sdir / safe_cand)
                if p.is_file():
                    found_path = p.resolve()
                    break
            if found_path:
                break
        if not found_path:
            missing_label = entry.get("remote_name") or entry.get("local_name") or "base backup"
            raise ValueError(
                f"Cannot restore incremental backup: required chain archive '{missing_label}' is missing"
            )
        resolved.append(found_path)

    if not resolved or resolved[-1] != archive:
        resolved.append(archive)
    return resolved


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


def restore_user_backup(
    backup_file: str,
    db,
    *,
    chain_files: Optional[list[str]] = None,
    restore_mode: str = "full",
    passphrase: Optional[str] = None,
) -> dict:
    mode = (restore_mode or "full").strip().lower()
    if mode not in VALID_RESTORE_MODES:
        mode = "full"
    archive = user_backup_path(backup_file)
    check = verify_backup_integrity(archive, passphrase=passphrase)
    if not check["valid"]:
        raise ValueError(check["error"] or "Backup archive failed SHA-256 integrity check")
    temp_decrypted: Optional[Path] = None
    if is_encrypted_backup(archive):
        temp_decrypted = archive.with_name(f".dec-{secrets.token_hex(4)}-{archive.name}")
        archive = decrypt_backup_archive(archive, passphrase or "", output_path=temp_decrypted)
        _write_sha256_sidecar(archive)
    manifest = read_backup_manifest(str(archive))
    if manifest.get("kind") not in RESTORABLE_BACKUP_KINDS:
        raise ValueError("This is not a spanel or opanel user backup")
    user_info = manifest.get("user") or {}
    username = user_info.get("username") or ""
    if not PANEL_USERNAME_RE.fullmatch(username):
        raise ValueError("Invalid user in backup")

    if chain_files:
        resolved_chain = [user_backup_path(p) for p in chain_files]
    else:
        resolved_chain = _resolve_local_user_chain(archive, manifest, username)

    layer_deleted_by_archive: dict[Path, list[str]] = {}
    for layer_arc in resolved_chain:
        if layer_arc == archive:
            layer_deleted_by_archive[layer_arc] = list(manifest.get("deleted_files") or [])
        else:
            try:
                lm = read_backup_manifest(str(layer_arc))
                layer_deleted_by_archive[layer_arc] = list(lm.get("deleted_files") or [])
            except Exception:
                layer_deleted_by_archive[layer_arc] = []

    user = db.query(User).filter(User.username == username).first()
    created_user = False
    generated_password = ""
    if user is None:
        email = user_info.get("email") or f"{username}@users.spanel.invalid"
        if email.endswith(_PLACEHOLDER_EMAIL_SUFFIXES):
            email = f"{username}@users.spanel.invalid"
        if db.query(User).filter(User.email == email).first():
            email = f"{username}-{secrets.token_hex(4)}@users.spanel.invalid"
        generated_password = secrets.token_urlsafe(18)
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(generated_password),
            role=Role.end_user.value,
            is_active=bool(user_info.get("is_active", True)),
            website_limit=int(user_info.get("website_limit") or 5),
            storage_limit_mb=int(user_info.get("storage_limit_mb") or 1024),
            sftp_password_set_at=datetime.utcnow(),
        )
        db.add(user)
        db.flush()
        created_user = True
        site_users.ensure_panel_user(user.username, site_users.generate_login_password())
    else:
        site_users.ensure_panel_user(user.username)

    restored_websites = []
    restored_apps = []
    restored_mail = []
    site_users.IMPORT_STAGE_BASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="spanel-user-restore-", dir=str(site_users.IMPORT_STAGE_BASE)
    ) as tmp:
        tmp_dir = Path(tmp)
        if mode in {"full", "files_only", "database_only"}:
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

                if mode in {"full", "files_only"}:
                    site_users.ensure_site_runtime(domain, root_path, runtime_php_version, linux_user)
                    site_stage = tmp_dir / "site" / domain
                    for layer_arc in resolved_chain:
                        _safe_extract_prefix(layer_arc, f"sites/{domain}/site", site_stage)
                        _apply_deleted_files(
                            site_stage,
                            f"sites/{domain}/site",
                            layer_deleted_by_archive.get(layer_arc) or [],
                        )
                    site_users.import_site_files(root_path, linux_user, str(site_stage))
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
                    if website.owner_id != user.id:
                        raise ValueError(f"Domain already belongs to another user: {domain}")
                    if mode in {"full", "files_only"}:
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

                if mode in {"full", "files_only"}:
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
                if db_info and mode in {"full", "database_only"}:
                    db_name = db_info.get("db_name")
                    db_user = db_info.get("db_user")
                    db_password = db_info.get("db_password") or mariadb.random_password()
                    conflict = db.query(DatabaseAccount).filter(
                        DatabaseAccount.db_name == db_name,
                        DatabaseAccount.website_id != website.id,
                    ).first()
                    if conflict:
                        raise ValueError(f"Database name already belongs to another website: {db_name}")
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

                if mode in {"full", "files_only"}:
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

        if mode in {"full", "database_only"}:
            for extra_db in manifest.get("databases") or []:
                db_name = (extra_db.get("db_name") or "").strip()
                db_user = (extra_db.get("db_user") or "").strip()
                if not db_name or not db_user:
                    continue
                db_password = extra_db.get("db_password") or mariadb.random_password()
                conflict = db.query(DatabaseAccount).filter(
                    DatabaseAccount.db_name == db_name,
                    DatabaseAccount.owner_id != user.id,
                ).first()
                if conflict:
                    raise ValueError(f"Database name already belongs to another user: {db_name}")
                mariadb.create_database_credentials(
                    db_name, db_user, db_password, allow_existing_user=True
                )
                sql_member = extra_db.get("sql_member")
                if sql_member:
                    sql_path = _extract_member_to_file(archive, sql_member, tmp_dir)
                    if sql_path:
                        mariadb.import_database(db_name, str(sql_path))
                linked_website_id = None
                linked_domain = (extra_db.get("website_domain") or "").strip().lower()
                if linked_domain:
                    linked_site = db.query(Website).filter(Website.domain == linked_domain, Website.owner_id == user.id).first()
                    if linked_site:
                        linked_website_id = linked_site.id
                db_account = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first()
                if db_account is None:
                    db.add(DatabaseAccount(
                        owner_id=user.id,
                        website_id=linked_website_id,
                        db_name=db_name,
                        db_user=db_user,
                        db_password=encrypt(db_password),
                    ))
                else:
                    db_account.owner_id = user.id
                    db_account.website_id = linked_website_id
                    db_account.db_user = db_user
                    db_account.db_password = encrypt(db_password)

        if mode in {"full", "files_only"}:
            restored_apps = _restore_applications(manifest, user, db, archive, tmp_dir)

        from app.services import mail

        if mode in {"full", "mail_only"}:
            def stage_mail(domain: str, local: str) -> Optional[Path]:
                stage = tmp_dir / "mail" / domain / local
                for layer_arc in resolved_chain:
                    _safe_extract_prefix(layer_arc, f"mail/{domain}/{local}", stage)
                    _apply_deleted_files(
                        stage,
                        f"mail/{domain}/{local}",
                        layer_deleted_by_archive.get(layer_arc) or [],
                    )
                return stage if stage.is_dir() and any(stage.iterdir()) else None

            restored_mail = mail.restore_manifest(db, user, manifest.get("mail") or {}, stage_mail)

    db.commit()
    from app.services import dns

    dns.sync_quietly(db)
    mail.sync_quietly(db)
    return {
        "created_user": created_user,
        "username": username,
        "generated_password": generated_password,
        "role": user.role,
        "restore_mode": mode,
        "sha256": check["sha256"],
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


def _read_site_manifest(archive: Path) -> Optional[dict]:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.getmember(SITE_MANIFEST)
            if member.size > 16 * 1024 * 1024:
                return None
            src = tar.extractfile(member)
            if src is None:
                return None
            return json.loads(src.read().decode("utf-8"))
    except Exception:
        return None


def restore_backup(
    website: Website,
    backup_file: str,
    db=None,
    *,
    restore_mode: str = "full",
    passphrase: Optional[str] = None,
) -> str:
    mode = (restore_mode or "full").strip().lower()
    if mode not in {"full", "files_only", "database_only", "mail_only"}:
        mode = "full"
    archive = backup_path(website.domain, backup_file)
    check = verify_backup_integrity(archive, passphrase=passphrase)
    if not check["valid"]:
        raise ValueError(check["error"] or "Backup archive failed SHA-256 integrity check")
    if is_encrypted_backup(archive):
        decrypt_backup_archive(archive, passphrase or "", replace_original=True)
    destination = Path(website.root_path).resolve()

    site_manifest = _read_site_manifest(archive) or {}
    chain_layers: list[tuple[Path, list[str]]] = []
    if site_manifest.get("backup_type") == "incremental" and isinstance(site_manifest.get("chain_archives"), list):
        for entry in site_manifest["chain_archives"][:-1]:
            if not isinstance(entry, dict):
                continue
            cand = entry.get("local_name") or entry.get("remote_name")
            if not cand:
                continue
            cand_path = archive.parent / Path(str(cand)).name
            if not cand_path.is_file():
                raise ValueError(f"Cannot restore incremental website backup: missing chain archive '{cand}'")
            lm = _read_site_manifest(cand_path) or {}
            chain_layers.append((cand_path.resolve(), list(lm.get("deleted_files") or [])))
    chain_layers.append((archive, list(site_manifest.get("deleted_files") or [])))

    def _find_sql_member(layer_arc: Path) -> Optional[str]:
        with tarfile.open(layer_arc, "r:gz") as tar:
            for m in tar.getmembers():
                if m.isfile() and m.name.startswith("database/") and m.name.endswith(".sql"):
                    return m.name
        return None

    def _extract_one_archive(layer_arc: Path, target_dir: Path) -> Optional[str]:
        sql_member_found: Optional[str] = None
        with tarfile.open(layer_arc, "r:gz") as tar:
            members = list(tar.getmembers())
            has_site_prefix = any(m.name == "site" or m.name.startswith("site/") for m in members)
            for m in members:
                if m.isfile() and m.name.startswith("database/") and m.name.endswith(".sql"):
                    sql_member_found = m.name
                    break

            def safe_filter(member: tarfile.TarInfo, dest_path: str):
                if member.islnk():
                    return None
                if member.name == SITE_MANIFEST or member.name.startswith("database/"):
                    return None
                if has_site_prefix:
                    if member.name == "site":
                        return None
                    if not member.name.startswith("site/"):
                        return None
                    member.name = member.name[len("site/"):]
                return tarfile.data_filter(member, dest_path)

            try:
                tar.extractall(path=str(target_dir), filter=safe_filter)
            except TypeError:
                _ensure_safe_tar(layer_arc, target_dir)
                for member in members:
                    if member.name == SITE_MANIFEST or member.name.startswith("database/"):
                        continue
                    if has_site_prefix:
                        if member.name == "site":
                            continue
                        if not member.name.startswith("site/"):
                            continue
                        member.name = member.name[len("site/"):]
                    tar.extract(member, str(target_dir))
        return sql_member_found

    def _extract_site_to(target_dir: Path) -> Optional[str]:
        last_sql: Optional[str] = None
        for layer_arc, deleted_list in chain_layers:
            sql_found = _extract_one_archive(layer_arc, target_dir)
            if sql_found:
                last_sql = sql_found
            _apply_deleted_files(target_dir, "site", deleted_list)
        return last_sql

    sql_member: Optional[str] = None
    if mode in {"full", "files_only"}:
        try:
            sql_member = _extract_site_to(destination)
        except PermissionError:
            if not website.linux_user:
                raise
            site_users.IMPORT_STAGE_BASE.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="spanel-site-restore-", dir=str(site_users.IMPORT_STAGE_BASE)
            ) as stage_tmp:
                stage_dir = Path(stage_tmp) / "site"
                stage_dir.mkdir(parents=True, exist_ok=True)
                sql_member = _extract_site_to(stage_dir)
                site_users.import_site_files(str(destination), website.linux_user, str(stage_dir))
    else:
        sql_member = _find_sql_member(archive)

    if mode in {"full", "database_only"} and sql_member and db is not None:
        db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
        if db_item and db_item.db_name:
            with tempfile.TemporaryDirectory(prefix="spanel-site-sql-") as sql_tmp:
                sql_path = _extract_member_to_file(archive, sql_member, Path(sql_tmp))
                if sql_path:
                    mariadb.import_database(db_item.db_name, str(sql_path))

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
    _sha256_sidecar_path(path).unlink(missing_ok=True)
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
    subpath: Optional[str] = None,
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    expected_host_key_type: Optional[str] = None,
    expected_host_key_fingerprint: Optional[str] = None,
) -> dict:
    local_path = Path(local_file).resolve()
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError("Local backup file not found")
    if not password and not private_key:
        raise ValueError("SFTP password or private key is required")

    pkey = _load_private_key(private_key, password=password) if private_key else None
    base_dir = posixpath.normpath(remote_path.strip() or ".")
    clean_sub = (subpath or "").strip().strip("/")
    remote_dir = posixpath.join(base_dir, clean_sub) if clean_sub else base_dir
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
