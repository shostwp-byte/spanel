"""The IPv6 switch.

Off on a fresh install, and off on every server that updates into this
release: nginx binding an address family the machine does not have refuses to
start, and that would take every website down, so nothing turns itself on.

Turning it on asks the helper first. The helper checks for a global IPv6
address, adds the IPv6 twin of every listen directive it manages, and puts
every file back exactly as it was if `nginx -t` refuses the result.

/etc/spanel/ipv6-enabled is the single source of truth. The helper writes it,
the panel and the vhost templates read it, and an update re-applies whatever it
says - so the switch cannot drift away from what is on disk.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException
from starlette.status import HTTP_400_BAD_REQUEST

from app.services.shell import shell

logger = logging.getLogger("spanel")

MARKER = Path(os.environ.get("spanel_IPV6_MARKER", "/etc/spanel/ipv6-enabled"))

NO_IPV6_MESSAGE = (
    "This server has no IPv6 address, so this feature cannot be used. "
    "Ask your provider for one, then turn this on again."
)


def is_enabled() -> bool:
    """Whether the vhosts on this machine carry IPv6 listen directives."""
    try:
        return MARKER.exists()
    except OSError:  # noqa: BLE001 - an unreadable marker is not an outage
        return False


def _run(command: str, timeout: int = 120):
    return shell.privileged(
        command,
        check=False,
        timeout=timeout,
        fallback=["bash", "-lc", f"echo dry-run-{command}"],
    )


def _parse_status(output: str) -> dict:
    values: dict[str, str] = {}
    for line in (output or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    addresses = [item for item in values.get("addresses", "").split(",") if item]
    return {
        "available": values.get("available") == "yes",
        "enabled": values.get("enabled") == "yes",
        "addresses": addresses,
    }


def status() -> dict:
    """What the server can do, and what it is currently doing."""
    result = _run("ipv6-status", timeout=30)
    if result.returncode != 0:
        # Never fail the settings page over this: report what the marker says
        # and let the detail line explain why the rest is unknown.
        return {
            "available": False,
            "enabled": is_enabled(),
            "addresses": [],
            "detail": (result.stderr or result.stdout or "").strip()[-300:]
            or "Could not read the server's IPv6 state.",
        }
    state = _parse_status(result.stdout)
    if not state["available"]:
        state["detail"] = NO_IPV6_MESSAGE
    elif state["enabled"]:
        state["detail"] = "Websites and the panel accept both IPv4 and IPv6."
    else:
        state["detail"] = "This server has IPv6. Turn it on so websites and the panel accept it too."
    return state


def set_enabled(enabled: bool) -> dict:
    """Turn the switch on or off, and say what happened."""
    if enabled and not status()["available"]:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=NO_IPV6_MESSAGE)
    result = _run("ipv6-enable" if enabled else "ipv6-disable", timeout=300)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        if "no global IPv6 address" in detail:
            detail = NO_IPV6_MESSAGE
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=detail or "Could not change the IPv6 configuration.",
        )
    state = status()
    state["message"] = (
        "IPv6 enabled for every website and the panel."
        if enabled
        else "IPv6 disabled. Websites and the panel accept IPv4 only."
    )
    return state
