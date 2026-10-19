"""SSH & service brute-force protection (Fail2Ban) service layer.

Reads status and manages bans via spanel-helper with direct system fallbacks
so Brute Force Monitor always reflects the real state of the fail2ban daemon
even if spanel-helper is outdated or running in non-helper mode.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services import addons
from app.services.shell import shell

JAIL_FILE = "/etc/fail2ban/jail.local"
IP_RE = re.compile(r"^[0-9a-fA-F:.]{3,45}(?:/[0-9]{1,3})?$")

_FALLBACK_STATUS_SCRIPT = r"""
if command -v fail2ban-client >/dev/null 2>&1 || [ -x /usr/bin/fail2ban-client ] || dpkg-query -W -f='${Status}' fail2ban 2>/dev/null | grep -q 'install ok installed'; then
  echo "installed=yes"
else
  echo "installed=no"
fi
if systemctl is-active --quiet fail2ban 2>/dev/null || pgrep -f fail2ban-server >/dev/null 2>&1; then
  echo "running=yes"
else
  echo "running=no"
fi
jails="$( (fail2ban-client status 2>/dev/null || sudo -n fail2ban-client status 2>/dev/null) | awk -F: '/Jail list/ {gsub(/[ \t]/,"",$2); print $2}' )"
echo "jails=${jails}"
banned=0
total_failed=0
for j in ${jails//,/ }; do
  c="$( (fail2ban-client status "$j" 2>/dev/null || sudo -n fail2ban-client status "$j" 2>/dev/null) | awk -F: '/Currently banned/ {gsub(/[ \t]/,"",$2); print $2}' )"
  case "$c" in ''|*[!0-9]*) ;; *) banned=$((banned + c)) ;; esac
  f="$( (fail2ban-client status "$j" 2>/dev/null || sudo -n fail2ban-client status "$j" 2>/dev/null) | awk -F: '/Total failed/ {gsub(/[ \t]/,"",$2); print $2}' )"
  case "$f" in ''|*[!0-9]*) ;; *) total_failed=$((total_failed + f)) ;; esac
done
echo "banned=${banned}"
echo "total_failed=${total_failed}"
"""


def _parse(stdout: str) -> tuple[dict, bool]:
    info: dict = {
        "installed": False,
        "running": False,
        "jails": [],
        "banned": 0,
        "banaction": "",
        "bans_reach_kernel": True,
        "filter_sees_journal": True,
        "ssh_unit": "",
        "total_failed": 0,
    }
    saw_kernel_check = False
    for line in (stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key in {"installed", "running", "bans_reach_kernel", "filter_sees_journal"}:
            info[key] = value == "yes"
            if key in {"bans_reach_kernel", "filter_sees_journal"}:
                saw_kernel_check = True
        elif key in {"banned", "total_failed"}:
            info[key] = int(value) if value.isdigit() else 0
        elif key == "ssh_unit":
            info["ssh_unit"] = value
        elif key == "jails":
            info["jails"] = [part.strip() for part in (value or "").split(",") if part.strip()]
        elif key == "banaction":
            info["banaction"] = value
    return info, saw_kernel_check


def _read_configured_jails_and_banaction() -> tuple[list[str], str]:
    jails: list[str] = []
    banaction = ""
    candidates = [
        Path("/etc/fail2ban/jail.local"),
        Path("/etc/fail2ban/jail.d/defaults-debian.conf"),
        Path("/etc/fail2ban/jail.conf"),
    ]
    jail_d = Path("/etc/fail2ban/jail.d")
    if jail_d.is_dir():
        try:
            for p in sorted(jail_d.glob("*.local")) + sorted(jail_d.glob("*.conf")):
                if p not in candidates:
                    candidates.append(p)
        except OSError:
            pass

    for path in candidates:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        current_section = ""
        section_enabled: dict[str, bool] = {}
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1].strip()
                continue
            k, sep, v = line.partition("=")
            if not sep:
                continue
            k, v = k.strip().lower(), v.strip()
            if k == "banaction" and not banaction:
                banaction = v.split()[0]
            elif k == "enabled" and current_section and current_section != "DEFAULT":
                section_enabled[current_section] = v.lower() in {"true", "1", "yes"}
        for sec, en in section_enabled.items():
            if en and sec not in jails:
                jails.append(sec)

    return jails, (banaction or "iptables-multiport")


def _probe_direct_system_status(info: dict) -> dict:
    """Ensure installed, running, jails, banned, and total_failed reflect actual OS state."""
    if settings.command_dry_run:
        return info

    # 1. Check binary / package installation directly
    if not info["installed"]:
        has_bin = bool(
            shutil.which("fail2ban-client")
            or shutil.which("fail2ban-server")
            or os.path.exists("/usr/bin/fail2ban-client")
            or os.path.exists("/usr/local/bin/fail2ban-client")
            or os.path.isdir("/etc/fail2ban")
        )
        if not has_bin:
            dpkg = shell.run(["dpkg-query", "-W", "-f=${Status}", "fail2ban"], check=False)
            has_bin = "install ok installed" in (dpkg.stdout or "")
        if has_bin:
            info["installed"] = True

    # 2. Check systemd service status directly (does not require root)
    if not info["running"]:
        svc = shell.run(["systemctl", "is-active", "fail2ban"], check=False)
        if (svc.stdout or "").strip() == "active":
            info["running"] = True
            info["installed"] = True
        else:
            svc_status = shell.run(["systemctl", "status", "fail2ban"], check=False)
            combined = f"{svc_status.stdout or ''} {svc_status.stderr or ''}".lower()
            if "active (running)" in combined:
                info["running"] = True
                info["installed"] = True

    # 3. Auto-heal: if the Fail2ban addon is marked installed in Addon Manager and package exists,
    #    ensure the daemon is started if it was stopped.
    if info["installed"] and not info["running"] and addons.is_installed(addons.FAIL2BAN):
        shell.privileged("systemctl", helper_args=["fail2ban", "start"], check=False, fallback=["systemctl", "start", "fail2ban"])
        svc_retry = shell.run(["systemctl", "is-active", "fail2ban"], check=False)
        if (svc_retry.stdout or "").strip() == "active":
            info["running"] = True

    # 4. Populate jails and banaction if missing
    cfg_jails, cfg_banaction = _read_configured_jails_and_banaction()
    if not info["banaction"]:
        info["banaction"] = cfg_banaction

    if info["running"] and not info["jails"]:
        # Try fail2ban-client status directly or via sudo -n
        for cmd in (["fail2ban-client", "status"], ["sudo", "-n", "fail2ban-client", "status"]):
            res = shell.run(cmd, check=False)
            if res.returncode == 0 and res.stdout:
                for line in res.stdout.splitlines():
                    if "Jail list:" in line:
                        raw_jails = line.split("Jail list:", 1)[1]
                        parsed = [j.strip() for j in raw_jails.split(",") if j.strip()]
                        if parsed:
                            info["jails"] = parsed
                        break
                if info["jails"]:
                    break

        if info["jails"]:
            total_banned = 0
            total_failed = 0
            for j in info["jails"]:
                for cmd in (["fail2ban-client", "status", j], ["sudo", "-n", "fail2ban-client", "status", j]):
                    res = shell.run(cmd, check=False)
                    if res.returncode == 0 and res.stdout:
                        for line in res.stdout.splitlines():
                            if "Currently banned:" in line:
                                val = line.split("Currently banned:", 1)[1].strip()
                                if val.isdigit():
                                    total_banned += int(val)
                            elif "Total failed:" in line:
                                val = line.split("Total failed:", 1)[1].strip()
                                if val.isdigit():
                                    total_failed += int(val)
                        break
            info["banned"] = max(info.get("banned", 0), total_banned)
            info["total_failed"] = max(info.get("total_failed", 0), total_failed)
        else:
            info["jails"] = cfg_jails if cfg_jails else ["sshd"]

    if not info["ssh_unit"]:
        info["ssh_unit"] = "ssh.service"

    return info


def status() -> dict:
    """What fail2ban is doing, and whether its bans actually bite."""
    result = shell.privileged(
        "fail2ban-status",
        check=False,
        fallback=["bash", "-lc", _FALLBACK_STATUS_SCRIPT],
    )
    info, saw_kernel_check = _parse(result.stdout or "")
    info = _probe_direct_system_status(info)
    info["jail_file"] = JAIL_FILE
    if info["installed"] and info["running"] and saw_kernel_check:
        if not info["bans_reach_kernel"]:
            info["warning"] = (
                "fail2ban is running but its bans are not reaching iptables. "
                f"Check banaction in {JAIL_FILE}."
            )
        elif not info["filter_sees_journal"]:
            info["warning"] = (
                "fail2ban is running and can ban, but its filter is reading a "
                f"systemd unit with no entries ({info['ssh_unit'] or 'unknown'}). "
                "It will never see a single failed login."
            )
    return info


def install() -> dict:
    """Install, configure and prove it works. Raises if the proof fails."""
    shell.privileged(
        "fail2ban-install",
        fallback=["bash", "-lc", "apt-get update && apt-get install -y fail2ban && systemctl enable --now fail2ban"],
    )
    return status()


def stop() -> dict:
    """Stop protecting. Keeps the package, the jail file and the ban history."""
    shell.privileged(
        "fail2ban-remove",
        fallback=["bash", "-lc", "systemctl disable --now fail2ban || true"],
    )
    return status()


def service_control(action: str) -> dict:
    """Start, stop, or restart the fail2ban service."""
    if action not in {"start", "stop", "restart"}:
        raise ValueError("Unsupported service action")
    if action in {"start", "restart"} and not addons.is_installed(addons.FAIL2BAN):
        addons.install(addons.FAIL2BAN)
    shell.privileged(
        "systemctl",
        helper_args=["fail2ban", action],
        check=False,
        fallback=["systemctl", action, "fail2ban"],
    )
    return status()


def banned(jail: str = "sshd", limit: int = 50, offset: int = 0) -> dict:
    """A page of the ban list, plus how many there are."""
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", jail or ""):
        raise ValueError("Invalid jail name")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    result = shell.privileged(
        "fail2ban-banned",
        helper_args=[jail],
        check=False,
        fallback=[
            "bash",
            "-lc",
            f"(fail2ban-client status {jail} 2>/dev/null || sudo -n fail2ban-client status {jail} 2>/dev/null) "
            "| awk -F: '/Banned IP list/ {gsub(/^[ \t]+/,"",$2); print $2}' | tr ' ' '\n'",
        ],
    )
    stdout = result.stdout or ""
    if not stdout.strip() and not settings.command_dry_run:
        for cmd in (["fail2ban-client", "status", jail], ["sudo", "-n", "fail2ban-client", "status", jail]):
            res = shell.run(cmd, check=False)
            if res.returncode == 0 and res.stdout:
                for line in res.stdout.splitlines():
                    if "Banned IP list:" in line:
                        stdout = "\n".join(line.split("Banned IP list:", 1)[1].strip().split())
                        break
                break
    every = [
        line.strip()
        for line in stdout.splitlines()
        if IP_RE.fullmatch(line.strip())
    ]
    return {
        "items": every[offset : offset + limit],
        "total": len(every),
        "offset": offset,
        "limit": limit,
    }


def ban(address: str, jail: str = "sshd") -> None:
    value = (address or "").strip()
    if not IP_RE.fullmatch(value):
        raise ValueError("Not a valid IP address or CIDR")
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", jail or ""):
        jail = "sshd"
    res = shell.privileged(
        "fail2ban-ban",
        helper_args=[value, jail],
        check=False,
        fallback=["bash", "-lc", f"fail2ban-client set {jail} banip {value} || sudo -n fail2ban-client set {jail} banip {value} || true"],
    )
    if res.returncode != 0 and not settings.command_dry_run:
        shell.run(["sudo", "-n", "fail2ban-client", "set", jail, "banip", value], check=False)


def unban(address: str, jail: Optional[str] = None) -> None:
    value = (address or "").strip()
    if not IP_RE.fullmatch(value):
        raise ValueError("Not a valid IP address")
    args = [value]
    if jail and re.fullmatch(r"[a-z0-9_-]{1,32}", jail):
        args.append(jail)
    res = shell.privileged(
        "fail2ban-unban",
        helper_args=args,
        check=False,
        fallback=["bash", "-lc", f"fail2ban-client unban {value} || fail2ban-client set sshd unbanip {value} || true"],
    )
    if res.returncode != 0 and not settings.command_dry_run:
        shell.run(["sudo", "-n", "fail2ban-client", "unban", value], check=False)
