import hashlib
import ipaddress
import logging
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.entities import ApiToken, DatabaseAccount, ProvisioningAccount, User, Website
from app.services import backup as backup_service, teardown
from app.services import mariadb, nginx, site_users, ssl, storage_quota, waf, wordpress

logger = logging.getLogger("spanel.provisioning")

# Requests per minute a token may make when it has no limit of its own. A
# billing system's cron sync (UsageUpdate, ListAccounts) is a handful of calls;
# this leaves plenty of room for bursts of orders while stopping a leaked
# token from hammering account creation.
DEFAULT_RATE_LIMIT_PER_MINUTE = 120
MAX_RATE_LIMIT_PER_MINUTE = 6000

# Fine-grained scopes. Each endpoint asks for exactly one of these.
SCOPES: dict[str, str] = {
    "plans:read": "List hosting packages",
    "accounts:read": "Read and list provisioned accounts",
    "accounts:write": "Create, suspend, unsuspend, terminate and modify accounts",
    "usage:read": "Read disk, bandwidth, website and database usage",
    "sso": "Create one-time login links for customer accounts",
    "sso:admin": "Create one-time login links for the administrator who issued the token",
    "tokens:rotate": "Rotate this token through the API",
}

# Tokens issued before the scopes above existed carry these two. They keep
# working with exactly the reach they always had: read covered plans, account
# lookups and usage; write covered every change and the customer login link.
# Neither ever covered admin login or self-rotation, so neither does now.
LEGACY_SCOPES: dict[str, frozenset[str]] = {
    "provisioning:read": frozenset({"plans:read", "accounts:read", "usage:read"}),
    "provisioning:write": frozenset({"accounts:write", "sso"}),
}


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str]:
    raw = f"bp_{secrets.token_urlsafe(48)}"
    return raw, hash_token(raw)


def normalize_scopes(raw: str) -> str:
    """Validate a comma-separated scope list and return it in canonical form."""
    items = []
    for item in (raw or "").split(","):
        scope = item.strip()
        if not scope:
            continue
        if scope not in SCOPES and scope not in LEGACY_SCOPES:
            raise ValueError(f"Unknown scope: {scope}")
        if scope not in items:
            items.append(scope)
    if not items:
        raise ValueError("At least one scope is required")
    return ",".join(items)


def token_scopes(token: ApiToken) -> set[str]:
    """The effective scopes of a token, legacy names expanded."""
    granted: set[str] = set()
    for item in (token.scopes or "").split(","):
        scope = item.strip()
        if not scope:
            continue
        granted.add(scope)
        granted |= LEGACY_SCOPES.get(scope, frozenset())
    return granted


def has_scope(token: ApiToken, scope: str) -> bool:
    return scope in token_scopes(token)


def normalize_allowed_ips(raw: str) -> str:
    """Validate an allowlist of addresses and CIDR ranges.

    Entries may be single addresses (203.0.113.7, 2001:db8::1) or networks
    (203.0.113.0/24). Host bits in a network are tolerated (203.0.113.7/24 is
    read as 203.0.113.0/24) because that is how people copy them.
    """
    items = []
    for item in (raw or "").replace("\n", ",").replace(" ", ",").split(","):
        entry = item.strip()
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid IP address or CIDR range: {entry}") from exc
        text = str(network) if "/" in entry else str(network.network_address)
        if text not in items:
            items.append(text)
    return ",".join(items)


def rate_limit_for(token: ApiToken) -> int:
    value = int(getattr(token, "rate_limit_per_minute", 0) or 0)
    if value <= 0:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    return min(value, MAX_RATE_LIMIT_PER_MINUTE)


def _utcnow() -> datetime:
    # Stored naive in UTC, like every other DateTime column in this schema.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_api_token(
    db: Session,
    name: str,
    scopes: str,
    allowed_ips: str,
    *,
    expires_at: Optional[datetime] = None,
    rate_limit_per_minute: int = 0,
    created_by_id: Optional[int] = None,
) -> tuple[str, ApiToken]:
    raw, token_hash = generate_token()
    token = ApiToken(
        name=name,
        token_hash=token_hash,
        scopes=scopes,
        allowed_ips=allowed_ips,
        expires_at=expires_at,
        rate_limit_per_minute=max(0, int(rate_limit_per_minute or 0)),
        created_by_id=created_by_id,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return raw, token


def rotate_api_token(db: Session, token: ApiToken, grace_minutes: int = 0) -> tuple[str, ApiToken]:
    """Issue a replacement for ``token`` and retire the old one.

    The replacement keeps the name, scopes, allowlist, rate limit and issuer,
    and the same lifetime if the old token had one. With ``grace_minutes`` the
    old token keeps working that long, so a billing system can be switched
    over without a window of failed calls; with 0 it stops at once.
    """
    now = _utcnow()
    expires_at = None
    if token.expires_at is not None:
        lifetime = token.expires_at - (token.created_at or now)
        expires_at = now + max(lifetime, timedelta(days=1))
    raw, replacement = create_api_token(
        db,
        token.name,
        token.scopes,
        token.allowed_ips,
        expires_at=expires_at,
        rate_limit_per_minute=token.rate_limit_per_minute or 0,
        created_by_id=token.created_by_id,
    )
    grace = max(0, int(grace_minutes or 0))
    if grace:
        cutoff = now + timedelta(minutes=grace)
        if token.expires_at is None or token.expires_at > cutoff:
            token.expires_at = cutoff
    else:
        token.is_active = False
        token.revoked_at = now
    db.commit()
    db.refresh(replacement)
    return raw, replacement


def authenticate_token(db: Session, raw: str) -> ApiToken | None:
    token_hash = hash_token(raw)
    now = _utcnow()
    token = db.query(ApiToken).filter(
        ApiToken.token_hash == token_hash,
        ApiToken.is_active == True,
        ApiToken.revoked_at == None,
        or_(ApiToken.expires_at == None, ApiToken.expires_at > now),
    ).first()
    if token:
        token.last_used_at = datetime.now(timezone.utc)
        db.commit()
    return token


def check_ip_allowed(token: ApiToken, client_ip: str) -> bool:
    """Whether ``client_ip`` is inside the token's allowlist.

    The allowlist holds addresses and CIDR ranges. An IPv4 client reaching an
    IPv6 socket arrives as ::ffff:a.b.c.d and is compared as the IPv4 address
    it is. Entries that do not parse (hand-edited rows) still match exactly,
    as they did before ranges were understood.
    """
    allowed = (token.allowed_ips or "").strip()
    if not allowed:
        return True
    client_ip = (client_ip or "").strip()
    entries = [ip.strip() for ip in allowed.split(",") if ip.strip()]
    if client_ip in entries:
        return True
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    candidates = [address] + ([mapped] if mapped is not None else [])
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if any(candidate.version == network.version and candidate in network for candidate in candidates):
            return True
    return False


def linux_user_exists(username: str) -> bool:
    """Whether the Linux account a panel user would get is already there.

    The helper's panel-user-ensure adopts an existing account, so a failed
    provisioning must only delete one it actually created.
    """
    try:
        import pwd  # noqa: PLC0415 - POSIX only
    except ImportError:  # pragma: no cover - Windows development hosts
        return False
    try:
        pwd.getpwnam(site_users.linux_user_for_panel_username(username))
    except (KeyError, ValueError):
        return False
    return True


def undo_failed_create(
    *,
    username: str,
    domain: Optional[str],
    db_info: Optional[dict],
    delete_linux_user: bool,
) -> None:
    """Remove what a failed create left on the system, best effort.

    The database rows are dropped by the caller's rollback; this is for the
    things outside the database: the MariaDB database, the vhost and WAF
    file, and the Linux account with its home directory.
    """
    if db_info:
        try:
            mariadb.drop_database(db_info["db_name"], db_info["db_user"])
        except Exception:  # noqa: BLE001
            logger.warning("Could not drop database of failed provisioning for %s", username, exc_info=True)
    if domain:
        for step in (nginx.delete_wordpress_vhost, waf.remove_site_rules):
            try:
                step(domain)
            except Exception:  # noqa: BLE001
                logger.warning("Cleanup step %s failed for %s", step.__name__, domain, exc_info=True)
    if delete_linux_user:
        try:
            site_users.delete_panel_user(username)
        except Exception:  # noqa: BLE001
            logger.warning("Could not delete Linux user of failed provisioning %s", username, exc_info=True)


def discard_failed_account(db: Session, account: ProvisioningAccount) -> None:
    """Clear a failed account so the billing system can retry the create.

    Accounts that failed before failed creates were rolled back still point at
    the panel user (and Linux user) the attempt left behind; a retry with the
    same username would then be refused as a duplicate. Tear those down the
    same way termination does, then drop the record.
    """
    if account.user is not None:
        terminate_account(db, account, backup=False)
    db.delete(account)
    db.commit()


def panel_base_url(request=None) -> str:
    """Absolute base URL of this panel, for links handed to billing systems.

    With a request in hand the link follows the hostname it arrived on, so a
    customer is sent back to the domain they already use instead of to the one
    domain PANEL_URL names.
    """
    from app.services.panel_urls import panel_base_url as build

    return build(request)


def account_to_dict(account: ProvisioningAccount, db: Session) -> dict:
    user = account.user
    website = account.primary_website
    package = account.package
    package_name = package.name if package else None
    service_label = package_name or "spanel Hosting"
    external_label = account.external_id
    if external_label.startswith("whmcs:"):
        external_label = "#" + external_label[6:]
    service_label = f"{service_label} {external_label}"
    return {
        "external_id": account.external_id,
        # A terminated account keeps its billing record but no longer has a
        # panel user, so these stay empty rather than null: the billing module
        # still reads the row to show the service as terminated.
        "username": user.username if user else "",
        "email": user.email if user else "",
        "domain": website.domain if website else None,
        "package_id": account.package_id,
        "package_name": package_name,
        "service_label": service_label,
        "status": account.status,
        "panel_url": panel_base_url() or None,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "is_reseller": bool(user and user.role == "reseller"),
        "reseller_package_id": user.reseller_package_id if user else None,
        "last_action": account.last_action or "",
    }


def apply_account_limits(user: User, package, limits=None) -> None:
    """Give ``user`` the package's limits, then the per-account overrides.

    The package side is the panel's own rule (api/users.py) so an account
    made by a billing system is limited exactly like one made in the panel:
    terminal, SFTP sub-accounts, mailboxes, cgroup and MySQL limits included,
    not only websites and disk as before.
    """
    from app.api.users import _apply_package_limits

    _apply_package_limits(user, package)
    if limits is None:
        return
    for field in ("storage_limit_mb", "bandwidth_limit_mb", "website_limit"):
        value = getattr(limits, field, None)
        if value is not None:
            setattr(user, field, int(value))


def account_owner_ids(db: Session, user: User) -> list[int]:
    """The user and, for a reseller, every account the reseller owns."""
    owner_ids = [user.id]
    if user.role == "reseller":
        owner_ids += [row.id for row in db.query(User.id).filter(User.reseller_id == user.id).all()]
    return owner_ids


def account_usage(db: Session, account: ProvisioningAccount, *, use_cache: bool = False) -> dict:
    """Disk, bandwidth, website and database usage of one billing account.

    Bandwidth is the current calendar month of nginx traffic counted by the
    resource monitor. A reseller's figures include the accounts they own,
    which is what the billing system charges them for.
    """
    from app.services import resource_monitor

    user = account.user
    owner_ids = account_owner_ids(db, user)
    summary = storage_quota.storage_usage_summary(db, user, use_cache=use_cache)
    storage_used = int(summary["storage_used_bytes"] or 0)
    bandwidth_used = 0
    for oid in owner_ids:
        bandwidth_used += resource_monitor.cached_user_bandwidth_bytes(oid)
    if len(owner_ids) > 1:
        for child in db.query(User).filter(User.id.in_(owner_ids[1:])).all():
            storage_used += int(storage_quota.storage_usage_summary(db, child, use_cache=True)["storage_used_bytes"] or 0)
    storage_limit = summary["storage_limit_bytes"]
    percent = summary["storage_percent"]
    if storage_limit:
        percent = min(999.0, round(storage_used / storage_limit * 100, 2))
    bandwidth_limit_mb = int(getattr(user, "bandwidth_limit_mb", 0) or 0)
    return {
        "external_id": account.external_id,
        "storage_used_bytes": storage_used,
        "storage_limit_bytes": storage_limit,
        "storage_percent": percent,
        "website_count": db.query(Website).filter(Website.owner_id.in_(owner_ids)).count(),
        "database_count": db.query(DatabaseAccount).filter(DatabaseAccount.owner_id.in_(owner_ids)).count(),
        "bandwidth_used_bytes": bandwidth_used,
        # 0 is unlimited, the same convention the package uses.
        "bandwidth_limit_bytes": bandwidth_limit_mb * storage_quota.BYTES_PER_MB if bandwidth_limit_mb > 0 else 0,
        "user_count": len(owner_ids) - 1,
    }


# Backups a billing system asked for, by account id. One at a time per
# account: a second request while one runs is refused rather than queued.
_backup_lock = threading.Lock()
_backups_running: set[int] = set()
_backup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spanel-provisioning-backup")


def queue_account_backup(account_id: int) -> bool:
    """Start a full user backup in the background. False if one is running."""
    with _backup_lock:
        if account_id in _backups_running:
            return False
        _backups_running.add(account_id)
    _backup_executor.submit(_run_account_backup, account_id)
    return True


def _run_account_backup(account_id: int) -> None:
    from app.core.database import SessionLocal
    from app.services.audit import log_action

    try:
        with SessionLocal() as db:
            account = db.query(ProvisioningAccount).filter(ProvisioningAccount.id == account_id).first()
            if account is None or account.user is None:
                return
            try:
                archive = backup_service.create_user_backup(account.user, db)
                account.last_message = f"backup={archive}"
                outcome = "ok"
            except Exception as exc:  # noqa: BLE001 - recorded on the account
                db.rollback()
                account.last_message = f"backup failed: {exc}"[:2000]
                outcome = "failed"
            account.last_action = "backup"
            db.commit()
            log_action(db, None, "provisioning_backup", account.external_id, detail=outcome)
            from app.services import webhooks

            data = account_to_dict(account, db)
            data["backup"] = account.last_message
            webhooks.emit_event(
                db,
                "account.backup_completed" if outcome == "ok" else "account.backup_failed",
                data,
                subject=account.user,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Provisioning backup of account %s failed", account_id)
    finally:
        with _backup_lock:
            _backups_running.discard(account_id)


def suspend_account(db: Session, account: ProvisioningAccount, reason: str = "") -> None:
    user = account.user
    if not user:
        return
    user.is_active = False
    user.token_version = (user.token_version or 0) + 1

    # Include reseller's children
    owner_ids = [user.id]
    if user.role == "reseller":
        children = db.query(User).filter(User.reseller_id == user.id).all()
        for child in children:
            child.is_active = False
            child.token_version = (child.token_version or 0) + 1
            owner_ids.append(child.id)

    websites = db.query(Website).filter(Website.owner_id.in_(owner_ids)).all()
    for website in websites:
        website.status = "suspended"
        nginx.delete_wordpress_vhost(website.domain)
        nginx.write_vhost(
            website.domain,
            website.root_path,
            app_type="static",
            php_version=website.php_version,
            document_root=website.document_root or "public_html",
            custom_directives="# SUSPENDED",
            rewrite_mode="none",
            preserve_existing_ssl=False,
        )
        if website.linux_user:
            try:
                site_users.lock_linux_user(website.linux_user)
            except Exception:
                pass

    account.status = "suspended"
    account.last_action = "suspend"
    account.last_message = reason
    db.commit()


def unsuspend_account(db: Session, account: ProvisioningAccount) -> None:
    user = account.user
    if not user:
        return
    user.is_active = True

    # Include reseller's children
    owner_ids = [user.id]
    if user.role == "reseller":
        children = db.query(User).filter(User.reseller_id == user.id).all()
        for child in children:
            child.is_active = True
            child.token_version = (child.token_version or 0) + 1
            owner_ids.append(child.id)

    websites = db.query(Website).filter(Website.owner_id.in_(owner_ids)).all()
    for website in websites:
        website.status = "active"
        rewrite_mode = "front_controller" if website.app_type == "wordpress" else (website.nginx_rewrite_mode or "none")
        php_version = website.php_version if website.app_type in {"wordpress", "php"} else website.php_version
        php_socket = site_users.site_php_fpm_socket(website.linux_user, website.root_path, php_version) if website.app_type in {"wordpress", "php"} else None
        nginx.rewrite_vhost(
            website.domain,
            website.root_path,
            app_type=website.app_type,
            php_version=php_version,
            php_fpm_socket_override=php_socket,
            custom_directives=website.nginx_custom or "",
            document_root=website.document_root or "public_html",
            rewrite_mode=rewrite_mode,
            waf_enabled=website.waf_enabled,
            http_flood_enabled=website.http_flood_enabled,
            http_flood_config=website.http_flood_config or "",
            aliases=[a.domain for a in (website.aliases or []) if a.mode == "alias"],
            redirects=[a.domain for a in (website.aliases or []) if a.mode == "redirect"],
        )
        if website.linux_user:
            try:
                site_users.unlock_linux_user(website.linux_user)
            except Exception:
                pass

    account.status = "active"
    account.last_action = "unsuspend"
    account.last_message = ""
    db.commit()


def terminate_account(db: Session, account: ProvisioningAccount, backup: bool = False) -> list[str]:
    user = account.user
    deleted_domains = []

    if user and backup:
        # Termination deletes the Linux user and its home directory, so take a
        # full user backup first. A failure here must not block the
        # termination the billing system asked for.
        try:
            archive = backup_service.create_user_backup(user, db)
            account.last_message = f"backup={archive}"
        except Exception as exc:  # pragma: no cover - depends on host state
            account.last_message = f"backup failed: {exc}"

    if user:
        owner_ids = [user.id]
        child_users = []
        if user.role == "reseller":
            child_users = db.query(User).filter(User.reseller_id == user.id).all()
            for child in child_users:
                owner_ids.append(child.id)

        websites = db.query(Website).filter(Website.owner_id.in_(owner_ids)).all()
        for website in websites:
            db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
            if db_item:
                mariadb.drop_database(db_item.db_name, db_item.db_user)
                db.delete(db_item)
            nginx.delete_wordpress_vhost(website.domain)
            # Terminating an account is a real deletion, not a suspension: the
            # certificate has nothing left to protect and should not outlive it.
            ssl.release_site_certificates(db, website.domain, exclude_website_id=website.id)
            waf.remove_site_rules(website.domain)
            wordpress.delete_wordpress(website.root_path)
            deleted_domains.append(website.domain)
            db.delete(website)

        # The loop above finds databases by website_id, so it cannot reach one
        # that belongs to no website - which is the only kind the panel's own
        # database page creates - and it never looks at applications at all.
        # Both tables are keyed on owner_id (migrations 0014/0015 and 0025) and
        # the panel database does not enforce the cleanup it declares, so
        # without this sweep a terminated account left its databases running in
        # MariaDB and its applications wedged the delete with an IntegrityError.
        for oid in owner_ids:
            teardown.purge_owned_resources(db, oid)

        for child in child_users:
            site_users.delete_panel_user(child.username)
            db.delete(child)

        site_users.delete_panel_user(user.username)
        db.delete(user)

    account.status = "terminated"
    account.last_action = "terminate"
    account.user_id = None
    account.primary_website_id = None
    db.commit()
    return deleted_domains
