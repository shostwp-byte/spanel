from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request

from app.core import panel_sni
from app.core.config import settings
from app.services import panel_settings


def _request_host_without_port(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    host = host.split(",", 1)[0].strip()
    return panel_sni.normalize_hostname(host)


def has_panel_certificate() -> bool:
    return (
        bool(settings.panel_ssl_cert)
        and bool(settings.panel_ssl_key)
        and Path(settings.panel_ssl_cert).exists()
        and Path(settings.panel_ssl_key).exists()
    )


def configured_panel_host() -> str:
    """The hostname in PANEL_URL, or PANEL_DOMAIN when there is no URL yet."""
    panel_url = panel_settings.configured_panel_url()
    parsed = urlparse(panel_url if "://" in panel_url else "")
    return panel_sni.normalize_hostname(parsed.hostname or settings.panel_domain or "")


def serves_hostname(hostname: str) -> bool:
    """True when the panel can answer for this name with a certificate of its own.

    The panel is no longer tied to one hostname: it holds a certificate per
    domain on this machine and picks one per handshake. Any of those names is a
    legitimate place to send somebody back to - and a Host header that is none
    of them is not, which is what keeps a spoofed one out of a login link.
    """
    host = panel_sni.normalize_hostname(hostname)
    if not host:
        return False
    if host == configured_panel_host():
        return True
    return panel_sni.store().context_for(host) is not None


def panel_hostnames() -> list[str]:
    """Every hostname the panel can be opened on, the configured one first."""
    configured = configured_panel_host()
    names = panel_sni.available_hostnames()
    if configured and configured not in names:
        names = [configured, *names]
    return names


def panel_base_url(request: Request | None = None) -> str:
    """Absolute base URL of the panel, for a link that leads back into it.

    Follows the hostname the request came in on, so a login link opens on the
    domain the customer already uses rather than on whichever single domain
    PANEL_URL happens to name. The port is always the panel's own: that is the
    only port it listens on, whatever port the request reached it through.
    """
    host = _request_host_without_port(request) if request is not None else ""
    if host and serves_hostname(host):
        scheme = "https" if has_panel_certificate() else "http"
        port = settings.panel_port or 2222
        return f"{scheme}://{host}:{port}"
    configured = panel_settings.configured_panel_url()
    if configured:
        return configured.rstrip("/")
    if settings.panel_domain:
        return f"https://{settings.panel_domain}:{settings.panel_port or 2222}"
    return ""


def tools_base_url(request: Request) -> str:
    """Public Nginx URL for phpMyAdmin helper routes.

    The panel itself listens on PANEL_PORT, but phpMyAdmin is
    served by Nginx on the normal web ports. Keep generated URLs off :2222.
    """
    host = settings.panel_domain or configured_panel_host() or _request_host_without_port(request)
    scheme = "https" if has_panel_certificate() else "http"
    return f"{scheme}://{host}"
