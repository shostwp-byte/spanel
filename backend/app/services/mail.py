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
from datetime import datetime
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
DKIM_SELECTOR = "spanel"
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
            row.dkim_public = _dkim_public(row.domain)
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

def _dkim_public(domain: str, rotate: bool = False) -> str:
    args = [domain, "rotate"] if rotate else [domain]
    result = _helper("mail-dkim", *args)
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
    name, webmail = row.domain, bool(row.webmail_host)
    owner = db.query(User).filter(User.id == row.owner_id).first()
    db.query(MailAccount).filter(MailAccount.domain == name).delete(synchronize_session=False)
    db.query(MailForwarder).filter(MailForwarder.domain == name).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    sync(db)
    if webmail:
        shell.privileged("mail-webmail-host-remove", helper_args=[name], check=False, fallback=["true"])
    # Last, once nothing routes there any more.
    if owner is not None:
        shell.privileged("mail-purge-domain", helper_args=[linux_user_of(owner), name], check=False,
                         fallback=["true"], timeout=600)
    return name


def delete_for_owner(db: Session, owner: User) -> list[str]:
    """An account is being deleted: its mail goes with its home. The caller commits."""
    rows = db.query(MailDomain).filter(MailDomain.owner_id == owner.id).all()
    names = [row.domain for row in rows]
    for row in rows:
        if row.webmail_host:
            shell.privileged("mail-webmail-host-remove", helper_args=[row.domain], check=False, fallback=["true"])
        db.query(MailForwarder).filter(MailForwarder.domain == row.domain).delete(synchronize_session=False)
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
    _require_active()
    row = get_domain(db, actor, domain_id)
    row.dkim_public = _dkim_public(row.domain, rotate=True)
    db.commit()
    sync(db)
    publish_dns_quietly(db, row)
    return row


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
        "mailboxes": db.query(MailAccount).filter(MailAccount.domain == row.domain).count(),
        "forwarders": db.query(MailForwarder).filter(MailForwarder.domain == row.domain).count(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# --- mailboxes ------------------------------------------------------------------------------

def owner_mailbox_count(db: Session, owner_id: int) -> int:
    return db.query(MailAccount).filter(MailAccount.owner_id == owner_id).count()


def _limit(user: User) -> int | None:
    """A customer's mailbox limit; None for an administrator or Unlimited (-1)."""
    if _is_admin(user):
        return None
    val = getattr(user, "mail_accounts_limit", None)
    if val is None and getattr(user, "package", None) is not None:
        val = getattr(user.package, "mail_accounts_limit", None)
    if val is None:
        return None
    val_int = int(val)
    return None if val_int < 0 else val_int


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
        {"key": "dkim", "type": "TXT", "name": f"{DKIM_SELECTOR}._domainkey.{domain}",
         "value": f"v=DKIM1; k=rsa; p={row.dkim_public}"},
        {"key": "dmarc", "type": "TXT", "name": f"_dmarc.{domain}", "value": DEFAULT_DMARC},
    ]
    if relay:
        for index, item in enumerate(relay.get("dns_records") or []):
            records.append({
                "key": f"relay-{index}", "type": item["type"], "name": _fqdn(item["name"], domain).lower(),
                "value": item["value"].replace("{domain}", domain), "priority": item.get("priority"),
                "source": "relay", "relay": relay.get("name") or relay["id"],
            })
    if ip:
        records.append({"key": "webmail", "type": "A", "name": f"webmail.{domain}", "value": ip, "optional": True})
    return records


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
        elif key == "dkim":
            found = lookup(name, "TXT")
            ok = found is not None and any(row.dkim_public and row.dkim_public in t.replace(" ", "") for t in found)
        elif key == "dmarc":
            txt = lookup(name, "TXT")
            found = None if txt is None else [t for t in txt if t.upper().startswith("V=DMARC1")]
            ok = bool(found)
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


def publish_dns(db: Session, row: MailDomain, *, replace: bool) -> list[str]:
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
        return [item.rstrip(".").lower() for item in existing] == [content.rstrip(".").lower()]

    for record in dns_records(row):
        key, rtype = record["key"], record["type"]
        if not record["value"]:
            continue
        name = dns._absolute(record["name"])
        if rtype == "MX":
            content = f"{record.get('priority') if record.get('priority') is not None else 10} {dns._absolute(record['value'])}"
        elif rtype == "CNAME":
            content = dns._absolute(record["value"])
        elif rtype == "TXT":
            content = dns._txt(record["value"])
        else:
            content = record["value"]
        existing = current(name, rtype)
        if key == "dkim" or record.get("source") == "relay":
            if rtype == "TXT" and key != "dkim":
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
        elif not existing or (replace and not same(existing, content, rtype)):
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


def publish_dns_quietly(db: Session, row: MailDomain) -> list[str]:
    from app.services import dns

    try:
        return publish_dns(db, row, replace=False)
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
        "domains": [{"domain": row.domain, "catch_all": row.catch_all or ""} for row in domains],
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


def sso_token(address: str, *, now: float | None = None, key: str | None = None) -> str:
    """What the webmail's /api/auth/sso accepts: payload.signature, base64url."""
    if key is None:
        try:
            key = SSO_KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MailError("The webmail is not set up on this server. Reinstall the Email addon.", status=503) from exc
    if not key:
        raise MailError("The webmail is not set up on this server. Reinstall the Email addon.", status=503)
    issued = int(now if now is not None else time.time())
    body = _b64(json.dumps({"email": address, "exp": issued + SSO_TOKEN_SECONDS,
                            "nonce": secrets.token_urlsafe(16)}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def sso_url(db: Session, actor: User, account_id: int) -> str:
    _require_active()
    account = get_account(db, actor, account_id)
    if not account.enabled:
        raise MailError("This mailbox is suspended.", status=409)
    row = _domain_row(db, account.domain)
    return f"{webmail_url(row)}/api/auth/sso?token={sso_token(account.address)}"


# --- backups ---------------------------------------------------------------------------------------

def backup_manifest(db: Session, user: User) -> dict:
    """An account's mail domains, mailboxes (password hashes) and forwarders."""
    domains = db.query(MailDomain).filter(MailDomain.owner_id == user.id).order_by(MailDomain.domain).all()
    names = [row.domain for row in domains]
    return {
        "domains": [{"domain": row.domain, "catch_all": row.catch_all or "", "relay": row.relay or ""}
                    for row in domains],
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
    return restored


# --- disk space ------------------------------------------------------------------------------------

def client_settings() -> dict:
    host = _hostname_or_blank()
    return {"host": host, "imap_port": PORTS["imap"], "pop3_port": PORTS["pop3"], "smtp_port": PORTS["smtps"],
            "submission_port": PORTS["submission"], "webmail": webmail_url()}

# --- autoresponders ------------------------------------------------------------------------------

def create_autoresponder(db: Session, actor: User, domain_id: int, local_part: str, subject: str, body: str) -> MailAutoresponder:
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
    row = MailAutoresponder(domain=domain.domain, local_part=local, subject=subject, body=body)
    db.add(row)
    db.flush()
    try:
        sync(db)
    except Exception as exc:
        db.rollback()
        raise exc
    return row

def update_autoresponder(db: Session, actor: User, autoresponder_id: int, subject: str, body: str) -> MailAutoresponder:
    row = get_autoresponder(db, actor, autoresponder_id)
    row.subject = subject
    row.body = body
    db.flush()
    try:
        sync(db)
    except Exception as exc:
        db.rollback()
        raise exc
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
    try:
        sync(db)
    except Exception as exc:
        db.rollback()
        raise exc
    return address

def autoresponder_out(db: Session, row: MailAutoresponder, domains: dict[str, MailDomain]) -> dict:
    domain = domains.get(row.domain)
    return {
        "id": row.id,
        "address": row.address,
        "domain": row.domain,
        "local_part": row.local_part,
        "subject": row.subject,
        "body": row.body,
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
