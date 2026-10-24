"""DNS Manager addon: authoritative DNS on this server, through PowerDNS.

Operator, 2026-09-29: "Phát triển thêm addon DNS Manager". Chosen with them:
a DNS server on the VPS itself, as DirectAdmin does; administrators edit every
zone and a customer the zones of their own websites; the zone stays when the
website goes. Then, the same day: "All domain trên VPS đều sẽ được cấp DNS zone
đầy đủ, chứ không cấp tay kiểu này. Mỗi user đều có thể sửa DNS của domain
trong user mình." So every website domain and alias on the server has a full
zone without anyone asking (sync() below), and a zone belongs to whoever owns
the domain's website.

PowerDNS (gsqlite3 backend) holds the zones. The panel edits them through
PowerDNS's HTTP API on 127.0.0.1:8053 with a key only root and the panel can
read - no helper call per record, no zone files to rewrite and reload. What
PowerDNS cannot know is who owns a zone: that is the panel's dns_zones table.

Records are shown one per line, but PowerDNS stores RRsets: every record that
shares a name and a type, with one TTL between them. Adding, changing and
deleting a record therefore rewrite its whole RRset, and a record's TTL is its
RRset's.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role
from app.models.entities import DnsZone, User, Website, WebsiteAlias
from app.services import addons, panel_settings, server_network
from app.services.shell import shell

logger = logging.getLogger("spanel.dns")

API_ROOT = "http://127.0.0.1:8053/api/v1/servers/localhost"
API_KEY_FILE = Path("/etc/spanel/pdns-api.key")
SETTINGS_KEY = "dns"
TIMEOUT = 10
# How PowerDNS moves the SOA serial when a zone is edited through its API:
# DEFAULT makes it YYYYMMDDnn and counts up with every change. The first
# version of this addon set INCEPTION-INCREMENT, a SOA-EDIT value that
# SOA-EDIT-API accepts without complaint and never applies - the serial sat at
# 1 through every edit (seen on .88, 2026-09-29).
SOA_EDIT_API = "DEFAULT"

RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA",
                "TLSA", "HTTPS", "SVCB", "PTR", "DS", "SSHFP", "NAPTR")
TYPE_ORDER = {name: index for index, name in enumerate(("NS", "DS", "A", "AAAA", "CNAME", "PTR", "MX", "TXT", "SRV", "CAA",
                                                        "HTTPS", "SVCB", "TLSA", "SSHFP", "NAPTR"))}
# Types whose priority is a field of its own on the form (like MX), not part
# of the value.
PRIORITY_TYPES = ("MX", "SRV", "HTTPS", "SVCB")
REVERSE_SUFFIXES = (".in-addr.arpa", ".ip6.arpa")
_HEX_RE = re.compile(r"^(?:[0-9a-f]{2})+$")
_SVC_KEY_RE = re.compile(r"^(mandatory|alpn|no-default-alpn|port|ipv4hint|ipv6hint|ech)(?:=(.*))?$")
_ALPN_RE = re.compile(r"^[A-Za-z0-9._/-]{1,64}$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_NAPTR_RE = re.compile(r'^(\d{1,5})\s+(\d{1,5})\s+"([A-Za-z0-9]*)"\s+"([A-Za-z0-9+:._-]*)"\s+"([^"\s]*)"\s+(\S+)$')
DEFAULT_TTL = 3600
MIN_TTL = 60
MAX_TTL = 604800
CAA_TAGS = ("issue", "issuewild", "iodef")
# What a new zone holds besides SOA and NS, one record per line: name, type,
# then the value (an MX or SRV value starts with its priority). {ip} is the
# address for new zones, {domain} the zone, {spf} the server's SPF record -
# "v=spf1 a mx ~all", with the smarthost's include when mail is relayed.
# DirectAdmin's template, less what spanel does not run (FTP, POP).
DEFAULT_TEMPLATE = """@     A    {ip}
www   A    {ip}
mail  A    {ip}
@     MX   10 mail
@     TXT  {spf}
"""
MAX_TEMPLATE_LINES = 100

# A zone is a domain: letters, digits and hyphens, a dot between labels, a
# top-level label of letters (or an IDN one, which arrives as xn--).
ZONE_RE = re.compile(r"^(?!-)([a-z0-9-]{1,63}\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
# A label inside a record name may start with an underscore (_dmarc, _sip._tcp)
# and the first may be a wildcard.
LABEL_RE = re.compile(r"^_?[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class DnsError(RuntimeError):
    """PowerDNS refused, or could not be reached."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class DnsInputError(ValueError):
    """What was typed is not a record or zone that can exist."""


# --- PowerDNS API ------------------------------------------------------------

def _api_key() -> str:
    try:
        key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DnsError("The DNS server's API key is missing. Remove the DNS Manager addon and install it again.") from exc
    if not key:
        raise DnsError("The DNS server's API key is missing. Remove the DNS Manager addon and install it again.")
    return key


def _request(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 address
        API_ROOT + path,
        data=data,
        method=method,
        headers={"X-API-Key": _api_key(), "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed http://127.0.0.1 address
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}").get("error", "")
        except (ValueError, AttributeError):
            pass
        raise DnsError(detail or f"The DNS server answered HTTP {exc.code}.", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DnsError("The DNS server is not answering. Check that PowerDNS is running.") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise DnsError("The DNS server gave an answer the panel could not read.") from exc


# --- the machine (through the helper) ------------------------------------------

def install() -> dict:
    """Install PowerDNS, open port 53 and prove it answers. Raises if it does not."""
    shell.privileged("dns-install", fallback=["true"], timeout=900)
    return server_status()


def stop() -> dict:
    """Stop answering and close port 53. The zones stay in PowerDNS's database."""
    shell.privileged("dns-remove", fallback=["true"])
    return server_status()


def server_status() -> dict:
    result = shell.privileged(
        "dns-status",
        check=False,
        fallback=["bash", "-lc", "echo installed=no; echo running=no; echo api=no; echo port_open=no"],
    )
    info = {"installed": False, "running": False, "api": False, "port_open": False, "listen": []}
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in {"installed", "running", "api", "port_open"}:
            info[key] = value == "yes"
        elif key == "listen":
            info["listen"] = [part.strip() for part in value.split(",") if part.strip()]
    return info


# --- settings ------------------------------------------------------------------

def _panel_hostname() -> str:
    host = (urlparse(panel_settings.configured_panel_url() or "").hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        return host if ZONE_RE.match(host) else ""


def default_nameservers() -> list[str]:
    host = _panel_hostname()
    return [f"ns1.{host}", f"ns2.{host}"] if host else []


def default_zone_ip() -> str:
    addresses = server_network.ipv4_addresses()
    return addresses[0] if addresses else ""


def settings() -> dict:
    stored = panel_settings._read_raw_lenient().get(SETTINGS_KEY)
    stored = stored if isinstance(stored, dict) else {}
    nameservers = [str(name) for name in (stored.get("nameservers") or []) if str(name).strip()]
    ttl = stored.get("ttl")
    template = stored.get("template")
    package_templates = stored.get("package_templates")
    return {
        "nameservers": nameservers or default_nameservers(),
        "zone_ip": str(stored.get("zone_ip") or "") or default_zone_ip(),
        "ttl": ttl if isinstance(ttl, int) and MIN_TTL <= ttl <= MAX_TTL else DEFAULT_TTL,
        "auto_zone": stored.get("auto_zone", True) is not False,
        "template": template if isinstance(template, str) and template.strip() else DEFAULT_TEMPLATE,
        # A hosting package may have records of its own for new zones.
        "package_templates": {str(key): value for key, value in package_templates.items()
                              if str(key).isdigit() and isinstance(value, str) and value.strip()}
        if isinstance(package_templates, dict) else {},
        "transfers": stored.get("transfers") is True,
        "spf": spf_record(),
    }


def template_for_owner(db: Session, owner_id: int | None, config: dict | None = None) -> str:
    """The records a new zone of this account gets: its package's template,
    or the server's."""
    config = config or settings()
    if owner_id:
        user = db.query(User).filter(User.id == owner_id).first()
        package_id = getattr(user, "package_id", None) if user is not None else None
        if package_id and str(package_id) in config["package_templates"]:
            return config["package_templates"][str(package_id)]
    return config["template"]


def spf_record() -> str:
    """The SPF record a new zone gets: this server by name and address, and the
    Email addon's default relay when there is one (mail.default_spf)."""
    from app.services import mail

    return mail.default_spf()


def template_records(template: str, zone: str, zone_ip: str, ttl: int) -> list[dict]:
    """A template's lines, filled in for one zone, as records to_pdns reads.

    A line using {ip} is left out when there is no address for new zones.
    Raises DnsInputError naming the first line it cannot use.
    """
    records = []
    # Numbered as the administrator sees them, comments and blank lines included.
    lines = [(number, line.strip()) for number, line in enumerate((template or "").splitlines(), start=1)]
    lines = [(number, line) for number, line in lines if line and not line.startswith("#")]
    if len(lines) > MAX_TEMPLATE_LINES:
        raise DnsInputError("The template can hold at most 100 records.")
    spf = spf_record()
    for number, line in lines:
        if "{ip}" in line and not zone_ip:
            continue
        filled = line.replace("{ip}", zone_ip).replace("{domain}", zone).replace("{spf}", spf)
        parts = filled.split(None, 2)
        if len(parts) < 3:
            raise DnsInputError(f"Line {number}: " + "Each line is a name, a type and a value, such as www A {ip}.")
        name, rtype, value = parts[0], parts[1].upper(), parts[2]
        record = {"name": name, "type": rtype, "ttl": ttl, "content": value}
        if rtype in {"MX", "SRV"}:
            priority, _, rest = value.partition(" ")
            record.update(priority=priority, content=rest.strip())
        try:
            to_pdns(zone, record)
        except DnsInputError as exc:
            raise DnsInputError(f"Line {number}: {exc}") from exc
        records.append(record)
    return records


def check_template(template: str) -> str:
    """The template, if every line makes a valid record; raises otherwise."""
    template = (template or "").replace("\r\n", "\n").strip()
    if not template:
        return DEFAULT_TEMPLATE
    template_records(template, "example.com", "192.0.2.10", DEFAULT_TTL)
    return template + "\n"


def save_settings(nameservers: list[str], zone_ip: str, ttl: int, auto_zone: bool, template: str | None = None,
                  package_templates: dict | None = None) -> dict:
    names = []
    for name in nameservers:
        name = (name or "").strip().lower().rstrip(".")
        if not name:
            continue
        if not ZONE_RE.match(name):
            raise DnsInputError("A nameserver must be a full hostname, such as ns1.example.com.")
        if name not in names:
            names.append(name)
    if len(names) < 2:
        raise DnsInputError("Give two nameservers: registrars ask for at least two.")
    zone_ip = (zone_ip or "").strip()
    try:
        if ipaddress.ip_address(zone_ip).version != 4:
            raise ValueError
    except ValueError as exc:
        raise DnsInputError("The address for new zones must be an IPv4 address.") from exc
    if not MIN_TTL <= int(ttl) <= MAX_TTL:
        raise DnsInputError("TTL must be between 60 seconds and 7 days.")
    stored = panel_settings._read_raw()
    previous = stored.get(SETTINGS_KEY) if isinstance(stored.get(SETTINGS_KEY), dict) else {}
    kept = previous.get("template") if template is None else None
    template = check_template(template) if template is not None else (kept or DEFAULT_TEMPLATE)
    if package_templates is None:
        packages = previous.get("package_templates") if isinstance(previous.get("package_templates"), dict) else {}
    else:
        packages = {}
        for key, value in package_templates.items():
            if not str(key).isdigit():
                raise DnsInputError("A package template is keyed by the package's number.")
            if str(value or "").strip():
                try:
                    packages[str(int(key))] = check_template(str(value))
                except DnsInputError as exc:
                    raise DnsInputError(f"Package {key}: {exc}") from exc
    stored[SETTINGS_KEY] = {"nameservers": names, "zone_ip": zone_ip, "ttl": int(ttl), "auto_zone": bool(auto_zone),
                            "template": template, "package_templates": packages,
                            "transfers": previous.get("transfers") is True}
    panel_settings._write_raw(stored)
    return settings()


def follow_nameservers(db: Session) -> list[str]:
    """Every zone the panel made takes the nameservers of the settings: the NS
    records of its apex and the primary name in its SOA. Returns the zones
    changed; one PowerDNS refuses is logged and skipped."""
    config = settings()
    if len(config["nameservers"]) < 2:
        return []
    wanted = [_absolute(ns) for ns in config["nameservers"]]
    changed = []
    for row in db.query(DnsZone).order_by(DnsZone.name).all():
        try:
            data = _zone_data(row.name)
            apex = _absolute(row.name)
            changes = {}
            ns = _rrset(data, apex, "NS")
            if sorted(_contents(ns)) != sorted(wanted):
                changes[(apex, "NS")] = ((ns or {}).get("ttl", config["ttl"]), wanted)
            soa = _rrset(data, apex, "SOA")
            parts = (_contents(soa) or [""])[0].split()
            if len(parts) == 7 and parts[0] != wanted[0]:
                changes[(apex, "SOA")] = (soa.get("ttl", config["ttl"]), [" ".join([wanted[0], *parts[1:]])])
            if changes:
                _serial_follows_edits(row.name, data)
                _patch(row.name, changes)
                changed.append(row.name)
        except DnsError as exc:
            logger.warning("Nameservers not updated in %s: %s", row.name, exc)
    return changed


# --- names -----------------------------------------------------------------------

def _absolute(name: str) -> str:
    return name.rstrip(".") + "."


def normalize_zone(name: str) -> str:
    name = (name or "").strip().lower().rstrip(".")
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DnsInputError("That is not a valid domain name.") from exc
    if not ZONE_RE.match(name) or len(name) > 253:
        raise DnsInputError("That is not a valid domain name.")
    return name


def _record_name(zone: str, name: str) -> str:
    """'@', 'www', 'www.example.com' or '*.shop' -> the absolute owner name."""
    name = (name or "").strip().lower().rstrip(".")
    if name in {"", "@"} or name == zone:
        return _absolute(zone)
    if name.endswith("." + zone):
        name = name[: -len(zone) - 1]
    labels = name.split(".")
    for index, label in enumerate(labels):
        if label == "*" and index == 0:
            continue
        if not LABEL_RE.match(label):
            raise DnsInputError("The record name may use letters, digits, hyphens and dots, such as www or mail.")
    full = f"{name}.{zone}"
    if len(full) > 253:
        raise DnsInputError("That record name is too long.")
    return _absolute(full)


def _relative(zone: str, absolute: str) -> str:
    name = absolute.rstrip(".")
    if name == zone:
        return "@"
    return name[: -len(zone) - 1] if name.endswith("." + zone) else name


def _hostname(zone: str, value: str) -> str:
    """A target hostname. '@' is the zone itself, and a bare label ('mail') is a
    name inside the zone, as in a zone file - not the top-level domain 'mail'."""
    value = (value or "").strip().lower()
    if not value:
        raise DnsInputError("Enter a hostname, such as mail.example.com.")
    if value == "@":
        return _absolute(zone)
    value = value.rstrip(".")
    if not all(LABEL_RE.match(label) for label in value.split(".")) or len(value) > 253:
        raise DnsInputError("Enter a hostname, such as mail.example.com.")
    if "." not in value:
        return _absolute(f"{value}.{zone}")
    return _absolute(value)


def _txt(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    if not text:
        raise DnsInputError("A TXT record needs some text.")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    # One TXT string holds 255 bytes; longer text (a DKIM key) is split.
    chunks = [escaped[index:index + 255] for index in range(0, len(escaped), 255)]
    return " ".join(f'"{chunk}"' for chunk in chunks)


def _untxt(content: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', content)
    joined = "".join(parts) if parts else content
    return joined.replace('\\"', '"').replace("\\\\", "\\")


# --- records: the form's fields <-> PowerDNS content ------------------------------

def to_pdns(zone: str, record: dict) -> tuple[str, str, int, str]:
    """A record as typed -> (absolute name, type, ttl, PowerDNS content)."""
    rtype = str(record.get("type") or "").upper()
    if rtype not in RECORD_TYPES:
        raise DnsInputError("Choose a record type: " + ", ".join(RECORD_TYPES) + ".")
    name = _record_name(zone, str(record.get("name") or "@"))
    try:
        ttl = int(record.get("ttl") or DEFAULT_TTL)
    except (TypeError, ValueError) as exc:
        raise DnsInputError("TTL must be between 60 seconds and 7 days.") from exc
    if not MIN_TTL <= ttl <= MAX_TTL:
        raise DnsInputError("TTL must be between 60 seconds and 7 days.")
    value = str(record.get("content") or "").strip()
    priority = record.get("priority")

    if rtype == "A":
        try:
            content = str(ipaddress.IPv4Address(value))
        except ValueError as exc:
            raise DnsInputError("An A record points at an IPv4 address, such as 203.0.113.10.") from exc
    elif rtype == "AAAA":
        try:
            content = ipaddress.IPv6Address(value).compressed
        except ValueError as exc:
            raise DnsInputError("An AAAA record points at an IPv6 address.") from exc
    elif rtype in {"CNAME", "NS"}:
        content = _hostname(zone, value)
    elif rtype == "MX":
        content = f"{_priority(priority)} {_hostname(zone, value)}"
    elif rtype == "TXT":
        content = _txt(value)
    elif rtype == "SRV":
        parts = value.split()
        if len(parts) != 3 or not all(part.isdigit() for part in parts[:2]):
            raise DnsInputError("An SRV value is weight, port and target, such as 5 5060 sip.example.com.")
        weight, port = int(parts[0]), int(parts[1])
        if weight > 65535 or not 0 < port <= 65535:
            raise DnsInputError("An SRV value is weight, port and target, such as 5 5060 sip.example.com.")
        content = f"{_priority(priority)} {weight} {port} {_hostname(zone, parts[2])}"
    elif rtype == "CAA":
        parts = value.split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or int(parts[0]) > 255 or parts[1].lower() not in CAA_TAGS:
            raise DnsInputError("A CAA value is flags, tag and value, such as 0 issue letsencrypt.org.")
        caa_value = parts[2].strip().strip('"')
        if not caa_value or '"' in caa_value:
            raise DnsInputError("A CAA value is flags, tag and value, such as 0 issue letsencrypt.org.")
        content = f'{int(parts[0])} {parts[1].lower()} "{caa_value}"'
    else:
        content = _more_types(zone, name, rtype, value, priority)
    return name, rtype, ttl, content


def _numbers(parts: list[str], limits: list[int], message: str) -> list[int]:
    if len(parts) < len(limits) or not all(part.isdigit() for part in parts[:len(limits)]):
        raise DnsInputError(message)
    numbers = [int(part) for part in parts[:len(limits)]]
    if any(number > limit for number, limit in zip(numbers, limits)):
        raise DnsInputError(message)
    return numbers


def _hex(text: str, message: str, length: int | None = None) -> str:
    data = text.replace(" ", "").lower()
    if not _HEX_RE.fullmatch(data) or (length is not None and len(data) != length) or len(data) > 4096:
        raise DnsInputError(message)
    return data


def _svc_params(params: list[str], alias: bool) -> str:
    """SVCB/HTTPS SvcParams (RFC 9460), each checked; at most one of each key."""
    message = "The parameters are key=value pairs such as alpn=h2,h3 port=443 ipv4hint=192.0.2.1."
    if alias and params:
        raise DnsInputError("A priority 0 (alias) record has a target and no parameters.")
    seen, out = set(), []
    for param in params:
        match = _SVC_KEY_RE.fullmatch(param.replace('"', ""))
        if not match or match.group(1) in seen:
            raise DnsInputError(message)
        key, raw = match.group(1), match.group(2)
        seen.add(key)
        if key == "no-default-alpn":
            if raw is not None:
                raise DnsInputError(message)
            out.append(key)
            continue
        if not raw:
            raise DnsInputError(message)
        items = raw.split(",")
        if key == "alpn" and all(_ALPN_RE.fullmatch(item) for item in items):
            pass
        elif key == "port" and raw.isdigit() and 0 < int(raw) <= 65535:
            pass
        elif key == "mandatory" and all(item in ("alpn", "no-default-alpn", "port", "ipv4hint", "ipv6hint", "ech")
                                        for item in items):
            pass
        elif key == "ipv4hint":
            try:
                items = [str(ipaddress.IPv4Address(item)) for item in items]
            except ValueError as exc:
                raise DnsInputError(message) from exc
        elif key == "ipv6hint":
            try:
                items = [ipaddress.IPv6Address(item).compressed for item in items]
            except ValueError as exc:
                raise DnsInputError(message) from exc
        elif key == "ech" and _B64_RE.fullmatch(raw) and len(raw) <= 4096:
            pass
        else:
            raise DnsInputError(message)
        out.append(f"{key}={','.join(items)}")
    return " ".join(out)


def _more_types(zone: str, name: str, rtype: str, value: str, priority) -> str:
    """Content of the types beyond the everyday ones, checked field by field."""
    parts = value.split()
    if rtype == "PTR":
        if not zone.endswith(REVERSE_SUFFIXES):
            raise DnsInputError("PTR records belong in a reverse zone (in-addr.arpa or ip6.arpa).")
        return _hostname(zone, value)
    if rtype == "TLSA":
        message = "A TLSA value is usage (0-3), selector (0-1), matching type (0-2) and the data in hex."
        usage, selector, matching = _numbers(parts, [3, 1, 2], message)
        length = {1: 64, 2: 128}.get(matching)
        return f"{usage} {selector} {matching} {_hex(''.join(parts[3:]), message, length)}"
    if rtype == "SSHFP":
        message = "An SSHFP value is algorithm (1-4 or 6), fingerprint type (1 or 2) and the fingerprint in hex."
        algorithm, kind = _numbers(parts, [6, 2], message)
        if algorithm not in (1, 2, 3, 4, 6) or kind not in (1, 2):
            raise DnsInputError(message)
        return f"{algorithm} {kind} {_hex(''.join(parts[2:]), message, {1: 40, 2: 64}[kind])}"
    if rtype == "DS":
        message = "A DS value is key tag, algorithm, digest type (1, 2 or 4) and the digest in hex."
        if name == _absolute(zone):
            raise DnsInputError("A DS record goes in the parent zone; here it belongs to a delegated subdomain.")
        tag, algorithm, digest = _numbers(parts, [65535, 255, 255], message)
        if digest not in (1, 2, 4):
            raise DnsInputError(message)
        return f"{tag} {algorithm} {digest} {_hex(''.join(parts[3:]), message, {1: 40, 2: 64, 4: 96}[digest])}"
    if rtype == "NAPTR":
        match = _NAPTR_RE.fullmatch(value.strip())
        if not match:
            raise DnsInputError('A NAPTR value is order, preference, "flags", "service", "regexp" and replacement, '
                                'such as 100 10 "S" "SIP+D2U" "" _sip._udp.example.com.')
        order, preference, flags, service, regexp, replacement = match.groups()
        if int(order) > 65535 or int(preference) > 65535:
            raise DnsInputError("NAPTR order and preference are 0 to 65535.")
        target = "." if replacement == "." else _hostname(zone, replacement)
        return f'{int(order)} {int(preference)} "{flags.upper()}" "{service}" "{regexp}" {target}'
    # SVCB and HTTPS: the priority is its own field; the value is the target and its parameters.
    number = _priority(priority if priority not in (None, "") else 1)
    if not parts:
        raise DnsInputError("An HTTPS or SVCB value is a target (. for the name itself) and its parameters.")
    target = "." if parts[0] == "." else _hostname(zone, parts[0])
    params = _svc_params(parts[1:], alias=number == 0)
    return f"{number} {target}" + (f" {params}" if params else "")


def _priority(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DnsInputError("Priority must be a number from 0 to 65535.") from exc
    if not 0 <= number <= 65535:
        raise DnsInputError("Priority must be a number from 0 to 65535.")
    return number


def to_form(zone: str, name: str, rtype: str, ttl: int, content: str) -> dict:
    """A PowerDNS record -> what the page shows and sends back to identify it."""
    record = {"name": _relative(zone, name), "type": rtype, "ttl": ttl, "content": content, "priority": None}
    if rtype in {"CNAME", "NS"}:
        record["content"] = content.rstrip(".")
    elif rtype == "MX":
        priority, _, host = content.partition(" ")
        record.update(priority=int(priority) if priority.isdigit() else None, content=host.rstrip("."))
    elif rtype == "SRV":
        parts = content.split()
        if len(parts) == 4 and parts[0].isdigit():
            record.update(priority=int(parts[0]), content=f"{parts[1]} {parts[2]} {parts[3].rstrip('.')}")
    elif rtype == "TXT":
        record["content"] = _untxt(content)
    elif rtype == "CAA":
        parts = content.split(None, 2)
        if len(parts) == 3:
            record["content"] = f"{parts[0]} {parts[1]} {parts[2].strip(chr(34))}"
    elif rtype == "PTR":
        record["content"] = content.rstrip(".")
    elif rtype in ("HTTPS", "SVCB"):
        number, _, rest = content.partition(" ")
        if number.isdigit():
            target, _, params = rest.partition(" ")
            record.update(priority=int(number), content=" ".join(filter(None, [target if target == "." else target.rstrip("."),
                                                                               params.replace('"', "")])))
    elif rtype == "NAPTR":
        head, _, target = content.rpartition(" ")
        record["content"] = f"{head} {target if target == '.' else target.rstrip('.')}"
    return record


# --- zones --------------------------------------------------------------------------

def active() -> bool:
    return addons.is_installed(addons.DNS)


def _row(db: Session, zone: str) -> DnsZone | None:
    return db.query(DnsZone).filter(DnsZone.name == zone).first()


def may_edit(db: Session, user: User, zone_name: str) -> str:
    """The zone, if this person may edit it. A customer who may not is told
    it does not exist, so they learn nothing about other people's zones."""
    zone = normalize_zone(zone_name)
    if is_admin_role(user.role):
        return zone
    row = _row(db, zone)
    if row is None:
        raise DnsError("There is no such zone.", status=404)
    if row.owner_id != user.id and zone not in _domains_of(db, user.id):
        raise DnsError("There is no such zone.", status=404)
    return zone


def _domains_of(db: Session, user_id: int) -> set[str]:
    """The website domains and aliases in one account."""
    names = {website.domain.lower() for website in db.query(Website).filter(Website.owner_id == user_id)}
    names |= {alias.domain.lower() for alias in db.query(WebsiteAlias).join(Website)
              .filter(Website.owner_id == user_id)}
    return names


def list_zones(db: Session, user: User) -> list[dict]:
    query = db.query(DnsZone)
    admin = is_admin_role(user.role)
    if not admin:
        query = query.filter(DnsZone.owner_id == user.id)
    rows = query.order_by(DnsZone.name).all()
    owners = {row.id: row.username for row in db.query(User).all()} if admin else {}
    zones = [{"name": row.name, "owner_id": row.owner_id, "owner": owners.get(row.owner_id, "")} for row in rows]
    if admin:
        # Zones PowerDNS serves that the panel did not make: shown, owned by nobody.
        known = {row.name for row in rows}
        for item in _request("GET", "/zones"):
            name = str(item.get("name", "")).rstrip(".")
            if name and name not in known:
                zones.append({"name": name, "owner_id": None, "owner": ""})
        zones.sort(key=lambda zone: zone["name"])
    return zones


def create_zone(db: Session, name: str, owner_id: int | None, *, claim_existing: bool = True) -> str:
    zone = normalize_zone(name)
    config = settings()
    if len(config["nameservers"]) < 2:
        raise DnsInputError("Set the nameservers on the DNS page first.")
    if _row(db, zone):
        raise DnsInputError("That zone already exists.")
    apex = _absolute(zone)
    ttl = config["ttl"]
    rrsets = [
        {"name": apex, "type": "SOA", "ttl": ttl, "records": [{
            "content": f"{_absolute(config['nameservers'][0])} hostmaster.{apex} 1 10800 3600 604800 3600",
            "disabled": False,
        }]},
        {"name": apex, "type": "NS", "ttl": ttl,
         "records": [{"content": _absolute(ns), "disabled": False} for ns in config["nameservers"]]},
    ]
    # The rest from the template (by default what DirectAdmin puts in a new
    # zone, less what spanel does not run): records of one name and type
    # become one RRset.
    grouped: dict[tuple[str, str], list[str]] = {}
    for record in template_records(template_for_owner(db, owner_id, config), zone, config["zone_ip"], ttl):
        name, rtype, _, content = to_pdns(zone, record)
        if rtype == "NS" and name == apex:
            continue
        contents = grouped.setdefault((name, rtype), [])
        if content not in contents:
            contents.append(content)
    for (name, rtype), contents in grouped.items():
        rrsets.append({"name": name, "type": rtype, "ttl": ttl,
                       "records": [{"content": content, "disabled": False} for content in contents]})
    try:
        _request("POST", "/zones", {
            "name": apex, "kind": "Native", "soa_edit_api": SOA_EDIT_API,
            "nameservers": [], "rrsets": rrsets,
        })
    except DnsError as exc:
        # Served already but not the panel's: an administrator may take it
        # over; a website being created may not.
        if exc.status != 409 or not claim_existing:
            raise
    db.add(DnsZone(name=zone, owner_id=owner_id))
    db.commit()
    # A mail domain in it gets its mail records now, not at its next change.
    from app.services import mail, multi_server

    mail.publish_all_quietly(db, zone)
    multi_server.trigger_background_zone_sync(zone, action="upsert")
    return zone


def delete_zone(db: Session, zone: str, *, sync_cluster: bool = True) -> None:
    try:
        _request("DELETE", f"/zones/{_absolute(zone)}")
    except DnsError as exc:
        if exc.status != 404:
            raise
    row = _row(db, zone)
    if row is not None:
        db.delete(row)
        db.commit()
    if sync_cluster:
        from app.services import multi_server

        multi_server.trigger_background_zone_sync(zone, action="delete")


def _zone_data(zone: str) -> dict:
    return _request("GET", f"/zones/{_absolute(zone)}")


def records(zone: str) -> list[dict]:
    data = _zone_data(zone)
    rows = []
    for rrset in data.get("rrsets", []):
        if rrset.get("type") == "SOA":
            continue
        for item in rrset.get("records", []):
            rows.append(to_form(zone, rrset["name"], rrset["type"], rrset.get("ttl", DEFAULT_TTL), item["content"]))
    rows.sort(key=lambda row: (row["name"] != "@", row["name"], TYPE_ORDER.get(row["type"], 99), row["content"]))
    return rows


def _rrset(data: dict, name: str, rtype: str) -> dict | None:
    for rrset in data.get("rrsets", []):
        if rrset.get("name") == name and rrset.get("type") == rtype:
            return rrset
    return None


def _contents(rrset: dict | None) -> list[str]:
    return [item["content"] for item in (rrset or {}).get("records", [])]


def _same(a: str, b: str) -> bool:
    return a.rstrip(".").lower() == b.rstrip(".").lower()


def _guard(zone: str, name: str, rtype: str, admin: bool) -> None:
    if not admin and rtype == "NS" and name == _absolute(zone):
        raise DnsInputError("Only an administrator can change the zone's own nameservers.")


def _check_cname(data: dict, zone: str, name: str, rtype: str, ignore: tuple[str, str, str] | None = None) -> None:
    """A CNAME stands alone: not at the zone's apex, not beside other records."""
    others = []
    for rrset in data.get("rrsets", []):
        if rrset.get("name") != name:
            continue
        for content in _contents(rrset):
            if ignore and (rrset["name"], rrset["type"]) == ignore[:2] and _same(content, ignore[2]):
                continue
            others.append(rrset["type"])
    if rtype == "CNAME":
        if name == _absolute(zone):
            raise DnsInputError("The zone's own name cannot be a CNAME; use an A record.")
        if [kind for kind in others if kind != "CNAME"]:
            raise DnsInputError("A name with a CNAME record can have no other records.")
        if "CNAME" in others:
            raise DnsInputError("A name can have only one CNAME record.")
    elif "CNAME" in others:
        raise DnsInputError("A name with a CNAME record can have no other records.")


def _serial_follows_edits(zone: str, data: dict) -> None:
    """Put right a zone made before SOA_EDIT_API was, so its serial moves."""
    if data.get("soa_edit_api") not in (None, SOA_EDIT_API):
        _request("PUT", f"/zones/{_absolute(zone)}", {"soa_edit_api": SOA_EDIT_API})


def _patch(zone: str, changes: dict[tuple[str, str], tuple[int, list[str]]]) -> None:
    rrsets = []
    for (name, rtype), (ttl, contents) in changes.items():
        if contents:
            rrsets.append({"name": name, "type": rtype, "ttl": ttl, "changetype": "REPLACE",
                           "records": [{"content": content, "disabled": False} for content in contents]})
        else:
            rrsets.append({"name": name, "type": rtype, "changetype": "DELETE"})
    _request("PATCH", f"/zones/{_absolute(zone)}", {"rrsets": rrsets})
    from app.services import multi_server

    multi_server.trigger_background_zone_sync(zone, action="upsert")


def add_record(zone: str, record: dict, *, admin: bool) -> None:
    name, rtype, ttl, content = to_pdns(zone, record)
    _guard(zone, name, rtype, admin)
    data = _zone_data(zone)
    _check_cname(data, zone, name, rtype)
    existing = _contents(_rrset(data, name, rtype))
    if any(_same(content, item) for item in existing):
        raise DnsInputError("That record already exists.")
    _serial_follows_edits(zone, data)
    _patch(zone, {(name, rtype): (ttl, existing + [content])})


def _remove(data: dict, zone: str, record: dict, changes: dict) -> tuple[str, str, str]:
    """Take one record out of its RRset in `changes`; returns what it matched."""
    name, rtype, _, content = to_pdns(zone, record)
    key = (name, rtype)
    if key in changes:
        ttl, current = changes[key]
    else:
        rrset = _rrset(data, name, rtype)
        ttl, current = (rrset or {}).get("ttl", DEFAULT_TTL), _contents(rrset)
    kept = [item for item in current if not _same(item, content)]
    if len(kept) == len(current):
        raise DnsError("That record is no longer there. Refresh the page.", status=404)
    changes[key] = (ttl, kept)
    return name, rtype, content


def update_record(zone: str, original: dict, record: dict, *, admin: bool) -> None:
    name, rtype, ttl, content = to_pdns(zone, record)
    _guard(zone, name, rtype, admin)
    old_name, old_type, _, _ = to_pdns(zone, original)
    _guard(zone, old_name, old_type, admin)
    data = _zone_data(zone)
    changes: dict = {}
    removed = _remove(data, zone, original, changes)
    _check_cname(data, zone, name, rtype, ignore=removed)
    key = (name, rtype)
    _, current = changes.get(key, (ttl, _contents(_rrset(data, name, rtype))))
    if any(_same(content, item) for item in current):
        raise DnsInputError("That record already exists.")
    changes[key] = (ttl, current + [content])
    _serial_follows_edits(zone, data)
    _patch(zone, changes)


def delete_record(zone: str, record: dict, *, admin: bool) -> None:
    name, rtype, _, _ = to_pdns(zone, record)
    _guard(zone, name, rtype, admin)
    changes: dict = {}
    data = _zone_data(zone)
    _remove(data, zone, record, changes)
    _serial_follows_edits(zone, data)
    _patch(zone, changes)


# --- zone files: BIND import and export ------------------------------------------------
# A zone file is read with dnspython (no $INCLUDE), and every record goes
# through to_form/to_pdns, the checks a record typed on the page gets. SOA and
# the zone's own NS are the panel's (DNS settings) and are never imported.

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_RECORDS = 5000
IMPORT_MODES = ("merge", "replace")


def export_zone(zone: str) -> str:
    """The zone as a BIND zone file: absolute names, one record a line, the
    SOA first. A disabled record is written as a comment."""
    data = _zone_data(zone)
    apex = _absolute(zone)
    order = {**TYPE_ORDER, "SOA": -2, "NS": -1}
    rrsets = sorted(data.get("rrsets", []),
                    key=lambda r: (r.get("name") != apex, r.get("name", ""), order.get(r.get("type"), 99)))
    lines = [f"; {zone} - exported by SPanel DNS Manager", f"$ORIGIN {apex}", f"$TTL {settings()['ttl']}"]
    for rrset in rrsets:
        for item in rrset.get("records", []):
            prefix = "; disabled: " if item.get("disabled") else ""
            lines.append(f"{prefix}{rrset['name']}\t{rrset.get('ttl', DEFAULT_TTL)}\tIN\t{rrset['type']}\t{item['content']}")
    return "\n".join(lines) + "\n"


def parse_zone_file(zone: str, text: str) -> dict:
    """A zone file -> what an import would write. Returns {"records": [form
    rows with "pdns" content], "skipped": [...], "errors": [...]}; raises
    DnsInputError when the file cannot be read at all."""
    import dns.exception
    import dns.rdatatype
    import dns.zone

    zone = normalize_zone(zone)
    if len((text or "").encode("utf-8")) > MAX_IMPORT_BYTES:
        raise DnsInputError("A zone file of at most 1 MB.")
    apex = _absolute(zone)
    text = text or ""
    if not re.search(r"^\$TTL\s", text, re.M):
        # A file without $TTL (an export of a single record set, say) gets the default.
        text = f"$TTL {DEFAULT_TTL}\n" + text
    try:
        parsed = dns.zone.from_text(text, origin=apex, relativize=False, check_origin=False,
                                    allow_include=False)
    except (dns.exception.DNSException, ValueError, UnicodeError) as exc:
        raise DnsInputError(f"The zone file could not be read: {exc}") from exc
    records, skipped, errors = [], [], []
    for owner, node in sorted(parsed.nodes.items(), key=lambda item: item[0].to_text()):
        name = owner.to_text().lower()
        if name != apex and not name.endswith("." + apex):
            skipped.append({"name": name, "type": "", "reason": f"outside {zone}"})
            continue
        for rdataset in node.rdatasets:
            rtype = dns.rdatatype.to_text(rdataset.rdtype)
            if rtype == "SOA" or (rtype == "NS" and name == apex):
                skipped.append({"name": name, "type": rtype, "reason": "kept by the DNS settings"})
                continue
            if rtype not in RECORD_TYPES:
                skipped.append({"name": name, "type": rtype, "reason": "not a type DNS Manager edits"})
                continue
            ttl = min(MAX_TTL, max(MIN_TTL, int(rdataset.ttl)))
            for rdata in rdataset:
                text_value = rdata.to_text()
                form = to_form(zone, name, rtype, ttl, text_value)
                try:
                    absolute, _, _, content = to_pdns(zone, form)
                except DnsInputError as exc:
                    errors.append({"name": name, "type": rtype, "value": text_value[:300], "error": str(exc)})
                    continue
                records.append({**form, "fqdn": absolute.rstrip("."), "pdns": content})
    if len(records) > MAX_IMPORT_RECORDS:
        raise DnsInputError("A zone file of at most 5000 records.")
    return {"records": records, "skipped": skipped, "errors": errors}


def _import_changes(zone: str, data: dict, records: list[dict], mode: str, admin: bool) -> dict:
    """The RRsets an import rewrites, with the zone's CNAME rules checked
    against the result."""
    if mode not in IMPORT_MODES:
        raise DnsInputError("Import merges records into the zone or replaces the record sets the file has.")
    grouped: dict[tuple[str, str], tuple[int, list[str]]] = {}
    for record in records:
        key = (_absolute(record["fqdn"]), record["type"])
        _guard(zone, key[0], key[1], admin)
        ttl, contents = grouped.get(key, (record["ttl"], []))
        if not any(_same(record["pdns"], item) for item in contents):
            contents.append(record["pdns"])
        grouped[key] = (ttl, contents)
    changes = {}
    for key, (ttl, contents) in grouped.items():
        existing = _contents(_rrset(data, *key))
        wanted = contents if mode == "replace" else existing + [c for c in contents if not any(_same(c, e) for e in existing)]
        if sorted(wanted) != sorted(existing) or (_rrset(data, *key) or {}).get("ttl") != ttl:
            changes[key] = (ttl, wanted)
    final: dict[tuple[str, str], list[str]] = {(r["name"], r["type"]): _contents(r) for r in data.get("rrsets", [])}
    final.update({key: value[1] for key, value in changes.items()})
    for (name, rtype), contents in final.items():
        if rtype != "CNAME" or not contents:
            continue
        if name == _absolute(zone):
            raise DnsInputError("The zone's own name cannot be a CNAME; use an A record.")
        if len(contents) > 1 or any(other_name == name and other_type != "CNAME" and other
                                    for (other_name, other_type), other in final.items()):
            raise DnsInputError(f"{name.rstrip('.')} would have a CNAME beside other records.")
    return changes


def preview_import(zone: str, text: str, mode: str = "merge", *, admin: bool) -> dict:
    parsed = parse_zone_file(zone, text)
    data = _zone_data(zone)
    for record in parsed["records"]:
        existing = _contents(_rrset(data, _absolute(record["fqdn"]), record["type"]))
        record["action"] = "exists" if any(_same(record["pdns"], item) for item in existing) else "add"
    removed = []
    if mode == "replace":
        kept = {(_absolute(r["fqdn"]), r["type"]) for r in parsed["records"]}
        for key in kept:
            for item in _contents(_rrset(data, *key)):
                if not any(_same(item, r["pdns"]) for r in parsed["records"]
                           if (_absolute(r["fqdn"]), r["type"]) == key):
                    removed.append(to_form(zone, key[0], key[1], (_rrset(data, *key) or {}).get("ttl", DEFAULT_TTL), item))
    try:
        changes = _import_changes(zone, data, parsed["records"], mode, admin)
        problem = ""
    except DnsInputError as exc:
        changes, problem = {}, str(exc)
    for record in parsed["records"]:
        record.pop("pdns", None)
    return {**parsed, "removed": removed, "rrsets": len(changes), "problem": problem, "mode": mode}


def import_zone(zone: str, text: str, mode: str = "merge", *, admin: bool) -> dict:
    """Write a zone file's records into the zone: merged with what is there,
    or replacing the record sets the file holds. Nothing is written while any
    record of the file is refused."""
    parsed = parse_zone_file(zone, text)
    if parsed["errors"]:
        first = parsed["errors"][0]
        raise DnsInputError(f"{len(parsed['errors'])} record(s) cannot be imported; first {first['name']} "
                            f"{first['type']}: {first['error']}")
    data = _zone_data(zone)
    changes = _import_changes(zone, data, parsed["records"], mode, admin)
    if changes:
        _serial_follows_edits(zone, data)
        _patch(zone, changes)
    return {"rrsets": len(changes), "records": len(parsed["records"]), "skipped": len(parsed["skipped"])}


# --- secondary nameservers: zone transfers and NOTIFY ----------------------------------
# PowerDNS answers AXFR only to the addresses a zone's ALLOW-AXFR-FROM lists
# (loopback otherwise), and sends NOTIFY to its ALSO-NOTIFY addresses; a zone
# with either is made a primary ("Master") so PowerDNS notifies at all. The
# server-wide switch (dns-transfers in the helper) turns AXFR on in pdns.conf.

MAX_SECONDARIES = 10


def _metadata(zone: str, kind: str) -> list[str]:
    try:
        data = _request("GET", f"/zones/{_absolute(zone)}/metadata/{kind}")
    except DnsError as exc:
        if exc.status in (404, 422):
            return []
        raise
    return [str(item) for item in (data or {}).get("metadata") or []]


def secondary_view(zone: str) -> dict:
    zone = normalize_zone(zone)
    data = _zone_data(zone)
    return {"zone": zone, "kind": data.get("kind") or "Native", "also_notify": _metadata(zone, "ALSO-NOTIFY"),
            "allow_axfr": _metadata(zone, "ALLOW-AXFR-FROM"), "transfers": settings()["transfers"]}


def normalize_secondaries(also_notify, allow_axfr) -> tuple[list[str], list[str]]:
    notify, allow = [], []
    for value in also_notify or []:
        try:
            address = str(ipaddress.ip_address(str(value).strip()))
        except ValueError as exc:
            raise DnsInputError("NOTIFY goes to IP addresses of the secondary nameservers.") from exc
        if address not in notify:
            notify.append(address)
    for value in allow_axfr or []:
        try:
            network = ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError as exc:
            raise DnsInputError("Zone transfers are allowed to IP addresses or networks such as 192.0.2.0/24.") from exc
        if network.prefixlen == 0:
            raise DnsInputError("Allow zone transfers to the secondaries' addresses, not to everyone.")
        text = str(network.network_address) if network.num_addresses == 1 else str(network)
        if text not in allow:
            allow.append(text)
    if len(notify) > MAX_SECONDARIES or len(allow) > MAX_SECONDARIES:
        raise DnsInputError("At most 10 addresses each.")
    return notify, allow


def _transfers_on() -> None:
    """Turn AXFR on server-wide (once): the helper rewrites pdns.conf."""
    stored = panel_settings._read_raw()
    current = stored.get(SETTINGS_KEY) if isinstance(stored.get(SETTINGS_KEY), dict) else {}
    if current.get("transfers") is True:
        return
    result = shell.privileged("dns-transfers", helper_args=["on"], check=False, fallback=["true"], timeout=120)
    if result.returncode != 0:
        raise DnsError("PowerDNS could not be set up for zone transfers: " + (result.stderr or "").strip()[-300:])
    current["transfers"] = True
    stored[SETTINGS_KEY] = current
    panel_settings._write_raw(stored)


def set_secondaries(zone: str, also_notify, allow_axfr) -> dict:
    zone = normalize_zone(zone)
    notify, allow = normalize_secondaries(also_notify, allow_axfr)
    if notify or allow:
        _transfers_on()
    apex = _absolute(zone)
    for kind, values in (("ALSO-NOTIFY", notify), ("ALLOW-AXFR-FROM", allow)):
        if values:
            _request("PUT", f"/zones/{apex}/metadata/{kind}", {"kind": kind, "metadata": values})
        else:
            try:
                _request("DELETE", f"/zones/{apex}/metadata/{kind}")
            except DnsError as exc:
                if exc.status not in (404, 422):
                    raise
    _request("PUT", f"/zones/{apex}", {"kind": "Master" if (notify or allow) else "Native"})
    if notify:
        try:
            _request("PUT", f"/zones/{apex}/notify")
        except DnsError as exc:
            logger.warning("NOTIFY for %s not sent: %s", zone, exc)
    return secondary_view(zone)


# --- every domain on the server -------------------------------------------------------

def _ready(config: dict) -> bool:
    return bool(active() and config["auto_zone"] and len(config["nameservers"]) >= 2 and config["zone_ip"])


def provision_domain(db: Session, domain: str, owner_id: int | None, config: dict | None = None) -> tuple[str, str] | None:
    """Give one domain its DNS here. Returns what was done, or None.

    Inside a zone the same person already has on this server (a subdomain of
    their own domain) the domain becomes an A record there, if the name has
    no record yet. Anywhere else it gets a full zone of its own. A zone
    PowerDNS already serves that the panel did not make is left alone.
    """
    config = config or settings()
    domain = normalize_zone(domain)
    containing = [row for row in db.query(DnsZone).all()
                  if domain == row.name or domain.endswith("." + row.name)]
    parent = max(containing, key=lambda row: len(row.name), default=None)
    if parent is not None and parent.name == domain:
        return None
    if parent is not None and parent.owner_id == owner_id:
        name = _absolute(domain)
        if any(rrset.get("name") == name for rrset in _zone_data(parent.name).get("rrsets", [])):
            return None
        label = domain[: -len(parent.name) - 1]
        add_record(parent.name, {"name": label, "type": "A", "ttl": config["ttl"],
                                 "content": config["zone_ip"]}, admin=True)
        return ("record", parent.name)
    try:
        return ("zone", create_zone(db, domain, owner_id, claim_existing=False))
    except DnsError as exc:
        if exc.status == 409:
            return None
        raise


def zone_for_new_domain(db: Session, domain: str, owner_id: int | None) -> tuple[str, str] | None:
    """A website or alias just added gets its DNS. Never the reason it fails."""
    try:
        config = settings()
        if not _ready(config):
            return None
        return provision_domain(db, domain, owner_id, config)
    except (DnsError, DnsInputError) as exc:
        logger.warning("No DNS for new domain %s: %s", domain, exc)
        db.rollback()
        return None


# --- the DNS page (laid out like OPanel's) --------------------------------------------------

def _panel_names(db: Session) -> set[str]:
    """Every name the panel serves: websites, their aliases, mail domains."""
    from app.models.entities import MailDomain

    names = {row.domain.lower() for row in db.query(Website).all()}
    names |= {row.domain.lower() for row in db.query(WebsiteAlias).all()}
    names |= {row.domain.lower() for row in db.query(MailDomain).all()}
    return names


def zones_page(db: Session, user: User, q: str = "", page: int = 1, per_page: int = 50) -> dict:
    """One page of the zones a user sees, searched by name. A zone that no
    longer holds anything of the panel's is said so: only that one may go."""
    zones = list_zones(db, user)
    term = (q or "").strip().lower()
    if term:
        zones = [zone for zone in zones if term in zone["name"]]
    page, per_page = max(1, int(page or 1)), max(1, min(int(per_page or 50), 200))
    rows = {row.name: row for row in db.query(DnsZone).all()}
    names = _panel_names(db)
    items = []
    for zone in zones[(page - 1) * per_page: page * per_page]:
        row = rows.get(zone["name"])
        items.append({
            **zone,
            "created_at": row.created_at.isoformat() if row is not None and row.created_at else None,
            "managed": row is not None,
            "on_panel": any(name == zone["name"] or name.endswith("." + zone["name"]) for name in names),
        })
    return {"items": items, "total": len(zones), "page": page, "per_page": per_page}


def _mail_marks(db: Session, zone: str) -> dict[tuple[str, str, str], str]:
    """The records the Email addon keeps in a zone, and for which mail domain."""
    from app.models.entities import MailDomain
    from app.services import mail

    if not mail.active():
        return {}
    marks = {}
    for row in db.query(MailDomain).all():
        if row.domain != zone and not row.domain.endswith("." + zone):
            continue
        for record in mail.dns_records(row):
            name = record["name"].rstrip(".").lower()
            value = "spf" if record["key"] == "spf" else record["value"].rstrip(".").lower()
            marks[(name, record["type"], value)] = row.domain
    return marks


def zone_records(db: Session, zone: str) -> list[dict]:
    """Every record of a zone as the DNS page lists it: SOA and the zone's own
    NS are locked (DNS settings write them); mail marks the Email addon's."""
    data = _zone_data(zone)
    apex = _absolute(zone)
    marks = _mail_marks(db, zone)
    rows = []
    for rrset in data.get("rrsets", []):
        rtype = rrset.get("type")
        fqdn = str(rrset.get("name", "")).rstrip(".").lower()
        for item in rrset.get("records", []):
            row = to_form(zone, rrset["name"], rtype, rrset.get("ttl", DEFAULT_TTL), item["content"])
            row["value"] = row["content"]
            row["fqdn"] = fqdn
            row["locked"] = rtype == "SOA" or (rtype == "NS" and rrset["name"] == apex)
            probe = "spf" if rtype == "TXT" and row["content"].lower().startswith("v=spf1") else row["content"].rstrip(".").lower()
            row["mail"] = marks.get((fqdn, rtype, probe), "")
            rows.append(row)
    order = {**TYPE_ORDER, "SOA": -1}
    rows.sort(key=lambda row: (row["name"] != "@", row["name"], order.get(row["type"], 99), row["content"]))
    return rows


def _lookup_ns(zone: str) -> list[str] | None:
    """The zone's NS records as public DNS answers; [] for none, None when DNS
    could not be asked."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None
    resolver = dns.resolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 5
    try:
        return sorted(str(item.target).rstrip(".").lower() for item in resolver.resolve(zone, "NS"))
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.exception.DNSException, OSError):
        return None


def delegation(zone: str) -> dict:
    """Whether public DNS asks this server's nameservers about the zone."""
    want = sorted(name.rstrip(".").lower() for name in settings()["nameservers"])
    found = _lookup_ns(zone)
    if found is None:
        return {"status": "unknown", "expected": want, "found": []}
    status = "ok" if found and set(want) <= set(found) else ("different" if found else "missing")
    return {"status": status, "expected": want, "found": found}


# --- 1-Click DNSSEC (DS / DNSKEY) --------------------------------------------------

def _parse_ds_record(zone: str, raw_ds: str) -> dict:
    """Parse a PowerDNS DS string ('<key_tag> <algo> <digest_type> <digest>') into structured registrar fields."""
    parts = (raw_ds or "").strip().split()
    if len(parts) >= 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        key_tag, algorithm, digest_type, digest = int(parts[0]), int(parts[1]), int(parts[2]), "".join(parts[3:])
    else:
        key_tag, algorithm, digest_type, digest = 0, 13, 2, raw_ds.strip()
    digest_names = {1: "SHA-1 (1)", 2: "SHA-256 (2)", 4: "SHA-384 (4)"}
    return {
        "record": f"{zone}. IN DS {key_tag} {algorithm} {digest_type} {digest}",
        "raw": raw_ds.strip(),
        "key_tag": key_tag,
        "algorithm": algorithm,
        "algorithm_name": "ECDSAP256SHA256 (13)" if algorithm == 13 else str(algorithm),
        "digest_type": digest_type,
        "digest_type_name": digest_names.get(digest_type, str(digest_type)),
        "digest": digest,
    }


def _format_cryptokeys_status(zone: str, keys: list[dict]) -> dict:
    active_keys = [k for k in (keys or []) if isinstance(k, dict) and k.get("active")]
    ds_records = []
    dnskey_records = []
    for key in active_keys:
        dnskey = str(key.get("dnskey") or "").strip()
        if dnskey:
            dnskey_records.append(f"{zone}. IN DNSKEY {dnskey}")
        for raw_ds in (key.get("ds") or []):
            if raw_ds:
                ds_records.append(_parse_ds_record(zone, str(raw_ds)))
    primary_ds = ds_records[0] if ds_records else None
    for item in ds_records:
        if item.get("digest_type") == 2:
            primary_ds = item
            break
    return {
        "zone": zone,
        "enabled": len(active_keys) > 0,
        "algorithm": "ECDSAP256SHA256 (Algorithm 13)",
        "key_count": len(active_keys),
        "primary_ds": primary_ds,
        "ds_records": ds_records,
        "dnskey_records": dnskey_records,
    }


def get_dnssec_status(zone: str) -> dict:
    """Return DNSSEC status, DS records, and DNSKEY records for a zone directly from PowerDNS."""
    zone = normalize_zone(zone)
    apex = _absolute(zone)
    keys = _request("GET", f"/zones/{apex}/cryptokeys")
    return _format_cryptokeys_status(zone, keys if isinstance(keys, list) else [])


def enable_dnssec(zone: str) -> dict:
    """Enable 1-Click DNSSEC (CSK ECDSAP256SHA256 / Algorithm 13) on a zone and return verified PowerDNS DS/DNSKEY records."""
    zone = normalize_zone(zone)
    apex = _absolute(zone)
    existing = _request("GET", f"/zones/{apex}/cryptokeys")
    active = [k for k in (existing or []) if isinstance(k, dict) and k.get("active")]
    if not active:
        _request(
            "POST",
            f"/zones/{apex}/cryptokeys",
            {"keytype": "csk", "active": True, "published": True, "algorithm": "ecdsa256"},
        )
    try:
        _request("PUT", f"/zones/{apex}/rectify")
    except DnsError:
        pass
    keys = _request("GET", f"/zones/{apex}/cryptokeys")
    status = _format_cryptokeys_status(zone, keys if isinstance(keys, list) else [])
    if not status["enabled"] or not status["ds_records"]:
        raise DnsError("PowerDNS did not confirm an active DNSSEC signing key or DS record for this zone.", status=502)
    from app.services import multi_server
    multi_server.trigger_background_zone_sync(zone, action="upsert")
    return status


def disable_dnssec(zone: str) -> dict:
    """Disable DNSSEC on a zone and remove its signing keys from PowerDNS."""
    zone = normalize_zone(zone)
    apex = _absolute(zone)
    keys = _request("GET", f"/zones/{apex}/cryptokeys")
    if isinstance(keys, list):
        for key in keys:
            key_id = key.get("id")
            if key_id is not None:
                _request("DELETE", f"/zones/{apex}/cryptokeys/{key_id}")
    remaining = _request("GET", f"/zones/{apex}/cryptokeys")
    from app.services import multi_server
    multi_server.trigger_background_zone_sync(zone, action="upsert")
    return _format_cryptokeys_status(zone, remaining if isinstance(remaining, list) else [])


def restore_defaults(db: Session, zone: str) -> None:
    """Put back what the panel writes into a zone: its nameservers, the records
    for new zones where the name has none of that type, an address for each
    website inside it, and the Email addon's records. Records the owner added
    stay."""
    config = settings()
    data = _zone_data(zone)
    apex = _absolute(zone)
    ttl = config["ttl"]
    changes: dict[tuple[str, str], tuple[int, list[str]]] = {}
    if len(config["nameservers"]) >= 2:
        wanted = [_absolute(ns) for ns in config["nameservers"]]
        if sorted(_contents(_rrset(data, apex, "NS"))) != sorted(wanted):
            changes[(apex, "NS")] = (ttl, wanted)

    def taken(name: str, rtype: str) -> bool:
        if (name, rtype) in changes:
            return True
        if _rrset(data, name, "CNAME") and rtype != "CNAME":
            return True
        if rtype == "TXT":
            # The template's {spf}: only an apex without an SPF record gets it.
            return any(_untxt(item).lower().startswith("v=spf1") for item in _contents(_rrset(data, name, "TXT")))
        return bool(_rrset(data, name, rtype))

    zone_row = _row(db, zone)
    template = template_for_owner(db, zone_row.owner_id if zone_row is not None else None, config)
    for record in template_records(template, zone, config["zone_ip"], ttl):
        name, rtype, _, content = to_pdns(zone, record)
        if rtype == "TXT" and not _untxt(content).lower().startswith("v=spf1"):
            if content in _contents(_rrset(data, name, "TXT")):
                continue
            changes[(name, rtype)] = (ttl, _contents(_rrset(data, name, "TXT")) + [content])
            continue
        if not taken(name, rtype):
            changes[(name, rtype)] = (ttl, [content])
    if config["zone_ip"]:
        for host in sorted(_panel_names(db)):
            if not host.endswith("." + zone):
                continue
            name = _absolute(host)
            if not any(_rrset(data, name, rtype) or (name, rtype) in changes for rtype in ("A", "AAAA", "CNAME")):
                changes[(name, "A")] = (ttl, [config["zone_ip"]])
    if changes:
        _serial_follows_edits(zone, data)
        _patch(zone, changes)
    # The Email addon's records, as its DNS page shows them.
    from app.models.entities import MailDomain
    from app.services import mail

    if mail.active():
        for row in db.query(MailDomain).all():
            if row.domain == zone or row.domain.endswith("." + zone):
                try:
                    mail.publish_dns(db, row, replace=True)
                except mail.MailError as exc:
                    logger.warning("Mail records not restored for %s: %s", row.domain, exc)


def zone_for_new_website(db: Session, website) -> str | None:
    done = zone_for_new_domain(db, website.domain, website.owner_id)
    return done[1] if done else None


def sync(db: Session) -> dict:
    """Every website domain and alias on the server has its DNS here.

    Makes the zones that are missing (existing websites when the addon is
    installed; websites that came by restore, DirectAdmin import or WHMCS),
    and hands a zone to whoever owns the website of the same name, so a
    customer can edit the DNS of every domain in their account.
    """
    config = settings()
    summary = {"zones": [], "records": [], "owners": [], "failed": [], "ready": _ready(config)}
    if not summary["ready"]:
        return summary
    domains: dict[str, int | None] = {}
    for website in db.query(Website).all():
        domains[website.domain.lower()] = website.owner_id
    for alias in db.query(WebsiteAlias).join(Website).all():
        domains.setdefault(alias.domain.lower(), alias.website.owner_id)

    rows = {row.name: row for row in db.query(DnsZone).all()}
    for domain, owner_id in domains.items():
        row = rows.get(domain)
        if row is not None and row.owner_id != owner_id:
            row.owner_id = owner_id
            summary["owners"].append(domain)
    db.commit()

    # Parents before their subdomains, so blog.example.com lands in
    # example.com's zone rather than one of its own.
    for domain in sorted(domains, key=lambda name: (name.count("."), name)):
        try:
            done = provision_domain(db, domain, domains[domain], config)
        except (DnsError, DnsInputError) as exc:
            db.rollback()
            summary["failed"].append(domain)
            logger.warning("No DNS for %s: %s", domain, exc)
            continue
        if done and done[0] == "zone":
            summary["zones"].append(done[1])
        elif done:
            summary["records"].append(domain)
    return summary


def sync_quietly(db: Session) -> dict | None:
    """sync() for the places that must not fail because of DNS: startup, a
    restore, an import, WHMCS provisioning."""
    try:
        if not active():
            return None
        return sync(db)
    except Exception:  # noqa: BLE001 - DNS is never the reason something else fails
        logger.warning("DNS sync failed", exc_info=True)
        db.rollback()
        return None


# --- mail (Email addon) -------------------------------------------------------------

def _spf_terms(text: str) -> set[str]:
    return {term.lower() for term in (text or "").split()[1:] if term.lower() not in ("~all", "-all", "?all", "+all", "all")}


# What a zone got before the SPF record named the server's addresses.
LEGACY_SPF_TERMS = {"a", "mx"}


def replace_spf(db: Session, old: str, new: str) -> list[str]:
    """Move every zone still publishing the panel's previous SPF record to the
    new one.

    A zone's SPF counts as the panel's when it has the same mechanisms as the
    old record (in any order), or the plain "a mx" zones got before. A
    customer who wrote their own keeps it. Returns the zones changed.
    """
    if not active() or old == new:
        return []
    old_terms = _spf_terms(old)
    changed = []
    for row in db.query(DnsZone).order_by(DnsZone.name).all():
        try:
            data = _zone_data(row.name)
        except DnsError:
            continue
        apex = _absolute(row.name)
        rrset = _rrset(data, apex, "TXT")
        contents = _contents(rrset)

        def panels(content: str) -> bool:
            text = _untxt(content)
            return text.lower().startswith("v=spf1") and _spf_terms(text) in (old_terms, LEGACY_SPF_TERMS)

        if not any(panels(content) for content in contents):
            continue
        updated = [_txt(new) if panels(content) else content for content in contents]
        _serial_follows_edits(row.name, data)
        _patch(row.name, {(apex, "TXT"): ((rrset or {}).get("ttl", DEFAULT_TTL), updated)})
        changed.append(row.name)
    return changed
