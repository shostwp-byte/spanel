"""Firewall service.

IP blocking runs on iptables + ipset. The spanel-helper owns the rule file and
the kernel state; this module only validates input and renders what the helper
reports.
"""

import ipaddress
import json
import re
from typing import Optional

from app.core.config import settings
from app.services.shell import CommandResult, shell


PORT_RE = re.compile(r"^[0-9]{1,5}$")
PROTOCOLS = {"tcp", "udp"}
DEFAULT_PROTECTED_PORTS = {22, 80, 443, 465, 587, 2222}
PANEL_ZONE = "PanelZone"
USER_ZONE = "UserZone"


def _validate_protocol(protocol: str) -> str:
    value = (protocol or "tcp").strip().lower()
    if value not in PROTOCOLS:
        raise ValueError("Protocol must be tcp or udp")
    return value


def _validate_port(port: str | int) -> str:
    value = str(port).strip()
    if not PORT_RE.match(value):
        raise ValueError("Port must be a number from 1 to 65535")
    number = int(value)
    if number < 1 or number > 65535:
        raise ValueError("Port must be a number from 1 to 65535")
    return value


def _validate_network(network: str) -> str:
    value = (network or "").strip()
    try:
        parsed = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError("IP must be a valid IPv4/IPv6 address or CIDR network") from exc
    return str(parsed)


def protected_ports() -> set[int]:
    ports = set(DEFAULT_PROTECTED_PORTS)
    try:
        ports.add(int(settings.panel_port or 2222))
    except (TypeError, ValueError):
        pass
    return ports


def status() -> CommandResult:
    return shell.privileged(
        "firewall-status",
        check=False,
        fallback=["bash", "-lc", "echo 'Status: unknown'; echo 'Engine: iptables + ipset'"],
    )


def is_enabled() -> bool | None:
    """Whether the firewall is on and filtering, from the helper's status.

    "Status: enabled" alone is the saved setting; "Chain active" says whether
    INPUT actually jumps to the panel's chain. Enabled without the chain is a
    firewall that is not protecting anything, so it counts as off. None when
    the output says neither (no helper on a dev box), so a caller can tell
    "off" from "could not tell".
    """
    state = chain = ""
    for line in (status().stdout or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Status":
            state = value.strip().lower()
        elif key.strip() == "Chain active":
            chain = value.strip().lower()
    if state == "disabled":
        return False
    if state == "enabled":
        return chain != "no"
    return None


def rules() -> list[dict]:
    """Return the helper's structured rule list.

    Falls back to an empty list when the helper is unavailable (dev mode) or
    emits something unparsable, so the status page never breaks on a bad line.
    """
    result = shell.privileged(
        "firewall-list",
        check=False,
        fallback=["bash", "-lc", 'echo "{\\"rules\\": []}"'],
    )
    if result.returncode != 0:
        return []
    return parse_rules(result.stdout)


def parse_rules(output: str) -> list[dict]:
    try:
        data = json.loads(output or "{}")
    except (ValueError, TypeError):
        return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("rules") or []
    else:
        return []
    return [item for item in items if isinstance(item, dict)]


def is_protected_rule(rule: dict) -> bool:
    if rule.get("protected"):
        return True
    if (rule.get("zone") or "") == PANEL_ZONE:
        return True
    if (rule.get("action") or "").upper() != "ALLOW":
        return False
    if rule.get("ip"):
        return False
    try:
        return int(rule.get("port") or 0) in protected_ports()
    except (TypeError, ValueError):
        return False


def enable() -> CommandResult:
    return shell.privileged("firewall-enable", fallback=["true"])


def disable() -> CommandResult:
    return shell.privileged("firewall-disable", fallback=["true"])


def reload() -> CommandResult:
    return shell.privileged("firewall-reload", fallback=["true"])


def repair() -> CommandResult:
    """Installs iptables/ipset if missing, takes over from UFW and applies the
    rules: for a firewall that is on as a setting but not in force."""
    return shell.privileged("firewall-repair", fallback=["true"], timeout=600)


def allow_port(port: str | int, protocol: str = "tcp") -> CommandResult:
    clean_port = _validate_port(port)
    clean_protocol = _validate_protocol(protocol)
    return shell.privileged(
        "firewall-allow-port",
        helper_args=[clean_port, clean_protocol],
        fallback=["true"],
    )


def allow_ip(network: str, port: Optional[str | int] = None, protocol: str = "tcp") -> CommandResult:
    clean_network = _validate_network(network)
    if not port:
        return shell.privileged(
            "firewall-allow-ip",
            helper_args=[clean_network],
            fallback=["true"],
        )
    clean_port = _validate_port(port)
    clean_protocol = _validate_protocol(protocol)
    return shell.privileged(
        "firewall-allow-ip",
        helper_args=[clean_network, clean_port, clean_protocol],
        fallback=["true"],
    )


def covers_address(network: str, address: Optional[str]) -> bool:
    """Would this rule cover the address the request is coming from?

    Deny rules are now evaluated before the ESTABLISHED return, and applying
    them closes matching sockets, so blocking your own address takes your panel
    session and your SSH with it - immediately, with nothing left to undo it
    from. Refusing is the whole safety net.
    """
    if not address:
        return False
    try:
        return ipaddress.ip_address(address.strip()) in ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False


def block_ip(
    network: str,
    port: Optional[str | int] = None,
    protocol: str = "tcp",
    requester: Optional[str] = None,
) -> CommandResult:
    clean_network = _validate_network(network)
    if covers_address(clean_network, requester):
        raise ValueError(
            f"{clean_network} covers your own address ({requester}). Blocking it would close "
            "this session and lock you out of the panel and SSH straight away."
        )
    if not port:
        return shell.privileged(
            "firewall-deny-ip",
            helper_args=[clean_network],
            fallback=["true"],
        )
    clean_port = _validate_port(port)
    clean_protocol = _validate_protocol(protocol)
    return shell.privileged(
        "firewall-deny-ip",
        helper_args=[clean_network, clean_port, clean_protocol],
        fallback=["true"],
    )


def delete_rule(number: int) -> CommandResult:
    if number < 1:
        raise ValueError("Rule number must be greater than 0")
    selected = next((rule for rule in rules() if rule.get("id") == number), None)
    if selected is None:
        raise ValueError(f"Rule #{number} was not found")
    if is_protected_rule(selected):
        raise ValueError("Default panel, mail, web, and SSH firewall rules cannot be deleted")
    return shell.privileged(
        "firewall-delete",
        helper_args=[str(number)],
        fallback=["true"],
    )


def blocklists() -> CommandResult:
    return shell.privileged(
        "firewall-blocklist-status",
        check=False,
        fallback=["bash", "-lc", "echo 'URLs:'; echo '  (none)'; echo; echo 'Engine:'; echo '  iptables + ipset'"],
    )


def add_blocklist_url(url: str) -> CommandResult:
    return shell.privileged(
        "firewall-blocklist-add",
        helper_args=[url],
        check=False,
        fallback=["bash", "-lc", "echo URL added"],
    )


def delete_blocklist_url(url: str) -> CommandResult:
    return shell.privileged(
        "firewall-blocklist-delete",
        helper_args=[url],
        check=False,
        fallback=["bash", "-lc", "echo URL removed"],
    )


def update_blocklists() -> CommandResult:
    return shell.privileged(
        "firewall-blocklist-run",
        check=False,
        fallback=["bash", "-lc", "echo blocklist update skipped"],
    )
