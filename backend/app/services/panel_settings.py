import json
import logging
import os
import re
import stat
import time
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.services import panel_ipv6, server_network
from app.services.shell import shell


SETTINGS_DIR = Path(os.environ.get("spanel_DATA_DIR", "/var/lib/spanel"))
SETTINGS_FILE = SETTINGS_DIR / "panel-settings.json"
ASSETS_DIR = SETTINGS_DIR / "assets"
MAX_ASSET_SIZE = 1024 * 1024
ALLOWED_ASSET_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "ico": "image/x-icon",
}
DOMAIN_RE = re.compile(r"^(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,}$")
IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


class SettingsUnreadable(RuntimeError):
    """The settings file is there but could not be read or parsed.

    Distinct from "there are no settings yet", which is an empty dict. The
    difference matters because almost every writer here reads the file, adds a
    key and writes the whole thing back: if a failed read looks like an empty
    file, that sequence silently erases every setting the panel had.

    It happened. A settings write performed as root left the file owned by
    root; the panel account's next read was denied, the error was swallowed
    into `{}`, and the following write cut the file from six keys to one,
    taking the malware schedule and the panel name with it.
    """


def _read_raw() -> dict:
    """Settings as stored. Empty only when the file genuinely is not there."""
    try:
        text = SETTINGS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise SettingsUnreadable(f"cannot read {SETTINGS_FILE}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsUnreadable(f"{SETTINGS_FILE} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsUnreadable(f"{SETTINGS_FILE} does not hold an object")
    return data


def _read_raw_lenient() -> dict:
    """For lookups that must not fail a request when the file is unreadable.

    These callers read one value and can live with a default. Nothing that
    writes may use this - see SettingsUnreadable.
    """
    try:
        return _read_raw()
    except SettingsUnreadable as exc:
        logging.getLogger("spanel.panel_settings").warning("%s", exc)
        return {}


def _write_raw(data: dict) -> None:
    """Replace the settings file, keeping whose it is.

    NamedTemporaryFile creates the replacement owned by whoever is running,
    mode 0600. Written by root - an admin running a maintenance script, say -
    that hands the panel account a file it can no longer read. Carry the
    existing owner and mode across, so a write by root leaves the file exactly
    as the panel account had it.
    """
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        previous = SETTINGS_FILE.stat()
    except FileNotFoundError:
        previous = None

    with NamedTemporaryFile("w", encoding="utf-8", dir=str(SETTINGS_DIR), delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=True, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)

    try:
        if previous is not None:
            os.chmod(tmp_path, stat.S_IMODE(previous.st_mode))
            if hasattr(os, "chown"):
                os.chown(tmp_path, previous.st_uid, previous.st_gid)
        else:
            os.chmod(tmp_path, 0o600)
            if hasattr(os, "chown"):
                # No file yet: the directory says who the panel runs as.
                owner = SETTINGS_DIR.stat()
                os.chown(tmp_path, owner.st_uid, owner.st_gid)
    except OSError:
        # Not permitted to chown (already the owner, or a filesystem that will
        # not). The replace below is still correct.
        pass

    tmp_path.replace(SETTINGS_FILE)


def configured_panel_url() -> str:
    """The panel URL an admin set, without the cost of current_settings().

    Link builders need this on every request; refreshing the malware scan
    status to read one string would be a poor trade.
    """
    return (_read_raw_lenient().get("panel_url") or settings.panel_url or "").strip()


def get_brand(db=None, reseller_id: int | None = None):
    """Return a lightweight brand descriptor with ``panel_domain``, ``panel_url``, and ``app_name``."""
    from types import SimpleNamespace

    raw = _read_raw_lenient()
    app_name = (raw.get("app_name") or settings.app_name or "spanel").strip() or "spanel"
    panel_url = (raw.get("panel_url") or settings.panel_url or "").strip()
    panel_domain = ""
    if panel_url:
        try:
            _scheme, panel_domain, _port = parse_panel_url(panel_url)
        except ValueError:
            panel_domain = ""
    if db is not None and reseller_id:
        try:
            from app.models.entities import ResellerBranding
            rb = db.query(ResellerBranding).filter(ResellerBranding.reseller_id == reseller_id).first()
            if rb:
                if getattr(rb, "brand_name", None):
                    app_name = rb.brand_name
                if getattr(rb, "custom_domain", None):
                    panel_domain = rb.custom_domain
                    panel_url = f"https://{rb.custom_domain}"
        except Exception:
            pass
    return SimpleNamespace(app_name=app_name, panel_url=panel_url, panel_domain=panel_domain)


def global_blocked_bots() -> list[str]:
    """The server-wide bad-bot list.

    Read through _read_raw rather than current_settings(): that one refreshes
    the malware scan status, which is far too much work to answer "what bots
    are blocked" on every vhost render.
    """
    from app.services import nginx

    return nginx.normalize_blocked_bots(_read_raw_lenient().get("global_blocked_bots") or "")


def crs_mode() -> str:
    """The server-wide OWASP CRS mode an admin chose: off, detect, or block.

    Read through _read_raw for the same reason global_blocked_bots does: this is
    consulted on every vhost and site-rule render.
    """
    return (_read_raw_lenient().get("crs_mode") or "off").strip().lower()


def stored_crs_mode() -> str | None:
    """The CRS mode an admin chose, or None when none was ever recorded.

    crs_mode() cannot tell those apart - both read as "off" - and the
    difference is the whole point: a missing key means the panel has lost
    track of the mode, not that somebody turned CRS off.
    """
    value = _read_raw_lenient().get("crs_mode")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def save_crs_mode(mode: str) -> str:
    """Store the CRS mode. Callers re-render the site rule files - see
    waf.set_crs_mode(), which is the only thing that should call this."""
    value = (mode or "off").strip().lower()
    data = _read_raw()
    data["crs_mode"] = value
    _write_raw(data)
    return value


def save_global_blocked_bots(raw) -> list[str]:
    """Store the server-wide list. Callers are responsible for re-rendering the
    vhosts afterwards - see waf.resync_bot_blocks()."""
    from app.services import nginx

    bots = nginx.normalize_blocked_bots(raw)
    data = _read_raw()
    data["global_blocked_bots"] = "\n".join(bots)
    _write_raw(data)
    return bots


def _asset_url(filename: str | None) -> str:
    if not filename:
        return ""
    path = ASSETS_DIR / filename
    if not path.exists():
        return ""
    info = path.stat()
    version = f"{info.st_mtime_ns}-{info.st_size}"
    return f"/brand-assets/{filename}?v={version}"


def _is_ipv4(host: str) -> bool:
    if not IPV4_RE.fullmatch(host):
        return False
    return all(0 <= int(part) <= 255 for part in host.split("."))


def is_domain(host: str) -> bool:
    return bool(DOMAIN_RE.fullmatch((host or "").lower()))


def default_ssl_email(host: str) -> str:
    return f"admin@{host.lower()}" if is_domain(host) else ""


def normalize_panel_hostname(value: str) -> str:
    host = (value or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Panel hostname is required")
    if "://" in host or "/" in host or ":" in host:
        raise ValueError("Panel hostname must not include a scheme, port, or path")
    if not is_domain(host) and not _is_ipv4(host) and host != "localhost":
        raise ValueError("Panel hostname must be a domain name or IPv4 address")
    return host


def normalize_panel_port(value: int | str | None) -> int:
    try:
        port = int(value or settings.panel_port or 2222)
    except (TypeError, ValueError) as exc:
        raise ValueError("Panel port is invalid") from exc
    if port < 1 or port > 65535:
        raise ValueError("Panel port is out of range")
    return port


def normalize_panel_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Panel URL is required")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Panel URL must start with http:// or https://")
    host = normalize_panel_hostname(host)
    port = normalize_panel_port(parsed.port or settings.panel_port or 2222)
    return f"{scheme}://{host}:{port}"


def panel_url_from_parts(hostname: str, port: int | str | None, scheme: str | None = None) -> str:
    safe_scheme = (scheme or "http").lower()
    if safe_scheme not in {"http", "https"}:
        raise ValueError("Panel URL scheme must be http or https")
    host = normalize_panel_hostname(hostname)
    safe_port = normalize_panel_port(port)
    return f"{safe_scheme}://{host}:{safe_port}"


def parse_panel_url(value: str) -> tuple[str, str, int]:
    normalized = normalize_panel_url(value)
    parsed = urlparse(normalized)
    return parsed.scheme, parsed.hostname or "", parsed.port or 2222


def panel_ssl_mode() -> str:
    """How the panel got the certificate it is serving.

    'letsencrypt' and 'domain' are both real certificates â€” the first issued for
    the panel's own hostname, the second borrowed from a website hosted here.
    'selfsigned' is the default a server with no domain gets; 'none' means the
    panel is answering in the clear, which should not happen any more.
    """
    mode = (getattr(settings, "panel_ssl_mode", "") or os.environ.get("PANEL_SSL_MODE") or "").strip().lower()
    if mode in {"letsencrypt", "domain", "selfsigned"}:
        return mode if has_panel_certificate() else "none"
    if not has_panel_certificate():
        return "none"
    # Installed before the mode was recorded: the filename says which it is.
    return "selfsigned" if "selfsigned" in (settings.panel_ssl_cert or "") else "letsencrypt"


def domains_with_certificate() -> list[str]:
    """Websites on this server whose certificate the panel could borrow.

    Asked through the helper: /etc/letsencrypt/live is root-only, so the panel
    reading it directly finds nothing and reports, wrongly, that there is
    nothing to borrow.
    """
    result = shell.privileged(
        "panel-ssl-domains",
        check=False,
        timeout=30,
        fallback=["bash", "-lc", "echo ''"],
    )
    if result.returncode != 0:
        return []
    found = []
    for line in (result.stdout or "").splitlines():
        name = line.strip().strip("/")
        if name and name != "README" and DOMAIN_RE.fullmatch(name):
            found.append(name)
    return sorted(set(found))


def use_domain_certificate(domain: str, panel_port: int | None = None) -> dict:
    """Serve the panel with a certificate a website here already has."""
    host = normalize_panel_hostname(domain)
    port = int(panel_port or settings.panel_port or 2222)
    if host not in domains_with_certificate():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{host} has no certificate on this machine. Install SSL for that website first.",
        )
    result = shell.privileged(
        "panel-ssl-use-domain",
        helper_args=[host, str(port)],
        check=False,
        timeout=300,
        fallback=["bash", "-lc", "echo dry-run-panel-ssl-use-domain"],
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(result.stderr or result.stdout or "Could not switch the panel certificate").strip()[-500:],
        )
    return {
        "message": (
            f"The panel uses {host}'s certificate by default. "
            "Other domains with SSL on this machine still reach the panel with their own certificate."
        ),
        "panel_url": f"https://{host}:{port}",
    }


def regenerate_self_signed(panel_port: int | None = None) -> dict:
    """Go back to a certificate the panel signs for itself."""
    data = _read_raw_lenient()
    host = ""
    if data.get("panel_url") or settings.panel_url:
        try:
            _scheme, host, _port = parse_panel_url(data.get("panel_url") or settings.panel_url)
        except ValueError:
            host = ""
    host = host or (os.environ.get("PANEL_DOMAIN") or "").strip() or "127.0.0.1"
    port = int(panel_port or settings.panel_port or 2222)
    result = shell.privileged(
        "panel-ssl-selfsigned",
        helper_args=[host, str(port)],
        check=False,
        timeout=300,
        fallback=["bash", "-lc", "echo dry-run-panel-ssl-selfsigned"],
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(result.stderr or result.stdout or "Could not generate a certificate").strip()[-500:],
        )
    return {"message": "The panel is using a self-signed certificate.", "panel_url": f"https://{host}:{port}"}


def has_panel_certificate() -> bool:
    cert_pairs = [
        (settings.panel_ssl_cert, settings.panel_ssl_key),
        ("/etc/spanel/panel-fullchain.pem", "/etc/spanel/panel-privkey.pem"),
    ]
    return any(bool(cert) and bool(key) and Path(cert).exists() and Path(key).exists() for cert, key in cert_pairs)


def current_settings() -> dict:
    data = _read_raw_lenient()
    app_name = (data.get("app_name") or settings.app_name or "spanel").strip() or "spanel"
    panel_url = data.get("panel_url") or settings.panel_url or ""
    panel_hostname = ""
    panel_port = settings.panel_port or 2222
    if panel_url:
        try:
            _scheme, panel_hostname, panel_port = parse_panel_url(panel_url)
        except ValueError:
            panel_hostname = ""
            panel_port = settings.panel_port or 2222
    ssl_enabled = panel_url.startswith("https://") or has_panel_certificate()
    from app.services import malware_scan

    mw = malware_scan.refresh_status()
    return {
        "app_name": app_name,
        "panel_url": panel_url,
        "panel_hostname": panel_hostname,
        "primary_color": data.get("primary_color") or "#4f46e5",
        "panel_port": panel_port,
        "logo_url": _asset_url(data.get("logo_filename")),
        "favicon_url": _asset_url(data.get("favicon_filename")) or "/favicon.png",
        "login_background_url": _asset_url(data.get("login_background_filename")),
        "ticket_url": data.get("ticket_url") or "",
        "ssl_enabled": ssl_enabled,
        "ssl_mode": panel_ssl_mode(),
        "ipv6": panel_ipv6.status(),
        # Where this server answers. Global addresses only: loopback and
        # link-local reach nobody.
        "server_ipv4": server_network.ipv4_addresses(),
        "malware_scan_enabled": mw["enabled"],
        "malware_scan_installed": mw["installed"],
        "malware_scan_active": mw["active"],
        "malware_scan_detail": mw["detail"],
    }


def update_settings(
    primary_color: str | None = None,
    app_name: str | None = None,
    panel_hostname: str | None = None,
    panel_port: int | None = None,
    panel_url: str | None = None,
    ticket_url: str | None = None,
) -> dict:
    if panel_port is not None:
        # Rewrite .env file to update PANEL_PORT
        env_path = Path(__file__).parent.parent.parent / '.env'
        if env_path.exists():
            env_content = env_path.read_text(encoding='utf-8')
            import re
            if 'PANEL_PORT=' in env_content:
                env_content = re.sub(r'^PANEL_PORT=.*', f'PANEL_PORT={panel_port}', env_content, flags=re.MULTILINE)
            else:
                env_content += f'\nPANEL_PORT={panel_port}'
            env_path.write_text(env_content, encoding='utf-8')
        else:
            env_path.write_text(f'PANEL_PORT={panel_port}', encoding='utf-8')
        data = _read_raw()
        data['panel_port'] = panel_port
        _write_raw(data)
    data = _read_raw()
    if app_name is not None:
        value = app_name.strip()
        if not 2 <= len(value) <= 80:
            raise ValueError("Panel name must be 2-80 characters")
        data["app_name"] = value
    if ticket_url is not None:
        data["ticket_url"] = ticket_url.strip()
    if (panel_hostname is not None and panel_hostname.strip()) or (panel_url is not None and panel_url.strip()):
        existing_url = data.get("panel_url") or settings.panel_url or ""
        existing_normalized = normalize_panel_url(existing_url) if existing_url else ""
        existing_scheme, existing_host, existing_port = parse_panel_url(existing_normalized) if existing_normalized else ("http", "", settings.panel_port or 2222)
        # Use the newly provided panel_port, otherwise fallback to existing_port
        target_port = panel_port if panel_port is not None else existing_port
        if panel_hostname is not None and panel_hostname.strip():
            normalized = panel_url_from_parts(panel_hostname, target_port, existing_scheme)
        elif panel_url is not None and panel_url.strip():
            requested_scheme, requested_host, _requested_port = parse_panel_url(panel_url)
            normalized = panel_url_from_parts(requested_host, target_port, requested_scheme)
        else:
            normalized = panel_url_from_parts(existing_host, target_port, existing_scheme)
        if not normalized:
            raise ValueError("Panel hostname is required")
        scheme, host, port = parse_panel_url(normalized)
        if scheme == "https" and not has_panel_certificate():
            raise ValueError("Use Install SSL before saving an HTTPS panel URL")
        if normalized != existing_normalized:
            result = shell.privileged(
                "panel-url-set",
                helper_args=[scheme, host, str(port)],
                check=False,
                fallback=["bash", "-lc", "true"],
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Could not update panel URL").strip())
        data["panel_url"] = normalized
    _write_raw(data)
    return current_settings()


def detect_asset_type(content: bytes, filename: str) -> tuple[str, str]:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    if content.startswith(b"\x00\x00\x01\x00"):
        return "ico", "image/x-icon"
    if suffix in ALLOWED_ASSET_TYPES:
        raise ValueError("Uploaded file content does not match its image type")
    raise ValueError("Only PNG, JPG, WEBP, and ICO images are supported")


async def save_asset(kind: str, upload: UploadFile) -> dict:
    if kind not in {"logo", "favicon", "login_background"}:
        raise ValueError("Invalid asset kind")
    content = await upload.read(MAX_ASSET_SIZE + 1)
    if len(content) > MAX_ASSET_SIZE:
        raise ValueError("Image must be 1 MB or smaller")
    ext, _media_type = detect_asset_type(content, upload.filename or "")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_raw()
    previous = data.get(f"{kind}_filename")
    if previous:
        try:
            (ASSETS_DIR / previous).unlink()
        except OSError:
            pass
    filename = f"{kind}.{ext}"
    (ASSETS_DIR / filename).write_bytes(content)
    data[f"{kind}_filename"] = filename
    _write_raw(data)
    return current_settings()


def asset_path(filename: str) -> tuple[Path, str]:
    if not re.fullmatch(r"(?:logo|favicon|login_background)\.(?:png|jpg|jpeg|webp|ico)", filename or ""):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    path = ASSETS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    media_type = ALLOWED_ASSET_TYPES.get(path.suffix.lower().lstrip("."), "application/octet-stream")
    return path, media_type


def install_panel_ssl(email: str | None = None, panel_hostname: str | None = None, panel_port: int | None = None, panel_url: str | None = None) -> dict:
    if panel_hostname:
        normalized = panel_url_from_parts(panel_hostname, panel_port, "http")
    elif panel_url:
        normalized = normalize_panel_url(panel_url)
    else:
        raise ValueError("Panel hostname is required")
    _scheme, host, port = parse_panel_url(normalized)
    if not is_domain(host):
        raise ValueError("Panel SSL requires a domain name, not an IP address")
    certbot_email = (email or settings.ssl_email or default_ssl_email(host)).strip()
    helper_args = [host, str(port)]
    if certbot_email:
        helper_args.append(certbot_email)
    result = shell.privileged(
        "panel-ssl-install",
        helper_args=helper_args,
        check=False,
        fallback=["bash", "-lc", "true"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not install panel SSL").strip())
    data = _read_raw()
    data["panel_url"] = f"https://{host}:{port}"
    _write_raw(data)
    current = current_settings()
    current["message"] = result.stdout.strip() or f"Panel SSL enabled for {host}"
    return current


# --------------------------------------------------------------------------
# Optional ClamAV malware scanning
# --------------------------------------------------------------------------
import threading

from app.services import malware_scan as _malware_scan

MALWARE_JOBS: dict[str, dict] = {}
MALWARE_JOB_THREADS: dict[str, threading.Thread] = {}
MALWARE_JOBS_LOCK = threading.RLock()
MALWARE_JOBS_DIR = SETTINGS_DIR / "malware-scan-jobs"
MAX_MALWARE_LOG_LINES = 1000


def _persist_malware_enabled(enabled: bool) -> None:
    """Write the malware_scan_enabled flag to the panel settings file so it
    survives restarts. The flag is the source of truth that the API reads via
    ``settings.malware_scan_enabled`` (overridden here from persisted state)."""
    data = _read_raw()
    data["malware_scan_enabled"] = bool(enabled)
    _write_raw(data)


def set_malware_scan_on_upload(enabled: bool) -> dict:
    """Scan each uploaded file after the request returns, or do not.

    Off by default. With a resident clamd this costs a socket round trip; with
    the one-shot clamscan the panel installs by default it costs a full
    signature load per file - 28.2 s and 1.05 GB for a 20 MB upload, measured.
    The scheduled maldet scan covers the files either way.
    """
    from app.services import malware_queue
    from app.services.shell import shell

    enabled = bool(enabled)
    data = _read_raw()
    data[malware_queue.SETTING_KEY] = enabled
    _write_raw(data)

    # The daemon exists for this feature and nothing else: maldet runs
    # `clamscan` and never opens clamd's socket. Leaving it installed while the
    # feature is off is a second resident copy of the same ~1 GB of signatures,
    # which is how a live 8 GB server collected 16 OOM kills in 7 days.
    verb = "clamav-daemon-install" if enabled else "clamav-daemon-remove"
    try:
        shell.privileged(verb, fallback=["true"])
    except Exception:  # noqa: BLE001 - the setting is saved either way
        logging.getLogger("spanel.panel_settings").exception(
            "could not %s while turning scan-on-upload %s",
            verb, "on" if enabled else "off",
        )

    return malware_scan_status()


def _persist_malware_realtime(enabled: bool) -> None:
    data = _read_raw()
    data["malware_realtime_enabled"] = bool(enabled)
    _write_raw(data)


def set_malware_realtime(enabled: bool) -> dict:
    """Level 2: turn the LMD inotify monitor on or off.

    Installs LMD first (in the background) if it is not present yet, exactly
    like enabling the scanner does for ClamAV.
    """
    from app.services import maldet

    enabled = bool(enabled)
    if enabled and not maldet.installed():
        _persist_malware_enabled(True)
        _persist_malware_realtime(True)
        threading.Thread(target=_install_realtime_flow, daemon=True).start()
        current = malware_scan_status()
        current["detail"] = "Installing LMD; real-time protection starts once it is in place."
        return current
    _persist_malware_realtime(enabled)
    try:
        if enabled:
            maldet.monitor_start()
        else:
            maldet.monitor_stop()
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    return malware_scan_status()


def _install_realtime_flow() -> None:
    from app.services import maldet

    try:
        maldet.install()
        maldet.monitor_start()
    except Exception:  # noqa: BLE001 - recorded in the status file
        _malware_scan._write_status({**_malware_scan.refresh_status(), "detail": "LMD install/monitor failed"})


def malware_in_use() -> bool:
    """Whether this server was using the malware scanner before it became an
    addon: switched on, watching in real time, or on a schedule. Decides,
    once, whether the addon starts out installed."""
    from app.services import malware_schedule

    raw = _read_raw_lenient()
    scheduled = any(isinstance(entry, dict) and entry.get("enabled")
                    for entry in malware_schedule.get_schedules().values())
    return bool(_malware_scan._persisted_enabled() or raw.get("malware_realtime_enabled") or scheduled)


def malware_scan_status() -> dict:
    return _malware_scan.refresh_status()


def set_malware_scan(enabled: bool) -> dict:
    """Toggle malware scanning on/off.

    Enabling on a server without the engine installs LMD + the ClamAV package
    in a background thread so the API returns immediately. Disabling just turns
    the flag off (LMD/clamav are left on disk).
    """
    from app.services import maldet

    enabled = bool(enabled)
    if enabled and not (maldet.installed() or _malware_scan.clamav_installed()):
        _persist_malware_enabled(True)
        threading.Thread(target=_install_and_enable_flow, daemon=True).start()
        current = current_settings()
        current["message"] = (
            "LMD and ClamAV are installing in the background. Scanning is ready once they finish "
            "(1-3 minutes)."
        )
        return current
    _persist_malware_enabled(enabled)
    current = current_settings()
    if enabled:
        current["message"] = "Malware scanner turned on"
    else:
        _persist_malware_realtime(False)
        try:
            maldet.monitor_stop()
        except Exception:  # noqa: BLE001
            pass
        _malware_scan.stop_clamd()
        current = current_settings()
        current["message"] = "Malware scanner turned off"
    return current


def _install_and_enable_flow() -> None:
    """Background worker: install the scan engine (LMD + the ClamAV package),
    then leave the persisted flag on.

    The flag was already persisted as True by set_malware_scan(); this just
    performs the heavy install. Failures are captured in the status file.
    """
    from app.services import maldet

    try:
        # maldet-install pulls the clamav package (engine) itself and does NOT
        # enable the resident daemon.
        maldet.install()
    except Exception as exc:  # noqa: BLE001 - record failure, do not crash thread
        try:
            _malware_scan.install_clamav()  # last resort: at least get clamscan
        except Exception:
            pass
        _malware_scan._write_status(
            {**_malware_scan.refresh_status(), "detail": f"LMD install failed: {exc}"}
        )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _public_malware_job(job: dict) -> dict:
    return dict(job)


def _finalize_stale_malware_job(job: dict) -> dict:
    if job.get("status") not in {"queued", "running"}:
        return job
    with MALWARE_JOBS_LOCK:
        thread = MALWARE_JOB_THREADS.get(job.get("job_id"))
        if thread and thread.is_alive():
            return job
        MALWARE_JOB_THREADS.pop(job.get("job_id"), None)
    job = dict(job)
    job.update(
        status="interrupted",
        message="Scan interrupted. Start a new scan to continue.",
        error="Scan worker is no longer running.",
        finished_at=job.get("finished_at") or _now_iso(),
        updated_at=_now_iso(),
    )
    _write_malware_job(job)
    with MALWARE_JOBS_LOCK:
        MALWARE_JOBS[job["job_id"]] = job
    return job


def _write_malware_job(job: dict) -> None:
    try:
        MALWARE_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = MALWARE_JOBS_DIR / f"{job['job_id']}.json"
        path.write_text(json.dumps(_public_malware_job(job), indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _remember_malware_job(job: dict) -> dict:
    with MALWARE_JOBS_LOCK:
        MALWARE_JOBS[job["job_id"]] = job
    _write_malware_job(job)
    return _public_malware_job(job)


def _update_malware_job(job_id: str, **updates) -> None:
    with MALWARE_JOBS_LOCK:
        job = MALWARE_JOBS.get(job_id)
        if not job:
            return
        updates.setdefault("updated_at", _now_iso())
        job.update(updates)
        if len(job.get("log", [])) > MAX_MALWARE_LOG_LINES:
            job["log"] = job["log"][-MAX_MALWARE_LOG_LINES:]
        snapshot = dict(job)
    _write_malware_job(snapshot)


def _append_malware_log(job_id: str, line: str) -> None:
    with MALWARE_JOBS_LOCK:
        job = MALWARE_JOBS.get(job_id)
        if not job:
            return
        job.setdefault("log", []).append(f"{_now_iso()} {line}")
        job["updated_at"] = _now_iso()
        if len(job["log"]) > MAX_MALWARE_LOG_LINES:
            job["log"] = job["log"][-MAX_MALWARE_LOG_LINES:]
        snapshot = dict(job)
    _write_malware_job(snapshot)


def get_malware_scan_job(job_id: str) -> dict:
    with MALWARE_JOBS_LOCK:
        job = MALWARE_JOBS.get(job_id)
        if job:
            return _public_malware_job(_finalize_stale_malware_job(job))
    path = MALWARE_JOBS_DIR / f"{job_id}.json"
    try:
        if path.exists():
            return _public_malware_job(_finalize_stale_malware_job(json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, json.JSONDecodeError):
        pass
    raise ValueError("Scan job not found")


def get_latest_malware_scan_job() -> dict:
    with MALWARE_JOBS_LOCK:
        jobs = list(MALWARE_JOBS.values())
    try:
        if MALWARE_JOBS_DIR.exists():
            for path in MALWARE_JOBS_DIR.glob("*.json"):
                try:
                    jobs.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    unique: dict[str, dict] = {}
    for job in jobs:
        job_id = job.get("job_id")
        if not job_id:
            continue
        current = unique.get(job_id)
        if not current or (job.get("updated_at") or job.get("started_at") or job.get("created_at") or "") >= (
            current.get("updated_at") or current.get("started_at") or current.get("created_at") or ""
        ):
            unique[job_id] = job
    if not unique:
        raise ValueError("Scan job not found")
    running = [job for job in unique.values() if job.get("status") in {"queued", "running"}]
    candidates = running or list(unique.values())
    return _public_malware_job(
        _finalize_stale_malware_job(max(candidates, key=lambda job: job.get("updated_at") or job.get("started_at") or job.get("created_at") or ""))
    )


def list_malware_scan_jobs(limit: int = 50) -> list[dict]:
    jobs = []
    with MALWARE_JOBS_LOCK:
        jobs.extend(MALWARE_JOBS.values())
    try:
        if MALWARE_JOBS_DIR.exists():
            for path in MALWARE_JOBS_DIR.glob("*.json"):
                try:
                    jobs.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    unique: dict[str, dict] = {}
    for job in jobs:
        job_id = job.get("job_id")
        if not job_id:
            continue
        current = unique.get(job_id)
        stamp = job.get("updated_at") or job.get("started_at") or job.get("created_at") or ""
        current_stamp = current.get("updated_at") or current.get("started_at") or current.get("created_at") or "" if current else ""
        if not current or stamp >= current_stamp:
            unique[job_id] = job
    sorted_jobs = sorted(
        (_finalize_stale_malware_job(job) for job in unique.values()),
        key=lambda job: job.get("updated_at") or job.get("started_at") or job.get("created_at") or "",
        reverse=True,
    )
    return [_public_malware_job(job) for job in sorted_jobs[: max(1, min(limit, 200))]]


def _select_scan_websites(website_id: int | None, db) -> list:
    from app.models.entities import Website

    if website_id is None:
        return db.query(Website).order_by(Website.domain.asc()).all()
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise ValueError("Website not found")
    return [website]


# --- whole-server scan ------------------------------------------------------
# A website scan walks a few thousand files from the panel's own account. The
# whole machine is a different job: 150,000 files on a small VPS, most of them
# unreadable to anyone but root. That one runs through the helper, which hands
# clamd an open descriptor per file, and reports its progress through a log the
# panel reads while it works.

SERVER_SCAN_TIMEOUT_SECONDS = 6 * 60 * 60
SERVER_SCAN_POLL_SECONDS = 3


def _server_scan_log(job_id: str) -> Path:
    return MALWARE_JOBS_DIR / f"{job_id}.scan.log"


def _parse_clamdscan_line(line: str) -> tuple[str, str, str]:
    """One line of clamdscan output as (status, path, detail)."""
    stripped = line.strip()
    if not stripped or ":" not in stripped:
        return ("", "", "")
    path, _, rest = stripped.rpartition(":")
    rest = rest.strip()
    if not path:
        return ("", "", "")
    if rest == "OK":
        return ("clean", path, "")
    if rest.endswith(" FOUND"):
        return ("infected", path, rest[: -len(" FOUND")].strip())
    if rest.endswith(" ERROR") or rest.endswith("ERROR"):
        return ("error", path, rest.removesuffix("ERROR").strip())
    return ("", "", "")


class _ServerScanProgress:
    """Reads the scanner's log as it grows and keeps the job in step with it."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.offset = 0
        self.total = 0
        self.scanned = 0
        self.errors = 0
        self.threats: list[dict] = []
        self.pending = b""

    def poll(self) -> None:
        path = _server_scan_log(self.job_id)
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < self.offset:  # the log was replaced under us
            self.offset = 0
        if size == self.offset:
            return
        try:
            with path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(size - self.offset)
                self.offset = handle.tell()
        except OSError:
            return
        data = self.pending + chunk
        lines = data.split(b"\n")
        self.pending = lines.pop()
        for raw_line in lines:
            self._consume(raw_line.decode("utf-8", "replace"))
        self._publish()

    def _consume(self, line: str) -> None:
        if line.startswith("total="):
            try:
                self.total = int(line.split("=", 1)[1])
            except ValueError:
                pass
            return
        status, path, detail = _parse_clamdscan_line(line)
        if not status:
            return
        self.scanned += 1
        if status == "infected":
            self.threats.append({"path": path, "signature": detail, "domain": ""})
            _append_malware_log(self.job_id, f"INFECTED {path}: {detail}")
        elif status == "error":
            self.errors += 1
            if self.errors <= 50:  # a log of 150,000 unreadable sockets helps nobody
                _append_malware_log(self.job_id, f"ERROR {path}: {detail}")

    def _publish(self) -> None:
        percent = int((self.scanned / self.total) * 100) if self.total else 0
        _update_malware_job(
            self.job_id,
            total_files=self.total,
            scanned=self.scanned,
            infected=len(self.threats),
            errors=self.errors,
            threats=self.threats,
            progress_percent=min(percent, 99),
            message=(
                f"Scanning {self.scanned}/{self.total} files"
                if self.total
                else "Listing files on the server"
            ),
        )


def start_server_scan_job() -> dict:
    """Scan every file on the machine, not just the websites."""
    from app.services import maldet

    if not (maldet.installed() or _malware_scan.engine_available()):
        raise RuntimeError("The malware scanner is not installed. Turn it on first.")
    job = _new_malware_job(scope="server", message="Queued")
    if maldet.installed():
        job["engine"] = "lmd"
        return _spawn_malware_job(job, lambda: _run_maldet_job(job["job_id"], "/"))
    return _spawn_malware_job(job, lambda: _run_server_scan_job(job["job_id"]))


def _run_server_scan_job(job_id: str) -> None:
    _update_malware_job(
        job_id,
        status="running",
        started_at=_now_iso(),
        message="Listing files on the server",
    )
    _append_malware_log(job_id, "Whole-server scan started")
    progress = _ServerScanProgress(job_id)
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            progress.poll()
            stop.wait(SERVER_SCAN_POLL_SECONDS)
        progress.poll()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        result = shell.privileged(
            "malware-scan-server",
            helper_args=[job_id],
            check=False,
            timeout=SERVER_SCAN_TIMEOUT_SECONDS,
            fallback=["bash", "-lc", "echo dry-run-malware-scan-server"],
        )
        stop.set()
        watcher.join(timeout=30)
        # clamdscan exits 1 when it found something and 2 on an error; neither
        # means the scan failed to run.
        if result.returncode not in (0, 1, 2):
            detail = (result.stderr or result.stdout or "").strip()[-400:]
            raise RuntimeError(detail or f"Scanner exited with {result.returncode}")
        status = "infected" if progress.threats else "done"
        _update_malware_job(
            job_id,
            status=status,
            progress_percent=100,
            total_files=progress.total,
            scanned=progress.scanned,
            infected=len(progress.threats),
            errors=progress.errors,
            threats=progress.threats,
            message=(
                f"Scan complete: {progress.scanned} files, "
                f"{len(progress.threats)} threats, {progress.errors} errors"
            ),
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, "Scan finished")
        _notify_threats(job_id, progress.threats)
    except Exception as exc:  # noqa: BLE001 - a failed scan is reported, not raised
        stop.set()
        _update_malware_job(
            job_id,
            status="error",
            message="Scan failed",
            error=str(exc),
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, f"ERROR scan failed: {exc}")
    finally:
        stop.set()
        with MALWARE_JOBS_LOCK:
            MALWARE_JOB_THREADS.pop(job_id, None)
        try:
            _server_scan_log(job_id).unlink(missing_ok=True)
        except OSError:
            pass


# --- LMD (Linux Malware Detect) scan path ---------------------------------
# When maldet is installed it replaces the per-file clamdscan loop: one
# `maldet -a /home` (or `-r /home <days>` for the incremental) is far faster and
# reports named malware families. The clamd path stays as the fallback.

_DOMAIN_IN_PATH = re.compile(r"^/home/[^/]+/([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+)/")


def _domain_from_path(path: str) -> str:
    m = _DOMAIN_IN_PATH.match(path or "")
    return m.group(1) if m else ""


def _notify_threats(job_id: str, threats: list[dict]) -> None:
    """Administrators hear about every find (customers get no notifications)."""
    if not threats:
        return
    try:
        from app.services import notifications

        brief = [{"path": t.get("path", ""), "signature": t.get("signature", "")} for t in threats]
        notifications.notify("malware_found_admin", {"threats": brief, "count": len(brief)}, admins=True,
                             dedupe_key=f"malware:{job_id}")
    except Exception:  # noqa: BLE001 - the scan's own record already holds the result
        pass


# What the job says at each stage of an LMD scan. Fixed sentences, so the
# interface can translate them; the numbers are in their own fields.
_MALDET_STAGE_MESSAGES = {
    "starting": "Starting the scan...",
    "listing": "Building the file list...",
    "scanning": "Scanning files...",
    "results": "Reading the results...",
}


def _publish_maldet_progress(job_id: str, progress: dict) -> None:
    stage = progress.get("stage")
    if stage not in _MALDET_STAGE_MESSAGES:
        return
    total = int(progress.get("total") or 0)
    scanned = total if stage == "results" else min(int(progress.get("scanned") or 0), total)
    _update_malware_job(
        job_id,
        total_files=total,
        scanned=scanned,
        progress_percent=min(99, int(scanned * 100 / total)) if total else 0,
        message=_MALDET_STAGE_MESSAGES[stage],
    )


def _run_maldet_job(job_id: str, target: str, *, recent_days: int | None = None) -> None:
    from app.services import maldet

    kind = f"incremental, {recent_days} days" if recent_days else "full"
    _update_malware_job(
        job_id, status="running", started_at=_now_iso(), engine="lmd",
        message=_MALDET_STAGE_MESSAGES["starting"],
    )
    _append_malware_log(job_id, f"maldet scan ({kind}) of {target} started")
    # maldet runs in the foreground until the scan is over - hours on a whole
    # server - and says nothing on the way. A second thread asks the helper
    # how far it has got, so the job's count and percentage move meanwhile.
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(SERVER_SCAN_POLL_SECONDS):
            try:
                progress = maldet.progress(job_id)
            except Exception as exc:  # noqa: BLE001 - a missed reading is not a failed scan
                logging.getLogger("spanel.panel_settings").debug("scan %s: no progress reading: %s", job_id, exc)
                continue
            if not stop.is_set():
                _publish_maldet_progress(job_id, progress)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        try:
            result = maldet.scan(job_id, [target], recent_days=recent_days)
        finally:
            # Stopped before the final figures are written, so a late reading
            # cannot put the finished job back at 99%.
            stop.set()
            watcher.join(timeout=30)
        total, threats = maldet.read_job_report(job_id)
        for threat in threats:
            threat["domain"] = threat.get("domain") or _domain_from_path(threat["path"])
            _append_malware_log(job_id, f"INFECTED {threat['path']}: {threat['signature']}")
        status = "infected" if threats else "done"
        # maldet exit: 0 ok, 2 malware found, 1 error running.
        if result["exit"] not in (0, 2) and not threats:
            raise RuntimeError((result.get("raw") or "maldet scan failed").strip()[-400:])
        _update_malware_job(
            job_id, status=status, progress_percent=100,
            total_files=total, scanned=total, infected=len(threats),
            threats=threats, scanid=result["scanid"],
            message="Scan finished.",
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, "maldet scan finished")
        _notify_threats(job_id, threats)
    except Exception as exc:  # noqa: BLE001 - a failed scan is reported, not raised
        _update_malware_job(
            job_id, status="error", message="Scan failed", error=str(exc), finished_at=_now_iso(),
        )
        _append_malware_log(job_id, f"ERROR scan failed: {exc}")
    finally:
        with MALWARE_JOBS_LOCK:
            MALWARE_JOB_THREADS.pop(job_id, None)


def _new_malware_job(**overrides) -> dict:
    job = {
        "job_id": uuid.uuid4().hex, "status": "queued", "scope": "website",
        "website_id": None, "domains": [], "message": "Queued", "engine": "",
        "progress_percent": 0, "total_files": 0, "scanned": 0, "infected": 0,
        "errors": 0, "skipped": 0, "threats": [], "log": [],
        "created_at": _now_iso(), "started_at": "", "finished_at": "", "updated_at": _now_iso(),
    }
    job.update(overrides)
    return job


def _spawn_malware_job(job: dict, run) -> dict:
    """``run`` is a zero-arg callable that executes the job in a daemon thread."""
    _remember_malware_job(job)
    thread = threading.Thread(target=run, daemon=True)
    with MALWARE_JOBS_LOCK:
        MALWARE_JOB_THREADS[job["job_id"]] = thread
    thread.start()
    return _public_malware_job(job)


def start_incremental_scan_job(days: int = 2) -> dict:
    """Daily 'recent files' LMD scan of every website root."""
    from app.services import maldet

    if not maldet.installed():
        raise RuntimeError("LMD is not installed. Turn on the malware scanner to install it.")
    days = max(1, min(int(days or 2), 30))
    job = _new_malware_job(scope="incremental", engine="lmd", message=f"Queued (recent {days}d)")
    return _spawn_malware_job(
        job, lambda: _run_maldet_job(job["job_id"], "/home", recent_days=days)
    )


def start_scan_job(website_id: int | None, db) -> dict:
    """Start a background malware scan for one website or all websites."""
    websites = _select_scan_websites(website_id, db)
    if not websites:
        raise ValueError("No websites found")
    targets = []
    for website in websites:
        root_path = website.root_path
        if not root_path or not Path(root_path).is_dir():
            raise ValueError(f"Website root not found: {root_path}")
        targets.append({"id": website.id, "domain": website.domain, "root_path": root_path})

    job = _new_malware_job(
        scope="all" if website_id is None else "website",
        website_id=website_id,
        domains=[target["domain"] for target in targets],
    )

    from app.services import maldet

    if maldet.installed():
        job["engine"] = "lmd"
        # One site -> its root; all sites -> /home (covers every user).
        scan_path = targets[0]["root_path"] if len(targets) == 1 else "/home"
        return _spawn_malware_job(job, lambda: _run_maldet_job(job["job_id"], scan_path))

    return _spawn_malware_job(job, lambda: _run_scan_job(job["job_id"], targets))


def _run_scan_job(job_id: str, targets: list[dict]) -> None:
    _update_malware_job(job_id, status="running", started_at=_now_iso(), message="Preparing scan")
    try:
        if not _malware_scan.clamd_running():
            raise RuntimeError("ClamAV daemon is not running. Enable malware scanning first.")
        target_files: list[tuple[dict, str]] = []
        skipped = 0
        for target in targets:
            files, skipped_lines = _malware_scan.collect_regular_files(target["root_path"])
            skipped += len(skipped_lines)
            _append_malware_log(job_id, f"Prepared {target['domain']}: {len(files)} files")
            for line in skipped_lines:
                _append_malware_log(job_id, line)
            target_files.extend((target, file_path) for file_path in files)
        total = len(target_files)
        _update_malware_job(job_id, total_files=total, skipped=skipped, message=f"Scanning 0/{total} files")

        threats = []
        errors = 0
        scanned = 0
        for target, file_path in target_files:
            result = _malware_scan.scan_file_with_clamdscan(file_path)
            scanned += 1
            status = result["status"]
            if status == "infected":
                threat = {"path": file_path, "signature": result["signature"], "domain": target["domain"]}
                threats.append(threat)
                _append_malware_log(job_id, f"INFECTED {target['domain']} {file_path}: {result['signature']}")
            elif status == "error":
                errors += 1
                _append_malware_log(job_id, f"ERROR {target['domain']} {file_path}: {result['detail']}")
            elif status == "skipped":
                skipped += 1
                _append_malware_log(job_id, f"SKIP {target['domain']} {file_path}: {result['detail']}")

            percent = int((scanned / total) * 100) if total else 100
            _update_malware_job(
                job_id,
                scanned=scanned,
                infected=len(threats),
                errors=errors,
                skipped=skipped,
                threats=threats,
                progress_percent=percent,
                message=f"Scanning {scanned}/{total} files",
            )

        status = "infected" if threats else "done"
        _update_malware_job(
            job_id,
            status=status,
            progress_percent=100,
            scanned=scanned,
            infected=len(threats),
            errors=errors,
            skipped=skipped,
            threats=threats,
            message=f"Scan complete: {scanned} files, {len(threats)} threats, {errors} errors",
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, "Scan finished")
        _notify_threats(job_id, threats)
    except Exception as exc:  # noqa: BLE001 - scan jobs should report errors, not crash the API
        _update_malware_job(
            job_id,
            status="error",
            message="Scan failed",
            error=str(exc),
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, f"ERROR scan failed: {exc}")
    finally:
        with MALWARE_JOBS_LOCK:
            MALWARE_JOB_THREADS.pop(job_id, None)


def run_scan(website_id: int, db) -> dict:
    """Trigger an on-demand malware scan of a website's root directory."""
    from app.models.entities import Website

    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise ValueError("Website not found")
    root_path = website.root_path
    if not root_path or not Path(root_path).is_dir():
        raise ValueError(f"Website root not found: {root_path}")
    return _malware_scan.scan_directory(root_path)



