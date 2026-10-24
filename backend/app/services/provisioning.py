import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.entities import ApiToken, DatabaseAccount, ProvisioningAccount, User, Website
from app.services import backup as backup_service, teardown
from app.services import db_backends, nginx, site_users, ssl, storage_quota, waf, wordpress


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str]:
    raw = f"bp_{secrets.token_urlsafe(48)}"
    return raw, hash_token(raw)


def create_api_token(db: Session, name: str, scopes: str, allowed_ips: str) -> tuple[str, ApiToken]:
    raw, token_hash = generate_token()
    token = ApiToken(
        name=name,
        token_hash=token_hash,
        scopes=scopes,
        allowed_ips=allowed_ips,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return raw, token


def authenticate_token(db: Session, raw: str) -> ApiToken | None:
    token_hash = hash_token(raw)
    token = db.query(ApiToken).filter(
        ApiToken.token_hash == token_hash,
        ApiToken.is_active == True,
        ApiToken.revoked_at == None,
    ).first()
    if token:
        token.last_used_at = datetime.now(timezone.utc)
        db.commit()
    return token


def check_ip_allowed(token: ApiToken, client_ip: str) -> bool:
    allowed = (token.allowed_ips or "").strip()
    if not allowed:
        return True
    ips = {ip.strip() for ip in allowed.split(",") if ip.strip()}
    return client_ip in ips


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
    }


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
    for owner_id in owner_ids:
        teardown.set_owner_databases_locked(db, owner_id, True)

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
    for owner_id in owner_ids:
        teardown.set_owner_databases_locked(db, owner_id, False)

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
                db_backends.backend_for(db_item).drop_database(db_item.db_name, db_item.db_user)
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
