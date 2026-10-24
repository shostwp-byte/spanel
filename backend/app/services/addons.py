"""Optional parts of the panel, installed only where they are wanted.

Everything here is off until an administrator asks for it. A server that hosts
WordPress has no reason to carry a container runtime, its firewall rules or its
upgrades, and a customer has no reason to see a section of the panel their
package does not include.

The state lives beside the panel's other settings rather than in the database,
so the answer to "is this installed" costs nothing and is available to code that
has no session to hand.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import HTTPException, status

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = PROJECT_ROOT / "VERSION"
ADDONS_DIR = Path(os.environ.get("SPANEL_DATA_DIR") or os.environ.get("spanel_DATA_DIR") or "/var/lib/spanel")
ADDONS_FILE = ADDONS_DIR / "addons.json"


def _panel_version() -> str:
    try:
        raw = VERSION_FILE.read_text(encoding="utf-8").strip().lstrip("vV")
        if raw:
            return raw
    except OSError:
        pass
    return "1.0.7"


_VERSION_CACHE: dict[str, object] = {"ts": 0.0, "data": {}}


def _clean_deb_version(raw: str) -> str:
    if not raw:
        return ""
    val = raw.strip()
    if ":" in val:
        val = val.split(":", 1)[1]
    match = re.match(r"^(\d+(?:\.\d+)+(?:\.[a-z0-9]+)?)", val, re.IGNORECASE)
    if match:
        return match.group(1)
    return re.split(r"[-+~]", val, maxsplit=1)[0].strip()


def _detect_system_versions() -> dict[str, str]:
    now = time.monotonic()
    cached_ts = float(_VERSION_CACHE.get("ts") or 0.0)
    cached_data = _VERSION_CACHE.get("data")
    if isinstance(cached_data, dict) and cached_data and (now - cached_ts) < 120.0:
        return dict(cached_data)

    detected: dict[str, str] = {}
    if os.name == "nt":
        return detected

    packages = [
        "fail2ban",
        "pdns-server",
        "exim4-daemon-heavy",
        "exim4-base",
        "dovecot-core",
        "rspamd",
        "clamav-daemon",
        "clamav",
        "docker-ce",
        "docker.io",
        "nodejs",
    ]
    try:
        res = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}|${db:Status-Abbrev}|${Version}\\n", *packages],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        for line in (res.stdout or "").splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) == 3:
                pkg, status_abbrev, raw_ver = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if status_abbrev.startswith("ii") and raw_ver:
                    cleaned = _clean_deb_version(raw_ver)
                    if cleaned:
                        detected[pkg] = cleaned
    except Exception:
        pass

    missing_pkgs = [p for p in packages if p not in detected]
    if missing_pkgs:
        try:
            res = subprocess.run(
                ["apt-cache", "policy", *missing_pkgs],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            current_pkg = ""
            for line in (res.stdout or "").splitlines():
                if not line.startswith(" ") and line.endswith(":"):
                    current_pkg = line[:-1].strip()
                elif current_pkg and line.strip().startswith("Candidate:"):
                    cand = line.strip().split("Candidate:", 1)[1].strip()
                    if cand and cand != "(none)":
                        cleaned = _clean_deb_version(cand)
                        if cleaned and current_pkg not in detected:
                            detected[current_pkg] = cleaned
        except Exception:
            pass

    for lmd_ver_path in (Path("/usr/local/maldetect/VERSION"), Path("/usr/local/maldetect/files/VERSION")):
        try:
            if lmd_ver_path.is_file():
                raw_lmd = lmd_ver_path.read_text(encoding="utf-8", errors="ignore").strip()
                m = re.search(r"(\d+(?:\.\d+)+)", raw_lmd)
                if m:
                    detected["lmd"] = m.group(1)
                    break
        except OSError:
            pass

    _VERSION_CACHE["ts"] = now
    _VERSION_CACHE["data"] = dict(detected)
    return detected


def _resolve_addon_version(slug: str, default_version: str) -> tuple[str, str]:
    panel_ver = _panel_version()
    sys_vers = _detect_system_versions()
    if slug == "fail2ban":
        ver = sys_vers.get("fail2ban") or default_version
        return ver, f"Fail2ban v{ver} · iptables-multiport"
    if slug == "dns":
        ver = sys_vers.get("pdns-server") or default_version
        return ver, f"PowerDNS Authoritative v{ver}"
    if slug == "mail":
        exim_ver = sys_vers.get("exim4-daemon-heavy") or sys_vers.get("exim4-base") or default_version
        dovecot_ver = sys_vers.get("dovecot-core") or "2.3.21"
        rspamd_ver = sys_vers.get("rspamd") or "3.8.4"
        return exim_ver, f"Exim v{exim_ver} · Dovecot v{dovecot_ver} · Rspamd v{rspamd_ver}"
    if slug == "malware":
        clam_ver = sys_vers.get("clamav-daemon") or sys_vers.get("clamav") or default_version
        lmd_ver = sys_vers.get("lmd") or "1.6.5"
        return clam_ver, f"ClamAV v{clam_ver} · LMD v{lmd_ver}"
    if slug == "application":
        docker_ver = sys_vers.get("docker-ce") or sys_vers.get("docker.io") or default_version
        node_ver = sys_vers.get("nodejs")
        node_label = f"v{node_ver}" if node_ver else "LTS (18/20/22)"
        return docker_ver, f"Docker Engine v{docker_ver} · Node.js {node_label}"
    if slug == "mcp":
        return panel_ver, f"MCP 2025-06-18 · SPanel v{panel_ver}"
    if slug == "notifications":
        return panel_ver, f"SPanel Alert Engine v{panel_ver} · SMTP & Telegram"
    if slug == "demo":
        return panel_ver, f"SPanel Sandbox Guard v{panel_ver}"
    return default_version, f"SPanel v{panel_ver}"

APPLICATION = "application"
DEMO = "demo"
DNS = "dns"
FAIL2BAN = "fail2ban"
MAIL = "mail"
MALWARE = "malware"
MCP = "mcp"
NOTIFICATIONS = "notifications"

# What an administrator sees in the Addons page. The version is the addon's own:
# it moves when the addon changes, independently of the panel's version.
CATALOGUE: dict[str, dict] = {
    FAIL2BAN: {
        "name": "Fail2ban",
        "version": "1.0.2",
        "summary": "Stops SSH password guessing by banning an address that keeps getting it wrong.",
        "details": [
            "sshd on a public address collects hundreds of password attempts a day with nobody watching; one live customer server logged 679 failures in 24 hours.",
            "Five failures within an hour bans an address for an hour, and longer each time it comes back, up to a week.",
            "Never bans the server itself: loopback and every address the machine holds are on the ignore list.",
        ],
        "notes": [
            "The ban action is pinned to iptables-multiport rather than left for fail2ban to detect. A machine that once had ufw keeps its ufw chains, which is enough for fail2ban to pick wrong and hand every ban to a firewall that is not running.",
            "On install the panel bans an address that is never routed, then checks it really is in iptables. If it is not, the install fails rather than leaving a service that looks healthy and protects nothing.",
            "Turning the addon off only stops the service: the package, the jail file and the ban history all stay.",
        ],
        "keeps_data_on_uninstall": True,
    },
    MCP: {
        "name": "AI assistants (MCP)",
        "version": _panel_version(),
        "summary": "Lets Claude Code, Cursor or VS Code read and operate the panel with a personal token.",
        "details": [
            "Each person creates their own token, and it acts with exactly their own permissions: an administrator's reaches the whole server, a customer's reaches only their own websites, databases, backups and files.",
            "A token is read-only unless the person ticks Allow actions when creating it. A read-only token is not even shown the tools that write.",
            "Nothing new listens on the network. The assistant talks to the panel's own address, over the panel's own certificate.",
        ],
        "notes": [
            "Needs a real certificate. MCP clients refuse a self-signed one, so on a panel still using the self-signed certificate this addon cannot be reached at all.",
            "Works with clients that send a Bearer token - Claude Code, Cursor, VS Code. The claude.ai and ChatGPT web connectors need OAuth, which this does not have.",
            "Turning the addon off closes the endpoint and leaves the tokens alone. Removing it revokes every token on the server.",
        ],
        # Stop is reversible and keeps the tokens; uninstall is the one that
        # revokes them, and the Addons page says so before it happens.
        "keeps_data_on_uninstall": False,
    },
    NOTIFICATIONS: {
        "name": "Notifications",
        "version": _panel_version(),
        "summary": "Tells administrators what needs their attention on the server, by email and Telegram.",
        "details": [
            "The server: a service that stopped, a disk filling up, the firewall off, a failed backup, malware, certificates about to expire, a panel update.",
            "Your own account: a sign-in from a new place, a password or two-factor change.",
            "Email goes through the SMTP server you set; Telegram through a bot you create with @BotFather. Each administrator picks their channels and mutes what they do not want.",
            "For administrators only: customers do not see it and are not sent anything.",
        ],
        "notes": [
            "Email needs an SMTP account (host, port, sender address). Mail sent straight from the server usually lands in spam.",
            "Turning the addon off stops every message and keeps the settings, each administrator's choices and the delivery log.",
        ],
        "keeps_data_on_uninstall": True,
    },
    MALWARE: {
        "name": "Malware Scanner",
        "version": "1.4.2",
        "summary": "Scans the websites and the server for malware with Linux Malware Detect and ClamAV.",
        "details": [
            "Scans a website, every website or the whole server on demand, and on a weekly schedule.",
            "Optional real-time monitoring of the websites (Level 2) and a scan of each uploaded file.",
            "Suspicious files are listed with their signature; nothing is deleted or quarantined without you.",
        ],
        "notes": [
            "Installing it installs LMD and the ClamAV engine if they are not there yet (1-3 minutes).",
            "A scan uses about 1.3 GB of memory while it runs and releases it afterwards; nothing stays resident unless Level 2 is on.",
            "Removing it stops the schedule, the scan of uploads and the real-time monitor, which stays off until you turn Level 2 on again. LMD, ClamAV, the history and the schedule are kept.",
        ],
        "keeps_data_on_uninstall": True,
    },
    DNS: {
        "name": "DNS Manager",
        "version": "4.8.3",
        "summary": "Runs the DNS for your domains on this server, and lets you edit their records in the panel.",
        "details": [
            "Installs PowerDNS and opens port 53. Point a domain's nameservers at this server and its records are served from here.",
            "Every domain on the server gets a full zone by itself: the websites already here when the addon is installed, new websites and their aliases. The domain, www and mail point at this server, with an MX and an SPF record.",
            "Administrators edit every zone; a customer edits the DNS of every domain in their account. A, AAAA, CNAME, MX, TXT, NS, SRV and CAA records.",
        ],
        "notes": [
            "Set the nameservers on the DNS page, then create glue records for them at the registrar, pointing to this server's IP address.",
            "Both nameservers are this one server, so the domains it serves are only as reachable as the server itself.",
            "If the server's IP address changes, remove the addon and install it again so PowerDNS listens on the new address.",
            "Deleting a website keeps its zone. Only administrators delete zones, and a zone whose website is still here comes back at the next sync.",
            "Removing the addon stops PowerDNS and closes port 53; every zone is kept and is served again when you install it again.",
        ],
        "keeps_data_on_uninstall": True,
    },
    MAIL: {
        "name": "Email",
        "version": "4.97",
        "summary": "Mailboxes on your domains, with a webmail your customers open from the panel without a password.",
        "details": [
            "Installs Exim to send and receive, Dovecot for IMAP and POP3, and SPanel Webmail on port 2096.",
            "Customers make mailboxes on the domains of their own websites, up to the number in their package. Administrators make them on any domain.",
            "One click in the panel opens a mailbox in the webmail, with no password to type. Once webmail.<domain> points here, it can have its own address and certificate.",
            "Mail is kept in the customer's home directory: it counts toward their disk space and is in their account backups.",
            "Outgoing mail is signed with DKIM. With DNS Manager on, the DKIM, DMARC and webmail records are added to the domain's zone by themselves.",
            "Rspamd filters incoming mail: clear spam is refused, likely spam goes to the Junk folder. The filtering log shows every decision and its reasons, and a sender blocked by mistake goes on the allowlist in one click.",
            "Outgoing mail can go through a smarthost (SMTP2GO, Mailgun, SendGrid...) when the provider blocks port 25; its SPF include is added to every domain's SPF.",
        ],
        "notes": [
            "Needs the panel on a domain with a real certificate: mail clients connect to that name.",
            "Opens ports 25, 465, 587, 143, 993, 110, 995 and 2096. Many VPS providers block outgoing port 25 until asked; the Email page says whether this server can send.",
            "Refuses to install next to another mail server (Postfix, Sendmail). Once installed, websites' PHP mail() is sent through Exim as well.",
            "Rspamd uses about 250 MB of memory while the spam filter is on, and keeps its log in the server's Redis.",
            "Removing the addon stops Exim, Dovecot, Rspamd and the webmail and closes the ports. The mail, the mailboxes, the DKIM keys and the settings are kept.",
        ],
        "keeps_data_on_uninstall": True,
    },
    DEMO: {
        "name": "Demo mode",
        "version": _panel_version(),
        "summary": "Public demo accounts that can look at every page and change nothing.",
        "details": [
            "Up to three demo accounts (Admin, Reseller, and End User), with one-click sign-in buttons on the login page.",
            "Every change a demo account tries is refused with a message saying this is a demo. So are file contents, downloads, phpMyAdmin, backups and the terminal.",
            "Your own administrator account is untouched and keeps full control.",
        ],
        "notes": [
            "The demo passwords are shown on the sign-in page, so pick accounts that exist only for the demo. They are never the SFTP or SSH password.",
            "Signing out of a demo account ends only that visitor's session, not everyone else's.",
            "Removing the addon gives the demo accounts a random password, so the public ones stop working. Installing it again restores them.",
        ],
        "keeps_data_on_uninstall": True,
    },
    APPLICATION: {
        "name": "Application",
        "version": "27.3.1",
        "summary": "Runs Node.js apps, containers and Docker Compose, and puts them behind a domain with Nginx.",
        "details": [
            "Installs Docker and the Node.js versions you need; none of it is in a default install.",
            "Each app gets its own internal port, its own memory and CPU limits, and runs as the customer's user.",
            "Set a website to Application mode and Nginx points at the app you installed.",
        ],
        "notes": [
            "Backups do not yet include application data (the apps directory and named volumes).",
            "Docker image and volume size is not counted against a customer's disk quota.",
        ],
        # Nothing is deleted when this goes away, so turning it off is safe to
        # try; see uninstall() for what actually happens.
        "keeps_data_on_uninstall": True,
    },
}


def _read() -> dict:
    try:
        with ADDONS_FILE.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    ADDONS_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(ADDONS_DIR), delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=True, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(ADDONS_FILE)


def known(slug: str) -> dict:
    entry = CATALOGUE.get(slug)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No such addon: {slug}")
    return entry


def is_installed(slug: str) -> bool:
    record = _read().get(slug)
    return bool(record and record.get("installed"))


def installed_slugs() -> list[str]:
    return sorted(slug for slug in CATALOGUE if is_installed(slug))


def state() -> list[dict]:
    """The catalogue with each addon's installed state folded in."""
    stored = _read()
    entries = []
    dirty = False
    for slug, entry in sorted(CATALOGUE.items()):
        record = stored.get(slug) or {}
        is_inst = bool(record.get("installed"))
        live_ver, stack_label = _resolve_addon_version(slug, entry["version"])
        stored_ver = str(record.get("version") or "").strip()
        if is_inst:
            if not stored_ver or stored_ver == "1.0.0" or stored_ver != live_ver:
                stored_ver = live_ver
                if isinstance(stored.get(slug), dict) and stored[slug].get("version") != live_ver:
                    stored[slug]["version"] = live_ver
                    dirty = True
        elif stored_ver == "1.0.0":
            stored_ver = ""
        entries.append({
            "slug": slug,
            **entry,
            "version": live_ver,
            "stack": stack_label,
            "installed": is_inst,
            "installed_version": stored_ver if is_inst else "",
            "installed_at": record.get("installed_at") or "",
        })
    if dirty:
        try:
            _write(stored)
        except OSError:
            pass
    return entries


def install(slug: str) -> dict:
    entry = known(slug)
    from datetime import datetime, timezone

    _VERSION_CACHE["ts"] = 0.0
    live_ver, _ = _resolve_addon_version(slug, entry["version"])
    data = _read()
    data[slug] = {
        "installed": True,
        "version": live_ver,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write(data)
    return data[slug]


def uninstall(slug: str) -> dict:
    """Turn an addon off without touching anything it created.

    Applications keep their files, their containers' volumes and their rows in
    the database; the panel simply stops offering the feature and stops the units
    so nothing keeps running behind a section nobody can see. Installing again
    picks up where it left off.
    """
    known(slug)
    _VERSION_CACHE["ts"] = 0.0
    data = _read()
    record = data.get(slug) or {}
    record["installed"] = False
    data[slug] = record
    _write(data)
    return record


def adopt_existing(slug: str, in_use: bool) -> bool:
    """Count a feature already in use as installed, once.

    The addon split arrives as an update, and a server that has been running
    applications for months must not lose them the moment the panel restarts.
    If there is no record either way and the feature is demonstrably in use, it
    was installed before there was anything to record. Only ever runs when the
    file has no entry, so an administrator who later removes the addon does not
    find it back after the next restart.
    """
    if not in_use:
        return False
    data = _read()
    if slug in data:
        return False
    install(slug)
    return True


def require(slug: str) -> None:
    """Refuse an API call that belongs to an addon nobody installed."""
    entry = known(slug)   # a slug that is not in the catalogue is a bug, not a 409
    if not is_installed(slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The {entry['name']} addon is not installed. Install it from the Addons page first.",
        )


def require_application() -> None:
    """FastAPI dependency for every route the Application addon owns."""
    require(APPLICATION)


def require_dns() -> None:
    """FastAPI dependency for every route the DNS Manager addon owns."""
    require(DNS)


def require_mail() -> None:
    """FastAPI dependency for every route the Email addon owns."""
    require(MAIL)


def require_demo() -> None:
    """FastAPI dependency for the Demo mode addon's settings routes."""
    require(DEMO)


def require_malware() -> None:
    """FastAPI dependency for every route the Malware Scanner addon owns."""
    require(MALWARE)
