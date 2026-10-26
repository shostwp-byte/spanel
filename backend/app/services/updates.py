"""Safe Online Update Service with Designated Update Server, Auto-Discovery,
Snapshot & Auto-Rollback Management, and Stealth URL/Token Redaction.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.version import APP_VERSION
from app.services.shell import shell

DATA_DIR = Path(os.environ.get("SPANEL_DATA_DIR") or os.environ.get("spanel_DATA_DIR") or "/var/lib/spanel")
UPDATE_STATE_FILE = Path(os.environ.get("SPANEL_UPDATE_STATE_FILE") or os.environ.get("spanel_UPDATE_STATE_FILE") or str(DATA_DIR / "update-status.json"))
UPDATE_SOURCE_FILE = Path(os.environ.get("SPANEL_UPDATE_SOURCE_FILE") or str(DATA_DIR / "update-source.json"))
# Root's directory, not under BACKUP_ROOT (which this account owns): a
# rollback installs what it finds here as root. See spanel-helper.
SNAPSHOT_DIR = Path("/var/backups/spanel-panel-snapshots")

DEFAULT_REPO_URL = os.environ.get("SPANEL_REPO_URL") or os.environ.get("spanel_REPO_URL") or "https://github.com/shostwp-byte/spanel.git"
DEFAULT_MANIFEST_URL = os.environ.get("SPANEL_UPDATE_MANIFEST_URL") or ""

DEFAULT_SNAPSHOT_KEEP = 5
SEMVER_TAG_RE = re.compile(r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
URL_REDACT_RE = re.compile(r"""(?:https?://|git@)[^\s"'<>]+""", re.IGNORECASE)
PATH_REDACT_RE = re.compile(r"""(?:/var/lib/spanel/update-[^\s"'<>]+|/etc/spanel/update-[^\s"'<>]+)""")

STATUS_CACHE_SECONDS = 300
DASHBOARD_CHECK_SECONDS = 6 * 3600
_background_check = threading.Lock()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _semver_tuple(value: str):
    raw = (value or "").strip()
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", raw)
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3)) if m.group(3) is not None else 0
    return (major, minor, patch)


def sanitize_update_text(text: str) -> str:
    """Remove any update server URLs, git remotes, tokens, or sensitive paths before exposing to UI/logs."""
    if not text:
        return ""
    cleaned = URL_REDACT_RE.sub("[PROTECTED-UPDATE-SERVER]", str(text))
    cleaned = PATH_REDACT_RE.sub("[PROTECTED-CONFIG]", cleaned)
    return cleaned


def mask_server_url(url: str) -> str:
    """Return a masked representation of the designated update server so the full hostname/path never leaks."""
    raw = (url or "").strip()
    if not raw:
        return "Protected Update Server (TLS)"
    try:
        if raw.startswith("git@"):
            host_part = raw.split("@", 1)[1].split(":", 1)[0]
            return f"git@{host_part[:2]}*** [SSH Protected]"
        parsed = urllib.parse.urlparse(raw)
        host = parsed.hostname or ""
        scheme = parsed.scheme or "https"
        if not host:
            return "Protected Update Server (TLS)"
        parts = host.split(".")
        masked_parts = []
        for idx, p in enumerate(parts):
            if idx == len(parts) - 1 and len(parts) > 1:
                masked_parts.append(p)
            elif len(p) <= 2:
                masked_parts.append(p[0] + "***")
            else:
                masked_parts.append(p[:2] + "****")
        masked_host = ".".join(masked_parts)
        return f"{scheme}://{masked_host}/*** [TLS Protected]"
    except Exception:
        return "Protected Update Server (TLS)"


def get_update_source_config() -> dict:
    """Read the protected update server configuration (internal use only)."""
    defaults = {
        "source_type": "auto",  # auto | manifest | git
        "server_url": DEFAULT_REPO_URL,
        "manifest_url": DEFAULT_MANIFEST_URL,
        "channel": "stable",    # stable | beta
        "auth_token": "",
        "verify_checksum": True,
        "auto_check_enabled": True,
        "auto_check_interval_hours": 6,
        "auto_update_enabled": False,
        "auto_update_window": "02:00",
        "auto_rollback_enabled": True,
        "snapshot_keep": DEFAULT_SNAPSHOT_KEEP,
    }
    try:
        if UPDATE_SOURCE_FILE.exists():
            stored = json.loads(UPDATE_SOURCE_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                defaults.update({k: v for k, v in stored.items() if v is not None})
    except Exception:
        pass
    return defaults


def save_update_source_config(payload: dict) -> dict:
    """Save update server configuration with 0600 permissions. Supports write-only URL and token updates."""
    current = get_update_source_config()
    if "source_type" in payload and payload["source_type"] in {"auto", "manifest", "git"}:
        current["source_type"] = payload["source_type"]
    if "channel" in payload and payload["channel"] in {"stable", "beta", "release"}:
        current["channel"] = "stable" if payload["channel"] == "release" else payload["channel"]

    new_url = (payload.get("server_url") or "").strip()
    if new_url and "***" not in new_url and "[TLS Protected]" not in new_url:
        current["server_url"] = new_url

    if "manifest_url" in payload:
        m_url = (payload.get("manifest_url") or "").strip()
        if "***" not in m_url and "[TLS Protected]" not in m_url:
            current["manifest_url"] = m_url

    if payload.get("clear_auth_token"):
        current["auth_token"] = ""
    elif payload.get("auth_token"):
        token_val = str(payload["auth_token"]).strip()
        if token_val and "***" not in token_val:
            current["auth_token"] = token_val

    for bool_key in ("verify_checksum", "auto_check_enabled", "auto_update_enabled", "auto_rollback_enabled"):
        if bool_key in payload and payload[bool_key] is not None:
            current[bool_key] = bool(payload[bool_key])

    if "auto_check_interval_hours" in payload and payload["auto_check_interval_hours"] is not None:
        try:
            current["auto_check_interval_hours"] = max(1, min(168, int(payload["auto_check_interval_hours"])))
        except (ValueError, TypeError):
            pass

    if "snapshot_keep" in payload and payload["snapshot_keep"] is not None:
        try:
            current["snapshot_keep"] = max(1, min(20, int(payload["snapshot_keep"])))
        except (ValueError, TypeError):
            pass

    if "auto_update_window" in payload and payload["auto_update_window"]:
        win = str(payload["auto_update_window"]).strip()
        if re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", win):
            current["auto_update_window"] = win

    try:
        UPDATE_SOURCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = UPDATE_SOURCE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(str(tmp), 0o600)
        except OSError:
            pass
        tmp.replace(UPDATE_SOURCE_FILE)
    except Exception:
        pass
    return get_masked_source_info()


def get_masked_source_info() -> dict:
    """Return safe, masked update source metadata for the Admin UI."""
    cfg = get_update_source_config()
    primary_url = cfg.get("manifest_url") or cfg.get("server_url") or DEFAULT_REPO_URL
    return {
        "source_type": cfg.get("source_type", "auto"),
        "masked_url": mask_server_url(primary_url),
        "has_custom_manifest": bool(cfg.get("manifest_url")),
        "channel": cfg.get("channel", "stable"),
        "has_auth_token": bool(cfg.get("auth_token")),
        "verify_checksum": bool(cfg.get("verify_checksum", True)),
        "auto_check_enabled": bool(cfg.get("auto_check_enabled", True)),
        "auto_check_interval_hours": int(cfg.get("auto_check_interval_hours", 6)),
        "auto_update_enabled": bool(cfg.get("auto_update_enabled", False)),
        "auto_update_window": cfg.get("auto_update_window", "02:00"),
        "auto_rollback_enabled": bool(cfg.get("auto_rollback_enabled", True)),
        "snapshot_keep": int(cfg.get("snapshot_keep") or DEFAULT_SNAPSHOT_KEEP),
    }


def _read_update_state() -> dict:
    try:
        if UPDATE_STATE_FILE.exists():
            return json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _write_update_state(state: dict) -> None:
    try:
        UPDATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = UPDATE_STATE_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(UPDATE_STATE_FILE)
    except Exception:
        return


def _discover_from_manifest(url: str, auth_token: str, channel: str) -> dict:
    headers = {
        "User-Agent": f"SPanel-Updater/{APP_VERSION}",
        "Accept": "application/json",
        "X-SPanel-Channel": channel,
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
        headers["X-SPanel-Update-Token"] = auth_token

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("Designated update server returned non-200 status.")
        data = json.loads(response.read().decode("utf-8"))

    # Support channel-nested manifest or flat manifest
    if isinstance(data.get("channels"), dict) and channel in data["channels"]:
        data = data["channels"][channel]

    raw_ver = str(data.get("version") or data.get("latest_version") or data.get("tag") or "").strip()
    if not raw_ver:
        raise RuntimeError("Manifest did not include a valid version field.")
    clean_ver = raw_ver[1:] if raw_ver.lower().startswith("v") else raw_ver
    tag = data.get("tag") or f"v{clean_ver}"
    changelog = data.get("changelog") or data.get("notes") or []
    if isinstance(changelog, str):
        changelog = [line.strip() for line in changelog.splitlines() if line.strip()]

    return {
        "latest_version": clean_ver,
        "latest_tag": tag,
        "released_at": str(data.get("released_at") or data.get("published_at") or ""),
        "severity": str(data.get("severity") or "normal"),
        "expected_sha256": str(data.get("sha256") or data.get("checksum") or ""),
        "download_url": str(data.get("download_url") or data.get("package_url") or ""),
        "changelog": changelog[:15] if isinstance(changelog, list) else [],
    }


def _discover_from_git(repo_url: str, auth_token: str) -> dict:
    cmd = ["git"]
    if auth_token and repo_url.startswith("http"):
        cmd.extend(["-c", f"http.extraHeader=Authorization: Bearer {auth_token}"])
    cmd.extend(["ls-remote", "--tags", "--refs", repo_url, "refs/tags/v*"])

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError("Không thể kết nối tới máy chủ cập nhật chỉ định. Vui lòng kiểm tra kết nối mạng hoặc cấu hình máy chủ Update.")
    candidates = []
    for line in completed.stdout.splitlines():
        ref = line.rsplit("/", 1)[-1].strip()
        match = SEMVER_TAG_RE.match(ref)
        if match:
            candidates.append((tuple(int(part) for part in match.groups()), ref))
    if not candidates:
        clean_cur = APP_VERSION[1:] if APP_VERSION.lower().startswith("v") else APP_VERSION
        return {
            "latest_version": clean_cur,
            "latest_tag": f"v{clean_cur}",
            "released_at": "",
            "severity": "normal",
            "expected_sha256": "",
            "download_url": "",
            "changelog": [],
        }
    _, latest_tag = max(candidates, key=lambda item: item[0])
    clean_ver = latest_tag[1:] if latest_tag.lower().startswith("v") else latest_tag
    return {
        "latest_version": clean_ver,
        "latest_tag": latest_tag if latest_tag.startswith("v") else f"v{clean_ver}",
        "released_at": "",
        "severity": "normal",
        "expected_sha256": "",
        "download_url": "",
        "changelog": [],
    }


def _discover_latest_release() -> dict:
    cfg = get_update_source_config()
    source_type = cfg.get("source_type", "auto")
    server_url = (cfg.get("server_url") or DEFAULT_REPO_URL).strip()
    manifest_url = (cfg.get("manifest_url") or "").strip()
    auth_token = (cfg.get("auth_token") or "").strip()
    channel = cfg.get("channel", "stable")

    if source_type == "manifest" or manifest_url:
        target_url = manifest_url or server_url
        try:
            return _discover_from_manifest(target_url, auth_token, channel)
        except Exception:
            if source_type == "manifest":
                raise RuntimeError("Không thể truy vấn Manifest phiên bản từ máy chủ cập nhật chỉ định.")

    if source_type == "auto" and server_url.startswith("http") and not server_url.endswith(".git") and "github.com" not in server_url.lower():
        try:
            return _discover_from_manifest(server_url, auth_token, channel)
        except Exception:
            pass

    return _discover_from_git(server_url, auth_token)


def panel_release_status(force_refresh: bool = False) -> dict:
    state = _read_update_state()
    now = _utc_now()
    current_version = APP_VERSION
    current_tuple = _semver_tuple(current_version)
    latest_version = state.get("latest_version") or ""
    latest_tag = state.get("latest_tag") or (f"v{latest_version}" if latest_version else "")
    check_error = ""

    checked_at = float(state.get("last_checked_epoch") or 0)
    should_refresh = force_refresh or not latest_version
    if not should_refresh and (time.time() - checked_at > STATUS_CACHE_SECONDS):
        _refresh_in_background()

    if should_refresh:
        try:
            discovered = _discover_latest_release()
            latest_version = discovered["latest_version"]
            latest_tag = discovered["latest_tag"]
            state.update(
                {
                    "current_version": current_version,
                    "latest_tag": latest_tag,
                    "latest_version": latest_version,
                    "released_at": discovered.get("released_at") or state.get("released_at") or "",
                    "severity": discovered.get("severity") or "normal",
                    "expected_sha256": discovered.get("expected_sha256") or "",
                    "download_url": discovered.get("download_url") or "",
                    "changelog": discovered.get("changelog") or state.get("changelog") or [],
                    "last_checked_at": now,
                    "last_checked_epoch": time.time(),
                    "check_error": "",
                }
            )
        except Exception as exc:
            err_str = sanitize_update_text(str(exc))
            if force_refresh or not latest_version:
                check_error = err_str
            state.update(
                {
                    "current_version": current_version,
                    "last_checked_at": now,
                    "last_checked_epoch": time.time(),
                    "check_error": check_error,
                }
            )
        _write_update_state(state)
    else:
        state["current_version"] = current_version

    latest_tuple = _semver_tuple(latest_version)
    update_available = False
    if current_tuple and latest_tuple:
        update_available = latest_tuple > current_tuple

    return {
        "current_version": current_version,
        "latest_version": latest_version or current_version,
        "latest_tag": latest_tag or f"v{current_version}",
        "update_available": bool(update_available),
        "released_at": state.get("released_at") or "",
        "severity": state.get("severity") or "normal",
        "changelog": state.get("changelog") or [],
        "sha256_ready": bool(state.get("expected_sha256")),
        "last_checked_at": state.get("last_checked_at") or "",
        "last_update_started_at": state.get("last_update_started_at") or "",
        "last_update_finished_at": state.get("last_update_finished_at") or "",
        "last_update_status": state.get("last_update_status") or "",
        "last_update_ref": state.get("last_update_ref") or "",
        "rollback_snapshot_id": state.get("rollback_snapshot_id") or "",
        "check_error": sanitize_update_text(check_error) if force_refresh else "",
        "progress_percent": state.get("progress_percent", 0),
        "progress_phase": state.get("progress_phase") or "",
        "progress_message": sanitize_update_text(state.get("progress_message") or ""),
    }


def cached_release_summary() -> dict:
    """Fast non-blocking release summary for Dashboard."""
    state = _read_update_state()
    latest_version = state.get("latest_version") or ""
    current_tuple = _semver_tuple(APP_VERSION)
    latest_tuple = _semver_tuple(latest_version)
    cfg = get_update_source_config()
    interval_sec = max(3600, int(cfg.get("auto_check_interval_hours", 6)) * 3600)
    if cfg.get("auto_check_enabled", True) and (time.time() - float(state.get("last_checked_epoch") or 0) > interval_sec):
        _refresh_in_background()
    return {
        "current_version": APP_VERSION,
        "latest_version": latest_version or APP_VERSION,
        "update_available": bool(current_tuple and latest_tuple and latest_tuple > current_tuple),
        "last_checked_at": state.get("last_checked_at") or "",
    }


def _refresh_in_background() -> None:
    if not _background_check.acquire(blocking=False):
        return

    def check():
        try:
            panel_release_status(force_refresh=True)
        finally:
            _background_check.release()

    threading.Thread(target=check, name="spanel-release-check", daemon=True).start()


def _read_panel_update_log(max_lines: int = 120) -> list[str]:
    lines: list[str] = []
    try:
        have_journal = subprocess.run(
            ["systemctl", "cat", "spanel-panel-update.service"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        ).returncode == 0
        journal_ok = False
        if have_journal:
            completed = subprocess.run(
                ["journalctl", "-u", "spanel-panel-update.service", "-n", str(max_lines), "--no-pager", "--output=cat"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                lines = completed.stdout.splitlines()[-max_lines:]
                journal_ok = True
        if not journal_ok:
            log_path = Path("/var/log/spanel-panel-update.log")
            if log_path.exists():
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    lines = handle.read().splitlines()[-max_lines:]
    except Exception:
        return []
    return [sanitize_update_text(line) for line in lines]


def list_snapshots() -> list[dict]:
    """Return available pre-update and manual rollback snapshots sorted newest first."""
    items: list[dict] = []
    try:
        if not SNAPSHOT_DIR.is_dir():
            return []
        for tar_file in sorted(SNAPSHOT_DIR.glob("snapshot-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
            snap_id = tar_file.name[:-7]  # strip .tar.gz
            meta_file = SNAPSHOT_DIR / f"{snap_id}.json"
            meta = {}
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            stat = tar_file.stat()
            size_bytes = int(meta.get("size_bytes") or stat.st_size)
            ver = meta.get("version") or ""
            if not ver and snap_id.startswith("snapshot-v"):
                parts = snap_id[len("snapshot-v"):].split("-", 1)
                ver = parts[0] if parts else APP_VERSION
            created_at = meta.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
            items.append({
                "id": snap_id,
                "version": ver or APP_VERSION,
                "created_at": created_at,
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 2),
                "reason": meta.get("reason") or "pre-update",
                "db_revision": meta.get("db_revision") or "",
                "has_db": bool(meta.get("has_db", True)),
                "has_system_files": bool(meta.get("has_system_files", False)),
            })
    except Exception:
        return []
    return items


def create_snapshot(reason: str = "manual") -> dict:
    """Create a recovery snapshot of the current Panel code, frontend dist, helper, and SQLite DB."""
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]", "", reason or "manual")[:32] or "manual"
    keep = int(get_update_source_config().get("snapshot_keep") or DEFAULT_SNAPSHOT_KEEP)
    res = shell.privileged(
        "panel-snapshot-create",
        helper_args=[safe_reason, str(max(1, min(20, keep)))],
        check=False,
        fallback=["bash", "-lc", f"echo snapshot-v{APP_VERSION}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"],
    )
    snap_id = (res.stdout or "").strip().splitlines()[-1] if (res.stdout or "").strip() else ""
    snapshots = list_snapshots()
    for s in snapshots:
        if s["id"] == snap_id:
            return s
    return {
        "id": snap_id or f"snapshot-v{APP_VERSION}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}",
        "version": APP_VERSION,
        "created_at": _utc_now(),
        "size_mb": 0.0,
        "reason": safe_reason,
    }


def rollback_to_snapshot(snapshot_id: str, restore_db: bool = False) -> dict:
    """Restore the panel to a snapshot: code, frontend, helper, the system
    files the updater rewrites, the recorded Python packages and, with
    restore_db, the database as it was before that update's migrations.

    The helper runs the restore in a unit of its own (it has to stop
    spanel-api, which would otherwise kill it half-way) and records the
    outcome in the update state when it is done.
    """
    if not re.fullmatch(r"snapshot-[A-Za-z0-9._-]+", snapshot_id or ""):
        raise ValueError("Invalid snapshot ID")
    args = [snapshot_id]
    if restore_db:
        args.append("--restore-db")
    state = _read_update_state()
    state.update({
        "last_update_status": "rolling_back",
        "progress_phase": "rolling_back",
        "progress_percent": 90,
        "progress_message": f"Đang khôi phục hệ thống về điểm sao lưu {snapshot_id}...",
        "rollback_snapshot_id": snapshot_id,
    })
    _write_update_state(state)

    res = shell.privileged(
        "panel-snapshot-restore",
        helper_args=args,
        check=False,
        fallback=["bash", "-lc", f"echo restored={snapshot_id}"],
    )
    if res.returncode != 0:
        state = _read_update_state()
        state.update({"last_update_status": "failed", "progress_phase": "failed",
                      "progress_message": sanitize_update_text(res.stderr or res.stdout or "Could not restore snapshot")})
        _write_update_state(state)
        raise RuntimeError(sanitize_update_text(res.stderr or res.stdout or "Could not restore snapshot"))

    if "scheduled=" in (res.stdout or ""):
        return {"restored": snapshot_id, "status": "rolling_back", "restore_db": restore_db}

    state = _read_update_state()
    state.update({
        "last_update_status": "rolled_back",
        "last_update_finished_at": _utc_now(),
        "progress_phase": "rolled_back",
        "progress_percent": 100,
        "progress_message": f"Đã khôi phục thành công về bản sao lưu {snapshot_id}.",
        "rollback_snapshot_id": snapshot_id,
    })
    _write_update_state(state)
    return {"restored": snapshot_id, "status": "rolled_back", "restore_db": restore_db}


def delete_snapshot(snapshot_id: str) -> dict:
    if not re.fullmatch(r"snapshot-[A-Za-z0-9._-]+", snapshot_id or ""):
        raise ValueError("Invalid snapshot ID")
    shell.privileged(
        "panel-snapshot-delete",
        helper_args=[snapshot_id],
        check=False,
        fallback=["bash", "-lc", f"echo deleted={snapshot_id}"],
    )
    return {"deleted": snapshot_id}


def status(force_refresh: bool = False) -> dict:
    result = shell.privileged(
        "updates-status",
        check=False,
        fallback=["bash", "-lc", "apt list --upgradable 2>/dev/null | head -40"],
    )
    payload = result.__dict__.copy()
    payload["stdout"] = sanitize_update_text(payload.get("stdout") or "")
    payload["stderr"] = sanitize_update_text(payload.get("stderr") or "")
    payload["panel"] = panel_release_status(force_refresh=force_refresh)
    payload["source"] = get_masked_source_info()
    payload["snapshots"] = list_snapshots()
    payload["panel_update_log"] = _read_panel_update_log()
    try:
        from app.services import php as php_service
        payload["php_smart_guard"] = php_service.check_php_updates(force_refresh=force_refresh)
    except Exception:
        payload["php_smart_guard"] = None
    try:
        payload["core_stack"] = check_core_stack_updates()
    except Exception:
        payload["core_stack"] = None
    return payload


CORE_STACK_SERVICES = [
    {"pkg": "nginx", "label": "Nginx Web Server", "systemd": "nginx", "default_ver": "1.24.0-2ubuntu7.1", "precheck": "nginx -t", "reload": "systemctl reload nginx"},
    {"pkg": "mariadb-server", "label": "MariaDB SQL Engine", "systemd": "mariadb", "default_ver": "1:10.11.8-0ubuntu0.24.04.1", "precheck": "", "reload": ""},
    {"pkg": "redis-server", "label": "Redis In-Memory Cache", "systemd": "redis-server", "default_ver": "5:7.0.15-1ubuntu0.24.04.1", "precheck": "", "reload": ""},
    {"pkg": "pdns-server", "label": "PowerDNS Authoritative", "systemd": "pdns", "default_ver": "4.8.3-1build3", "precheck": "", "reload": ""},
    {"pkg": "exim4-daemon-heavy", "label": "Exim4 Mail MTA", "systemd": "exim4", "default_ver": "4.97-4ubuntu4.2", "precheck": "", "reload": "systemctl reload exim4"},
    {"pkg": "dovecot-core", "label": "Dovecot IMAP/POP3", "systemd": "dovecot", "default_ver": "1:2.3.21+dfsg1-2ubuntu6.1", "precheck": "", "reload": "systemctl reload dovecot"},
    {"pkg": "rspamd", "label": "Rspamd Anti-Spam", "systemd": "rspamd", "default_ver": "3.8.4-1~noble", "precheck": "", "reload": "systemctl reload rspamd"},
    {"pkg": "openssl", "label": "OpenSSL TLS Library", "systemd": "", "default_ver": "3.0.13-0ubuntu3.4", "precheck": "", "reload": ""},
    {"pkg": "fail2ban", "label": "Fail2ban Intrusion IPS", "systemd": "fail2ban", "default_ver": "1.0.2-3ubuntu0.1", "precheck": "", "reload": "systemctl reload fail2ban"},
]


def check_core_stack_updates() -> dict:
    """Inspect core infrastructure packages (Nginx, MariaDB, Redis, PowerDNS, Exim4, Dovecot, Rspamd, OpenSSL, Fail2ban) for security patches."""
    items: list[dict] = []
    updates_count = 0
    is_linux = os.name != "nt" and Path("/usr/bin/dpkg-query").exists()

    for svc in CORE_STACK_SERVICES:
        pkg = svc["pkg"]
        installed_ver = svc["default_ver"]
        candidate_ver = svc["default_ver"]
        update_available = False
        service_active = True

        if is_linux:
            try:
                pol = subprocess.run(
                    ["apt-cache", "policy", pkg],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if pol.returncode == 0 and pol.stdout:
                    for line in pol.stdout.splitlines():
                        ls = line.strip()
                        if ls.startswith("Installed:"):
                            val = ls.split(":", 1)[1].strip()
                            if val and val != "(none)":
                                installed_ver = val
                        elif ls.startswith("Candidate:"):
                            val = ls.split(":", 1)[1].strip()
                            if val and val != "(none)":
                                candidate_ver = val
                    if installed_ver and candidate_ver and installed_ver != candidate_ver:
                        update_available = True
            except Exception:
                pass

            if svc["systemd"]:
                try:
                    act = subprocess.run(
                        ["systemctl", "is-active", svc["systemd"]],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=3,
                    )
                    service_active = (act.stdout or "").strip() == "active"
                except Exception:
                    service_active = True

        if update_available:
            updates_count += 1

        items.append({
            "pkg": pkg,
            "label": svc["label"],
            "systemd": svc["systemd"],
            "current_version": installed_ver,
            "latest_version": candidate_ver,
            "update_available": update_available,
            "service_active": service_active,
            "zero_downtime_reload": bool(svc["reload"] or not svc["systemd"]),
        })

    state = _read_update_state()
    history = state.get("core_stack_history") if isinstance(state.get("core_stack_history"), list) else []
    return {
        "checked_at": _utc_now(),
        "updates_available_count": updates_count,
        "services": items,
        "recent_updates": history[:10],
    }


CORE_STACK_RESULT_RE = re.compile(r"^(upgraded|post_check|reloaded)=([A-Za-z0-9.,:+_-]*)$")


def parse_core_stack_output(stdout: str) -> dict:
    """What the helper says it did: `upgraded=a,b`, `post_check=ok|failed:...`,
    `reloaded=a,b`, one per line. Anything else is apt's own chatter."""
    found: dict = {"upgraded": [], "reloaded": [], "post_check": ""}
    for line in (stdout or "").splitlines():
        match = CORE_STACK_RESULT_RE.match(line.strip())
        if not match:
            continue
        key, value = match.groups()
        if key == "post_check":
            found["post_check"] = value
        else:
            found[key] = [item for item in value.split(",") if item]
    return found


def upgrade_core_stack_packages(packages: Optional[list[str]] = None, trigger: str = "manual") -> dict:
    """Upgrade selected (or all) core stack packages through the helper.

    Everything that needs root happens there in one place: nginx -t before
    apt is touched, `apt-get install --only-upgrade`, nginx -t again, and a
    reload of each service whose package version actually changed. These
    checks used to run here as the panel account, where `systemctl reload`
    is refused and `nginx -t` cannot read the certificates - so every run
    reported a warning whatever happened.

    Packages are no longer put on hold afterwards: a hold also stops security
    updates, and unattended-upgrades is kept away from these packages by a
    Package-Blacklist instead (see set_core_stack_blacklist).
    """
    allowed_map = {item["pkg"]: item for item in CORE_STACK_SERVICES}
    if packages:
        target_pkgs = [p for p in packages if p in allowed_map]
    else:
        target_pkgs = list(allowed_map.keys())
    if not target_pkgs:
        raise ValueError("Không có gói hạ tầng hợp lệ để cập nhật.")

    res = shell.privileged(
        "updates-core-stack-patch",
        helper_args=target_pkgs,
        check=False,
        fallback=["bash", "-lc", "echo upgraded=; echo post_check=ok"],
        timeout=3600,
    )
    parsed = parse_core_stack_output(res.stdout)
    ok = res.returncode == 0 and not parsed["post_check"].startswith("failed")
    upgraded = [p for p in parsed["upgraded"] if p in allowed_map]
    labels = [allowed_map[p]["label"] for p in upgraded]
    if not ok:
        lines = (res.stderr or res.stdout or "").strip().splitlines()
        detail = parsed["post_check"] or sanitize_update_text(lines[-1] if lines else "helper failed")
        summary = f"Cập nhật Core Stack thất bại: {detail[:300]}"
    elif upgraded:
        summary = (f"Đã cập nhật an toàn {len(upgraded)} thành phần Core Stack: "
                   f"{', '.join(labels[:4])}{'...' if len(labels) > 4 else ''}.")
    else:
        summary = "Không có bản cập nhật mới cho các gói Core Stack đã chọn."

    state = _read_update_state()
    history = state.get("core_stack_history") if isinstance(state.get("core_stack_history"), list) else []
    history.insert(0, {
        "id": f"core-{int(time.time())}",
        "timestamp": _utc_now(),
        "packages": target_pkgs,
        "upgraded": upgraded,
        "reloaded": parsed["reloaded"],
        "trigger": trigger if trigger in {"manual", "scheduled"} else "manual",
        "summary": summary,
        "status": "completed" if ok else "failed",
    })
    state["core_stack_history"] = history[:20]
    _write_update_state(state)

    return {
        "status": "completed" if ok else "failed",
        "upgraded": upgraded,
        "reloaded": parsed["reloaded"],
        "summary": summary,
        "core_stack": check_core_stack_updates(),
    }


def set_core_stack_blacklist(enabled: bool, packages: Optional[list[str]] = None):
    """Keep unattended-upgrades off the packages the panel patches itself.

    Two mechanisms upgrading nginx is one too many: unattended-upgrades does
    not run nginx -t and does not know the panel's window. While scheduled
    core stack patching is on, the helper writes a Package-Blacklist for the
    selected packages; turning it off removes the file and hands them back.
    """
    allowed = {item["pkg"] for item in CORE_STACK_SERVICES}
    selected = [p for p in (packages or []) if p in allowed]
    if enabled and not selected:
        enabled = False
    return shell.privileged(
        "updates-core-blacklist",
        helper_args=(["on", *selected] if enabled else ["off"]),
        check=False,
        fallback=["bash", "-lc", "echo core stack blacklist unchanged (no helper)"],
    )


def run_os_update():
    return shell.privileged(
        "updates-os-run",
        check=False,
        fallback=[
            "bash",
            "-lc",
            "nohup bash -lc 'apt-get update && apt-get upgrade -y' >/tmp/spanel-os-update.log 2>&1 & echo OS update started in background. Log: /tmp/spanel-os-update.log",
        ],
    )


def configure_os_auto_update(enabled: bool, mode: str, auto_reboot: bool):
    if mode not in {"security", "all"}:
        raise ValueError("Unsupported OS auto-update mode")
    return shell.privileged(
        "updates-os-auto",
        helper_args=["on" if enabled else "off", mode, "on" if auto_reboot else "off"],
        check=False,
        fallback=["bash", "-lc", "echo unattended-upgrades helper is not installed"],
    )


def run_panel_update():
    state = _read_update_state()
    state.update(
        {
            "last_update_status": "running",
            "last_update_started_at": _utc_now(),
            "last_update_message": "Đang khởi tạo tiến trình cập nhật an toàn (Transactional Update)...",
            "progress_percent": 5,
            "progress_phase": "starting",
            "progress_message": "Đang khởi tạo tiến trình cập nhật an toàn & chuẩn bị Snapshot dự phòng...",
        }
    )
    _write_update_state(state)
    result = shell.privileged(
        "updates-panel-run",
        check=False,
        fallback=["bash", "installer/update.sh"],
    )
    if result.returncode != 0:
        state = _read_update_state()
        err_msg = sanitize_update_text((result.stderr or result.stdout or "Không thể khởi chạy tiến trình cập nhật Panel.").strip())
        state.update(
            {
                "last_update_status": "failed",
                "last_update_finished_at": _utc_now(),
                "last_update_message": err_msg,
                "progress_phase": "failed",
                "progress_message": err_msg,
            }
        )
        _write_update_state(state)
    return result
