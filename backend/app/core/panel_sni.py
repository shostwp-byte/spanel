"""Per-hostname certificates for the panel port.

The panel used to be pinned to one hostname: uvicorn was handed a single
certificate, so PANEL_DOMAIN had to name the one domain whose certificate the
browser would accept, and every login link had to point back at that domain.

Every certificate on the machine is copied by the helper into

    /etc/spanel/sni/<name>/fullchain.pem
    /etc/spanel/sni/<name>/privkey.pem

(root:spanel 0640, so the panel can read them but a site user cannot), and this
module answers the TLS handshake with whichever one covers the hostname the
browser asked for. Anything without a certificate of its own still gets the
default certificate uvicorn was started with.

The store re-reads the directory on a timer, so a renewal or a newly issued
certificate is picked up without restarting the panel.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger("spanel.sni")

DEFAULT_SNI_DIR = "/etc/spanel/sni"
CERT_NAME = "fullchain.pem"
KEY_NAME = "privkey.pem"
RELOAD_INTERVAL = 30.0

ContextFactory = Callable[[str, str], ssl.SSLContext]


def _default_context_factory(certfile: str, keyfile: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    return context


def sni_directory() -> Path:
    return Path(os.environ.get("PANEL_SNI_DIR") or DEFAULT_SNI_DIR)


def normalize_hostname(hostname: str) -> str:
    """Lower-case, without the root dot and without a port.

    SNI never carries a port, but the same names arrive from Host headers,
    where they do.
    """
    host = (hostname or "").strip().lower().rstrip(".")
    if host.startswith("["):  # [::1]:2222
        end = host.find("]")
        return host[1:end] if end > 0 else host
    if host.count(":") == 1:  # host:port - more colons means a bare IPv6 address
        host = host.split(":", 1)[0]
    return host


def certificate_hostnames(certfile: Path, fallback: str) -> list[str]:
    """Every name the certificate is valid for, so aliases work too.

    A certificate issued for example.com usually covers www.example.com as
    well, and the lineage is named after the first domain only. Reading the
    subject alternative names is what makes the alias reachable.
    """
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(certfile.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = [normalize_hostname(name) for name in san.value.get_values_for_type(x509.DNSName)]
        names = [name for name in names if name]
        if names:
            return names
    except Exception:  # noqa: BLE001 - a certificate we cannot parse still serves its own name
        logger.debug("Could not read the names out of %s", certfile, exc_info=True)
    fallback = normalize_hostname(fallback)
    return [fallback] if fallback else []


class _Entry:
    """One certificate on disk, with the stamp that says when to reload it."""

    def __init__(self, name: str, certfile: Path, keyfile: Path, factory: ContextFactory):
        self.name = name
        self.certfile = certfile
        self.keyfile = keyfile
        self.stamp = _stamp(certfile, keyfile)
        self.hostnames = certificate_hostnames(certfile, name)
        self.context = factory(str(certfile), str(keyfile))


def _stamp(certfile: Path, keyfile: Path) -> tuple:
    def one(path: Path) -> tuple:
        try:
            info = path.stat()
        except OSError:
            return (0, 0)
        return (info.st_mtime_ns, info.st_size)

    return (one(certfile), one(keyfile))


class SniStore:
    def __init__(
        self,
        directory: Path | str | None = None,
        factory: ContextFactory | None = None,
        reload_interval: float = RELOAD_INTERVAL,
    ):
        self.directory = Path(directory) if directory else sni_directory()
        self.factory = factory or _default_context_factory
        self.reload_interval = reload_interval
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._by_hostname: dict[str, ssl.SSLContext] = {}
        self._wildcards: list[tuple[str, ssl.SSLContext]] = []
        self._checked_at = 0.0

    # -- loading ----------------------------------------------------------
    def _candidates(self) -> Iterable[tuple[str, Path, Path]]:
        try:
            children = sorted(self.directory.iterdir())
        except OSError:
            return []
        found = []
        for child in children:
            if not child.is_dir():
                continue
            certfile = child / CERT_NAME
            keyfile = child / KEY_NAME
            if certfile.is_file() and keyfile.is_file():
                found.append((child.name, certfile, keyfile))
        return found

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            if not force and (now - self._checked_at) < self.reload_interval:
                return
            self._checked_at = now
            entries: dict[str, _Entry] = {}
            for name, certfile, keyfile in self._candidates():
                current = self._entries.get(name)
                if current is not None and current.stamp == _stamp(certfile, keyfile):
                    entries[name] = current
                    continue
                try:
                    entries[name] = _Entry(name, certfile, keyfile, self.factory)
                    if current is None:
                        logger.info("Panel certificate loaded for %s", name)
                    else:
                        logger.info("Panel certificate reloaded for %s", name)
                except Exception:  # noqa: BLE001 - one bad certificate must not take the panel down
                    logger.warning("Ignoring the certificate in %s", certfile.parent, exc_info=True)
                    if current is not None:
                        entries[name] = current
            self._entries = entries
            self._reindex()

    def _reindex(self) -> None:
        by_hostname: dict[str, ssl.SSLContext] = {}
        wildcards: list[tuple[str, ssl.SSLContext]] = []
        for entry in self._entries.values():
            for hostname in entry.hostnames:
                if hostname.startswith("*."):
                    wildcards.append((hostname[1:], entry.context))
                else:
                    by_hostname.setdefault(hostname, entry.context)
        self._by_hostname = by_hostname
        self._wildcards = wildcards

    # -- lookup -----------------------------------------------------------
    def context_for(self, hostname: str) -> ssl.SSLContext | None:
        host = normalize_hostname(hostname)
        if not host:
            return None
        self.refresh()
        with self._lock:
            context = self._by_hostname.get(host)
            if context is not None:
                return context
            # A wildcard covers exactly one more label: *.example.com matches
            # panel.example.com but not a.b.example.com, nor example.com.
            for suffix, wildcard in self._wildcards:
                if host.endswith(suffix) and "." not in host[: -len(suffix)]:
                    return wildcard
        return None

    def hostnames(self) -> list[str]:
        self.refresh()
        with self._lock:
            return sorted(set(self._by_hostname) | {"*" + suffix for suffix, _ in self._wildcards})

    # -- wiring -----------------------------------------------------------
    def sni_callback(self, sslsocket, server_name, sslcontext):  # noqa: ANN001 - ssl module signature
        """Swap in the certificate for this hostname, or keep the default one.

        This runs inside the TLS handshake: an exception here aborts the
        connection, so every failure falls back to the default certificate
        rather than refusing to talk to the browser at all.
        """
        try:
            if server_name:
                context = self.context_for(server_name)
                if context is not None:
                    sslsocket.context = context
        except Exception:  # noqa: BLE001
            logger.warning("SNI lookup failed for %r", server_name, exc_info=True)
        return None

    def install(self, context: ssl.SSLContext) -> ssl.SSLContext:
        self.refresh(force=True)
        context.sni_callback = self.sni_callback
        logger.info(
            "Panel serves %d extra hostname(s) from %s", len(self.hostnames()), self.directory
        )
        return context


_store: SniStore | None = None
_store_lock = threading.Lock()


def store(factory: ContextFactory | None = None) -> SniStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SniStore(factory=factory)
        elif factory is not None:
            _store.factory = factory
        return _store


def install(context: ssl.SSLContext, factory: ContextFactory | None = None) -> ssl.SSLContext:
    return store(factory).install(context)


def available_hostnames() -> list[str]:
    """Hostnames the panel can be opened on with a certificate of their own."""
    try:
        return store().hostnames()
    except Exception:  # noqa: BLE001 - the panel settings page must never fail on this
        logger.debug("Could not list the panel hostnames", exc_info=True)
        return []
