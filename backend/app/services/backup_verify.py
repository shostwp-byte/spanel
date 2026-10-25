"""Scheduled proof that the backups can be restored.

An archive was only ever checked when somebody restored from it - which is
the worst moment to learn it is truncated, encrypted with a passphrase
nobody has any more, or missing its database dumps. This runs from the
maintenance window (weekly by default) and checks:

- on this server, the newest archive of every account: SHA-256 against its
  sidecar, decryption (SPANEL_BACKUP_PASSPHRASE, as the scheduler encrypts
  with), a full tar listing, the manifest present and readable;
- on every server-wide destination, a sample: the newest archive of
  `remote_samples` accounts chosen at random, downloaded and put through the
  same checks; and the newest panel self-backup there is structurally sound
  (header and HMAC trailer - it is encrypted with a key only root holds);
- optionally, each verified archive's SQL dumps imported into a throwaway
  MariaDB database that is dropped straight after, so a dump that does not
  load is found now and not during a restore.

Any failure goes to administrators by email/Telegram and to the alert
webhooks.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import secrets
import shutil
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("spanel.backup_verify")

DATA_DIR = Path(os.environ.get("SPANEL_DATA_DIR") or os.environ.get("spanel_DATA_DIR") or "/var/lib/spanel")
CONFIG_FILE = DATA_DIR / "backup-verify.json"
WINDOW_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SHARED_FOLDERS = {"restore", "uploads"}
MAX_SQL_TEST_BYTES = 512 * 1024 * 1024

DEFAULTS = {
    "enabled": False,
    "window": "04:30",
    "weekdays": [6],
    "remote_samples": 1,
    "test_restore_db": False,
    "max_download_mb": 4096,
    "last_run": {},
    "history": [],
}


def load_config() -> dict:
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    config = json.loads(json.dumps(DEFAULTS))
    if isinstance(stored, dict):
        config.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return config


def _write(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def save_settings(changes: dict) -> dict:
    config = load_config()
    if changes.get("enabled") is not None:
        config["enabled"] = bool(changes["enabled"])
    if changes.get("window") is not None:
        if not WINDOW_RE.match(str(changes["window"])):
            raise ValueError("The window must be HH:MM (24-hour)")
        config["window"] = str(changes["window"])
    if changes.get("weekdays") is not None:
        config["weekdays"] = sorted({int(d) for d in changes["weekdays"] if 0 <= int(d) <= 6})
    if changes.get("remote_samples") is not None:
        config["remote_samples"] = max(0, min(20, int(changes["remote_samples"])))
    if changes.get("test_restore_db") is not None:
        config["test_restore_db"] = bool(changes["test_restore_db"])
    if changes.get("max_download_mb") is not None:
        config["max_download_mb"] = max(10, min(100 * 1024, int(changes["max_download_mb"])))
    _write(config)
    return public_status(config)


def public_status(config: Optional[dict] = None) -> dict:
    config = config or load_config()
    return {k: config[k] for k in ("enabled", "window", "weekdays", "remote_samples", "test_restore_db",
                                   "max_download_mb")} | {
        "last_run": config.get("last_run") or {}, "history": (config.get("history") or [])[:10]}


# --- checking one archive ------------------------------------------------------------

def inspect_archive(path: Path, passphrase: Optional[str], *, work_dir: Path,
                    test_restore_db: bool = False) -> dict:
    """Every check one archive can be put through without restoring it."""
    from app.services import backup

    result = {"name": path.name, "ok": False, "size": 0, "sha256": "", "sidecar_match": None,
              "encrypted": False, "members": 0, "manifest": False, "databases": [], "db_test": [],
              "detail": ""}
    try:
        result["size"] = path.stat().st_size
        result["sha256"] = backup._compute_sha256(path)
        expected = backup._read_sha256_sidecar(path, compute_if_missing=False)
        if expected:
            result["sidecar_match"] = expected.lower() == result["sha256"]
            if not result["sidecar_match"]:
                result["detail"] = "SHA-256 does not match the .sha256 written when the backup was made"
                return result
        plain = path
        result["encrypted"] = backup.is_encrypted_backup(path)
        if result["encrypted"]:
            if not passphrase:
                result["detail"] = "Encrypted, and SPANEL_BACKUP_PASSPHRASE is not set: it cannot be decrypted"
                return result
            plain = work_dir / f"verify-{secrets.token_hex(4)}.tar.gz"
            backup.decrypt_backup_archive(path, passphrase, output_path=plain)
        try:
            with tarfile.open(plain, "r:gz") as tar:
                members = tar.getmembers()
                result["members"] = len(members)
                names = {m.name for m in members}
                result["manifest"] = backup.BACKUP_MANIFEST in names
                result["databases"] = sorted(m.name for m in members
                                             if m.isfile() and m.name.startswith("databases/") and m.name.endswith(".sql"))
                if result["manifest"]:
                    source = tar.extractfile(backup.BACKUP_MANIFEST)
                    manifest = json.loads(source.read().decode("utf-8")) if source else {}
                    if manifest.get("kind") not in backup.RESTORABLE_BACKUP_KINDS:
                        result["detail"] = "The manifest is not a spanel account backup"
                        return result
                if test_restore_db and result["databases"]:
                    for member_name in result["databases"]:
                        result["db_test"].append(_test_restore_member(tar, member_name, work_dir))
        finally:
            if plain != path:
                plain.unlink(missing_ok=True)
        if not result["manifest"]:
            result["detail"] = "No manifest.json in the archive"
            return result
        failed_db = [t for t in result["db_test"] if not t["ok"]]
        if failed_db:
            result["detail"] = "SQL dump did not load: " + "; ".join(f"{t['member']}: {t['detail']}" for t in failed_db[:3])
            return result
        result["ok"] = True
        result["detail"] = "ok"
    except (OSError, ValueError, tarfile.TarError, EOFError) as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"[:400]
    return result


def _test_restore_member(tar: tarfile.TarFile, member_name: str, work_dir: Path) -> dict:
    member = tar.getmember(member_name)
    entry = {"member": member_name, "ok": False, "tables": 0, "detail": ""}
    if member.size > MAX_SQL_TEST_BYTES:
        entry.update(ok=True, detail=f"skipped: {member.size // (1024 * 1024)} MB is over the test-restore limit")
        return entry
    source = tar.extractfile(member)
    if source is None:
        entry["detail"] = "cannot be read"
        return entry
    sql_path = work_dir / f"verify-{secrets.token_hex(4)}.sql"
    try:
        with open(sql_path, "wb") as out:
            shutil.copyfileobj(source, out, 1024 * 1024)
        entry.update(trial_restore_sql(sql_path))
    finally:
        sql_path.unlink(missing_ok=True)
    return entry


def trial_restore_sql(sql_path: Path) -> dict:
    """Import one dump into a throwaway database, count its tables, drop it."""
    from app.services import mariadb

    name = mariadb._validate_identifier(f"spanel_verify_{secrets.token_hex(4)}")
    quoted = mariadb._quote_identifier(name)
    try:
        mariadb._run_sql(f"CREATE DATABASE {quoted} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n")
        mariadb.import_database(name, str(sql_path))
        res = mariadb.shell.run(mariadb._mysql_args(["-N"]), check=False, sensitive=True,
                                input=f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = "
                                      f"{mariadb._quote_sql_string(name)};\n")
        tables = int((res.stdout or "0").strip().splitlines()[-1] or 0) if (res.stdout or "").strip() else 0
        return {"ok": True, "tables": tables, "detail": f"{tables} table(s) loaded"}
    except Exception as exc:  # noqa: BLE001 - reported per dump
        return {"ok": False, "tables": 0, "detail": str(exc)[:300]}
    finally:
        try:
            mariadb._run_sql(f"DROP DATABASE IF EXISTS {quoted};\n", check=False)
        except Exception:  # noqa: BLE001
            logger.warning("could not drop the verification database %s", name)


def check_dr_archive_structure(path: Path) -> dict:
    """A panel self-backup is encrypted with a root-only key: check what can
    be checked without it - the salted header and the HMAC trailer."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(8)
        handle.seek(max(0, size - 40))
        trailer = handle.read(8)
    ok = header == b"Salted__" and trailer == b"SPNLMAC1" and size > 16 + 40
    return {"name": path.name, "ok": ok, "size": size,
            "detail": "ok" if ok else "not a complete encrypted archive (header or HMAC trailer missing)"}


# --- what to check -----------------------------------------------------------------------

def latest_local_archives() -> list[Path]:
    """The newest archive of each account under <backup_root>/users."""
    root = Path(settings.backup_root) / "users"
    newest: dict[str, Path] = {}
    if not root.is_dir():
        return []
    for path in root.rglob("*.tar.gz"):
        if path.is_symlink() or not path.is_file():
            continue
        if any(part in SHARED_FOLDERS for part in path.relative_to(root).parts[:-1]):
            continue
        from app.services import backup

        account = backup.username_from_archive_name(path.name) or path.parent.name
        current = newest.get(account)
        if current is None or path.stat().st_mtime > current.stat().st_mtime:
            newest[account] = path
    return [newest[k] for k in sorted(newest)]


def _server_targets(db) -> list:
    from app.models.entities import SftpBackupTarget

    return db.query(SftpBackupTarget).filter(SftpBackupTarget.owner_id == None,  # noqa: E711
                                             SftpBackupTarget.is_active == True).all()  # noqa: E712


def _accounts(db) -> list[str]:
    from app.models.entities import User

    return [u.username for u in db.query(User).filter(User.is_active == True).all()]  # noqa: E712


def run_verification(db, *, trigger: str = "manual", rng: Optional[random.Random] = None) -> dict:
    from app.services import backup, backup_remote, panel_backup

    config = load_config()
    rng = rng or random.Random()
    passphrase = (os.environ.get("SPANEL_BACKUP_PASSPHRASE") or "").strip() or None
    started = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    checks: list[dict] = []
    host = backup.get_server_hostname()
    max_bytes = int(config["max_download_mb"]) * 1024 * 1024

    # Not under users/: the restore page lists that tree, and these are
    # scratch copies, not backups.
    work_root = Path(settings.backup_root) / ".verify-work"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".verify-", dir=str(work_root)) as tmp:
        work = Path(tmp)
        for path in latest_local_archives():
            checks.append({"scope": "local", "target": "this server",
                           **inspect_archive(path, passphrase, work_dir=work, test_restore_db=config["test_restore_db"])})

        accounts = _accounts(db) if config["remote_samples"] else []
        for target in _server_targets(db) if config["remote_samples"] else []:
            sample = rng.sample(accounts, min(len(accounts), int(config["remote_samples"])))
            for account in sample:
                try:
                    listing = backup_remote.list_files(target, f"{host}/{account}")
                except Exception as exc:  # noqa: BLE001
                    checks.append({"scope": "remote", "target": target.name, "name": account, "ok": False,
                                   "detail": f"listing failed: {exc}"[:400]})
                    continue
                if not listing:
                    continue  # this account is not backed up to this destination
                newest = listing[0]
                if newest["size"] and newest["size"] > max_bytes:
                    checks.append({"scope": "remote", "target": target.name, "name": newest["name"], "ok": True,
                                   "detail": f"skipped: {newest['size'] // (1024 * 1024)} MB is over the download limit"})
                    continue
                local = work / f"remote-{secrets.token_hex(4)}.tar.gz"
                try:
                    backup_remote.download(target, newest["key"], str(local))
                    result = inspect_archive(local, passphrase, work_dir=work, test_restore_db=config["test_restore_db"])
                    result["name"] = newest["name"]
                    if newest["size"] and result["size"] != newest["size"]:
                        result.update(ok=False, detail=f"downloaded {result['size']} bytes, the listing said {newest['size']}")
                    checks.append({"scope": "remote", "target": target.name, **result})
                except Exception as exc:  # noqa: BLE001
                    checks.append({"scope": "remote", "target": target.name, "name": newest["name"], "ok": False,
                                   "detail": f"download failed: {exc}"[:400]})
                finally:
                    local.unlink(missing_ok=True)
            # The newest panel self-backup on this destination, if it has any.
            try:
                dr_listing = backup_remote.list_files(target, f"{host}/{panel_backup.REMOTE_FOLDER}",
                                                      suffixes=(panel_backup.ARCHIVE_SUFFIX,))
            except Exception:  # noqa: BLE001
                dr_listing = []
            if dr_listing and (not dr_listing[0]["size"] or dr_listing[0]["size"] <= max_bytes):
                local = work / f"dr-{secrets.token_hex(4)}.spdr"
                try:
                    backup_remote.download(target, dr_listing[0]["key"], str(local))
                    result = check_dr_archive_structure(local)
                    result["name"] = dr_listing[0]["name"]
                    checks.append({"scope": "remote-dr", "target": target.name, **result})
                except Exception as exc:  # noqa: BLE001
                    checks.append({"scope": "remote-dr", "target": target.name, "name": dr_listing[0]["name"],
                                   "ok": False, "detail": f"download failed: {exc}"[:400]})
                finally:
                    local.unlink(missing_ok=True)

    failed = [c for c in checks if not c.get("ok")]
    run = {"started_at": started, "finished_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "trigger": trigger, "checked": len(checks), "failed": len(failed),
           "status": "ok" if not failed else "failed", "checks": checks[:200]}
    config = load_config()
    config["last_run"] = run
    config["history"] = [{k: v for k, v in run.items() if k != "checks"}, *(config.get("history") or [])][:20]
    _write(config)
    if failed:
        _report(failed, started)
    return run


def _report(failed: list[dict], started: str) -> None:
    lines = [f"{c.get('target')}: {c.get('name')}: {c.get('detail')}" for c in failed]
    try:
        from app.services import notifications

        notifications.notify("backup_verify_failed", {"when": started, "errors": lines},
                             admins=True, dedupe_key=f"backup_verify:{started[:10]}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import alert_webhooks

        alert_webhooks.alert("backup_verify_failed", f"Backup verification: {len(failed)} problem(s)",
                             "\n".join(lines[:10]), {"failed": len(failed)}, dedupe_key=f"backup_verify:{started[:10]}")
    except Exception:  # noqa: BLE001
        pass


# --- maintenance window hooks ---------------------------------------------------------------

def scheduled_window():
    from app.services.maintenance_window import Schedule

    config = load_config()
    if not config["enabled"]:
        return None
    return Schedule(window=config["window"], weekdays=list(config["weekdays"]))


def scheduled_run() -> tuple[str, str]:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        run = run_verification(db, trigger="scheduled")
    finally:
        db.close()
    return ("ok" if run["status"] == "ok" else "failed"), f"{run['checked']} checked, {run['failed']} failed"
