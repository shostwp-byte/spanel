"""Start the panel.

uvicorn's command line takes exactly one certificate. The panel needs one per
hostname, so the server is built here instead: the same configuration the
command line would have produced, plus the SNI callback that answers each
handshake with the certificate for the name the browser asked for.

Everything is read from the environment, which systemd fills from
/opt/spanel/backend/.env:

    PANEL_PORT        port to listen on (default 2222)
    PANEL_SSL_CERT    default certificate, used for hostnames without one
    PANEL_SSL_KEY     its private key
    PANEL_SNI_DIR     where the helper keeps the per-hostname certificates
"""

from __future__ import annotations

import copy
import logging
import os
import socket
from pathlib import Path

import uvicorn
import uvicorn.config
from uvicorn.config import create_ssl_context

from app.core import panel_sni
from app.services import panel_ipv6

logger = logging.getLogger("spanel")

# Nothing is trusted to set X-Forwarded-For / X-Forwarded-Proto, because nothing
# sits in front of the panel: this process terminates TLS itself (see the SNI
# store below) and binds 0.0.0.0 directly. The installer writes no vhost for the
# panel port - the only proxy_pass spanel generates is for tenant application
# sites - so the "local Nginx" this used to trust does not exist.
#
# Trusting 127.0.0.1 was therefore trusting every local process, and on a
# shared-hosting box that includes every tenant's PHP-FPM pool and Node app.
# uvicorn's ProxyHeadersMiddleware rewrites scope["client"] from the header for
# any trusted peer, and that value is the login rate-limit key (api/auth.py) and
# the audit log's source IP (services/audit.py) - so a tenant could evade the
# lockout and write any address they liked into the panel's own audit trail.
#
# If a deployment ever does put a reverse proxy in front, this becomes a setting
# that names that proxy - it must not go back to a blanket loopback trust.
TRUSTED_FORWARDERS: list[str] = []


def dual_stack_socket(port: int) -> socket.socket | None:
    """A listening socket that answers on IPv4 and on IPv6.

    Handing uvicorn the host ":: " would not do: asyncio sets IPV6_V6ONLY on
    every AF_INET6 socket it binds itself - deliberately, to keep the families
    apart - so the panel would answer over IPv6 and refuse every IPv4 client.
    The socket is therefore built here, with that option cleared, and passed in
    ready to serve.

    None means "IPv4 only": IPv6 is switched off, or this machine cannot give
    us a dual-stack socket. Losing IPv4 is far worse than not gaining IPv6, so
    every failure lands there rather than raising.
    """
    if not panel_ipv6.is_enabled():
        return None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    except OSError as exc:
        logger.warning("IPv6 is on but this machine has no IPv6 (%s); listening on IPv4 only", exc)
        return None
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        # net.ipv6.bindv6only only sets the default; a kernel that refuses to
        # clear it would cost us IPv4, so check rather than assume.
        if sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) != 0:
            raise OSError("this kernel will not share the socket with IPv4")
        sock.bind(("::", port))
        sock.listen(2048)
        sock.set_inheritable(True)
    except OSError as exc:
        logger.warning("Could not listen on IPv6 (%s); listening on IPv4 only", exc)
        sock.close()
        return None
    logger.info("Listening on IPv4 and IPv6, port %d", port)
    return sock


def _port() -> int:
    try:
        return int(os.environ.get("PANEL_PORT") or 2222)
    except ValueError:
        return 2222


def _certificate_pair() -> tuple[str, str] | None:
    cert = (os.environ.get("PANEL_SSL_CERT") or "").strip()
    key = (os.environ.get("PANEL_SSL_KEY") or "").strip()
    if cert and key and Path(cert).is_file() and Path(key).is_file():
        return cert, key
    return None


def _log_config() -> dict:
    """uvicorn's logging config, extended so the panel's own logger is heard.

    uvicorn only configures the uvicorn.* loggers. Everything the panel logs
    goes to logging.getLogger("spanel"), which propagates to a root logger
    that has no handler - so it went nowhere. Every logger.exception() in the
    codebase was silently discarded, including the one in the unhandled-error
    handler. That is how a plain 500 could reach a user with no trace of the
    cause anywhere on the server: the panel reported "Internal server error"
    and the journal stayed empty.
    """
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["loggers"]["spanel"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return config


def build_config() -> uvicorn.Config:
    pair = _certificate_pair()
    options: dict = {
        "host": "0.0.0.0",
        "port": _port(),
        "proxy_headers": False,
        "forwarded_allow_ips": TRUSTED_FORWARDERS,
        "log_config": _log_config(),
    }
    if pair:
        options["ssl_certfile"], options["ssl_keyfile"] = pair
    config = uvicorn.Config("app.main:app", **options)
    config.load()
    if config.ssl is not None:
        # Per-hostname contexts are built exactly like the default one, so a
        # certificate picked by SNI is served with the same TLS settings.
        def factory(certfile: str, keyfile: str):
            return create_ssl_context(
                certfile=certfile,
                keyfile=keyfile,
                password=config.ssl_keyfile_password,
                ssl_version=config.ssl_version,
                cert_reqs=config.ssl_cert_reqs,
                ca_certs=config.ssl_ca_certs,
                ciphers=config.ssl_ciphers,
            )

        panel_sni.install(config.ssl, factory)
    return config


def main() -> None:
    config = build_config()
    server = uvicorn.Server(config)
    listener = dual_stack_socket(_port())
    if listener is None:
        server.run()
    else:
        # uvicorn binds config.host only when it is not handed a socket.
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
