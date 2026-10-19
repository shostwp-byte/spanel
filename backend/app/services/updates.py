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
SNAPSHOT_DIR = Path(os.environ.get("BACKUP_ROOT") or "/var/backups/spanel") / "panel-snapshots"

DEFAULT_REPO_URL = os.environ.get("SPANEL_REPO_URL") or os.environ.get("spanel_REPO_URL") or "https://github.com/shostwp-byte/spanel.git"
DEFAULT_MANIFEST_URL = os.environ.get("SPANEL_UPDATE_MANIFEST_URL") or ""

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
            })
    except Exception:
        return []
    return items


def create_snapshot(reason: str = "manual") -> dict:
    """Create a recovery snapshot of the current Panel code, frontend dist, helper, and SQLite DB."""
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]", "", reason or "manual")[:32] or "manual"
    res = shell.privileged(
        "panel-snapshot-create",
        helper_args=[safe_reason],
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
    """Restore the panel to a specific snapshot and restart spanel-api."""
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
        raise RuntimeError(sanitize_update_text(res.stderr or res.stdout or "Could not restore snapshot"))

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
    return {"restored": snapshot_id, "status": "rolled_back"}


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
    return payload


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
