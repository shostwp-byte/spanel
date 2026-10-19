"""The addresses this server answers on.

Reading them needs no privileges - `ip addr show` is a read-only netlink query
any user may make - so this goes straight to the command rather than through
the sudo helper.

Only global addresses are reported: loopback and link-local are real addresses
but nothing outside the machine can reach them, so listing them on the settings
page would only invite somebody to hand one to a customer.
"""

from __future__ import annotations

import ipaddress
import logging
import shutil
import subprocess

logger = logging.getLogger("spanel")

TIMEOUT_SECONDS = 5
# `ip -4` already returns only inet lines, but naming the token here keeps
# _query("-4") honest about what it returns whatever ip decides to print.
FAMILY_TOKEN = {"-4": "inet", "-6": "inet6"}


def _query(family: str) -> list[str]:
    binary = shutil.which("ip")
    if not binary:
        import os
        for p in ("/sbin/ip", "/usr/sbin/ip", "/bin/ip"):
            if os.path.exists(p):
                binary = p
                break
    if not binary:  # not Linux, or a stripped image
        return []
    try:
        result = subprocess.run(
            [binary, "-o", family, "addr", "show", "scope", "global"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Could not read the server's %s addresses", family, exc_info=True)
        return []
    if result.returncode != 0:
        return []

    wanted = FAMILY_TOKEN[family]
    found: list[str] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        # "2: eth0    inet 64.118.132.44/22 brd ... scope global eth0"
        for index, token in enumerate(parts):
            if token != wanted or index + 1 >= len(parts):
                continue
            candidate = parts[index + 1].split("/", 1)[0].split("%", 1)[0]
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if address.is_loopback or address.is_link_local:
                continue
            if address.compressed not in found:
                found.append(address.compressed)
            break
    return found


def ipv4_addresses() -> list[str]:
    ips = _query("-4")
    if not ips:
        try:
            import urllib.request
            with urllib.request.urlopen("https://api.ipify.org?format=text", timeout=3) as response:
                ip = response.read().decode("utf-8").strip()
                if ip:
                    ips = [ip]
        except Exception:
            pass
    return ips


def ipv6_addresses() -> list[str]:
    return _query("-6")


def addresses() -> dict:
    return {"ipv4": ipv4_addresses(), "ipv6": ipv6_addresses()}
