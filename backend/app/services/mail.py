"""Email addon: mail domains, mailboxes, forwarders, relays and the spam filter.

Operator, 2026-09-29: "phát triển addon email server exim dovecot kết hợp
webmail (https://github.com/shostwp-byte/spanel) có thể làm SSO login", then
"Addon cần code thêm rspamd để có thể xem log chặn mail xem có nhầm không.
Thêm config relay smarthost cho exim -> Custom spf/dns mẫu", then "Chưa ổn.
Mình thấy bạn nên login vào opanel.media.io.vn để xem mail bên đó cấu trúc
sao. Bạn còn thiếu mẫu DNS cho người ta cấu hình nữa."

So this follows OPanel's Email addon (SPanel Email stack):
- Email is turned on per domain. A mail domain has its catch-all, its DKIM
  key, its webmail.<domain> host, the relay its mail leaves through, and its
  mailboxes and forwarders. Its DNS page lists every record it needs - MX,
  SPF, DKIM, DMARC, what its relay asks for, an administrator's extras - each
  checked against live DNS.
- Relays (smarthosts) are a list: each with its login, the SPF part it needs
  and a DNS template of the records every domain sending through it must
  publish ({domain} stands for the domain). One is the server's default; a
  domain may pick another, or direct delivery.
- Administrators also get Rspamd's statistics, history and log, the Exim log,
  and the server's limits.

And keeps what spanel chose with the operator: mail lives in the customer's
home, /home/<user>/mail/<domain>/<name>, so it counts toward their disk
space and goes into their backups; a package limits the number of
mailboxes; one click opens a mailbox in the webmail (single sign-on). Where
this server also runs a domain's DNS (DNS Manager), the records go into its
zone.

The panel's database is the source of truth. After every change the whole
state goes to the helper (mail-sync); server settings and relays go to
mail-configure. A sync that fails leaves the previous maps in force, and the
next one repairs everything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from passlib.hash import sha512_crypt
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.permissions import is_admin_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import MailAccount, MailDomain, MailForwarder, MailAutoresponder, User, Website, WebsiteAlias
from app.services import addons, panel_settings, server_network, site_users
from app.services.shell import shell
from app.services.storage_quota import path_usage_bytes

logger = logging.getLogger("spanel.mail")

SSO_KEY_FILE = Path("/etc/spanel/webmail-sso.key")
RSPAMD_KEY_FILE = Path("/etc/spanel/rspamd-controller.key")
RSPAMD_CONTROLLER = "http://127.0.0.1:11334"
SETTINGS_KEY = "mail"
# The selector of every domain's first key; a rotation moves to s<date>.
DKIM_SELECTOR = "spanel"
DKIM_SELECTOR_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
WEBMAIL_PORT = 2096
SSO_TOKEN_SECONDS = 60
DEFAULT_QUOTA_MB = 1024
# An end user may not make an unlimited mailbox.
MAX_USER_QUOTA_MB = 51200
MAX_QUOTA_MB = 1048576
MAX_DESTINATIONS = 20
USAGE_CACHE_SECONDS = 120
MAX_ALLOW = 5000

# Kept in step with the helper's own checks (mail-sync).
LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SHA512_RE = re.compile(r"^\{SHA512-CRYPT\}\$6\$(rounds=[0-9]{4,9}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}$")

SETTINGS_DEFAULTS = {
    "auth_rate_per_hour": 300,
    "local_rate_per_hour": 300,
    "domain_rate_per_hour": 500,
    "auto_hold_on_rate_exceed": True,
    "max_message_mb": 50,
    "spam_enabled": True,
    "spam_header_score": 6.0,
    "spam_rewrite_score": 8.0,
    "spam_reject_score": 15.0,
    "spam_subject_tag": "[SPAM]",
    "greylisting": True,
    "default_quota_mb": DEFAULT_QUOTA_MB,
    # DKIM keys rotate on their own every so many days (0: only by hand),
    # and a replaced key stays published this long.
    "dkim_rotate_days": 0,
    "dkim_retire_days": 7,
}
PORTS = {"imap": 993, "pop3": 995, "smtps": 465, "submission": 587}

_sync_lock = threading.Lock()
_usage_cache: dict = {"at": 0.0, "data": {}}


class MailError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def active() -> bool:
    return addons.is_installed(addons.MAIL)


def _require_active() -> None:
    if not active():
        raise MailError("The Email addon is not installed.", status=409)


def _helper_message(exc_or_text) -> str:
    """The helper's own sentence, out of shell.privileged's error or stderr."""
    text = str(exc_or_text or "")
    lines = [line for line in text.splitlines() if line.startswith("spanel-helper: ")]
    return lines[-1][len("spanel-helper: "):] if lines else "The mail server did not accept the change."


def _helper(verb: str, *args: str, input: str | None = None, sensitive: bool = False, timeout: float = 300):
    """A helper call whose failure is the helper's own sentence."""
    result = shell.privileged(verb, helper_args=list(args), input=input, check=False, sensitive=sensitive,
                              fallback=["true"], timeout=timeout)
    if result.returncode != 0:
        raise MailError(_helper_message(result.stderr or result.stdout), status=502)
    return result


# --- the machine ------------------------------------------------------------------

def hostname() -> str:
    """The mail server's name: MX target, IMAP/SMTP host and webmail host."""
    candidates = (
        app_settings.panel_domain,
        urlparse(panel_settings.configured_panel_url() or "").hostname or "",
        socket.getfqdn(),
    )
    for candidate in candidates:
        name = (candidate or "").strip().lower().rstrip(".")
        if DOMAIN_RE.fullmatch(name):
            return name
    raise MailError("The mail server needs a name. Give the panel a domain first, in Settings.")


def _hostname_or_blank() -> str:
    try:
        return hostname()
    except MailError:
        return ""


def install() -> dict:
    """Install Exim, Dovecot, Rspamd and the webmail, prove they answer, then
    put back the settings, relays and mail domains saved in the panel."""
    shell.privileged("mail-install", helper_args=[hostname()], fallback=["true"], timeout=1500)
    apply_settings()
    return server_status()


def after_install(db: Session) -> None:
    """Keys kept from an earlier install are reused and any gone missing are
    made again; the records shown follow. Then every domain is served again."""
    for row in db.query(MailDomain).all():
        try:
            row.dkim_public = _dkim_public(row.domain, row.dkim_selector or DKIM_SELECTOR)
            if row.dkim_next_selector:
                row.dkim_next_public = _dkim_public(row.domain, row.dkim_next_selector)
        except MailError:
            continue
    db.commit()
    sync(db)


def stop() -> dict:
    """Stop the services and close the ports. Mail, mailboxes and settings stay."""
    shell.privileged("mail-remove", fallback=["true"], timeout=300)
    return server_status()


def server_status() -> dict:
    result = shell.privileged("mail-status", check=False, fallback=["bash", "-lc", "echo installed=no"])
    info = {"installed": False, "exim": False, "dovecot": False, "webmail": False, "port_open": False,
            "rspamd": False, "unbound": False, "spam_filter": False, "hostname": "", "resolver": "local"}
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in {"installed", "exim", "dovecot", "webmail", "port_open", "rspamd", "unbound", "spam_filter"}:
            info[key] = value == "yes"
        elif key in {"hostname", "resolver"}:
            info[key] = value
    return info


def outbound_smtp_open(timeout: float = 5.0) -> bool:
    """Whether this server can reach another mail server on port 25. Many VPS
    providers block it until asked; mail then waits for a relay."""
    try:
        with socket.create_connection(("gmail-smtp-in.l.google.com", 25), timeout=timeout):
            return True
    except OSError:
        return False


def panel_host() -> str:
    host = (urlparse(panel_settings.configured_panel_url() or "").hostname or "").lower()
    return host or _hostname_or_blank()


def webmail_url(row: MailDomain | None = None) -> str:
    """The webmail: on webmail.<domain> when that is served, else on the
    panel's own name and certificate."""
    if row is not None and row.webmail_host:
        return f"https://webmail.{row.domain}"
    host = panel_host()
    try:
        if ipaddress.ip_address(host).version == 6:
            host = f"[{host}]"
    except ValueError:
        pass
    return f"https://{host}:{WEBMAIL_PORT}"


# Docker's bridge (172.17.0.1) and a provider's private LAN are "scope global"
# too, but no receiving mail server ever sees them: they have no place in SPF.
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))


def server_addresses() -> tuple[list[str], list[str]]:
    """The addresses other mail servers see this one send from."""
    try:
        ipv4, ipv6 = server_network.ipv4_addresses(), server_network.ipv6_addresses()
    except Exception:  # noqa: BLE001 - informational
        return [], []

    def public(values: list[str]) -> list[str]:
        return [value for value in values
                if not any(ipaddress.ip_address(value) in network for network in _PRIVATE_NETWORKS)]

    return public(ipv4), public(ipv6)


# --- validation ----------------------------------------------------------------------

def normalize_domain(value: str) -> str:
    name = (value or "").strip().lower().rstrip(".")
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise MailError("Enter a domain name such as example.com.") from exc
    if not DOMAIN_RE.fullmatch(name) or len(name) > 253:
        raise MailError("Enter a domain name such as example.com.")
    return name


def normalize_local(value: str) -> str:
    local = (value or "").strip().lower()
    if not LOCAL_RE.fullmatch(local) or ".." in local:
        raise MailError("The mailbox name may use letters, digits, dots, hyphens and underscores, such as info or sales.")
    return local


def normalize_destination(value: str) -> str:
    address = (value or "").strip()
    if not ADDRESS_RE.fullmatch(address) or ".." in address or len(address) > 254:
        raise MailError("That is not an email address.")
    return address.lower()


def normalize_destinations(values, own_address: str = "") -> list[str]:
    clean: list[str] = []
    for raw in values or []:
        for part in re.split(r"[\s,;]+", str(raw or "")):
            if not part:
                continue
            address = normalize_destination(part)
            if address == own_address:
                raise MailError("A forwarder cannot forward to itself; add a mailbox of the same name instead.")
            if address not in clean:
                clean.append(address)
    if not clean:
        raise MailError("Enter at least one address to forward to.")
    if len(clean) > MAX_DESTINATIONS:
        raise MailError("A forwarder can have at most 20 addresses.")
    return clean


def check_password(password: str, address: str) -> str:
    value = password or ""
    if len(value) < 8 or len(value) > 128:
        raise MailError("The password needs 8 to 128 characters.")
    if any(ord(char) < 32 for char in value):
        raise MailError("The password cannot contain control characters.")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise MailError("The password must contain both letters and digits.")
    local = address.split("@", 1)[0]
    if len(local) >= 3 and local in value.lower():
        raise MailError("The password must not contain the mailbox name.")
    return value


def hash_password(password: str) -> str:
    # SHA512-CRYPT at glibc's default 5000 rounds: what Dovecot checks on every
    # IMAP login, so passlib's default of 656000 would cost each one a second.
    return "{SHA512-CRYPT}" + sha512_crypt.using(rounds=5000).hash(password)


def _quota(actor: User, value) -> int:
    if value is None or value == "":
        value = int(current_settings().get("default_quota_mb") or DEFAULT_QUOTA_MB)
    try:
        quota = int(value)
    except (TypeError, ValueError) as exc:
        raise MailError("The mailbox size is a whole number of MB.") from exc
    if is_admin_role(actor.role):
        if not 0 <= quota <= MAX_QUOTA_MB:
            raise MailError("The mailbox size is 0 (no limit) to 1048576 MB.")
        return quota
    if not 1 <= quota <= MAX_USER_QUOTA_MB:
        raise MailError("The mailbox size is 1 to 51200 MB.")
    return quota


# --- access -------------------------------------------------------------------------

def _is_admin(user: User) -> bool:
    return is_admin_role(user.role)


def get_domain(db: Session, actor: User, domain_id: int) -> MailDomain:
    row = db.query(MailDomain).filter(MailDomain.id == domain_id).first()
    # Someone else's domain does not exist, as far as a customer can tell.
    if row is None or (not _is_admin(actor) and row.owner_id != actor.id):
        raise MailError("There is no such mail domain.", status=404)
    return row


def _domain_row(db: Session, name: str) -> MailDomain | None:
    return db.query(MailDomain).filter(MailDomain.domain == name).first()


def get_account(db: Session, actor: User, account_id: int) -> MailAccount:
    account = db.query(MailAccount).filter(MailAccount.id == account_id).first()
    if account is None or (not _is_admin(actor) and account.owner_id != actor.id):
        raise MailError("There is no such mailbox.", status=404)
    return account


def get_forwarder(db: Session, actor: User, forwarder_id: int) -> MailForwarder:
    row = db.query(MailForwarder).filter(MailForwarder.id == forwarder_id).first()
    domain = _domain_row(db, row.domain) if row else None
    if row is None or domain is None or (not _is_admin(actor) and domain.owner_id != actor.id):
        raise MailError("There is no such forwarder.", status=404)
    return row


def _website_owner(db: Session, name: str) -> User | None:
    site = db.query(Website).filter(func.lower(Website.domain) == name).first()
    if site is not None:
        return site.owner
    alias = db.query(WebsiteAlias).filter(func.lower(WebsiteAlias.domain) == name).first()
    if alias is not None and alias.website is not None:
        return alias.website.owner
    return None


def candidate_domains(db: Session, actor: User) -> list[str]:
    """Website and alias names email could be turned on for."""
    sites = db.query(Website)
    if not _is_admin(actor):
        sites = sites.filter(Website.owner_id == actor.id)
    names: set[str] = set()
    for site in sites.all():
        names.add((site.domain or "").lower())
        for alias in site.aliases or []:
            names.add((alias.domain or "").lower())
    taken = {row[0] for row in db.query(MailDomain.domain).all()}
    return sorted(n for n in names if DOMAIN_RE.fullmatch(n) and not n.startswith("www.") and n not in taken)


def linux_user_of(owner: User) -> str:
    return site_users.linux_user_for_panel_username(owner.username)


def box_path(owner: User, account: MailAccount) -> str:
    return f"/home/{linux_user_of(owner)}/mail/{account.domain}/{account.local_part}"


# --- mail domains -------------------------------------------------------------------------

def _dkim_public(domain: str, selector: str = DKIM_SELECTOR) -> str:
    """The public key of a domain's selector, the helper making the key pair
    first if there is none."""
    if not DKIM_SELECTOR_RE.fullmatch(selector or ""):
        raise MailError("That is not a DKIM selector.")
    result = _helper("mail-dkim", domain, selector)
    key = (result.stdout or "").strip().splitlines()[-1:] if (result.stdout or "").strip() else []
    if not key or not re.fullmatch(r"[A-Za-z0-9+/=]{200,1000}", key[0]):
        if app_settings.command_dry_run or not shell_uses_helper():
            return ""
        raise MailError("The mail server did not make a DKIM key.", status=502)
    return key[0]


def shell_uses_helper() -> bool:
    from app.services import shell as shell_module

    return shell_module._use_helper()


def add_domain(db: Session, actor: User, domain: str, owner_id: int | None = None) -> MailDomain:
    _require_active()
    name = normalize_domain(domain)
    if _domain_row(db, name) is not None:
        raise MailError("Email is already on for that domain.", status=409)
    owner = _website_owner(db, name)
    if _is_admin(actor):
        if owner_id:
            owner = db.query(User).filter(User.id == owner_id).first()
            if owner is None:
                raise MailError("That account does not exist.", status=404)
        elif owner is None:
            owner = actor
    elif owner is None or owner.id != actor.id:
        raise MailError("You can only turn on email for the domains of your own websites.", status=403)
    row = MailDomain(domain=name, owner_id=owner.id, catch_all="", webmail_host=False, relay="",
                     dkim_public=_dkim_public(name))
    db.add(row)
    db.commit()
    db.refresh(row)
    sync(db)
    publish_dns_quietly(db, row)
    return row


def delete_domain(db: Session, actor: User, domain_id: int) -> str:
    """Email off for a domain: its mailboxes with their mail, its forwarders."""
    row = get_domain(db, actor, domain_id)
    name, webmail, hosts = row.domain, bool(row.webmail_host), bool(row.mail_hosts)
    owner = db.query(User).filter(User.id == row.owner_id).first()
    db.query(MailAccount).filter(MailAccount.domain == name).delete(synchronize_session=False)
    db.query(MailForwarder).filter(MailForwarder.domain == name).delete(synchronize_session=False)
    db.query(MailAutoresponder).filter(MailAutoresponder.domain == name).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    sync(db)
    if webmail:
        shell.privileged("mail-webmail-host-remove", helper_args=[name], check=False, fallback=["true"])
    if hosts:
        shell.privileged("mail-hosts-remove", helper_args=[name], check=False, fallback=["true"])
    # Last, once nothing routes there any more.
    if owner is not None:
        shell.privileged("mail-purge-domain", helper_args=[linux_user_of(owner), name], check=False,
                         fallback=["true"], timeout=600)
    return name


def move_domain_maildirs(old_linux_user: str, new_linux_user: str, domain: str) -> None:
    """Move /home/<old_user>/mail/<domain> to /home/<new_user>/mail/<domain> on website owner transfer."""
    if not old_linux_user or not new_linux_user or old_linux_user == new_linux_user:
        return
    res = shell.privileged(
        "mail-move-domain",
        helper_args=[old_linux_user, new_linux_user, domain],
        fallback=["true"],
        check=False,
        timeout=600,
    )
    if getattr(res, "returncode", 0) != 0:
        err = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "mail-move-domain failed").strip()
        raise MailError(f"Could not move mail directory for {domain}: {err}", status=500)


def delete_for_owner(db: Session, owner: User) -> list[str]:
    """An account is being deleted: its mail goes with its home. The caller commits."""
    rows = db.query(MailDomain).filter(MailDomain.owner_id == owner.id).all()
    names = [row.domain for row in rows]
    for row in rows:
        if row.webmail_host:
            shell.privileged("mail-webmail-host-remove", helper_args=[row.domain], check=False, fallback=["true"])
        if row.mail_hosts:
            shell.privileged("mail-hosts-remove", helper_args=[row.domain], check=False, fallback=["true"])
        db.query(MailForwarder).filter(MailForwarder.domain == row.domain).delete(synchronize_session=False)
        db.query(MailAutoresponder).filter(MailAutoresponder.domain == row.domain).delete(synchronize_session=False)
        db.delete(row)
    db.query(MailAccount).filter(MailAccount.owner_id == owner.id).delete(synchronize_session=False)
    db.flush()
    return names


def set_catch_all(db: Session, actor: User, domain_id: int, target: str) -> MailDomain:
    row = get_domain(db, actor, domain_id)
    value = (target or "").strip()
    if value and not _is_admin(actor) and getattr(actor, "package", None) is not None:
        if not bool(getattr(actor.package, "catch_all_email", True)):
            raise MailError("Catch-all email is not enabled on your hosting package.", status=403)
    if value:
        value = normalize_destination(value)
        local, _, domain = value.partition("@")
        if domain == row.domain:
            # An address of this domain that does not exist would hand the
            # message straight back to the catch-all.
            exists = db.query(MailAccount).filter(MailAccount.domain == domain, MailAccount.local_part == local).first() \
                or db.query(MailForwarder).filter(MailForwarder.domain == domain, MailForwarder.local_part == local).first()
            if not exists:
                raise MailError("The catch-all must be a mailbox or forwarder of this domain, or an outside address.")
    row.catch_all = value
    db.commit()
    sync(db)
    return row


def rotate_dkim(db: Session, actor: User, domain_id: int) -> MailDomain:
    """The "New DKIM key" button: a two-selector rotation, never a key
    replaced in place (see DKIM rotation below)."""
    _require_active()
    return start_dkim_rotation(db, get_domain(db, actor, domain_id))


def set_webmail_host(db: Session, actor: User, domain_id: int, enabled: bool) -> MailDomain:
    _require_active()
    row = get_domain(db, actor, domain_id)
    host = f"webmail.{row.domain}"
    if enabled:
        taken = db.query(Website).filter(func.lower(Website.domain) == host).first() is not None or \
            db.query(WebsiteAlias).filter(func.lower(WebsiteAlias.domain) == host).first() is not None
        if taken:
            raise MailError("That webmail address is already a website on this server.", status=409)
        args = [row.domain]
        if app_settings.ssl_email:
            args.append(app_settings.ssl_email)
        _helper("mail-webmail-host", *args, timeout=300)
    else:
        shell.privileged("mail-webmail-host-remove", helper_args=[row.domain], check=False, fallback=["true"])
    row.webmail_host = bool(enabled)
    db.commit()
    return row


# --- mail hosts: mail.<domain> and the others, with a certificate of their own ------------------
# mail.<domain> is the domain's MX; with its own certificate Exim and Dovecot
# answer it by SNI, so mail apps and MTA-STS senders see a name that matches.
# mta-sts., autoconfig. and autodiscover. serve what their names say. One
# Let's Encrypt certificate (the webroot flow webmail.<domain> uses) covers
# the hosts turned on; the helper's mail-hosts-apply does the rest.

MAIL_HOST_LABELS = ("mail", "mta-sts", "autoconfig", "autodiscover")


def served_hosts(row: MailDomain) -> list[str]:
    labels = [label.strip() for label in (row.mail_hosts or "").split(",")]
    return [label for label in MAIL_HOST_LABELS if label in labels]


def client_host(row: MailDomain | None) -> str:
    """The name mail apps use for a domain: mail.<domain> once it has its own
    certificate, else the server's own name (the panel's certificate)."""
    if row is not None and "mail" in served_hosts(row):
        return f"mail.{row.domain}"
    return _hostname_or_blank()


def normalize_hosts(labels) -> list[str]:
    chosen = {str(label or "").strip().lower() for label in labels or []}
    unknown = chosen - set(MAIL_HOST_LABELS)
    if unknown:
        raise MailError("The mail hosts are mail, mta-sts, autoconfig and autodiscover.")
    chosen.add("mail")
    return [label for label in MAIL_HOST_LABELS if label in chosen]


def _points_here(name: str) -> bool | None:
    """Whether public DNS sends name to this server; None if DNS could not be asked."""
    answer = _resolve(name, "A")
    if answer is None:
        return None
    own = set(server_addresses()[0]) | ({server_ip()} if server_ip() else set())
    return bool(set(answer) & own)


def set_mail_hosts(db: Session, actor: User, domain_id: int, labels) -> MailDomain:
    """Serve the domain's mail hosts with a certificate of their own."""
    _require_active()
    row = get_domain(db, actor, domain_id)
    chosen = normalize_hosts(labels)
    names = [f"{label}.{row.domain}" for label in chosen]
    taken = [name for name in names
             if db.query(Website).filter(func.lower(Website.domain) == name).first() is not None
             or db.query(WebsiteAlias).filter(func.lower(WebsiteAlias.domain) == name).first() is not None
             or name == panel_host()]
    if taken:
        raise MailError(f"{', '.join(taken)} is already a website or the panel's own address on this server.", status=409)
    # Where this server runs the domain's DNS, the names go in first.
    publish_dns_quietly(db, row, hosts=set(chosen))
    elsewhere = [name for name in names if _points_here(name) is False]
    if elsewhere:
        raise MailError(f"Point {', '.join(elsewhere)} at this server first (an A record to {server_ip() or 'its address'}); "
                        "a certificate is issued only for names that reach it.")
    _apply_mail_hosts(row, chosen)
    row.mail_hosts = ",".join(chosen)
    db.commit()
    publish_dns_quietly(db, row)
    return row


def _apply_mail_hosts(row: MailDomain, chosen: list[str]) -> None:
    mode = row.mta_sts_mode or ""
    payload = {"domain": row.domain, "hosts": chosen, "email": app_settings.ssl_email or "",
               "mta_sts": {"mode": mode, "max_age": MTA_STS_MAX_AGE} if mode in MTA_STS_MODES else None}
    _helper("mail-hosts-apply", input=json.dumps(payload), timeout=600)


def remove_mail_hosts(db: Session, actor: User, domain_id: int) -> MailDomain:
    _require_active()
    row = get_domain(db, actor, domain_id)
    if row.mta_sts_mode in ("testing", "enforce"):
        # Senders keep the policy for max_age and would refuse mail.<domain>
        # once its certificate is gone.
        raise MailError("Set MTA-STS to none and wait a week before the mail hosts stop being served.", status=409)
    _helper("mail-hosts-remove", row.domain, timeout=300)
    row.mail_hosts = ""
    db.commit()
    publish_dns_quietly(db, row)
    return row


# --- MTA-STS and TLS-RPT ----------------------------------------------------------------------------
# MTA-STS (RFC 8461) tells other mail servers to deliver to mail.<domain>
# only over TLS with a certificate that matches: the policy file on
# https://mta-sts.<domain>/ (written by the helper from the mode alone) and
# _mta-sts.<domain> TXT naming its id. TLS-RPT (RFC 8460) asks them to report
# TLS failures to the addresses in _smtp._tls.<domain>.

MTA_STS_MODES = ("testing", "enforce", "none")
MTA_STS_MAX_AGE = 604800


def tls_rpt_addresses(row: MailDomain) -> list[str]:
    return [line.strip() for line in (row.tls_rpt or "").splitlines() if line.strip()]


def mta_sts_view(row: MailDomain) -> dict:
    served = served_hosts(row)
    return {"mode": row.mta_sts_mode or "", "id": row.mta_sts_id or "", "tls_rpt": tls_rpt_addresses(row),
            "ready": "mail" in served and "mta-sts" in served, "max_age": MTA_STS_MAX_AGE,
            "policy_url": f"https://mta-sts.{row.domain}/.well-known/mta-sts.txt"}


def set_mta_sts(db: Session, actor: User, domain_id: int, mode: str, tls_rpt) -> MailDomain:
    """The domain's MTA-STS mode ("" off) and TLS-RPT addresses."""
    _require_active()
    row = get_domain(db, actor, domain_id)
    mode = (mode or "").strip().lower()
    if mode and mode not in MTA_STS_MODES:
        raise MailError("The MTA-STS mode is testing, enforce or none, or empty for off.")
    if mode and not {"mail", "mta-sts"} <= set(served_hosts(row)):
        raise MailError(f"Serve mail.{row.domain} and mta-sts.{row.domain} first (Mail hosts): the policy is "
                        f"published there, and enforce needs mail.{row.domain}'s own certificate.")
    addresses = _report_addresses(tls_rpt, "TLS report")
    old_mode, old_id, old_rpt = row.mta_sts_mode or "", row.mta_sts_id or "", tls_rpt_addresses(row)
    if mode != old_mode:
        row.mta_sts_mode = mode
        row.mta_sts_id = datetime.utcnow().strftime("%Y%m%d%H%M%S") if mode else ""
    row.tls_rpt = "\n".join(addresses)
    db.flush()
    if mode != old_mode and served_hosts(row):
        try:
            _apply_mail_hosts(row, served_hosts(row))
        except MailError:
            db.rollback()
            raise
    db.commit()
    # The TXT records follow; one turned off leaves the zone.
    if old_mode and not mode and old_id:
        withdraw_dns_quietly(db, row, f"_mta-sts.{row.domain}", "TXT", f"v=STSv1; id={old_id}")
    if old_rpt and not addresses:
        withdraw_dns_quietly(db, row, f"_smtp._tls.{row.domain}", "TXT", render_tls_rpt(old_rpt))
    publish_dns_quietly(db, row)
    return row


def render_tls_rpt(addresses: list[str]) -> str:
    return "v=TLSRPTv1; rua=" + ",".join(f"mailto:{address}" for address in addresses)


def webmail_host_taken(db: Session, domain: str) -> bool:
    """Whether a website name is the webmail.<domain> of a mail domain here."""
    name = (domain or "").strip().lower()
    if not name.startswith("webmail."):
        return False
    return db.query(MailDomain).filter(MailDomain.domain == name[len("webmail."):],
                                       MailDomain.webmail_host.is_(True)).first() is not None


def domain_out(db: Session, row: MailDomain, owners: dict[int, User] | None = None) -> dict:
    owner = (owners or {}).get(row.owner_id) or db.query(User).filter(User.id == row.owner_id).first()
    return {
        "id": row.id,
        "domain": row.domain,
        "owner_id": row.owner_id,
        "owner": owner.username if owner else "",
        "catch_all": row.catch_all or "",
        "webmail_host": bool(row.webmail_host),
        "webmail_url": webmail_url(row) + "/",
        "relay": row.relay or "",
        "dkim_selector": row.dkim_selector or DKIM_SELECTOR,
        "dkim_state": row.dkim_state or "",
        "mail_hosts": served_hosts(row),
        "client_host": client_host(row),
        "mailboxes": db.query(MailAccount).filter(MailAccount.domain == row.domain).count(),
        "forwarders": db.query(MailForwarder).filter(MailForwarder.domain == row.domain).count(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# --- mailboxes ------------------------------------------------------------------------------

def owner_mailbox_count(db: Session, owner_id: int) -> int:
    return db.query(MailAccount).filter(MailAccount.owner_id == owner_id).count()


def effective_mail_limit(user: User | None) -> int | None:
    """A customer's mailbox limit; None for an administrator or Unlimited (-1)."""
    if user is None or _is_admin(user):
        return None
    pkg = getattr(user, "package", None)
    val = getattr(pkg, "mail_accounts_limit", None) if pkg is not None else None
    if val is None:
        val = getattr(user, "mail_accounts_limit", None)
    if val is None:
        return None
    val_int = int(val)
    return None if val_int < 0 else val_int


_limit = effective_mail_limit


def create_account(db: Session, actor: User, domain_id: int, local_part: str, password: str, quota_mb=None) -> MailAccount:
    _require_active()
    domain = get_domain(db, actor, domain_id)
    local = normalize_local(local_part)
    address = f"{local}@{domain.domain}"
    if db.query(MailAccount).filter(MailAccount.domain == domain.domain, MailAccount.local_part == local).first():
        raise MailError("That address already exists.", status=409)
    owner = db.query(User).filter(User.id == domain.owner_id).first()
    if owner is None:
        raise MailError("That domain's account no longer exists.", status=404)
    limit = _limit(owner)
    if not _is_admin(actor) and limit is not None and owner_mailbox_count(db, owner.id) >= limit:
        raise MailError("All the mailboxes in your hosting package are in use.", status=403)
    check_password(password, address)
    account = MailAccount(owner_id=owner.id, domain=domain.domain, local_part=local,
                          password_hash=hash_password(password), quota_mb=_quota(actor, quota_mb), enabled=True)
    db.add(account)
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    db.refresh(account)
    _usage_cache["at"] = 0.0
    return account


def update_account(db: Session, actor: User, account_id: int, *, password: str | None = None, quota_mb=None,
                   enabled: bool | None = None) -> MailAccount:
    account = get_account(db, actor, account_id)
    if password is not None:
        account.password_hash = hash_password(check_password(password, account.address))
    if quota_mb is not None:
        account.quota_mb = _quota(actor, quota_mb)
    if enabled is not None:
        account.enabled = bool(enabled)
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    db.refresh(account)
    return account


def delete_account(db: Session, actor: User, account_id: int) -> str:
    """Delete a mailbox and the mail in it."""
    account = get_account(db, actor, account_id)
    owner = db.query(User).filter(User.id == account.owner_id).first()
    address, domain, local = account.address, account.domain, account.local_part
    row = _domain_row(db, domain)
    if row is not None and row.catch_all == address and not db.query(MailForwarder).filter(
            MailForwarder.domain == domain, MailForwarder.local_part == local).first():
        row.catch_all = ""
    db.delete(account)
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    # Only once Exim no longer delivers there.
    if owner is not None:
        result = shell.privileged("mail-delete-box", helper_args=[linux_user_of(owner), domain, local],
                                  check=False, fallback=["true"], timeout=300)
        if result.returncode != 0:
            logger.warning("Mailbox %s is deleted but its mail is still on disk: %s", address, result.stderr)
    _usage_cache["at"] = 0.0
    return address


def usage_bytes(db: Session) -> dict[str, int]:
    """Each mailbox's size on disk, walked at most every two minutes."""
    now = time.monotonic()
    if now - _usage_cache["at"] < USAGE_CACHE_SECONDS:
        return _usage_cache["data"]
    owners = {row.id: row for row in db.query(User).all()}
    data = {}
    for account in db.query(MailAccount).all():
        owner = owners.get(account.owner_id)
        if owner is not None:
            data[account.address] = path_usage_bytes(box_path(owner, account))
    _usage_cache.update(at=now, data=data)
    return data


def account_out(account: MailAccount, domains: dict[str, MailDomain], owners: dict[int, User], usage: dict) -> dict:
    owner = owners.get(account.owner_id)
    domain = domains.get(account.domain)
    used = usage.get(account.address)
    return {
        "id": account.id,
        "address": account.address,
        "local_part": account.local_part,
        "domain_id": domain.id if domain else None,
        "domain": account.domain,
        "owner": owner.username if owner else "",
        "quota_mb": account.quota_mb or 0,
        "used_mb": round(used / 1048576, 1) if used is not None else None,
        "enabled": bool(account.enabled),
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


def _page(page, per_page) -> tuple[int, int]:
    return max(1, int(page or 1)), max(1, min(int(per_page or 50), 200))


def list_accounts(db: Session, actor: User, domain_id: int | None = None, q: str = "", page: int = 1,
                  per_page: int = 50) -> dict:
    query = db.query(MailAccount)
    if not _is_admin(actor):
        query = query.filter(MailAccount.owner_id == actor.id)
    if domain_id:
        row = get_domain(db, actor, domain_id)
        query = query.filter(MailAccount.domain == row.domain)
    term = (q or "").strip().lower()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(MailAccount.local_part.like(like), MailAccount.domain.like(like)))
    total = query.count()
    page, per_page = _page(page, per_page)
    rows = query.order_by(MailAccount.domain, MailAccount.local_part).offset((page - 1) * per_page).limit(per_page).all()
    domains = {row.domain: row for row in db.query(MailDomain).all()}
    owners = {row.id: row for row in db.query(User).all()}
    usage = usage_bytes(db)
    return {"items": [account_out(row, domains, owners, usage) for row in rows], "total": total, "page": page,
            "per_page": per_page}


# --- forwarders ------------------------------------------------------------------------------

def create_forwarder(db: Session, actor: User, domain_id: int, local_part: str, destinations) -> MailForwarder:
    _require_active()
    domain = get_domain(db, actor, domain_id)
    if not _is_admin(actor) and getattr(actor, "package", None) is not None:
        raw_fwd = getattr(actor.package, "email_forwarders_limit", None)
        fwd_limit = int(-1 if raw_fwd is None else raw_fwd)
        if fwd_limit == 0:
            raise MailError("Email forwarders are disabled for your hosting package (limit: 0).", status=403)
        if fwd_limit > 0:
            my_domains = [d.domain for d in db.query(MailDomain).filter(MailDomain.owner_id == domain.owner_id).all()]
            used_fwd = db.query(MailForwarder).filter(MailForwarder.domain.in_(my_domains or [""])).count()
            if used_fwd >= fwd_limit:
                raise MailError(f"Email forwarder limit reached ({used_fwd}/{fwd_limit}).", status=403)
    local = normalize_local(local_part)
    address = f"{local}@{domain.domain}"
    if db.query(MailForwarder).filter(MailForwarder.domain == domain.domain, MailForwarder.local_part == local).first():
        raise MailError("That address already forwards; edit that forwarder instead.", status=409)
    row = MailForwarder(domain=domain.domain, local_part=local,
                        destinations="\n".join(normalize_destinations(destinations, own_address=address)))
    db.add(row)
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    db.refresh(row)
    return row


def update_forwarder(db: Session, actor: User, forwarder_id: int, destinations) -> MailForwarder:
    row = get_forwarder(db, actor, forwarder_id)
    row.destinations = "\n".join(normalize_destinations(destinations, own_address=row.address))
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    return row


def delete_forwarder(db: Session, actor: User, forwarder_id: int) -> str:
    row = get_forwarder(db, actor, forwarder_id)
    address = row.address
    domain = _domain_row(db, row.domain)
    if domain is not None and domain.catch_all == address and not db.query(MailAccount).filter(
            MailAccount.domain == row.domain, MailAccount.local_part == row.local_part).first():
        domain.catch_all = ""
    db.delete(row)
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    return address


def forwarder_out(db: Session, row: MailForwarder, domains: dict[str, MailDomain]) -> dict:
    domain = domains.get(row.domain)
    return {
        "id": row.id,
        "address": row.address,
        "local_part": row.local_part,
        "domain_id": domain.id if domain else None,
        "domain": row.domain,
        "destinations": row.destination_list,
        "keeps_copy": db.query(MailAccount).filter(MailAccount.domain == row.domain,
                                                  MailAccount.local_part == row.local_part).first() is not None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_forwarders(db: Session, actor: User, domain_id: int | None = None, q: str = "", page: int = 1,
                    per_page: int = 50) -> dict:
    query = db.query(MailForwarder)
    if not _is_admin(actor):
        mine = [row.domain for row in db.query(MailDomain).filter(MailDomain.owner_id == actor.id)]
        query = query.filter(MailForwarder.domain.in_(mine or [""]))
    if domain_id:
        row = get_domain(db, actor, domain_id)
        query = query.filter(MailForwarder.domain == row.domain)
    term = (q or "").strip().lower()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(MailForwarder.local_part.like(like), MailForwarder.domain.like(like),
                                 MailForwarder.destinations.like(like)))
    total = query.count()
    page, per_page = _page(page, per_page)
    rows = query.order_by(MailForwarder.domain, MailForwarder.local_part).offset((page - 1) * per_page).limit(per_page).all()
    domains = {row.domain: row for row in db.query(MailDomain).all()}
    return {"items": [forwarder_out(db, row, domains) for row in rows], "total": total, "page": page,
            "per_page": per_page}


# --- overview ---------------------------------------------------------------------------------

def overview(db: Session, actor: User) -> dict:
    installed = active()
    query = db.query(MailDomain)
    if not _is_admin(actor):
        query = query.filter(MailDomain.owner_id == actor.id)
    owners = {row.id: row for row in db.query(User).all()}
    limit = _limit(actor)
    count = owner_mailbox_count(db, actor.id)
    host = _hostname_or_blank()
    return {
        "installed": installed,
        "is_admin": _is_admin(actor),
        "hostname": host,
        "webmail_url": webmail_url() + "/",
        "mailbox_limit": limit,
        "mailbox_count": count,
        "at_limit": limit is not None and count >= limit,
        "max_user_quota_mb": MAX_USER_QUOTA_MB,
        "default_quota_mb": int(current_settings().get("default_quota_mb") or DEFAULT_QUOTA_MB),
        "domains": [domain_out(db, row, owners) for row in query.order_by(MailDomain.domain).all()],
        "candidates": candidate_domains(db, actor) if installed else [],
        "client": {"host": host, "imap_port": PORTS["imap"], "pop3_port": PORTS["pop3"],
                   "smtp_port": PORTS["smtps"], "submission_port": PORTS["submission"]},
    }


# --- DNS --------------------------------------------------------------------------------------

DNS_TYPES = ("TXT", "CNAME", "MX", "A", "AAAA")
MAX_RELAY_RECORDS = 10
DEFAULT_DMARC = "v=DMARC1; p=quarantine; adkim=r; aspf=r"
_LABEL_RE = re.compile(r"^(?:@|[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,200}[A-Za-z0-9_])?)$")
_SPF_TOKEN_RE = re.compile(r"^[+~?-]?(?:include|ip4|ip6|a|mx|exists|ptr)(?::[A-Za-z0-9.:/_%{}-]{1,253})?(?:/\d{1,3})?$")
_SPF_ALL = ("~all", "-all", "?all", "+all", "all")


# --- DMARC: the domain's policy, as its owner sets it ----------------------------------------
# Stored as JSON in mail_domains.dmarc_policy; "" is DEFAULT_DMARC. The record
# is rendered from the stored fields only, so nothing typed reaches DNS as is.

DMARC_POLICIES = ("none", "quarantine", "reject")
DMARC_ALIGNMENT = ("r", "s")
DMARC_MAX_REPORT_ADDRESSES = 5
_DMARC_FO_RE = re.compile(r"^[01ds](?::[01ds]){0,3}$")
DMARC_DEFAULT_POLICY = {"p": "quarantine", "sp": "", "pct": 100, "rua": [], "ruf": [], "adkim": "r", "aspf": "r",
                        "fo": ""}


def _report_addresses(values, what: str) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[\s,;]+", values)
    clean: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip()
        if text.lower().startswith("mailto:"):
            text = text[len("mailto:"):]
        if not text:
            continue
        try:
            address = normalize_destination(text)
        except MailError as exc:
            raise MailError(f"Each {what} address is an email address, such as dmarc@example.com.") from exc
        if address not in clean:
            clean.append(address)
    if len(clean) > DMARC_MAX_REPORT_ADDRESSES:
        raise MailError(f"At most {DMARC_MAX_REPORT_ADDRESSES} {what} addresses.")
    return clean


def normalize_dmarc(payload: dict) -> dict:
    """A DMARC policy as the editor sends it, checked field by field."""
    if not isinstance(payload, dict):
        raise MailError("A DMARC policy has a p, and may have sp, pct, rua, ruf, adkim, aspf and fo.")
    policy = str(payload.get("p") or "").strip().lower()
    if policy not in DMARC_POLICIES:
        raise MailError("The DMARC policy (p) is none, quarantine or reject.")
    subdomains = str(payload.get("sp") or "").strip().lower()
    if subdomains and subdomains not in DMARC_POLICIES:
        raise MailError("The subdomain policy (sp) is none, quarantine or reject, or empty to follow p.")
    try:
        pct = int(payload.get("pct") if payload.get("pct") not in (None, "") else 100)
    except (TypeError, ValueError) as exc:
        raise MailError("The percentage (pct) is a whole number from 0 to 100.") from exc
    if not 0 <= pct <= 100:
        raise MailError("The percentage (pct) is a whole number from 0 to 100.")
    alignment = {}
    for key in ("adkim", "aspf"):
        value = str(payload.get(key) or "r").strip().lower()
        if value not in DMARC_ALIGNMENT:
            raise MailError(f"{key} is r (relaxed) or s (strict).")
        alignment[key] = value
    failure = str(payload.get("fo") or "").strip().lower()
    if failure and not _DMARC_FO_RE.fullmatch(failure):
        raise MailError("The failure options (fo) are 0, 1, d or s, joined by colons, such as 1:d.")
    return {"p": policy, "sp": subdomains, "pct": pct, "rua": _report_addresses(payload.get("rua"), "aggregate report"),
            "ruf": _report_addresses(payload.get("ruf"), "failure report"), **alignment, "fo": failure}


def render_dmarc(policy: dict) -> str:
    """The _dmarc TXT record of a checked policy, tags in RFC 7489's order."""
    parts = ["v=DMARC1", f"p={policy['p']}"]
    if policy.get("sp"):
        parts.append(f"sp={policy['sp']}")
    if int(policy.get("pct", 100)) != 100:
        parts.append(f"pct={int(policy['pct'])}")
    if policy.get("rua"):
        parts.append("rua=" + ",".join(f"mailto:{address}" for address in policy["rua"]))
    if policy.get("ruf"):
        parts.append("ruf=" + ",".join(f"mailto:{address}" for address in policy["ruf"]))
    parts += [f"adkim={policy.get('adkim') or 'r'}", f"aspf={policy.get('aspf') or 'r'}"]
    if policy.get("fo"):
        parts.append(f"fo={policy['fo']}")
    return "; ".join(parts)


def parse_dmarc(text: str) -> dict:
    """A DMARC record as published -> the editor's fields. Raises MailError for
    a record that is not DMARC or that the editor could not express."""
    tags = {}
    for part in (text or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key:
            tags[key.strip().lower()] = value.strip()
    if tags.get("v", "").upper() != "DMARC1":
        raise MailError("That is not a DMARC record.")
    return normalize_dmarc({
        "p": tags.get("p"), "sp": tags.get("sp", ""), "pct": tags.get("pct", 100), "adkim": tags.get("adkim", "r"),
        "aspf": tags.get("aspf", "r"), "fo": tags.get("fo", ""),
        "rua": [item for item in tags.get("rua", "").split(",") if item.strip()],
        "ruf": [item for item in tags.get("ruf", "").split(",") if item.strip()],
    })


def dmarc_policy(row: MailDomain) -> dict:
    """The domain's DMARC policy: what its owner saved, or the default."""
    try:
        stored = json.loads(row.dmarc_policy) if row.dmarc_policy else None
        if isinstance(stored, dict):
            return normalize_dmarc(stored)
    except (ValueError, MailError):
        logger.warning("Stored DMARC policy of %s is unusable; the default applies", row.domain)
    return dict(DMARC_DEFAULT_POLICY)


def dmarc_external_domains(row: MailDomain, policy: dict) -> list[str]:
    """Report domains outside the mail domain: each must publish
    <domain>._report._dmarc.<report domain> TXT "v=DMARC1" (RFC 7489 7.1)."""
    out = []
    for address in policy.get("rua", []) + policy.get("ruf", []):
        target = address.rpartition("@")[2]
        if target != row.domain and not target.endswith("." + row.domain) and target not in out:
            out.append(target)
    return out


def dmarc_view(row: MailDomain) -> dict:
    policy = dmarc_policy(row)
    return {"policy": policy, "record": render_dmarc(policy), "custom": bool(row.dmarc_policy),
            "external": [{"domain": target, "name": f"{row.domain}._report._dmarc.{target}", "value": "v=DMARC1"}
                         for target in dmarc_external_domains(row, policy)]}


def set_dmarc(db: Session, actor: User, domain_id: int, payload: dict | None) -> MailDomain:
    """Save the domain's DMARC policy (None goes back to the default) and put
    the record in its zone here, replacing the one there."""
    row = get_domain(db, actor, domain_id)
    row.dmarc_policy = "" if payload is None else json.dumps(normalize_dmarc(payload), sort_keys=True)
    db.commit()
    from app.services import dns

    try:
        publish_dns(db, row, replace=False, force_keys={"dmarc"})
    except (dns.DnsError, dns.DnsInputError) as exc:
        logger.warning("DMARC record not published for %s: %s", row.domain, exc)
    return row


def _fqdn(label: str, domain: str) -> str:
    """A record name as written ("@", "mail", "x._domainkey"), made absolute
    within the domain."""
    label = (label or "@").strip().rstrip(".")
    if label in ("@", "", domain):
        return domain
    if label.endswith("." + domain):
        return label
    return f"{label}.{domain}"


def normalize_dns_record(item: dict) -> dict:
    """One record of a relay's DNS template. The name is relative to each
    domain that uses the relay; {domain} in the value becomes that domain."""
    if not isinstance(item, dict):
        raise MailError("A DNS record has a type, a name and a value.")
    rtype = str(item.get("type") or "").strip().upper()
    if rtype not in DNS_TYPES:
        raise MailError("The record type is TXT, CNAME, MX, A or AAAA.")
    label = str(item.get("name") or "@").strip()
    if "{domain}" in label:
        raise MailError("{domain} goes in the value; the name is relative to the domain already.")
    if not _LABEL_RE.fullmatch(label):
        raise MailError("A record name is @ for the domain itself, or a name under it such as mail or s1._domainkey.")
    value = str(item.get("value") or "").strip()
    if not value or len(value) > 2048 or any(char in value for char in "\r\n\0"):
        raise MailError("Every record needs a value, on one line, of at most 2048 characters.")
    check = value.replace("{domain}", "example.com")
    if rtype in ("CNAME", "MX"):
        check = check.rstrip(".").lower()
        if not DOMAIN_RE.fullmatch(check):
            raise MailError("A CNAME or MX record points at a hostname.")
        value = value.rstrip(".").lower()
    elif rtype in ("A", "AAAA"):
        try:
            address = ipaddress.ip_address(check)
        except ValueError as exc:
            raise MailError("An A record points at an IPv4 address, an AAAA record at an IPv6 one.") from exc
        if (rtype == "A") != (address.version == 4):
            raise MailError("An A record points at an IPv4 address, an AAAA record at an IPv6 one.")
    record = {"type": rtype, "name": label, "value": value}
    if rtype == "MX":
        try:
            priority = int(item.get("priority") if item.get("priority") not in (None, "") else 10)
        except (TypeError, ValueError) as exc:
            raise MailError("The MX priority is a number from 0 to 65535.") from exc
        if not 0 <= priority <= 65535:
            raise MailError("The MX priority is a number from 0 to 65535.")
        record["priority"] = priority
    return record


def normalize_spf_include(value: str) -> str:
    text = " ".join((value or "").split())
    if len(text) > 200:
        raise MailError("The relay's SPF part is at most 200 characters.")
    for token in text.split(" ") if text else []:
        if not _SPF_TOKEN_RE.fullmatch(token):
            raise MailError("The relay's SPF part is one or more mechanisms, such as include:spf.brevo.com.")
    return text


def server_ip() -> str:
    """The address mail.<domain> and webmail.<domain> point at. With DNS
    Manager on it is its address for new zones, so its zones and the Email
    page say the same."""
    from app.services import dns

    if dns.active():
        try:
            address = str(dns.settings().get("zone_ip") or "")
        except Exception:  # noqa: BLE001 - the interfaces will do
            address = ""
        if address:
            return address
    ipv4, _ = server_addresses()
    return ipv4[0] if ipv4 else ""


def suggested_spf(relay: dict | None) -> str:
    """The SPF record the panel suggests: this server by name and address, and
    the relay the domain sends through."""
    ipv4, ipv6 = server_addresses()
    spf = ["v=spf1", "mx", "a"] + [f"ip4:{ip}" for ip in ipv4[:2]] + [f"ip6:{ip}" for ip in ipv6[:1]]
    if relay and relay.get("spf_include"):
        spf += relay["spf_include"].split()
    return " ".join(spf + ["~all"])


def default_spf() -> str:
    """The SPF record for a domain on the server's default relay; new DNS
    zones get it through their template's {spf}."""
    relay = None
    if active():
        relays = {item["id"]: item for item in _relays()}
        relay = relays.get(_stored().get("default_relay") or "")
    return suggested_spf(relay)


def _spf_mechanisms(text: str) -> set[str]:
    return {token.lower() for token in (text or "").split()[1:] if token.lower() not in _SPF_ALL}


def _server_spf_terms() -> set[str]:
    ipv4, ipv6 = server_addresses()
    own = server_ip()
    return ({"a", "mx", "+a", "+mx"} | {f"ip4:{ip}" for ip in ipv4 + ([own] if own else [])}
            | {f"ip6:{ip}" for ip in ipv6})


def _relay_spf_terms(relay: dict | None) -> set[str]:
    return {token.lower() for token in ((relay or {}).get("spf_include") or "").split()}


def spf_covers(text: str, relay: dict | None) -> bool:
    """Whether an SPF record lets this server send, and the domain's relay
    too. More of the owner's own (Google, a newsletter service) is fine."""
    terms = _spf_mechanisms(text)
    return bool(terms & _server_spf_terms()) and _relay_spf_terms(relay) <= terms


def spf_is_panels(text: str) -> bool:
    """An SPF record the panel wrote - this server and relay includes, nothing
    else - which it may rewrite. Anything more makes it the owner's."""
    terms = _spf_mechanisms(text)
    known = _server_spf_terms()
    for relay in _relays():
        known |= _relay_spf_terms(relay)
    return bool(terms) and terms <= known


def effective_relay(row: MailDomain) -> dict | None:
    """The relay this domain's outgoing mail leaves through, or None."""
    choice = row.relay or ""
    if choice == "direct":
        return None
    relays = {relay["id"]: relay for relay in _relays()}
    if choice:
        return relays.get(choice)
    return relays.get(_stored().get("default_relay") or "")


def dns_records(row: MailDomain) -> list[dict]:
    """What the domain publishes for mail. MX goes to mail.<domain>, which
    points here - DNS Manager's zones are made that way - then SPF, DKIM,
    DMARC, the records the domain's relay asks for, and the webmail address."""
    domain = row.domain
    ip = server_ip()
    relay = effective_relay(row)
    records = []
    if ip:
        records += [
            {"key": "mx", "type": "MX", "name": domain, "value": f"mail.{domain}", "priority": 10},
            {"key": "mail", "type": "A", "name": f"mail.{domain}", "value": ip},
        ]
    else:
        records.append({"key": "mx", "type": "MX", "name": domain, "value": _hostname_or_blank(), "priority": 10})
    records += [
        {"key": "spf", "type": "TXT", "name": domain, "value": suggested_spf(relay)},
        {"key": "dkim", "type": "TXT", "name": f"{row.dkim_selector or DKIM_SELECTOR}._domainkey.{domain}",
         "value": f"v=DKIM1; k=rsa; p={row.dkim_public}"},
    ]
    # A rotation publishes the next key before mail is signed with it, and
    # keeps the previous one until mail signed with it has been checked.
    for key, selector, public in (("dkim-next", row.dkim_next_selector, row.dkim_next_public),
                                  ("dkim-old", row.dkim_old_selector, row.dkim_old_public)):
        if selector and public:
            records.append({"key": key, "type": "TXT", "name": f"{selector}._domainkey.{domain}",
                            "value": f"v=DKIM1; k=rsa; p={public}"})
    records.append({"key": "dmarc", "type": "TXT", "name": f"_dmarc.{domain}", "value": render_dmarc(dmarc_policy(row))})
    if relay:
        for index, item in enumerate(relay.get("dns_records") or []):
            records.append({
                "key": f"relay-{index}", "type": item["type"], "name": _fqdn(item["name"], domain).lower(),
                "value": item["value"].replace("{domain}", domain), "priority": item.get("priority"),
                "source": "relay", "relay": relay.get("name") or relay["id"],
            })
    if ip:
        records.append({"key": "webmail", "type": "A", "name": f"webmail.{domain}", "value": ip, "optional": True})
        # The other mail hosts: published once they are served (Mail hosts on
        # the DNS page), and needed in DNS before their certificate is issued.
        for label in MAIL_HOST_LABELS[1:]:
            records.append({"key": label, "type": "A", "name": f"{label}.{domain}", "value": ip, "optional": True,
                            "host": label})
    # Where mail apps find the servers (RFC 6186, RFC 8314): the name they
    # connect to is mail.<domain> once it has its own certificate.
    host = client_host(row)
    if host:
        for key, service, port in (("srv-imaps", "_imaps._tcp", PORTS["imap"]),
                                   ("srv-pop3s", "_pop3s._tcp", PORTS["pop3"]),
                                   ("srv-submissions", "_submissions._tcp", PORTS["smtps"]),
                                   ("srv-submission", "_submission._tcp", PORTS["submission"])):
            records.append({"key": key, "type": "SRV", "name": f"{service}.{domain}", "value": f"1 {port} {host}",
                            "priority": 0})
    if "autodiscover" in served_hosts(row):
        records.append({"key": "srv-autodiscover", "type": "SRV", "name": f"_autodiscover._tcp.{domain}",
                        "value": f"1 443 autodiscover.{domain}", "priority": 0, "host": "autodiscover"})
    if row.mta_sts_mode in MTA_STS_MODES and row.mta_sts_id:
        records.append({"key": "mta-sts-txt", "type": "TXT", "name": f"_mta-sts.{domain}",
                        "value": f"v=STSv1; id={row.mta_sts_id}", "host": "mta-sts"})
    if tls_rpt_addresses(row):
        records.append({"key": "tls-rpt", "type": "TXT", "name": f"_smtp._tls.{domain}",
                        "value": render_tls_rpt(tls_rpt_addresses(row))})
    return records


# Records the panel alone writes: in the zone they always follow the panel.
PANEL_RECORD_KEYS = ("srv-imaps", "srv-pop3s", "srv-submissions", "srv-submission", "srv-autodiscover",
                     "mta-sts-txt", "tls-rpt")


def _srv_text(text: str) -> str:
    return " ".join(part.rstrip(".").lower() for part in (text or "").split())


def _resolve(name: str, rtype: str) -> list[str] | None:
    """Answers as text, [] for no record, None when DNS could not be asked."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None
    resolver = dns.resolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 4
    try:
        answer = resolver.resolve(name, rtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.exception.DNSException, OSError):
        return None
    out = []
    for item in answer:
        if rtype == "TXT":
            out.append(b"".join(item.strings).decode("utf-8", "replace"))
        elif rtype == "MX":
            out.append(str(item.exchange).rstrip(".").lower())
        elif rtype in ("CNAME", "NS"):
            out.append(str(item.target).rstrip(".").lower())
        else:
            out.append(item.to_text())
    return out


DKIM_KEYS = ("dkim", "dkim-next", "dkim-old")


def _dkim_record_public(row: MailDomain, key: str) -> str:
    return {"dkim": row.dkim_public, "dkim-next": row.dkim_next_public, "dkim-old": row.dkim_old_public}.get(key) or ""


def _zone_lookup(zone_name: str):
    """A resolver that answers from this server's zone, as _resolve does."""
    from app.services import dns

    data = dns._zone_data(zone_name)

    def lookup(name: str, rtype: str) -> list[str]:
        contents = dns._contents(dns._rrset(data, dns._absolute(name.lower()), rtype))
        if rtype == "TXT":
            return [dns._untxt(content) for content in contents]
        if rtype == "MX":
            return [content.split()[-1].rstrip(".").lower() for content in contents if content.split()]
        if rtype == "CNAME":
            return [content.rstrip(".").lower() for content in contents]
        return list(contents)

    return lookup


def _squash(text: str) -> str:
    return "".join((text or "").split()).strip('"').lower()


def check_dns(row: MailDomain, lookup=None, zone_name: str = "") -> list[dict]:
    """Each record with its status: ok, different, missing, or unknown when
    DNS could not be asked. Asked of public DNS, or with lookup of this
    server's zone (zone_name) - the one DNS Manager shows."""
    lookup = lookup or _resolve
    records = dns_records(row)
    relay = effective_relay(row)
    own = server_ip()
    ips = set(server_addresses()[0]) | ({own} if own else set())

    def points_here(target: str) -> bool:
        inside = bool(zone_name) and (target == zone_name or target.endswith("." + zone_name))
        return bool(set((lookup(target, "A") if inside else _resolve(target, "A")) or []) & ips)

    for record in records:
        key, rtype, want, name = record["key"], record["type"], record["value"], record["name"]
        if key == "spf":
            txt = lookup(name, "TXT")
            found = None if txt is None else [t for t in txt if t.lower().startswith("v=spf1")]
            ok = bool(found) and len(found) == 1 and spf_covers(found[0], relay)
        elif key in DKIM_KEYS:
            public = _dkim_record_public(row, key)
            found = lookup(name, "TXT")
            ok = found is not None and any(public and public in t.replace(" ", "") for t in found)
        elif key == "dmarc":
            txt = lookup(name, "TXT")
            found = None if txt is None else [t for t in txt if t.upper().startswith("V=DMARC1")]
            # Any DMARC record will do until the owner sets a policy; then it
            # has to be that one.
            ok = bool(found) and (not row.dmarc_policy or any(_squash(t) == _squash(want) for t in found))
        elif key == "mx":
            found = lookup(name, "MX")
            # mail.<domain>, the server's own name, or any name that points here.
            ok = found is not None and (want in found or any(points_here(target) for target in found[:3]))
        elif rtype == "TXT":
            found = lookup(name, "TXT")
            ok = found is not None and any(_squash(t) == _squash(want) for t in found)
        elif rtype in ("MX", "CNAME"):
            found = lookup(name, rtype)
            ok = found is not None and want.rstrip(".").lower() in found
        elif rtype == "SRV":
            found = lookup(name, rtype)
            wanted = _srv_text(f"{record.get('priority') or 0} {want}")
            ok = found is not None and any(_srv_text(item) == wanted for item in found)
        else:
            found = lookup(name, rtype)
            ok = found is not None and want in found
        if found is None:
            record["status"] = "unknown"
        elif ok:
            record["status"] = "ok"
        else:
            record["status"] = "different" if found else "missing"
        record["found"] = found or []
    return records


def _hosted_zone(db: Session, row: MailDomain):
    """The DNS Manager zone that holds this domain, if this server serves it."""
    from app.models.entities import DnsZone
    from app.services import dns

    if not dns.active():
        return None
    containing = [zone for zone in db.query(DnsZone).all()
                  if row.domain == zone.name or row.domain.endswith("." + zone.name)]
    return max(containing, key=lambda zone: len(zone.name), default=None)


def dns_view(db: Session, row: MailDomain, actor: User, check: bool = True) -> dict:
    """The domain's DNS records page, as OPanel's: each record's status is what
    public DNS answers; when DNS Manager serves the domain, in_zone also says
    whether this server's zone - the one the DNS page shows - holds it."""
    from app.services import dns

    relay = effective_relay(row)
    zone = _hosted_zone(db, row)
    records = check_dns(row) if check else dns_records(row)
    if check and zone is not None:
        try:
            in_zone = {record["key"]: record["status"] == "ok"
                       for record in check_dns(row, _zone_lookup(zone.name), zone.name)}
        except dns.DnsError:
            in_zone = {}
        for record in records:
            record["in_zone"] = in_zone.get(record["key"])
    return {
        "domain": row.domain,
        "records": records,
        "dkim": dkim_view(row),
        "dmarc": dmarc_view(row),
        "hosts": {"served": served_hosts(row), "available": list(MAIL_HOST_LABELS), "client": client_settings(row)},
        "mta_sts": mta_sts_view(row),
        "hosted_zone": zone.name if zone else "",
        "relay": {
            "choice": row.relay or "",
            "effective": relay["id"] if relay else "",
            "effective_name": (relay.get("name") or relay["id"]) if relay else "",
            # Customers see which relay their mail uses; only an admin picks it.
            "options": [{"id": item["id"], "name": item.get("name") or item["id"]} for item in _relays()]
            if _is_admin(actor) else [],
        },
    }


def publish_dns(db: Session, row: MailDomain, *, replace: bool, force_keys: set[str] | frozenset = frozenset(),
                hosts: set[str] | None = None) -> list[str]:
    """Keep the domain's mail records in its zone, when DNS Manager serves it.

    DKIM and the relay's records always follow the panel, and so does an SPF
    record the panel wrote (spf_is_panels). What is missing is added. An MX,
    address, DMARC or SPF of the owner's own stays - the records page shows it
    as different - unless replace is set, which is the page's button.
    Returns the names written.
    """
    from app.services import dns

    zone = _hosted_zone(db, row)
    if zone is None:
        return []
    data = dns._zone_data(zone.name)
    ttl = dns.settings()["ttl"]
    changes: dict = {}

    def current(name: str, rtype: str) -> list[str]:
        # What an earlier record of this pass already set counts: SPF and a
        # relay's TXT at the apex share one RRset.
        if (name, rtype) in changes:
            return list(changes[(name, rtype)][1])
        return dns._contents(dns._rrset(data, name, rtype))

    def same(existing: list[str], content: str, rtype: str) -> bool:
        if rtype == "TXT":
            return [dns._untxt(item) for item in existing] == [dns._untxt(content)]
        if rtype == "SRV":
            return [_srv_text(item) for item in existing] == [_srv_text(content)]
        return [item.rstrip(".").lower() for item in existing] == [content.rstrip(".").lower()]

    wanted_hosts = set(served_hosts(row)) | set(hosts or ())
    for record in dns_records(row):
        key, rtype = record["key"], record["type"]
        if not record["value"]:
            continue
        if record.get("host") and record["host"] not in wanted_hosts:
            continue
        name = dns._absolute(record["name"])
        if rtype == "MX":
            content = f"{record.get('priority') if record.get('priority') is not None else 10} {dns._absolute(record['value'])}"
        elif rtype == "SRV":
            weight, port, target = record["value"].split()
            content = f"{record.get('priority') or 0} {weight} {port} {dns._absolute(target)}"
        elif rtype == "CNAME":
            content = dns._absolute(record["value"])
        elif rtype == "TXT":
            content = dns._txt(record["value"])
        else:
            content = record["value"]
        existing = current(name, rtype)
        if key in DKIM_KEYS or key in PANEL_RECORD_KEYS or record.get("source") == "relay":
            if rtype == "TXT" and key not in DKIM_KEYS and key not in PANEL_RECORD_KEYS:
                # Another TXT of the same name (a verification string, say) stays.
                wanted = [item for item in existing if dns._untxt(item) != record["value"]] + [content]
                if sorted(wanted) != sorted(existing):
                    changes[(name, rtype)] = (ttl, wanted)
            elif not same(existing, content, rtype):
                changes[(name, rtype)] = (ttl, [content])
        elif key == "spf":
            others = [item for item in existing if not dns._untxt(item).lower().startswith("v=spf1")]
            spf_now = [dns._untxt(item) for item in existing if dns._untxt(item).lower().startswith("v=spf1")]
            if spf_now == [record["value"]]:
                continue
            if not spf_now or replace or (len(spf_now) == 1 and spf_is_panels(spf_now[0])):
                changes[(name, rtype)] = (ttl, others + [content])
        elif not existing or ((replace or key in force_keys) and not same(existing, content, rtype)):
            # mx, mail, dmarc, webmail
            changes[(name, rtype)] = (ttl, [content])
    # A name that is a CNAME cannot hold another record. The button says so;
    # keeping the zone in step quietly leaves that name as it is.
    blocked = [key for key in changes if key[1] != "CNAME" and (key[0], "CNAME") not in changes
               and dns._rrset(data, key[0], "CNAME")]
    if blocked and replace:
        raise MailError("A name in this zone is a CNAME and cannot also hold the mail record. Change it on the DNS page.")
    for key in blocked:
        changes.pop(key)
    if not changes:
        return []
    dns._serial_follows_edits(zone.name, data)
    dns._patch(zone.name, changes)
    return sorted({name.rstrip(".") for name, _ in changes})


def publish_dns_quietly(db: Session, row: MailDomain, hosts: set[str] | None = None) -> list[str]:
    from app.services import dns

    try:
        return publish_dns(db, row, replace=False, hosts=hosts)
    except (MailError, dns.DnsError, dns.DnsInputError) as exc:
        logger.warning("Mail records not published for %s: %s", row.domain, exc)
        return []


def publish_all_quietly(db: Session, zone_name: str = "") -> list[str]:
    """Every mail domain's records into its zone - or only those in zone_name:
    when a zone is made, DNS Manager is installed, or the panel starts."""
    from app.services import dns

    if not active() or not dns.active():
        return []
    written = []
    for row in db.query(MailDomain).order_by(MailDomain.domain).all():
        if zone_name and not (row.domain == zone_name or row.domain.endswith("." + zone_name)):
            continue
        written += publish_dns_quietly(db, row)
    return written


def withdraw_dns(db: Session, row: MailDomain, name: str, rtype: str, value: str) -> bool:
    """Take one record the panel published out of the domain's zone here,
    leaving anything else at that name. True when it was there."""
    from app.services import dns

    zone = _hosted_zone(db, row)
    if zone is None:
        return False
    data = dns._zone_data(zone.name)
    absolute = dns._absolute(name)
    rrset = dns._rrset(data, absolute, rtype)
    contents = dns._contents(rrset)

    def matches(content: str) -> bool:
        text = dns._untxt(content) if rtype == "TXT" else content
        return _squash(text) == _squash(value)

    kept = [content for content in contents if not matches(content)]
    if len(kept) == len(contents):
        return False
    dns._serial_follows_edits(zone.name, data)
    dns._patch(zone.name, {(absolute, rtype): ((rrset or {}).get("ttl", dns.DEFAULT_TTL), kept)})
    return True


def withdraw_dns_quietly(db: Session, row: MailDomain, name: str, rtype: str, value: str) -> bool:
    from app.services import dns

    try:
        return withdraw_dns(db, row, name, rtype, value)
    except (MailError, dns.DnsError, dns.DnsInputError) as exc:
        logger.warning("Mail record %s %s not withdrawn: %s", name, rtype, exc)
        return False


# --- DKIM rotation ------------------------------------------------------------------------------
# Two selectors, so no signature ever fails: the next key is published under
# a selector of its own (s<date>) while mail is still signed with the current
# one. Exim switches only once public DNS shows the new record - or when it
# does not show the current one either, so the switch breaks nothing - and
# the previous key stays published for DKIM_RETIRE_DAYS, until mail signed
# with it has been checked, before it and its record go. The maintenance
# loop moves each domain along; an administrator may also have it rotate on
# its own every dkim_rotate_days.

DKIM_MIN_PUBLISH = 3600
DKIM_RETIRE_DAYS = 7
DKIM_STATES = ("", "publishing", "retiring")


def new_dkim_selector(row: MailDomain, now: datetime) -> str:
    base = "s" + now.strftime("%Y%m%d")
    taken = {row.dkim_selector or DKIM_SELECTOR, row.dkim_next_selector, row.dkim_old_selector}
    selector, number = base, 1
    while selector in taken:
        number += 1
        selector = f"{base}-{number}"
    return selector


def dkim_next_step(row: MailDomain, now: datetime, *, current_status: str = "", next_status: str = "",
                   rotate_days: int = 0, retire_days: int = DKIM_RETIRE_DAYS, manual: bool = False) -> str:
    """What a domain's DKIM rotation does next: "switch", "retire", "start",
    "wait" or "none". The DNS statuses are check_dns's (ok, missing,
    different, unknown) for the current and the next selector's record."""
    state = row.dkim_state or ""
    since = row.dkim_state_at or now
    if state == "publishing":
        if next_status == "unknown":
            return "wait"
        safe = next_status == "ok" or current_status in ("missing", "different")
        if safe and (manual or (now - since).total_seconds() >= DKIM_MIN_PUBLISH):
            return "switch"
        return "wait"
    if state == "retiring":
        if manual or (now - since).total_seconds() >= max(1, retire_days) * 86400:
            return "retire"
        return "wait"
    if rotate_days > 0:
        last = row.dkim_rotated_at or row.created_at
        if last is not None and (now - last).total_seconds() >= rotate_days * 86400:
            return "start"
    return "none"


def start_dkim_rotation(db: Session, row: MailDomain, now: datetime | None = None) -> MailDomain:
    """Make and publish the next key; mail stays signed with the current one."""
    if (row.dkim_state or "") != "":
        raise MailError("A DKIM key rotation is already under way for this domain.", status=409)
    now = now or datetime.utcnow()
    selector = new_dkim_selector(row, now)
    public = _dkim_public(row.domain, selector)
    row.dkim_next_selector, row.dkim_next_public = selector, public
    row.dkim_state, row.dkim_state_at = "publishing", now
    db.commit()
    publish_dns_quietly(db, row)
    return row


def _dkim_statuses(db: Session, row: MailDomain) -> tuple[str, str]:
    """check_dns's status of the current and the next selector's record in
    public DNS: what receiving servers see."""
    statuses = {record["key"]: record["status"] for record in check_dns(row) if record["key"] in DKIM_KEYS}
    return statuses.get("dkim", "unknown"), statuses.get("dkim-next", "unknown")


def _switch_dkim(db: Session, row: MailDomain, now: datetime) -> None:
    previous = (row.dkim_selector or DKIM_SELECTOR, row.dkim_public or "")
    row.dkim_selector, row.dkim_public = row.dkim_next_selector, row.dkim_next_public
    row.dkim_old_selector, row.dkim_old_public = previous
    row.dkim_next_selector, row.dkim_next_public = "", ""
    row.dkim_state, row.dkim_state_at, row.dkim_rotated_at = "retiring", now, now
    db.flush()
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()
    publish_dns_quietly(db, row)


def _retire_dkim(db: Session, row: MailDomain) -> None:
    selector, public = row.dkim_old_selector, row.dkim_old_public
    if selector:
        result = shell.privileged("mail-dkim-drop", helper_args=[row.domain, selector], check=False,
                                  fallback=["true"])
        if result.returncode != 0:
            logger.warning("DKIM key %s of %s not removed: %s", selector, row.domain, result.stderr)
        if public:
            withdraw_dns_quietly(db, row, f"{selector}._domainkey.{row.domain}", "TXT", f"v=DKIM1; k=rsa; p={public}")
    row.dkim_old_selector, row.dkim_old_public = "", ""
    row.dkim_state, row.dkim_state_at = "", None
    db.commit()


def cancel_dkim_rotation(db: Session, row: MailDomain) -> MailDomain:
    """Drop the next key before mail was ever signed with it."""
    if row.dkim_state != "publishing":
        raise MailError("There is no new DKIM key waiting to be used.", status=409)
    selector, public = row.dkim_next_selector, row.dkim_next_public
    if selector:
        shell.privileged("mail-dkim-drop", helper_args=[row.domain, selector], check=False, fallback=["true"])
        if public:
            withdraw_dns_quietly(db, row, f"{selector}._domainkey.{row.domain}", "TXT", f"v=DKIM1; k=rsa; p={public}")
    row.dkim_next_selector, row.dkim_next_public = "", ""
    row.dkim_state, row.dkim_state_at = "", None
    db.commit()
    return row


def advance_dkim(db: Session, row: MailDomain, *, now: datetime | None = None, manual: bool = False) -> str:
    """Take one step of the domain's rotation if it is due; returns the step."""
    now = now or datetime.utcnow()
    settings = current_settings()
    current_status = next_status = ""
    if row.dkim_state == "publishing":
        current_status, next_status = _dkim_statuses(db, row)
    step = dkim_next_step(row, now, current_status=current_status, next_status=next_status,
                          rotate_days=int(settings.get("dkim_rotate_days") or 0),
                          retire_days=int(settings.get("dkim_retire_days") or DKIM_RETIRE_DAYS), manual=manual)
    if step == "switch":
        _switch_dkim(db, row, now)
    elif step == "retire":
        _retire_dkim(db, row)
    elif step == "start" and not manual:
        start_dkim_rotation(db, row, now)
    return step


MAINTENANCE_SECONDS = 900
_maintenance_started = False


def maintenance_tick(db: Session, now: datetime | None = None) -> dict[str, str]:
    """One pass of the background work every mail domain may have due: the
    next step of its DKIM rotation. A domain that fails is logged and left
    for the next pass. Returns {domain: step} for the steps taken."""
    if not active():
        return {}
    done = {}
    for row in db.query(MailDomain).order_by(MailDomain.domain).all():
        try:
            step = advance_dkim(db, row, now=now)
        except Exception:  # noqa: BLE001 - one domain never stops the others
            logger.warning("DKIM rotation step failed for %s", row.domain, exc_info=True)
            db.rollback()
            continue
        if step not in ("none", "wait"):
            done[row.domain] = step
            logger.info("DKIM rotation of %s: %s", row.domain, step)
    return done


def _maintenance_lock():
    """An exclusive lock, so of several API workers one does the pass."""
    try:
        import fcntl
    except ImportError:  # not Linux: tests and development
        return None
    path = panel_settings.SETTINGS_DIR / "mail-maintenance.lock"
    try:
        handle = open(path, "a")  # noqa: SIM115 - held for the pass
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return handle


def start_maintenance_daemon() -> None:
    global _maintenance_started
    if _maintenance_started:
        return
    _maintenance_started = True

    def loop() -> None:
        from app.core.database import SessionLocal

        while True:
            time.sleep(MAINTENANCE_SECONDS)
            lock = _maintenance_lock()
            if lock is False:
                continue
            try:
                with SessionLocal() as db:
                    maintenance_tick(db)
            except Exception:  # noqa: BLE001 - the loop outlives any one failure
                logger.warning("Mail maintenance failed", exc_info=True)
            finally:
                if lock:
                    lock.close()

    threading.Thread(target=loop, name="spanel-mail-maintenance", daemon=True).start()


def dkim_view(row: MailDomain) -> dict:
    settings = current_settings()
    retire_days = int(settings.get("dkim_retire_days") or DKIM_RETIRE_DAYS)
    retire_at = None
    if row.dkim_state == "retiring" and row.dkim_state_at:
        retire_at = (row.dkim_state_at + timedelta(days=retire_days)).isoformat()
    return {
        "selector": row.dkim_selector or DKIM_SELECTOR,
        "state": row.dkim_state or "",
        "state_at": row.dkim_state_at.isoformat() if row.dkim_state_at else None,
        "next_selector": row.dkim_next_selector or "",
        "old_selector": row.dkim_old_selector or "",
        "rotated_at": row.dkim_rotated_at.isoformat() if row.dkim_rotated_at else None,
        "retire_at": retire_at,
        "rotate_days": int(settings.get("dkim_rotate_days") or 0),
    }


def set_domain_relay(db: Session, actor: User, domain_id: int, choice: str) -> MailDomain:
    if not _is_admin(actor):
        raise MailError("Only an administrator chooses the relay a domain sends through.", status=403)
    row = get_domain(db, actor, domain_id)
    choice = choice or ""
    if choice not in ("", "direct") and choice not in {relay["id"] for relay in _relays()}:
        raise MailError("There is no such relay.", status=404)
    row.relay = choice
    db.commit()
    sync(db)
    publish_dns_quietly(db, row)
    return row


# --- the whole state, to the mail server --------------------------------------------------------

def build_state(db: Session) -> dict:
    domains = db.query(MailDomain).order_by(MailDomain.domain).all()
    owners = {row.id: row for row in db.query(User).all()}
    by_owner: dict[int, list[str]] = {}
    for row in domains:
        by_owner.setdefault(row.owner_id, []).append(row.domain)
    state: dict = {
        "domains": [{"domain": row.domain, "catch_all": row.catch_all or "",
                     "dkim_selector": row.dkim_selector or DKIM_SELECTOR} for row in domains],
        "mailboxes": [],
        "forwarders": [],
        # A mailbox may send as any domain of the account that owns it.
        "senders": {},
        # PHP on an account's websites may have its mail signed for that
        # account's domains.
        "local_senders": {},
    }
    names = {row.domain for row in domains}
    for account in db.query(MailAccount).order_by(MailAccount.domain, MailAccount.local_part).all():
        owner = owners.get(account.owner_id)
        if owner is None or account.domain not in names:
            continue
        state["mailboxes"].append({
            "local": account.local_part,
            "domain": account.domain,
            "user": linux_user_of(owner),
            "hash": account.password_hash,
            "quota_mb": int(account.quota_mb or 0),
            # A suspended mailbox, or one of a suspended account, still
            # receives but cannot sign in.
            "enabled": bool(account.enabled) and bool(owner.is_active),
            "gen": mailbox_generation(account),
        })
        state["senders"][account.address] = by_owner.get(account.owner_id, [])
    for row in db.query(MailForwarder).order_by(MailForwarder.domain, MailForwarder.local_part).all():
        if row.domain in names and row.destination_list:
            state["forwarders"].append({"address": row.address, "to": row.destination_list})
    for owner_id, owned in by_owner.items():
        owner = owners.get(owner_id)
        if owner is not None:
            try:
                state["local_senders"][linux_user_of(owner)] = owned
            except ValueError:
                continue
    relay_ids = {relay["id"] for relay in _relays()}
    state["relay_routes"] = {row.domain: row.relay for row in domains if row.relay == "direct" or row.relay in relay_ids}
    default = _stored().get("default_relay") or ""
    state["default_relay"] = default if default in relay_ids else ""
    # Switched-on autoresponders of mailboxes; the helper writes each as a
    # Sieve vacation into the mailbox and removes the others'.
    boxes = set(state["senders"])
    state["autoresponders"] = [
        autoresponder_state(row)
        for row in db.query(MailAutoresponder).order_by(MailAutoresponder.domain, MailAutoresponder.local_part).all()
        if row.enabled and row.address in boxes
    ]
    return state


def sync(db: Session) -> dict:
    """Hand the whole state to the mail server."""
    _require_active()
    with _sync_lock:
        result = shell.privileged("mail-sync", input=json.dumps(build_state(db)), check=False, sensitive=True,
                                  timeout=300, fallback=["bash", "-lc", "echo '{\"domains\": [], \"dkim\": {}}'"])
    if result.returncode != 0:
        raise MailError(_helper_message(result.stderr or result.stdout), status=502)
    try:
        report = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise MailError("The mail server gave an answer the panel could not read.", status=502) from exc
    keys = {str(domain): str(key) for domain, key in (report.get("dkim") or {}).items()}
    changed = False
    for row in db.query(MailDomain).filter(MailDomain.domain.in_(list(keys) or [""])).all():
        if keys.get(row.domain) and row.dkim_public != keys[row.domain]:
            row.dkim_public = keys[row.domain]
            changed = True
    if changed:
        db.flush()
    return report


def sync_quietly(db: Session) -> dict | None:
    """sync() for the places that must not fail because of mail: startup, a
    restore, deleting a user."""
    try:
        if not active():
            return None
        report = sync(db)
        db.commit()
        publish_all_quietly(db)
        return report
    except Exception:  # noqa: BLE001 - mail is never the reason something else fails
        logger.warning("Mail sync failed", exc_info=True)
        db.rollback()
        return None


# --- server settings and relays (administrators) ----------------------------------------------------

RELAY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RELAY_TLS = ("starttls", "ssl", "none")
MAX_RELAYS = 20


def _stored() -> dict:
    data = panel_settings._read_raw_lenient().get(SETTINGS_KEY)
    data = dict(data) if isinstance(data, dict) else {}
    # The first version of this addon kept a "spam" section and one "relay".
    old_spam = data.get("spam")
    if isinstance(old_spam, dict):
        data.setdefault("spam_enabled", old_spam.get("enabled", True) is not False)
        data.setdefault("allow", old_spam.get("allow") or [])
        data.setdefault("spam_header_score", old_spam.get("junk_score", 6.0))
        data.setdefault("spam_reject_score", old_spam.get("reject_score", 15.0))
    return data


def _store(data: dict) -> None:
    raw = panel_settings._read_raw()
    data = {key: value for key, value in data.items() if key not in ("spam", "relay")}
    raw[SETTINGS_KEY] = data
    panel_settings._write_raw(raw)


def current_settings() -> dict:
    stored = _stored()
    merged = dict(SETTINGS_DEFAULTS)
    for key in SETTINGS_DEFAULTS:
        if key in stored:
            merged[key] = stored[key]
    merged["allow"] = [str(item) for item in stored.get("allow") or [] if str(item).strip()]
    merged["block"] = [str(item) for item in stored.get("block") or [] if str(item).strip()]
    raw_limits = stored.get("domain_rate_limits")
    domain_limits: dict[str, int] = {}
    if isinstance(raw_limits, dict):
        for k, v in raw_limits.items():
            try:
                d = normalize_domain(str(k))
                domain_limits[d] = max(0, min(100000, int(v)))
            except (MailError, TypeError, ValueError):
                continue
    merged["domain_rate_limits"] = domain_limits
    return merged


def _split_allow(entries, label: str = "allowlist") -> tuple[list[str], list[str], list[str]]:
    """An allowlist or blocklist as typed -> (addresses, domains, the cleaned list)."""
    senders, domains, cleaned = [], [], []
    for entry in entries or []:
        item = str(entry).strip().lower()
        if not item or item in cleaned:
            continue
        if "@" in item:
            if not ADDRESS_RE.fullmatch(item):
                raise MailError(f"Each {label} line is an email address or a domain, such as friend@example.com or example.com.")
            senders.append(item)
        else:
            try:
                item = normalize_domain(item)
            except MailError as exc:
                raise MailError(f"Each {label} line is an email address or a domain, such as friend@example.com or example.com.") from exc
            domains.append(item)
        cleaned.append(item)
    if len(cleaned) > MAX_ALLOW:
        raise MailError(f"The {label} is limited to 5000 lines.")
    return senders, domains, cleaned


def save_settings(payload: dict) -> dict:
    stored = _stored()
    merged = current_settings()

    def whole(key, low, high, message):
        try:
            value = int(payload[key])
        except (TypeError, ValueError) as exc:
            raise MailError(message) from exc
        if not low <= value <= high:
            raise MailError(message)
        return value

    def score(key):
        try:
            value = float(payload[key])
        except (TypeError, ValueError) as exc:
            raise MailError("A spam score is a number from 1 to 100.") from exc
        if not 1 <= value <= 100:
            raise MailError("A spam score is a number from 1 to 100.")
        return round(value, 2)

    if "auth_rate_per_hour" in payload:
        merged["auth_rate_per_hour"] = whole("auth_rate_per_hour", 0, 100000, "A sending limit is 0 (none) to 100000.")
    if "local_rate_per_hour" in payload:
        merged["local_rate_per_hour"] = whole("local_rate_per_hour", 0, 100000, "A sending limit is 0 (none) to 100000.")
    if "domain_rate_per_hour" in payload:
        merged["domain_rate_per_hour"] = whole("domain_rate_per_hour", 0, 100000, "A domain sending limit is 0 (none) to 100000.")
    if "auto_hold_on_rate_exceed" in payload:
        merged["auto_hold_on_rate_exceed"] = bool(payload["auto_hold_on_rate_exceed"])
    if "max_message_mb" in payload:
        merged["max_message_mb"] = whole("max_message_mb", 1, 200, "The largest message is 1 to 200 MB.")
    if "default_quota_mb" in payload:
        merged["default_quota_mb"] = whole("default_quota_mb", 1, MAX_USER_QUOTA_MB, "The size of a new mailbox is 1 to 51200 MB.")
    if "dkim_rotate_days" in payload:
        merged["dkim_rotate_days"] = whole("dkim_rotate_days", 0, 3650, "DKIM keys rotate every 0 (never on their own) to 3650 days.")
        if 0 < merged["dkim_rotate_days"] < 30:
            raise MailError("Rotate DKIM keys every 30 days or more, or 0 for only by hand.")
    if "dkim_retire_days" in payload:
        merged["dkim_retire_days"] = whole("dkim_retire_days", 1, 60, "A replaced DKIM key stays published 1 to 60 days.")
    if "spam_header_score" in payload:
        merged["spam_header_score"] = score("spam_header_score")
    if "spam_rewrite_score" in payload:
        merged["spam_rewrite_score"] = score("spam_rewrite_score")
    if "spam_reject_score" in payload:
        merged["spam_reject_score"] = score("spam_reject_score")
    if merged["spam_reject_score"] <= merged["spam_header_score"]:
        raise MailError("The reject score must be higher than the Junk score.")
    if "spam_subject_tag" in payload:
        tag = str(payload["spam_subject_tag"] or "[SPAM]").strip()[:32]
        merged["spam_subject_tag"] = tag or "[SPAM]"
    if "greylisting" in payload:
        merged["greylisting"] = bool(payload["greylisting"])
    if "spam_enabled" in payload:
        merged["spam_enabled"] = bool(payload["spam_enabled"])
    if "allow" in payload:
        _, _, merged["allow"] = _split_allow(payload["allow"], "allowlist")
    if "block" in payload:
        _, _, merged["block"] = _split_allow(payload["block"], "blocklist")
    if "domain_rate_limits" in payload and isinstance(payload["domain_rate_limits"], dict):
        clean_limits: dict[str, int] = {}
        for k, v in payload["domain_rate_limits"].items():
            d = normalize_domain(str(k))
            try:
                limit_val = int(v)
            except (TypeError, ValueError) as exc:
                raise MailError("A domain rate limit must be an integer between 0 and 100000.") from exc
            if not 0 <= limit_val <= 100000:
                raise MailError("A domain rate limit must be between 0 and 100000.")
            clean_limits[d] = limit_val
        merged["domain_rate_limits"] = clean_limits
    stored.update(merged)
    _store(stored)
    if active():
        apply_settings()
    return current_settings()


def _relays() -> list[dict]:
    relays = _stored().get("relays")
    return [dict(item) for item in relays if isinstance(item, dict) and RELAY_ID_RE.fullmatch(str(item.get("id") or ""))] \
        if isinstance(relays, list) else []


def relay_out(db: Session, relay: dict, default: str = "") -> dict:
    return {
        "id": relay["id"],
        "name": relay.get("name") or relay["id"],
        "host": relay.get("host") or "",
        "port": int(relay.get("port") or 587),
        "tls": relay.get("tls") or "starttls",
        "username": relay.get("username") or "",
        "password_set": bool(relay.get("password")),
        "spf_include": relay.get("spf_include") or "",
        "dns_records": list(relay.get("dns_records") or []),
        "default": relay["id"] == default,
        "domains": [row.domain for row in db.query(MailDomain).filter(MailDomain.relay == relay["id"]).all()],
    }


def list_relays(db: Session) -> dict:
    default = _stored().get("default_relay") or ""
    return {"relays": [relay_out(db, relay, default) for relay in _relays()], "default_relay": default}


def save_relay(db: Session, payload: dict, relay_id: str | None = None) -> dict:
    stored = _stored()
    relays = _relays()
    current = next((relay for relay in relays if relay["id"] == relay_id), None) if relay_id else None
    if relay_id and current is None:
        raise MailError("There is no such relay.", status=404)
    if not relay_id and len(relays) >= MAX_RELAYS:
        raise MailError("At most 20 relays.")
    relay = dict(current or {})
    name = " ".join(str(payload.get("name") or "").split())
    if not 1 <= len(name) <= 64:
        raise MailError("Give the relay a name of at most 64 characters.")
    host = str(payload.get("host") or "").strip().lower().rstrip(".")
    try:
        port = int(payload.get("port") or 587)
    except (TypeError, ValueError) as exc:
        raise MailError("The relay port is a number from 1 to 65535.") from exc
    if not 1 <= port <= 65535:
        raise MailError("The relay port is a number from 1 to 65535.")
    tls = str(payload.get("tls") or ("ssl" if port == 465 else "starttls"))
    if tls not in RELAY_TLS:
        raise MailError("The relay's TLS is STARTTLS, SSL or none.")
    try:
        ipaddress.IPv4Address(host)
        if tls != "none":
            raise MailError("A relay reached over TLS is given by name, such as smtp.example.com: its certificate is checked against it.")
    except ipaddress.AddressValueError:
        if not HOST_RE.fullmatch(host):
            raise MailError("The relay host is a name such as smtp.example.com.") from None
    username = str(payload.get("username") or "").strip()
    if len(username) > 255 or any(ord(char) < 32 for char in username):
        raise MailError("That relay user name cannot be used.")
    old_default_spf = default_spf()
    relay.update(name=name, host=host, port=port, tls=tls, username=username,
                 spf_include=normalize_spf_include(payload.get("spf_include") or ""))
    records = payload.get("dns_records") or []
    if not isinstance(records, list) or len(records) > MAX_RELAY_RECORDS:
        raise MailError("A relay's DNS template has at most 10 records.")
    relay["dns_records"] = [normalize_dns_record(item) for item in records]
    password = payload.get("password")
    if not username:
        relay.pop("password", None)
    elif password:
        password = str(password)
        if len(password) > 255 or password != password.strip() or any(ord(char) < 32 for char in password):
            raise MailError("That relay password cannot be used; it cannot start or end with a space.")
        relay["password"] = encrypt(password)
    elif not relay.get("password"):
        raise MailError("Enter the relay's password, or leave the user name empty for a relay without a login.")
    # Exim finds a relay's login by its host, so one host carries one login.
    if username and any(item["host"] == host and item.get("username") and item["id"] != relay.get("id") for item in relays):
        raise MailError("Another relay already signs in to that host.", status=409)
    if not relay.get("id"):
        relay["id"] = "r" + secrets.token_hex(4)
        relays.append(relay)
    else:
        relays = [relay if item["id"] == relay["id"] else item for item in relays]
    stored["relays"] = relays
    if payload.get("make_default"):
        stored["default_relay"] = relay["id"]
    _store(stored)
    _relays_changed(db, old_default_spf)
    return relay_out(db, relay, stored.get("default_relay") or "")


def delete_relay(db: Session, relay_id: str) -> str:
    stored = _stored()
    relays = _relays()
    relay = next((item for item in relays if item["id"] == relay_id), None)
    if relay is None:
        raise MailError("There is no such relay.", status=404)
    old_default_spf = default_spf()
    stored["relays"] = [item for item in relays if item["id"] != relay_id]
    if stored.get("default_relay") == relay_id:
        stored["default_relay"] = ""
    # Domains that used it follow the default again.
    for row in db.query(MailDomain).filter(MailDomain.relay == relay_id).all():
        row.relay = ""
    db.commit()
    _store(stored)
    _relays_changed(db, old_default_spf)
    return relay.get("name") or relay_id


def set_default_relay(db: Session, relay_id: str) -> str:
    relay_id = relay_id or ""
    if relay_id and relay_id not in {relay["id"] for relay in _relays()}:
        raise MailError("There is no such relay.", status=404)
    old_default_spf = default_spf()
    stored = _stored()
    stored["default_relay"] = relay_id
    _store(stored)
    _relays_changed(db, old_default_spf)
    return relay_id


def _relays_changed(db: Session, old_default_spf: str) -> None:
    """The relays reach the mail server, and zones still on the server's
    previous SPF record follow the new one."""
    if active():
        apply_settings()
        sync(db)
        db.commit()
        for row in db.query(MailDomain).all():
            publish_dns_quietly(db, row)
    new_default_spf = default_spf()
    if new_default_spf != old_default_spf:
        from app.services import dns

        try:
            dns.replace_spf(db, old_default_spf, new_default_spf)
        except (dns.DnsError, dns.DnsInputError) as exc:
            logger.warning("SPF not updated in the zones: %s", exc)


def helper_settings() -> dict:
    """What mail-configure receives, relay passwords in clear."""
    values = current_settings()
    senders, domains, _ = _split_allow(values["allow"], "allowlist")
    block_senders, block_domains, _ = _split_allow(values.get("block") or [], "blocklist")
    relays = []
    for relay in _relays():
        password = ""
        if relay.get("username") and relay.get("password"):
            try:
                password = decrypt(relay["password"])
            except RuntimeError:
                password = ""
        relays.append({"id": relay["id"], "host": relay.get("host") or "", "port": int(relay.get("port") or 587),
                       "tls": relay.get("tls") or "starttls", "username": relay.get("username") or "",
                       "password": password})
    return {
        "auth_rate_per_hour": int(values["auth_rate_per_hour"]),
        "local_rate_per_hour": int(values["local_rate_per_hour"]),
        "domain_rate_per_hour": int(values.get("domain_rate_per_hour", 500)),
        "auto_hold_on_rate_exceed": bool(values.get("auto_hold_on_rate_exceed", True)),
        "domain_rate_limits": dict(values.get("domain_rate_limits") or {}),
        "max_message_mb": int(values["max_message_mb"]),
        "spam_enabled": bool(values["spam_enabled"]),
        "spam_header_score": float(values["spam_header_score"]),
        "spam_rewrite_score": float(values.get("spam_rewrite_score", 8.0)),
        "spam_reject_score": float(values["spam_reject_score"]),
        "spam_subject_tag": str(values.get("spam_subject_tag") or "[SPAM]"),
        "greylisting": bool(values["greylisting"]),
        "allow_senders": senders,
        "allow_domains": domains,
        "block_senders": block_senders,
        "block_domains": block_domains,
        "relays": relays,
    }


def apply_settings() -> None:
    _helper("mail-configure", input=json.dumps(helper_settings()), sensitive=True, timeout=300)


def relay_test(to: str) -> list[str]:
    """Send one message now and return what Exim logged for it."""
    address = (to or "").strip()
    if not ADDRESS_RE.fullmatch(address):
        raise MailError("Enter the address to send the test message to.")
    result = _helper("mail-relay-test", address, timeout=150)
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


# --- logs, queue and Rspamd (administrators) -------------------------------------------------------

def _filter_lines(text: str, q: str, limit: int) -> list[str]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    term = (q or "").strip().lower()
    if term:
        lines = [line for line in lines if term in line.lower()]
    return lines[-limit:]


def mail_log(lines: int = 200, q: str = "") -> list[str]:
    _require_active()
    count = max(1, min(int(lines or 200), 5000))
    # A filter looks further back than the lines it shows.
    result = _helper("mail-log", str(5000 if q else count))
    return _filter_lines(result.stdout, q, count)


def rspamd_log(lines: int = 300, q: str = "") -> list[str]:
    _require_active()
    count = max(1, min(int(lines or 300), 5000))
    result = _helper("mail-rspamd-log", str(5000 if q else count))
    return _filter_lines(result.stdout, q, count)


def queue_size() -> int | None:
    if not active():
        return None
    result = shell.privileged("mail-queue", check=False, fallback=["bash", "-lc", "echo 0"])
    text = (result.stdout or "").strip()
    return int(text) if result.returncode == 0 and text.isdigit() else None


# The queue: listed from `exim -bp`, each message acted on by its id. Exim
# 4.97 (Ubuntu 24.04) writes ids as 6-11-4 characters, older ones 6-6-2.
QUEUE_ID_RE = re.compile(r"^[0-9A-Za-z]{6}-(?:[0-9A-Za-z]{11}-[0-9A-Za-z]{4}|[0-9A-Za-z]{6}-[0-9A-Za-z]{2})$")
QUEUE_ACTIONS = ("retry", "freeze", "thaw", "delete")
MAX_QUEUE_BATCH = 100
_QUEUE_HEAD_RE = re.compile(
    r"^\s*(?P<age>\d+[smhdw])\s+(?P<size>\d+(?:\.\d+)?[KMG]?)\s+(?P<id>\S+)\s+<(?P<sender>[^>]*)>"
    r"(?P<frozen>\s+\*\*\* frozen \*\*\*)?\s*$")
_QUEUE_RCPT_RE = re.compile(r"^\s+(?:(?P<mark>\+?D)\s+)?(?P<address>\S+)\s*$")
_AGE_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_queue(text: str) -> list[dict]:
    """`exim -bp` output -> one dict a message: id, age (as printed and in
    seconds), size, sender ("" for a bounce), frozen, recipients with whether
    each is delivered. Lines that fit nothing are skipped."""
    messages: list[dict] = []
    current = None
    for line in (text or "").splitlines():
        if not line.strip():
            current = None
            continue
        head = _QUEUE_HEAD_RE.match(line)
        if head and QUEUE_ID_RE.fullmatch(head.group("id")):
            age = head.group("age")
            current = {"id": head.group("id"), "age": age, "age_seconds": int(age[:-1]) * _AGE_SECONDS[age[-1]],
                       "size": head.group("size"), "sender": head.group("sender"),
                       "frozen": bool(head.group("frozen")), "recipients": []}
            messages.append(current)
            continue
        rcpt = _QUEUE_RCPT_RE.match(line)
        if current is not None and rcpt:
            current["recipients"].append({"address": rcpt.group("address"), "delivered": bool(rcpt.group("mark"))})
    for message in messages:
        message["pending"] = sum(1 for item in message["recipients"] if not item["delivered"])
    return messages


def queue_list(q: str = "", state: str = "", page: int = 1, per_page: int = 50) -> dict:
    """The queue, oldest first, searched by id, sender or recipient; state
    "frozen" (held) or "active" narrows it."""
    _require_active()
    result = _helper("mail-queue-list", timeout=120)
    messages = parse_queue(result.stdout)
    term = (q or "").strip().lower()
    if term:
        messages = [item for item in messages if term in item["id"].lower() or term in item["sender"].lower()
                    or any(term in rcpt["address"].lower() for rcpt in item["recipients"])]
    if state == "frozen":
        messages = [item for item in messages if item["frozen"]]
    elif state == "active":
        messages = [item for item in messages if not item["frozen"]]
    messages.sort(key=lambda item: -item["age_seconds"])
    page, per_page = _page(page, per_page)
    return {"items": messages[(page - 1) * per_page: page * per_page], "total": len(messages),
            "frozen": sum(1 for item in messages if item["frozen"]), "page": page, "per_page": per_page}


def _queue_id(value: str) -> str:
    qid = str(value or "").strip()
    if not QUEUE_ID_RE.fullmatch(qid):
        raise MailError("That is not a message id of the mail queue.")
    return qid


def queue_message(qid: str) -> dict:
    """A queued message's envelope and headers - never its body."""
    _require_active()
    result = _helper("mail-queue-show", _queue_id(qid), timeout=60)
    return {"id": qid, "headers": result.stdout or ""}


def queue_action(action: str, ids) -> list[str]:
    """retry, freeze (hold), thaw (release) or delete messages; Exim's lines."""
    _require_active()
    if action not in QUEUE_ACTIONS:
        raise MailError("The queue actions are retry, freeze, thaw and delete.")
    clean = []
    for value in ids or []:
        qid = _queue_id(value)
        if qid not in clean:
            clean.append(qid)
    if not clean:
        raise MailError("Choose at least one message.")
    if len(clean) > MAX_QUEUE_BATCH:
        raise MailError(f"At most {MAX_QUEUE_BATCH} messages at a time.")
    result = _helper("mail-queue-action", action, *clean, timeout=120)
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


def queue_retry_all() -> None:
    _require_active()
    _helper("mail-queue-retry-all", timeout=60)


def _controller(path: str):
    try:
        key = RSPAMD_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise MailError("The spam filter is not set up on this server. Reinstall the Email addon.", status=503) from exc
    request = urllib.request.Request(RSPAMD_CONTROLLER + path, headers={"Password": key})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed loopback URL
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise MailError("The spam filter is not answering. Check that Rspamd is running.", status=502) from exc


def rspamd_stat() -> dict:
    _require_active()
    data = _controller("/stat")
    data = data if isinstance(data, dict) else {}
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    return {
        "scanned": int(data.get("scanned") or 0),
        "spam": int(data.get("spam_count") or 0),
        "ham": int(data.get("ham_count") or 0),
        "learned": int(data.get("learned") or 0),
        "uptime": int(data.get("uptime") or 0),
        "version": str(data.get("version") or ""),
        "actions": {str(key): int(value or 0) for key, value in actions.items()},
    }


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)] if value else []


def _history_row(row: dict) -> dict:
    symbols = row.get("symbols") if isinstance(row.get("symbols"), dict) else {}
    ranked = []
    for name, info in symbols.items():
        info = info if isinstance(info, dict) else {}
        try:
            value = round(float(info.get("score") or 0), 2)
        except (TypeError, ValueError):
            value = 0.0
        ranked.append({"name": str(name), "score": value})
    ranked.sort(key=lambda item: -abs(item["score"]))
    sender_mime = _as_list(row.get("sender_mime"))
    try:
        stamp = int(float(row.get("unix_time") or 0))
    except (TypeError, ValueError):
        stamp = 0

    def number(key):
        try:
            return round(float(row.get(key) or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    return {
        "time": stamp,
        "ip": str(row.get("ip") or ""),
        "from": sender_mime[0] if sender_mime else str(row.get("sender_smtp") or ""),
        "envelope_from": str(row.get("sender_smtp") or ""),
        "to": (_as_list(row.get("rcpt_mime")) or _as_list(row.get("rcpt_smtp")))[:10],
        "subject": str(row.get("subject") or ""),
        "score": number("score"),
        "required": number("required_score"),
        "action": str(row.get("action") or ""),
        "symbols": ranked[:15],
        "size": int(number("size")),
        "user": str(row.get("user") or ""),
        "message_id": str(row.get("message-id") or ""),
        "allowed": any(item["name"].startswith("spanel_ALLOW") for item in ranked),
        "blocked": any(item["name"].startswith("spanel_BLOCK") for item in ranked),
    }


def rspamd_history(page: int = 1, per_page: int = 50, q: str = "", action: str = "") -> dict:
    _require_active()
    data = _controller("/history")
    rows = [_history_row(row) for row in ((data or {}).get("rows") or []) if isinstance(row, dict)]
    term = (q or "").strip().lower()
    if term:
        rows = [row for row in rows if term in " ".join(
            [row["from"], row["envelope_from"], " ".join(row["to"]), row["subject"], row["ip"], row["message_id"]]).lower()]
    if action:
        rows = [row for row in rows if row["action"] == action]
    rows.sort(key=lambda row: -row["time"])
    page, per_page = _page(page, per_page)
    return {"items": rows[(page - 1) * per_page: page * per_page], "total": len(rows), "page": page, "per_page": per_page}


def allow_sender(value: str) -> dict:
    """Put a sender or a domain on the spam filter's allowlist."""
    settings = current_settings()
    clean_val = str(value or "").strip().lower()
    block_list = [item for item in (settings.get("block") or []) if item.lower() != clean_val]
    return save_settings({"allow": settings["allow"] + [value], "block": block_list})


def block_sender(value: str) -> dict:
    """Put a sender or a domain on the spam filter's blocklist (blacklist)."""
    settings = current_settings()
    clean_val = str(value or "").strip().lower()
    allow_list = [item for item in (settings.get("allow") or []) if item.lower() != clean_val]
    return save_settings({"block": (settings.get("block") or []) + [value], "allow": allow_list})


# --- webmail -------------------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mailbox_generation(account: MailAccount) -> str:
    """Unique generation token for a mailbox row (id:owner_id:created_ts)."""
    created_dt = getattr(account, "created_at", None)
    ts = int(created_dt.timestamp()) if created_dt is not None else 0
    return f"{account.id or 0}:{account.owner_id or 0}:{ts}"


def sso_token(address: str, *, now: float | None = None, key: str | None = None, gen: str | None = None) -> str:
    """What the webmail's /api/auth/sso accepts: payload.signature, base64url."""
    if key is None:
        try:
            key = SSO_KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MailError("The webmail is not set up on this server. Reinstall the Email addon.", status=503) from exc
    if not key:
        raise MailError("The webmail is not set up on this server. Reinstall the Email addon.", status=503)
    issued = int(now if now is not None else time.time())
    payload: dict = {"email": address, "exp": issued + SSO_TOKEN_SECONDS, "nonce": secrets.token_urlsafe(16)}
    if gen:
        payload["gen"] = str(gen)
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def sso_url(db: Session, actor: User, account_id: int) -> str:
    _require_active()
    account = get_account(db, actor, account_id)
    if not account.enabled:
        raise MailError("This mailbox is suspended.", status=409)
    row = _domain_row(db, account.domain)
    return f"{webmail_url(row)}/api/auth/sso?token={sso_token(account.address, gen=mailbox_generation(account))}"


# --- backups ---------------------------------------------------------------------------------------

PEM_KEY_RE = re.compile(r"^-----BEGIN (?:RSA )?PRIVATE KEY-----\n[A-Za-z0-9+/=\n]{100,12000}\n-----END (?:RSA )?PRIVATE KEY-----\n?$")


def _dkim_keys_for_backup(row: MailDomain) -> dict[str, str]:
    """The domain's DKIM private keys in use ({selector: PEM}), so a restore
    signs with the keys its DNS already publishes instead of new ones."""
    if not active():
        return {}
    result = shell.privileged("mail-dkim-export", helper_args=[row.domain], check=False, sensitive=True,
                              fallback=["bash", "-lc", "echo '{}'"])
    try:
        keys = json.loads((result.stdout or "").strip().splitlines()[-1]) if result.returncode == 0 else {}
    except (IndexError, ValueError):
        keys = {}
    wanted = {row.dkim_selector or DKIM_SELECTOR, row.dkim_next_selector, row.dkim_old_selector} - {""}
    return {str(selector): str(pem) for selector, pem in (keys or {}).items()
            if selector in wanted and PEM_KEY_RE.fullmatch(str(pem).replace("\r\n", "\n"))}


def backup_manifest(db: Session, user: User) -> dict:
    """An account's mail domains (with their DKIM keys), mailboxes (password
    hashes), forwarders and autoresponders."""
    domains = db.query(MailDomain).filter(MailDomain.owner_id == user.id).order_by(MailDomain.domain).all()
    names = [row.domain for row in domains]
    return {
        "domains": [{"domain": row.domain, "catch_all": row.catch_all or "", "relay": row.relay or "",
                     "dkim_selector": row.dkim_selector or DKIM_SELECTOR,
                     "dkim_keys": _dkim_keys_for_backup(row),
                     "dmarc": dmarc_policy(row) if row.dmarc_policy else None,
                     "tls_rpt": tls_rpt_addresses(row)}
                    for row in domains],
        "autoresponders": [{"local_part": row.local_part, "domain": row.domain, "subject": row.subject,
                            "body": row.body, "enabled": bool(row.enabled), "start_date": row.start_date or "",
                            "end_date": row.end_date or "", "interval_days": int(row.interval_days or 1)}
                           for row in db.query(MailAutoresponder).filter(MailAutoresponder.domain.in_(names or [""]))
                           .order_by(MailAutoresponder.domain, MailAutoresponder.local_part).all()],
        "mailboxes": [{"local_part": account.local_part, "domain": account.domain, "password_hash": account.password_hash,
                       "quota_mb": account.quota_mb, "enabled": bool(account.enabled)}
                      for account in db.query(MailAccount).filter(MailAccount.owner_id == user.id)
                      .order_by(MailAccount.domain, MailAccount.local_part).all()],
        "forwarders": [{"local_part": row.local_part, "domain": row.domain, "destinations": row.destination_list}
                       for row in db.query(MailForwarder).filter(MailForwarder.domain.in_(names or [""]))
                       .order_by(MailForwarder.domain, MailForwarder.local_part).all()],
    }


def restore_manifest(db: Session, user: User, section, stage_mail) -> list[str]:
    """Bring back a user backup's mail: domains, mailboxes, forwarders, mail.

    stage_mail(domain, local) returns a staged directory holding that
    mailbox's mail from the archive, or None. A domain or address that
    belongs to another account here is refused, as a website would be. Older
    archives list only mailboxes; their domains are made for them.
    """
    if isinstance(section, list):
        section = {"mailboxes": section}
    section = section if isinstance(section, dict) else {}
    restored = []
    wanted_domains = {str(item.get("domain") or "").lower(): item for item in section.get("domains") or []
                      if isinstance(item, dict)}
    for entry in section.get("mailboxes") or []:
        wanted_domains.setdefault(str(entry.get("domain") or "").lower(), {"domain": entry.get("domain")})
    for name, entry in sorted(wanted_domains.items()):
        try:
            name = normalize_domain(name)
        except MailError as exc:
            raise ValueError(f"Invalid mail domain in backup: {exc.message}") from exc
        row = _domain_row(db, name)
        if row is not None and row.owner_id != user.id:
            raise ValueError(f"Mail domain already belongs to another user: {name}")
        if row is None:
            row = MailDomain(domain=name, owner_id=user.id, catch_all="", dkim_public="", webmail_host=False,
                             relay="")
            db.add(row)
        catch_all = str(entry.get("catch_all") or "")
        row.catch_all = normalize_destination(catch_all) if catch_all else ""
        relay = str(entry.get("relay") or "")
        row.relay = relay if relay == "direct" or relay in {item["id"] for item in _relays()} else ""
        if isinstance(entry.get("tls_rpt"), list):
            try:
                row.tls_rpt = "\n".join(_report_addresses(entry["tls_rpt"], "TLS report"))
            except MailError as exc:
                raise ValueError(f"Invalid TLS report address for {name} in backup: {exc.message}") from exc
        if isinstance(entry.get("dmarc"), dict):
            try:
                row.dmarc_policy = json.dumps(normalize_dmarc(entry["dmarc"]), sort_keys=True)
            except MailError as exc:
                raise ValueError(f"Invalid DMARC policy for {name} in backup: {exc.message}") from exc
        # The key its DNS publishes comes back with it; without one (an older
        # archive) mail-sync makes a new key and the DNS page shows the record.
        keys = entry.get("dkim_keys") if isinstance(entry.get("dkim_keys"), dict) else {}
        selector = str(entry.get("dkim_selector") or DKIM_SELECTOR)
        pem = str(keys.get(selector) or "").replace("\r\n", "\n")
        if DKIM_SELECTOR_RE.fullmatch(selector) and PEM_KEY_RE.fullmatch(pem):
            public = _import_dkim_key(name, selector, pem)
            if public:
                row.dkim_selector, row.dkim_public = selector, public
                row.dkim_next_selector = row.dkim_next_public = row.dkim_old_selector = row.dkim_old_public = ""
                row.dkim_state, row.dkim_state_at = "", None
        db.flush()
    for entry in section.get("mailboxes") or []:
        try:
            local = normalize_local(str(entry.get("local_part") or ""))
            domain = normalize_domain(str(entry.get("domain") or ""))
        except MailError as exc:
            raise ValueError(f"Invalid mailbox in backup: {exc.message}") from exc
        secret = str(entry.get("password_hash") or "")
        if not SHA512_RE.fullmatch(secret):
            raise ValueError(f"Invalid password hash for {local}@{domain} in backup")
        existing = db.query(MailAccount).filter(MailAccount.domain == domain, MailAccount.local_part == local).first()
        if existing is not None and existing.owner_id != user.id:
            raise ValueError(f"Mailbox already belongs to another user: {local}@{domain}")
        quota = entry.get("quota_mb")
        quota = int(quota) if isinstance(quota, int) and 0 <= quota <= MAX_QUOTA_MB else DEFAULT_QUOTA_MB
        if existing is None:
            existing = MailAccount(owner_id=user.id, domain=domain, local_part=local, password_hash=secret,
                                   quota_mb=quota, enabled=bool(entry.get("enabled", True)), created_at=datetime.utcnow())
            db.add(existing)
        else:
            existing.password_hash = secret
            existing.quota_mb = quota
            existing.enabled = bool(entry.get("enabled", True))
        db.flush()
        staged = stage_mail(domain, local)
        if staged:
            shell.privileged("mail-import", helper_args=[linux_user_of(user), domain, local, str(staged)],
                             fallback=["true"], timeout=1800)
        restored.append(f"{local}@{domain}")
    for entry in section.get("forwarders") or []:
        try:
            local = normalize_local(str(entry.get("local_part") or ""))
            domain = normalize_domain(str(entry.get("domain") or ""))
            targets = normalize_destinations(entry.get("destinations") or [], own_address=f"{local}@{domain}")
        except MailError as exc:
            raise ValueError(f"Invalid forwarder in backup: {exc.message}") from exc
        row = db.query(MailForwarder).filter(MailForwarder.domain == domain, MailForwarder.local_part == local).first()
        if row is None:
            db.add(MailForwarder(domain=domain, local_part=local, destinations="\n".join(targets)))
        else:
            row.destinations = "\n".join(targets)
        db.flush()
    owned = {row.domain for row in db.query(MailDomain).filter(MailDomain.owner_id == user.id).all()}
    for entry in section.get("autoresponders") or []:
        if not isinstance(entry, dict):
            continue
        try:
            local = normalize_local(str(entry.get("local_part") or ""))
            domain = normalize_domain(str(entry.get("domain") or ""))
            values = normalize_autoresponder(entry.get("subject"), entry.get("body"), entry.get("start_date"),
                                             entry.get("end_date"), entry.get("interval_days") or 1)
        except MailError as exc:
            raise ValueError(f"Invalid autoresponder in backup: {exc.message}") from exc
        if domain not in owned:
            raise ValueError(f"Autoresponder outside the account's mail domains: {local}@{domain}")
        row = db.query(MailAutoresponder).filter(MailAutoresponder.domain == domain,
                                                 MailAutoresponder.local_part == local).first()
        if row is None:
            row = MailAutoresponder(domain=domain, local_part=local)
            db.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        row.enabled = bool(entry.get("enabled", True))
        db.flush()
    return restored


def _import_dkim_key(domain: str, selector: str, pem: str) -> str:
    """Put a DKIM private key from a backup in place; its public key, or ""."""
    result = shell.privileged("mail-dkim-import", helper_args=[domain, selector], input=pem, check=False,
                              sensitive=True, fallback=["true"])
    lines = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not lines or not re.fullmatch(r"[A-Za-z0-9+/=]{200,1000}", lines[-1]):
        logger.warning("DKIM key of %s not restored: %s", domain, _helper_message(result.stderr))
        return ""
    return lines[-1]


# --- disk space ------------------------------------------------------------------------------------

def client_settings(row: MailDomain | None = None) -> dict:
    host = client_host(row)
    return {"host": host, "imap_port": PORTS["imap"], "pop3_port": PORTS["pop3"], "smtp_port": PORTS["smtps"],
            "submission_port": PORTS["submission"], "webmail": webmail_url()}

# --- autoresponders ------------------------------------------------------------------------------
# A Sieve vacation in the mailbox: mail-sync hands the list to the helper,
# which renders each one (spanel_mailtool.render_vacation) into the mailbox's
# home, where Dovecot runs it on delivery (sieve_before2). Only a mailbox can
# answer: mail to a forwarder never reaches Dovecot.

AUTORESPONDER_SUBJECT_MAX = 255
AUTORESPONDER_BODY_MAX = 8000
AUTORESPONDER_DAYS = (1, 30)


def _clean_text(value, limit: int, multiline: bool, what: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip() if not multiline else text.strip("\n").rstrip()
    if not text:
        raise MailError(f"The autoresponder needs a {what}.")
    if len(text) > limit:
        raise MailError(f"The autoresponder's {what} is at most {limit} characters.")
    for char in text:
        code = ord(char)
        if (char == "\n" and multiline) or char == "\t":
            continue
        if code < 32 or code == 127 or 0x80 <= code < 0xA0 or char in "  ":
            raise MailError(f"The autoresponder's {what} cannot contain control characters.")
    return text


def _clean_date(value, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise MailError(f"The {what} date is a day such as 2026-12-24.") from exc
    if not 1900 <= parsed.year <= 2199:
        raise MailError(f"The {what} date is a day such as 2026-12-24.")
    return parsed.isoformat()


def normalize_autoresponder(subject, body, start_date="", end_date="", interval_days=1) -> dict:
    """An autoresponder as typed, checked the way the helper checks it."""
    start, end = _clean_date(start_date, "start"), _clean_date(end_date, "end")
    if start and end and end < start:
        raise MailError("The autoresponder ends before it starts.")
    try:
        days = int(interval_days if interval_days not in (None, "") else 1)
    except (TypeError, ValueError) as exc:
        raise MailError("Answer the same sender at most once every 1 to 30 days.") from exc
    if not AUTORESPONDER_DAYS[0] <= days <= AUTORESPONDER_DAYS[1]:
        raise MailError("Answer the same sender at most once every 1 to 30 days.")
    return {
        "subject": _clean_text(subject, AUTORESPONDER_SUBJECT_MAX, False, "subject"),
        "body": _clean_text(body, AUTORESPONDER_BODY_MAX, True, "message"),
        "start_date": start,
        "end_date": end,
        "interval_days": days,
    }


def autoresponder_state(row: MailAutoresponder) -> dict:
    """What mail-sync sends the helper for one autoresponder."""
    return {"address": row.address, "subject": row.subject, "body": row.body, "start": row.start_date or "",
            "end": row.end_date or "", "days": int(row.interval_days or 1)}


def _sync_or_rollback(db: Session) -> None:
    try:
        sync(db)
    except MailError:
        db.rollback()
        raise
    db.commit()


def create_autoresponder(db: Session, actor: User, domain_id: int, local_part: str, subject: str, body: str,
                         *, enabled: bool = True, start_date: str = "", end_date: str = "",
                         interval_days: int = 1) -> MailAutoresponder:
    _require_active()
    domain = get_domain(db, actor, domain_id)
    if not _is_admin(actor) and getattr(actor, "package", None) is not None:
        raw_ar = getattr(actor.package, "autoresponders_limit", None)
        ar_limit = int(-1 if raw_ar is None else raw_ar)
        if ar_limit == 0:
            raise MailError("Autoresponders are disabled for your hosting package (limit: 0).", status=403)
        if ar_limit > 0:
            my_domains = [d.domain for d in db.query(MailDomain).filter(MailDomain.owner_id == domain.owner_id).all()]
            used_ar = db.query(MailAutoresponder).filter(MailAutoresponder.domain.in_(my_domains or [""])).count()
            if used_ar >= ar_limit:
                raise MailError(f"Autoresponder limit reached ({used_ar}/{ar_limit}).", status=403)
    local = normalize_local(local_part)
    if db.query(MailAutoresponder).filter(MailAutoresponder.domain == domain.domain, MailAutoresponder.local_part == local).first():
        raise MailError("That address already has an autoresponder.", status=409)
    if not db.query(MailAccount).filter(MailAccount.domain == domain.domain, MailAccount.local_part == local).first():
        raise MailError("An autoresponder answers the mail of a mailbox: create the mailbox first.")
    values = normalize_autoresponder(subject, body, start_date, end_date, interval_days)
    row = MailAutoresponder(domain=domain.domain, local_part=local, enabled=bool(enabled), **values)
    db.add(row)
    db.flush()
    _sync_or_rollback(db)
    db.refresh(row)
    return row


def update_autoresponder(db: Session, actor: User, autoresponder_id: int, subject: str, body: str,
                         *, enabled: bool | None = None, start_date: str | None = None, end_date: str | None = None,
                         interval_days: int | None = None) -> MailAutoresponder:
    row = get_autoresponder(db, actor, autoresponder_id)
    values = normalize_autoresponder(
        subject, body,
        row.start_date if start_date is None else start_date,
        row.end_date if end_date is None else end_date,
        row.interval_days if interval_days is None else interval_days,
    )
    for key, value in values.items():
        setattr(row, key, value)
    if enabled is not None:
        row.enabled = bool(enabled)
    db.flush()
    _sync_or_rollback(db)
    return row


def get_autoresponder(db: Session, actor: User, autoresponder_id: int) -> MailAutoresponder:
    row = db.query(MailAutoresponder).filter(MailAutoresponder.id == autoresponder_id).first()
    domain = _domain_row(db, row.domain) if row else None
    if row is None or domain is None or (not _is_admin(actor) and domain.owner_id != actor.id):
        raise MailError("There is no such autoresponder.", status=404)
    return row

def delete_autoresponder(db: Session, actor: User, autoresponder_id: int) -> str:
    row = get_autoresponder(db, actor, autoresponder_id)
    address = row.address
    db.delete(row)
    db.flush()
    _sync_or_rollback(db)
    return address


def autoresponder_out(db: Session, row: MailAutoresponder, domains: dict[str, MailDomain]) -> dict:
    domain = domains.get(row.domain)
    today = datetime.now().date().isoformat()
    active_now = bool(row.enabled) and (not row.start_date or row.start_date <= today) \
        and (not row.end_date or today <= row.end_date)
    return {
        "id": row.id,
        "address": row.address,
        "domain": row.domain,
        "domain_id": domain.id if domain else None,
        "local_part": row.local_part,
        "subject": row.subject,
        "body": row.body,
        "enabled": bool(row.enabled),
        "start_date": row.start_date or "",
        "end_date": row.end_date or "",
        "interval_days": int(row.interval_days or 1),
        "active_now": active_now,
        "has_mailbox": db.query(MailAccount).filter(MailAccount.domain == row.domain,
                                                    MailAccount.local_part == row.local_part).first() is not None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_autoresponders(db: Session, actor: User, domain_id: int | None = None, q: str = "", page: int = 1, per_page: int = 50) -> dict:
    query = db.query(MailAutoresponder)
    if not _is_admin(actor):
        mine = [row.domain for row in db.query(MailDomain).filter(MailDomain.owner_id == actor.id)]
        query = query.filter(MailAutoresponder.domain.in_(mine or [""]))
    if domain_id:
        row = get_domain(db, actor, domain_id)
        query = query.filter(MailAutoresponder.domain == row.domain)
    term = (q or "").strip().lower()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(MailAutoresponder.local_part.like(like), MailAutoresponder.domain.like(like), MailAutoresponder.subject.like(like)))
    total = query.count()
    page, per_page = _page(page, per_page)
    rows = query.order_by(MailAutoresponder.domain, MailAutoresponder.local_part).offset((page - 1) * per_page).limit(per_page).all()
    domains = {row.domain: row for row in db.query(MailDomain).all()}
    return {"items": [autoresponder_out(db, row, domains) for row in rows], "total": total, "page": page, "per_page": per_page}
