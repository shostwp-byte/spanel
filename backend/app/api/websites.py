import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role, normalize_role
from app.core.secrets import encrypt
from app.models.entities import CloudflareCredential, DatabaseAccount, SiteApp, User, Website, WebsiteAlias
from app.schemas.schemas import (
    CloudflareZoneOut,
    SharedSslRequest,
    SslSourceOut,
    WebsiteAliasCreate,
    WebsiteAliasOut,
    WebsiteCreate,
    WebsiteHttpFloodUpdate,
    WebsiteLogOut,
    WebsiteNginxConfig,
    WebsiteNginxCustom,
    WebsiteOut,
    WebsiteUpdate,
    WebsiteWafUpdate,
    WebsiteWordPressInstall,
    WildcardSslRequest,
)
from app.services import addons, cloudflare, cron, dns, file_manager, mariadb, nginx, site_apps, site_users, ssl, storage_quota, teardown, waf, wordpress
from app.services.audit import log_action

_PLACEHOLDER_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "nginx"

router = APIRouter(prefix="/websites", tags=["websites"])


def _command_error(result):
    return (result.stderr or result.stdout or f"Command failed with code {result.returncode}").strip()


def _cleanup_failed_site(root_path: str, linux_user: str | None, delete_files: bool = True) -> None:
    if delete_files:
        try:
            if linux_user:
                site_users.delete_site_runtime(root_path, linux_user)
            else:
                wordpress.delete_wordpress(root_path)
        except Exception:
            pass


def _ensure_default_waf_file(domain: str) -> None:
    # No CRS: a website starts with crs_enabled off, and the rule file has to
    # agree with the flag or the opt-in means nothing.
    result = waf.sync_site_rules(domain, [rule["id"] for rule in waf.DEFAULT_RULES], "", crs_mode="off")
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=_command_error(result))


def _sync_http_flood_zones(db: Session) -> None:
    db.flush()
    result = nginx.sync_http_flood_zones(db.query(Website).all())
    if result.returncode != 0:
        raise RuntimeError(_command_error(result))


def _write_placeholder_page(domain: str, root_path: str, linux_user: str | None, php_version: str) -> None:
    placeholder = site_users.document_root(root_path) / "index.html"
    try:
        if placeholder.exists():
            return
    except OSError:
        pass
    # This one renders HTML, so it escapes - unlike the vhost templates, where
    # escaping would corrupt the config. The only variable is `domain`, already
    # constrained to [a-z0-9-.] by DOMAIN_RE, so escaping is a no-op for every
    # value that can reach here today; it is here so that stops being load-bearing.
    env = Environment(loader=FileSystemLoader(_PLACEHOLDER_TEMPLATE_DIR), autoescape=True)
    tmpl = env.get_template("placeholder.html.j2")
    placeholder_site = Website(
        domain=domain,
        owner_id=0,
        root_path=root_path,
        document_root="public_html",
        linux_user=linux_user,
        php_version=php_version,
        app_type="php",
    )
    file_manager.write_text_file(
        placeholder_site,
        "public_html/index.html",
        tmpl.render(domain=domain),
        allow_executable=True,
    )


def _website_http_flood_config(website: Website) -> dict:
    return nginx.http_flood_config_for_website(website)


def _borrowed_ssl_paths(source_domain: str) -> dict:
    """Cert file paths for ``source_domain``.

    Prefers its manual dir (group-readable by the panel). Otherwise its certbot
    lineage — ``/etc/letsencrypt/live`` is root-only, so the path is returned
    unchecked; whoever puts a site into shared/cloudflare mode has already
    verified the certificate with ``ssl.cert_info`` (the helper).
    """
    manual = ssl.manual_ssl_paths(source_domain)
    try:
        if Path(manual["cert"]).is_file() and Path(manual["key"]).is_file():
            has_ca = bool(manual["ca"]) and Path(manual["ca"]).is_file()
            return {
                "ssl_cert_path": manual["cert"],
                "ssl_key_path": manual["key"],
                "ssl_ca_path": manual["ca"] if has_ca else None,
            }
    except OSError:
        pass
    live = Path("/etc/letsencrypt/live") / source_domain
    return {
        "ssl_cert_path": str(live / "fullchain.pem"),
        "ssl_key_path": str(live / "privkey.pem"),
        "ssl_ca_path": None,
    }


def _rewrite_ssl_kwargs(website: Website) -> dict:
    mode = getattr(website, "ssl_mode", "none")
    if mode == "manual" and website.ssl_cert_path and website.ssl_key_path:
        return {
            "ssl_cert_path": website.ssl_cert_path,
            "ssl_key_path": website.ssl_key_path,
            "ssl_ca_path": website.ssl_ca_path,
        }
    if mode in {"cloudflare", "shared"} and website.ssl_source_domain:
        borrowed = _borrowed_ssl_paths(website.ssl_source_domain)
        if borrowed:
            return borrowed
    return {}


def _website_rewrite_mode(website: Website) -> str:
    return getattr(website, "nginx_rewrite_mode", "none") or "none"


def _is_authorized_for_website(db: Session, website: Website, current_user: User) -> bool:
    if website.owner_id == current_user.id or is_admin_role(current_user.role):
        return True
    if normalize_role(current_user.role) == Role.reseller:
        owner = db.query(User).filter(User.id == website.owner_id).first()
        if owner and owner.reseller_id == current_user.id:
            return True
    return False


def _get_authorized_website(db: Session, website_id: int, current_user: User) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    return website


def _alias_domains(website: Website) -> list[str]:
    return [
        alias.domain
        for alias in sorted(getattr(website, "aliases", []) or [], key=lambda item: item.domain)
        if getattr(alias, "mode", "alias") == "alias"
    ]


def _redirect_domains(website: Website) -> list[str]:
    return [
        alias.domain
        for alias in sorted(getattr(website, "aliases", []) or [], key=lambda item: item.domain)
        if getattr(alias, "mode", "alias") == "redirect"
    ]


def _ssl_domains(website: Website) -> list[str]:
    return [*_alias_domains(website), *_redirect_domains(website)]


def _sync_alias_ssl_flags(website: Website) -> None:
    """Mark each alias/redirect as covered or not by the certificate that
    exists right now.

    issue_ssl() can succeed overall while dropping one requested name (bad or
    not-yet-pointed DNS - see --allow-subset-of-names): a plain "Added alias"
    toast then told the admin nothing went wrong for that name, even though
    it still has no working SSL. WebsiteAlias.ssl_enabled already existed for
    this and was never read or written anywhere.
    """
    if getattr(website, "ssl_mode", "none") != "letsencrypt":
        return
    sans = ssl.cert_info(website.domain).get("sans") or []
    for alias in website.aliases or []:
        alias.ssl_enabled = bool(sans) and ssl.cert_covers(sans, alias.domain)


def _unique_domains(domains: list[str] | tuple[str, ...]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for domain in domains:
        value = (domain or "").strip().lower()
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _reserved_hostnames(db: Session, exclude_website_id: int | None = None, exclude_alias_id: int | None = None) -> set[str]:
    names: set[str] = set()
    for website in db.query(Website).all():
        if exclude_website_id is not None and website.id == exclude_website_id:
            continue
        domain = (website.domain or "").strip().lower()
        if not domain:
            continue
        names.add(domain)
        names.add(f"www.{domain}")
    for alias in db.query(WebsiteAlias).all():
        if exclude_alias_id is not None and alias.id == exclude_alias_id:
            continue
        domain = (alias.domain or "").strip().lower()
        if domain:
            names.add(domain)
    return names


def _hostname_conflicts(db: Session, domain: str, *, exclude_website_id: int | None = None, exclude_alias_id: int | None = None) -> bool:
    safe_domain = (domain or "").strip().lower()
    if not safe_domain:
        return True
    return bool({safe_domain, f"www.{safe_domain}"} & _reserved_hostnames(db, exclude_website_id=exclude_website_id, exclude_alias_id=exclude_alias_id))


def _resolve_app_for_owner(db: Session, owner_id: int, app_id: int | None, current_user: User) -> SiteApp:
    """The app a website may serve: one its owner owns.

    Without the ownership check a customer could aim their domain at another
    tenant's application by guessing an id.
    """
    # Application mode belongs to the addon; without it a website cannot be
    # pointed at one, whichever route the request arrived on.
    addons.require(addons.APPLICATION)
    if app_id is None:
        raise HTTPException(
            status_code=400,
            detail="Pick an installed application for this website, or choose a different website mode.",
        )
    app = db.query(SiteApp).filter(SiteApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.owner_id != owner_id:
        ensure_role(current_user.role, Role.admin)
    return app


def _rewrite_website_vhost(website: Website, **overrides) -> str:
    app_type = overrides.pop("app_type", website.app_type or "wordpress")
    php_version = overrides.pop("php_version", website.php_version)
    root_path = overrides.pop("root_path", website.root_path)
    linux_user = overrides.pop("linux_user", website.linux_user)
    runtime_php_version = php_version if app_type in {"wordpress", "php"} else None
    if "php_fpm_socket_override" in overrides:
        php_fpm_socket_override = overrides.pop("php_fpm_socket_override")
    elif runtime_php_version:
        php_fpm_socket_override = site_users.site_php_fpm_socket(linux_user, root_path, runtime_php_version)
    else:
        php_fpm_socket_override = None
    rewrite_kwargs = {
        "app_type": app_type,
        "php_version": php_version,
        "custom_directives": overrides.pop("custom_directives", website.nginx_custom or ""),
        "php_fpm_socket_override": php_fpm_socket_override,
        "waf_enabled": overrides.pop("waf_enabled", website.waf_enabled),
        "http_flood_enabled": overrides.pop("http_flood_enabled", website.http_flood_enabled),
        "http_flood_config": overrides.pop("http_flood_config", website.http_flood_config or ""),
        "document_root": overrides.pop("document_root", website.document_root or "public_html"),
        "rewrite_mode": overrides.pop("rewrite_mode", _website_rewrite_mode(website)),
        "aliases": overrides.pop("aliases", _alias_domains(website)),
        "redirects": overrides.pop("redirects", _redirect_domains(website)),
        "app_port": overrides.pop("app_port", site_apps.app_port_for_website(website)),
    }
    if overrides.pop("include_ssl", True):
        rewrite_kwargs.update(_rewrite_ssl_kwargs(website))
    rewrite_kwargs.update(overrides)
    return nginx.rewrite_vhost(
        website.domain,
        root_path,
        **rewrite_kwargs,
    )


def _has_live_certificate(website: Website) -> bool:
    domain = website.domain
    mode = getattr(website, "ssl_mode", "none")
    if mode == "manual" and website.ssl_cert_path and website.ssl_key_path:
        try:
            return Path(website.ssl_cert_path).is_file() and Path(website.ssl_key_path).is_file()
        except OSError:
            return False
    if mode in {"cloudflare", "shared"}:
        # The lineage lives under root-only /etc/letsencrypt/live and the daily
        # renew keeps it alive; trust the mode rather than stat what we can't.
        return bool(website.ssl_source_domain)
    live_dir = Path("/etc/letsencrypt/live") / domain
    try:
        return (live_dir / "fullchain.pem").is_file() and (live_dir / "privkey.pem").is_file()
    except OSError:
        return False


def _has_wordpress_install(website: Website) -> bool:
    try:
        public = site_users.document_root(website.root_path, website.document_root or "public_html")
        return (public / "wp-config.php").is_file() and (public / "wp-admin").is_dir()
    except (OSError, ValueError):
        return False


def _sync_live_ssl_flags(db: Session, websites: list[Website]) -> list[Website]:
    changed = False
    for website in websites:
        if not website.ssl_enabled and _has_live_certificate(website):
            website.ssl_enabled = True
            changed = True
    if changed:
        db.commit()
        for website in websites:
            db.refresh(website)
    # Read the server-wide switch once rather than per site: it is a file read.
    crs_live = waf.active_crs_mode() != "off"
    for website in websites:
        website.wordpress_installed = _has_wordpress_install(website)
        # Transient, like wordpress_installed above - WebsiteOut reads it and
        # nothing persists it. crs_enabled is a real column and needs no help.
        website.crs_active = bool(getattr(website, "crs_enabled", False)) and crs_live
    return websites


def _http_flood_payload_config(payload: WebsiteHttpFloodUpdate) -> dict:
    return nginx.validate_http_flood_config({
        "access_limit_requests": payload.access_limit_requests,
        "access_limit_window": payload.access_limit_window,
        "access_limit_burst": payload.access_limit_burst,
        "connection_limit": payload.connection_limit,
    })


def _read_ssl_input(upload: UploadFile | None, text: str | None, label: str, required: bool = True) -> bytes:
    if upload is not None and upload.filename:
        return ssl.read_ssl_part(upload, label=label, required=required)
    return ssl.read_ssl_part(text or "", label=label, required=required)


@router.post("", response_model=WebsiteOut)
def create_website(payload: WebsiteCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Create a website. If install_wordpress is False, only creates the domain
    folder + Nginx vhost (no DB, no WordPress files)."""
    requested_owner_id = payload.owner_id
    if requested_owner_id is not None and requested_owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    if _hostname_conflicts(db, payload.domain) or nginx.vhost_exists(payload.domain):
        raise HTTPException(status_code=409, detail="Domain already exists")
    from app.services import mail

    if mail.webmail_host_taken(db, payload.domain):
        # The Email addon serves webmail.<domain> for that mail domain already.
        raise HTTPException(status_code=409, detail="That name is the webmail address of a mail domain here.")

    if requested_owner_id is not None:
        owner = db.query(User).filter(User.id == requested_owner_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner not found")
    else:
        owner = current_user

    owner_id = owner.id
    existing_sites = db.query(Website).filter(Website.owner_id == owner_id).all()
    existing_domains = {(s.domain or "").strip().lower() for s in existing_sites if s.domain}
    new_domain_lower = (payload.domain or "").strip().lower()
    is_subdomain_create = any(new_domain_lower != d and new_domain_lower.endswith("." + d) for d in existing_domains)
    existing_subdomains_count = sum(
        1 for d in existing_domains if any(d != parent and d.endswith("." + parent) for parent in existing_domains)
    )
    existing_root_domains_count = len(existing_domains) - existing_subdomains_count
    if is_subdomain_create:
        eff_subdomain_limit = int((owner.package.subdomain_limit if getattr(owner, "package", None) else 0) or 0)
        if not is_admin_role(owner.role) and eff_subdomain_limit > 0 and existing_subdomains_count >= eff_subdomain_limit:
            raise HTTPException(status_code=403, detail="Subdomain limit reached")
    else:
        eff_site_limit = int(owner.website_limit if owner.website_limit is not None else (owner.package.website_limit if getattr(owner, "package", None) else 0) or 0)
        if not is_admin_role(owner.role) and eff_site_limit != 0 and existing_root_domains_count >= eff_site_limit:
            raise HTTPException(status_code=403, detail="Website limit reached")

    install_wp = payload.install_wordpress and payload.app_type == "wordpress"
    create_estimate_bytes = storage_quota.WORDPRESS_SITE_ESTIMATE_BYTES if install_wp else storage_quota.STATIC_SITE_ESTIMATE_BYTES
    try:
        storage_quota.enforce_user_storage_quota(db, owner, incoming_bytes=create_estimate_bytes)
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    linux_user = site_users.linux_user_for_panel_username(owner.username)
    root_path = site_users.site_root_for_panel_user(owner.username, payload.domain)
    if install_wp and (not payload.admin_email or not payload.admin_password):
        raise HTTPException(status_code=400, detail="admin_email and admin_password are required when install_wordpress is true")

    if install_wp:
        db_info = mariadb.create_database(payload.domain)
        try:
            linux_user = site_users.ensure_site_runtime(payload.domain, root_path, payload.php_version, linux_user)
            root_path = wordpress.install_wordpress(
                payload.domain,
                db_info,
                payload.title,
                payload.admin_user,
                payload.admin_password,
                str(payload.admin_email),
                payload.php_version,
                linux_user,
                root_path=root_path,
            )
        except HTTPException:
            mariadb.drop_database(db_info["db_name"], db_info["db_user"])
            _cleanup_failed_site(root_path, linux_user)
            raise
        except Exception as exc:
            mariadb.drop_database(db_info["db_name"], db_info["db_user"])
            _cleanup_failed_site(root_path, linux_user)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            _ensure_default_waf_file(payload.domain)
            nginx.write_vhost(
                payload.domain,
                root_path,
                app_type="wordpress",
                php_version=payload.php_version,
                php_fpm_socket_override=site_users.site_php_fpm_socket(linux_user, root_path, payload.php_version),
                document_root="public_html",
                rewrite_mode="front_controller",
            )
        except HTTPException:
            mariadb.drop_database(db_info["db_name"], db_info["db_user"])
            _cleanup_failed_site(root_path, linux_user)
            raise
        except Exception as exc:
            mariadb.drop_database(db_info["db_name"], db_info["db_user"])
            _cleanup_failed_site(root_path, linux_user)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app_type_value = "wordpress"
    else:
        app_type_value = "php" if payload.app_type == "wordpress" else payload.app_type
        runtime_php_version = payload.php_version if app_type_value in {"wordpress", "php"} else None
        selected_app = (
            _resolve_app_for_owner(db, owner_id, payload.app_id, current_user)
            if app_type_value in nginx.PROXIED_APP_TYPES
            else None
        )
        try:
            linux_user = site_users.ensure_site_runtime(payload.domain, root_path, runtime_php_version, linux_user)
            # Just create the public_html/ folder skeleton and write a vhost.
            public = site_users.document_root(root_path)
            if not settings.command_dry_run:
                _write_placeholder_page(payload.domain, root_path, linux_user, payload.php_version)
                site_users.fix_site_path(str(public), linux_user)
            _ensure_default_waf_file(payload.domain)
            nginx.write_vhost(
                payload.domain,
                root_path,
                app_type=app_type_value,
                php_version=payload.php_version,
                php_fpm_socket_override=site_users.site_php_fpm_socket(linux_user, root_path, runtime_php_version),
                document_root="public_html",
                rewrite_mode="none",
                app_port=selected_app.port if selected_app else None,
            )
        except HTTPException:
            _cleanup_failed_site(root_path, linux_user)
            raise
        except Exception as exc:
            _cleanup_failed_site(root_path, linux_user)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db_info = None

    website = Website(
        domain=payload.domain,
        owner_id=owner_id,
        root_path=root_path,
        document_root="public_html",
        linux_user=linux_user,
        php_version=payload.php_version,
        app_type=app_type_value,
        nginx_rewrite_mode="front_controller" if app_type_value == "wordpress" else "none",
        app_id=selected_app.id if (not install_wp and selected_app) else None,
        status="active",
    )
    db.add(website)
    db.commit()
    db.refresh(website)
    if db_info:
        # Store password encrypted; phpMyAdmin SSO decrypts on demand.
        db.add(DatabaseAccount(
            owner_id=owner_id,
            website_id=website.id,
            db_name=db_info["db_name"],
            db_user=db_info["db_user"],
            db_password=encrypt(db_info["db_password"]),
        ))
        db.commit()
    log_action(
        db,
        current_user.id,
        "create_wordpress" if install_wp else "create_site",
        payload.domain,
        request=request,
    )
    storage_quota.forget_user_storage(owner_id)
    # DNS Manager (operator, 2026-09-29): the new website gets its zone. Quiet
    # when the addon is off, and never the reason a website is not created.
    try:
        dns.zone_for_new_website(db, website)
    except Exception:
        db.rollback()
    db.refresh(website)
    website.wordpress_installed = install_wp
    return website


@router.post("/wordpress", response_model=WebsiteOut)
def create_wordpress(payload: WebsiteCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Legacy endpoint for backwards compatibility. Forces install_wordpress=True."""
    payload = payload.model_copy(update={"install_wordpress": True, "app_type": "wordpress"})
    return create_website(payload, request, db, current_user)


@router.post("/{website_id}/wordpress", response_model=WebsiteOut)
def install_wordpress_on_website(
    website_id: int,
    payload: WebsiteWordPressInstall,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    if _has_wordpress_install(website):
        raise HTTPException(status_code=400, detail="WordPress is already installed for this website")

    owner = db.query(User).filter(User.id == website.owner_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    try:
        storage_quota.enforce_user_storage_quota(
            db,
            owner,
            incoming_bytes=storage_quota.WORDPRESS_SITE_ESTIMATE_BYTES,
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        db_info = mariadb.create_database(website.domain, if_not_exists=False)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        root_path = wordpress.install_wordpress(
            website.domain,
            db_info,
            payload.title.strip() or website.domain,
            payload.admin_user,
            payload.admin_password,
            str(payload.admin_email),
            website.php_version,
            website.linux_user,
            root_path=website.root_path,
        )
        _ensure_default_waf_file(website.domain)
        _rewrite_website_vhost(
            website,
            app_type="wordpress",
            php_version=website.php_version,
            root_path=root_path,
            rewrite_mode="front_controller",
        )
    except (RuntimeError, ValueError, OSError) as exc:
        mariadb.drop_database(db_info["db_name"], db_info["db_user"])
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    website.root_path = root_path
    website.app_type = "wordpress"
    website.nginx_rewrite_mode = "front_controller"
    db_account = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_info["db_name"]).first()
    if db_account:
        db_account.owner_id = website.owner_id
        db_account.website_id = website.id
        db_account.db_user = db_info["db_user"]
        db_account.db_password = encrypt(db_info["db_password"])
    else:
        db.add(DatabaseAccount(
            owner_id=website.owner_id,
            website_id=website.id,
            db_name=db_info["db_name"],
            db_user=db_info["db_user"],
            db_password=encrypt(db_info["db_password"]),
        ))
    db.commit()
    db.refresh(website)
    website.wordpress_installed = True
    log_action(db, current_user.id, "install_wordpress", website.domain, request=request)
    return website


@router.get("", response_model=List[WebsiteOut])
def list_websites(q: str = Query(default="", max_length=255), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    search = (q or "").strip().lower()
    if is_admin_role(current_user.role):
        query = db.query(Website)
    elif normalize_role(current_user.role) == Role.reseller:
        sub_ids = db.query(User.id).filter(User.reseller_id == current_user.id)
        query = db.query(Website).filter(or_(Website.owner_id == current_user.id, Website.owner_id.in_(sub_ids)))
    else:
        query = db.query(Website).filter(Website.owner_id == current_user.id)
    if search:
        pattern = f"%{search}%"
        query = query.outerjoin(WebsiteAlias).filter(or_(
            Website.domain.ilike(pattern),
            Website.root_path.ilike(pattern),
            Website.linux_user.ilike(pattern),
            WebsiteAlias.domain.ilike(pattern),
        )).distinct()
    websites = query.order_by(Website.id.desc()).all()
    return _sync_live_ssl_flags(db, websites)


@router.get("/{website_id}/aliases", response_model=List[WebsiteAliasOut])
def list_website_aliases(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _get_authorized_website(db, website_id, current_user)
    return sorted(website.aliases or [], key=lambda alias: alias.domain)


@router.post("/{website_id}/aliases", response_model=WebsiteAliasOut)
def create_website_alias(
    website_id: int,
    payload: WebsiteAliasCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = _get_authorized_website(db, website_id, current_user)
    if payload.domain == website.domain or _hostname_conflicts(db, payload.domain):
        raise HTTPException(status_code=409, detail="Domain alias already exists")
    alias = WebsiteAlias(website_id=website.id, domain=payload.domain, mode=payload.mode)
    db.add(alias)
    db.flush()
    try:
        aliases = _alias_domains(website)
        redirects = _redirect_domains(website)
        if payload.mode == "alias" and payload.domain not in aliases:
            aliases.append(payload.domain)
        if payload.mode == "redirect" and payload.domain not in redirects:
            redirects.append(payload.domain)
        aliases = _unique_domains(aliases)
        redirects = _unique_domains(redirects)
        _rewrite_website_vhost(website, aliases=aliases, redirects=redirects)
        # No SSL attempt here on purpose - same split DirectAdmin uses:
        # adding a domain wires it into Nginx immediately, but a certificate
        # covering it is a separate, explicit step on the SSL page. Trying to
        # issue one right here meant this request's success or failure hinged
        # on that domain's DNS being ready *this second*, and a partial
        # success (--allow-subset-of-names dropping just this domain) was
        # easy to miss in a one-line toast. ssl_enabled below still reflects
        # whatever the certificate already covers, so the Domains list is
        # honest about it without this call ever trying to change that.
        _sync_alias_ssl_flags(website)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Cannot write Nginx config: {exc}") from exc
    db.commit()
    db.refresh(alias)
    log_action(db, current_user.id, "create_website_alias", website.domain, payload.domain, request=request)
    # DNS Manager: every domain on the server gets its DNS (operator, 2026-09-29).
    dns.zone_for_new_domain(db, payload.domain, website.owner_id)
    return alias


@router.delete("/{website_id}/aliases/{alias_id}")
def delete_website_alias(
    website_id: int,
    alias_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = _get_authorized_website(db, website_id, current_user)
    alias = db.query(WebsiteAlias).filter(
        WebsiteAlias.id == alias_id,
        WebsiteAlias.website_id == website.id,
    ).first()
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    domain = alias.domain
    db.delete(alias)
    db.flush()
    try:
        aliases = [item for item in _alias_domains(website) if item != domain]
        redirects = [item for item in _redirect_domains(website) if item != domain]
        _rewrite_website_vhost(website, aliases=aliases, redirects=redirects)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Cannot write Nginx config: {exc}") from exc
    db.commit()
    log_action(db, current_user.id, "delete_website_alias", website.domain, domain, request=request)
    return {"ok": True}


@router.patch("/{website_id}", response_model=WebsiteOut)
def update_website(website_id: int, payload: WebsiteUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    if payload.domain is not None and payload.domain != website.domain:
        if _hostname_conflicts(db, payload.domain) or nginx.vhost_exists(payload.domain):
            raise HTTPException(status_code=409, detail="Domain already exists")
        old_domain = website.domain
        website.domain = payload.domain
        try:
            _ensure_default_waf_file(website.domain)
            _rewrite_website_vhost(website)
            nginx.delete_wordpress_vhost(old_domain)
        except Exception as exc:
            website.domain = old_domain
            raise HTTPException(status_code=400, detail=f"Cannot rename domain: {exc}") from exc
    if payload.php_version is not None:
        try:
            runtime_php_version = payload.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
            if website.linux_user and runtime_php_version:
                site_users.ensure_site_runtime(website.domain, website.root_path, payload.php_version, website.linux_user)
            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError(_command_error(result))
            if website.http_flood_enabled:
                _sync_http_flood_zones(db)
            app_type = website.app_type or "wordpress"
            _rewrite_website_vhost(
                website,
                app_type=app_type,
                php_version=payload.php_version,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot write Nginx config: {exc}") from exc
        website.php_version = payload.php_version
        try:
            # Existing cron lines carry the previous PHP CLI path; leaving them
            # behind would keep running the site on a version it no longer uses.
            cron.retarget_php_binary(website)
        except (RuntimeError, ValueError):
            pass
    if payload.app_type is not None and payload.app_type != (website.app_type or "wordpress"):
        try:
            next_app_type = payload.app_type
            next_app = (
                _resolve_app_for_owner(db, website.owner_id, payload.app_id, current_user)
                if next_app_type in nginx.PROXIED_APP_TYPES
                else None
            )
            runtime_php_version = website.php_version if next_app_type in {"wordpress", "php"} else None
            if website.linux_user and runtime_php_version:
                site_users.ensure_site_runtime(
                    website.domain,
                    website.root_path,
                    website.php_version,
                    website.linux_user,
                )
            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError(_command_error(result))
            if website.http_flood_enabled:
                _sync_http_flood_zones(db)
            next_rewrite_mode = (
                "front_controller" if next_app_type == "wordpress"
                else "none" if next_app_type == "static"
                else payload.nginx_rewrite_mode or _website_rewrite_mode(website)
            )
            _rewrite_website_vhost(
                website,
                app_type=next_app_type,
                php_version=website.php_version,
                rewrite_mode=next_rewrite_mode,
                app_port=next_app.port if next_app else None,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot change website mode: {exc}") from exc
        website.app_type = next_app_type
        website.nginx_rewrite_mode = next_rewrite_mode
        website.app_id = next_app.id if next_app else None
    elif payload.app_id is not None and (website.app_type or "") in nginx.PROXIED_APP_TYPES:
        # Same mode, different application behind it.
        next_app = _resolve_app_for_owner(db, website.owner_id, payload.app_id, current_user)
        try:
            _rewrite_website_vhost(website, app_port=next_app.port)
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot point this website at {next_app.name}: {exc}") from exc
        website.app_id = next_app.id
    if payload.status is not None:
        website.status = payload.status
    if payload.document_root is not None and payload.document_root != (website.document_root or "public_html"):
        try:
            next_document_root = site_users.validate_document_root(payload.document_root)
            site_users.ensure_document_root(website.root_path, next_document_root, website.linux_user)
            app_type = website.app_type or "wordpress"
            runtime_php_version = website.php_version if app_type in {"wordpress", "php"} else None
            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError(_command_error(result))
            if website.http_flood_enabled:
                _sync_http_flood_zones(db)
            _rewrite_website_vhost(
                website,
                app_type=app_type,
                php_version=website.php_version,
                document_root=next_document_root,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot change document root: {exc}") from exc
        website.document_root = next_document_root
    if payload.owner_id is not None:
        ensure_role(current_user.role, Role.reseller)
        owner = db.query(User).filter(User.id == payload.owner_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner not found")
        if not is_admin_role(current_user.role) and owner.id != current_user.id and owner.reseller_id != current_user.id:
            raise HTTPException(status_code=403, detail="Cannot assign domain to a user outside your reseller pool")
        assigned_count = db.query(Website).filter(Website.owner_id == owner.id, Website.id != website.id).count()
        if not is_admin_role(owner.role) and owner.website_limit != 0 and assigned_count >= owner.website_limit:
            raise HTTPException(status_code=403, detail="Website limit reached")
        if payload.owner_id != website.owner_id:
            try:
                storage_quota.enforce_user_storage_quota(
                    db,
                    owner,
                    incoming_bytes=storage_quota.website_storage_used_bytes(website),
                )
                new_linux_user = site_users.linux_user_for_panel_username(owner.username)
                new_root_path = site_users.site_root_for_panel_user(owner.username, website.domain)
                runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
                site_users.move_site_runtime(website.root_path, new_root_path, new_linux_user, runtime_php_version)
                result = waf.sync_website_rules(website)
                if result.returncode != 0:
                    raise RuntimeError(_command_error(result))
                if website.http_flood_enabled:
                    _sync_http_flood_zones(db)
                _rewrite_website_vhost(
                    website,
                    root_path=new_root_path,
                    linux_user=new_linux_user,
                    app_type=website.app_type or "wordpress",
                    php_version=website.php_version,
                )
                website.root_path = new_root_path
                website.linux_user = new_linux_user
            except storage_quota.StorageQuotaExceeded as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        website.owner_id = payload.owner_id
    if payload.nginx_custom is not None:
        try:
            nginx.update_custom_block(website.domain, payload.nginx_custom)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        website.nginx_custom = payload.nginx_custom
        website.nginx_config_mode = "managed"
    if payload.nginx_rewrite_mode is not None and payload.nginx_rewrite_mode != _website_rewrite_mode(website):
        app_type = website.app_type or "wordpress"
        next_rewrite_mode = "front_controller" if app_type == "wordpress" else "none" if app_type == "static" else payload.nginx_rewrite_mode
        runtime_php_version = website.php_version if app_type in {"wordpress", "php"} else None
        try:
            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError(_command_error(result))
            if website.http_flood_enabled:
                _sync_http_flood_zones(db)
            _rewrite_website_vhost(
                website,
                app_type=app_type,
                php_version=website.php_version,
                rewrite_mode=next_rewrite_mode,
            )
        except (RuntimeError, ValueError, OSError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot change Nginx rewrite: {exc}") from exc
        website.nginx_rewrite_mode = next_rewrite_mode
        website.nginx_config_mode = "managed"
    if payload.waf_enabled is not None:
        from app.api.waf import may_manage_waf

        # This endpoint already restricts the website itself to its owner, so
        # the only extra question is whether their package includes the WAF.
        if not may_manage_waf(current_user):
            raise HTTPException(status_code=403, detail="Your hosting package does not include WAF settings")
        # Set before the rule file is rendered: the renderer reads it to
        # decide whether the site's own CRS opt-in loads, and rendering first
        # wrote the old state - turning the WAF back on left CRS out.
        previous = website.waf_enabled
        website.waf_enabled = bool(payload.waf_enabled)
        try:
            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError(_command_error(result))
            nginx.update_waf_block(website.domain, payload.waf_enabled)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            website.waf_enabled = previous
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.http_flood_enabled is not None:
        ensure_role(current_user.role, Role.admin)
        next_enabled = bool(payload.http_flood_enabled)
        try:
            website.http_flood_enabled = next_enabled
            if next_enabled:
                _sync_http_flood_zones(db)
                nginx.update_http_flood_block(website.domain, True, _website_http_flood_config(website))
            else:
                nginx.update_http_flood_block(website.domain, False, _website_http_flood_config(website))
                _sync_http_flood_zones(db)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "update_website", website.domain)
    return website


@router.get("/{website_id}/nginx-custom", response_model=WebsiteNginxCustom)
def get_website_nginx_custom(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    return WebsiteNginxCustom(nginx_custom=website.nginx_custom or "")


@router.get("/{website_id}/nginx-config", response_model=WebsiteNginxConfig)
def get_website_nginx_config(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    try:
        return WebsiteNginxConfig(nginx_config=nginx.read_vhost_config(website.domain))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{website_id}/nginx-config", response_model=WebsiteOut)
def set_website_nginx_config(website_id: int, payload: WebsiteNginxConfig, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    raise HTTPException(
        status_code=405,
        detail="The main Nginx vhost is managed by spanel. Use Custom Nginx instead.",
    )


@router.post("/{website_id}/nginx-config/reset", response_model=WebsiteOut)
def reset_website_nginx_config(website_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    try:
        result = waf.sync_website_rules(website)
        if result.returncode != 0:
            raise RuntimeError(_command_error(result))
        if website.http_flood_enabled:
            _sync_http_flood_zones(db)
        _rewrite_website_vhost(
            website,
            custom_directives="",
        )
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    website.nginx_custom = ""
    website.nginx_config_mode = "managed"
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "reset_nginx_config", website.domain, request=request)
    return website


@router.patch("/{website_id}/waf", response_model=WebsiteOut)
def set_website_waf(website_id: int, payload: WebsiteWafUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # A customer may turn the WAF on and off for a site they own, gated on the
    # same package flag as the rest of the WAF screens.
    from app.api.waf import may_manage_waf

    website = _get_authorized_website(db, website_id, current_user)
    if not may_manage_waf(current_user):
        raise HTTPException(status_code=403, detail="Your hosting package does not include WAF settings")
    # Before the render, for the reason given in update_website.
    previous = website.waf_enabled
    website.waf_enabled = bool(payload.waf_enabled)
    try:
        result = waf.sync_website_rules(website)
        if result.returncode != 0:
            raise RuntimeError(_command_error(result))
        nginx.update_waf_block(website.domain, payload.waf_enabled)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        website.waf_enabled = previous
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "update_waf", website.domain, "enabled" if payload.waf_enabled else "disabled", request=request)
    return website


@router.patch("/{website_id}/http-flood", response_model=WebsiteOut)
def set_website_http_flood(website_id: int, payload: WebsiteHttpFloodUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    config = _http_flood_payload_config(payload)
    next_enabled = bool(payload.http_flood_enabled)
    try:
        website.http_flood_enabled = next_enabled
        website.http_flood_config = json.dumps(config, ensure_ascii=True)
        if next_enabled:
            _sync_http_flood_zones(db)
            nginx.update_http_flood_block(website.domain, True, config)
        else:
            nginx.update_http_flood_block(website.domain, False, config)
            _sync_http_flood_zones(db)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "update_http_flood", website.domain, "enabled" if next_enabled else "disabled", request=request)
    return website


@router.get("/{website_id}/logs", response_model=WebsiteLogOut)
def get_website_log(
    website_id: int,
    kind: str = Query(default="access", pattern="^(access|error)$"),
    lines: int = Query(default=200, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    try:
        return nginx.read_site_log(website.domain, kind, lines)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{website_id}/nginx-custom", response_model=WebsiteOut)
def set_website_nginx_custom(website_id: int, payload: WebsiteNginxCustom, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    try:
        nginx.update_custom_block(website.domain, payload.nginx_custom)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    website.nginx_custom = payload.nginx_custom
    website.nginx_config_mode = "managed"
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "update_nginx_custom", website.domain, request=request)
    return website


@router.delete("/{website_id}")
def delete_website(website_id: int, request: Request, delete_files: bool = True, delete_database: bool = True, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _get_authorized_website(db, website_id, current_user)
    _block_if_source(db, website)
    domain = website.domain
    db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
    if delete_database and db_item:
        mariadb.drop_database(db_item.db_name, db_item.db_user)
    db.query(WebsiteAlias).filter(WebsiteAlias.website_id == website.id).delete(synchronize_session=False)
    nginx.delete_wordpress_vhost(website.domain)
    # The vhost is gone, so nothing reads the certificate or the rule file any
    # more: retire both before the row disappears and we no longer know which
    # names were ours.
    ssl_note = ssl.release_site_certificates(db, website.domain, exclude_website_id=website.id)
    waf.remove_site_rules(website.domain)
    if delete_files:
        if website.linux_user:
            site_users.delete_site_runtime(website.root_path, website.linux_user)
        else:
            wordpress.delete_wordpress(website.root_path)
    if db_item:
        db.delete(db_item)
    # Real Linux logins scoped to this site. Leaving one behind would leave a
    # working credential pointing at a chroot whose bind mount source is about
    # to be deleted or handed to another customer.
    teardown.purge_website_sftp_accounts(db, website.id)
    had_http_flood = bool(website.http_flood_enabled)
    owner_id = website.owner_id
    db.delete(website)
    if had_http_flood:
        try:
            _sync_http_flood_zones(db)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    storage_quota.forget_user_storage(owner_id)
    log_action(db, current_user.id, "delete_website", domain, ssl_note, request=request)
    # Say what happened to the certificate: "kept" is the surprising outcome and
    # the admin needs to know the lineage is still on the machine.
    message = f"Deleted {domain}."
    if ssl_note:
        message = f"{message} {ssl_note[0].upper()}{ssl_note[1:]}"
    return {"ok": True, "message": message}


@router.post("/{website_id}/fix-nginx-security")
def fix_nginx_security(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    result = waf.sync_website_rules(website)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=_command_error(result))
    if website.http_flood_enabled:
        try:
            _sync_http_flood_zones(db)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = _rewrite_website_vhost(website)
    log_action(db, current_user.id, "fix_nginx_security", website.domain)
    return {"message": f"Rewrote Nginx security template for {website.domain}", "path": target}


@router.post("/{website_id}/ssl", response_model=WebsiteOut)
def enable_ssl(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    _block_if_source(db, website)
    previous_manual_paths = (website.ssl_cert_path, website.ssl_key_path, website.ssl_ca_path)
    previous_snapshot = ssl.snapshot_manual_ssl_domain(website.domain)
    if getattr(website, "ssl_mode", "none") == "manual":
        try:
            _rewrite_website_vhost(
                website,
                preserve_existing_ssl=False,
                include_ssl=False,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Cannot prepare Nginx config for Let's Encrypt: {exc}") from exc
    result = ssl.issue_ssl(website.domain, _ssl_domains(website))
    if result.returncode != 0:
        if getattr(website, "ssl_mode", "none") == "manual":
            ssl.restore_manual_ssl(previous_snapshot)
            try:
                _rewrite_website_vhost(website)
            except (RuntimeError, ValueError):
                pass
        raise HTTPException(status_code=500, detail=_command_error(result))
    website.ssl_enabled = True
    website.ssl_mode = "letsencrypt"
    website.ssl_updated_at = datetime.utcnow()
    website.ssl_cert_path = None
    website.ssl_key_path = None
    website.ssl_ca_path = None
    website.ssl_source_domain = None
    ssl.remove_manual_ssl_files(*previous_manual_paths)
    if _redirect_domains(website):
        # A redirect-domain alias has no server block of its own until this
        # runs (see nginx._append_certbot_redirect_vhosts) - it only gets one
        # here, reusing the certificate file this site now has, never from
        # certbot's own nginx plugin (that only ever touches "$domain" for
        # exactly this reason: it has no way to create a new, correctly
        # confined block for an alias, and falls back to cloning whatever
        # server block it finds first).
        try:
            _rewrite_website_vhost(website)
        except (RuntimeError, ValueError):
            pass
    _sync_alias_ssl_flags(website)
    _resync_shared_dependents(db, website.domain)
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "enable_ssl", website.domain)
    return website


@router.post("/{website_id}/ssl/manual", response_model=WebsiteOut)
async def install_manual_ssl(
    website_id: int,
    request: Request,
    certificate: UploadFile | None = File(default=None),
    private_key: UploadFile | None = File(default=None),
    ca_bundle: UploadFile | None = File(default=None),
    certificate_text: str | None = Form(default=None),
    private_key_text: str | None = Form(default=None),
    ca_bundle_text: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not _is_authorized_for_website(db, website, current_user):
        ensure_role(current_user.role, Role.admin)
    _block_if_source(db, website)

    try:
        cert_raw = _read_ssl_input(certificate, certificate_text, "certificate")
        key_raw = _read_ssl_input(private_key, private_key_text, "private_key")
        ca_raw = _read_ssl_input(ca_bundle, ca_bundle_text, "ca_bundle", required=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        previous_snapshot = ssl.snapshot_manual_ssl_domain(website.domain)
        written = ssl.install_manual_ssl(website.domain, cert_raw, key_raw, ca_raw, aliases=_ssl_domains(website))
        website.ssl_enabled = True
        website.ssl_mode = "manual"
        website.ssl_cert_path = written["cert"]
        website.ssl_key_path = written["key"]
        website.ssl_ca_path = written["ca"]
        website.ssl_updated_at = datetime.utcnow()
        result = waf.sync_website_rules(website)
        if result.returncode != 0:
            raise RuntimeError(_command_error(result))
        if website.http_flood_enabled:
            _sync_http_flood_zones(db)
        _rewrite_website_vhost(website)
    except (RuntimeError, ValueError) as exc:
        ssl.restore_manual_ssl(previous_snapshot)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        ssl.restore_manual_ssl(previous_snapshot)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _resync_shared_dependents(db, website.domain)
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "install_manual_ssl", website.domain, request=request)
    return website


# --- shared / wildcard SSL ------------------------------------------------------


def _shared_dependents(db: Session, source_domain: str) -> list[Website]:
    return (
        db.query(Website)
        .filter(Website.ssl_mode == "shared", Website.ssl_source_domain == source_domain)
        .all()
    )


def _block_if_source(db: Session, website: Website) -> None:
    dependents = _shared_dependents(db, website.domain)
    if dependents:
        names = ", ".join(sorted(d.domain for d in dependents))
        raise HTTPException(
            status_code=409,
            detail=f"{len(dependents)} website(s) borrow this certificate ({names}). "
            "Change their SSL first.",
        )


def _resync_shared_dependents(db: Session, source_domain: str) -> None:
    """A source's cert just changed — repoint every borrower's vhost at it."""
    for dependent in _shared_dependents(db, source_domain):
        try:
            _rewrite_website_vhost(dependent)
        except (RuntimeError, ValueError):
            pass


def _cloudflare_zone_for(db: Session, domain: str) -> tuple[str | None, str | None]:
    """(zone, token) for ``domain``, resolved from a saved Cloudflare credential.
    Tries each saved zone that is a suffix of the domain, longest first."""
    domain = domain.strip().lower()
    rows = db.query(CloudflareCredential).all()
    matches = [
        r for r in rows
        if domain == r.zone or domain.endswith("." + r.zone)
    ]
    if not matches:
        return None, None
    best = max(matches, key=lambda r: len(r.zone))
    return best.zone, cloudflare.get_token(db, best.zone)


@router.get("/{website_id}/ssl/cloudflare-zone", response_model=CloudflareZoneOut)
def cloudflare_zone(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _get_authorized_website(db, website_id, current_user)
    zone, _token = _cloudflare_zone_for(db, website.domain)
    return CloudflareZoneOut(zone=zone, has_token=zone is not None)


@router.post("/{website_id}/ssl/wildcard", response_model=WebsiteOut)
def install_wildcard_ssl(
    website_id: int,
    payload: WildcardSslRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = _get_authorized_website(db, website_id, current_user)

    token = payload.cloudflare_api_token
    if token:
        try:
            cloudflare.verify_token(token)
            zone = cloudflare.zone_for_domain(token, website.domain)
        except cloudflare.CloudflareError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cloudflare.save_credential(db, zone, token)
    else:
        zone, token = _cloudflare_zone_for(db, website.domain)
        if not token:
            raise HTTPException(
                status_code=409,
                detail="No Cloudflare API token saved for this domain's zone. Provide one.",
            )

    plugin = ssl.ensure_cloudflare_plugin()
    if plugin.returncode != 0:
        raise HTTPException(status_code=500, detail=_command_error(plugin))

    previous = (website.ssl_mode, website.ssl_source_domain, website.ssl_cert_path, website.ssl_key_path, website.ssl_ca_path)
    result = ssl.issue_wildcard_ssl(zone, token, settings.ssl_email or "")
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=_command_error(result))

    website.ssl_enabled = True
    website.ssl_mode = "cloudflare"
    website.ssl_source_domain = zone
    website.ssl_cert_path = None
    website.ssl_key_path = None
    website.ssl_ca_path = None
    website.ssl_updated_at = datetime.utcnow()
    try:
        waf_result = waf.sync_website_rules(website)
        if waf_result.returncode != 0:
            raise RuntimeError(_command_error(waf_result))
        if website.http_flood_enabled:
            _sync_http_flood_zones(db)
        _rewrite_website_vhost(website)
    except Exception as exc:  # noqa: BLE001 - roll the row back on any wiring failure
        (
            website.ssl_mode,
            website.ssl_source_domain,
            website.ssl_cert_path,
            website.ssl_key_path,
            website.ssl_ca_path,
        ) = previous
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _resync_shared_dependents(db, website.domain)
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "install_wildcard_ssl", website.domain, detail=zone, request=request)
    return website


@router.get("/{website_id}/ssl/sources", response_model=List[SslSourceOut])
def ssl_sources(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _get_authorized_website(db, website_id, current_user)
    candidates = db.query(Website).filter(Website.id != website.id, Website.ssl_enabled.is_(True)).all()
    if not is_admin_role(current_user.role):
        candidates = [c for c in candidates if c.owner_id == current_user.id]
    out: list[SslSourceOut] = []
    for candidate in candidates:
        info = ssl.cert_info(candidate.ssl_source_domain or candidate.domain)
        sans = info.get("sans") or []
        if not sans or not ssl.cert_covers(sans, website.domain):
            continue
        out.append(
            SslSourceOut(
                domain=candidate.domain,
                ssl_mode=candidate.ssl_mode,
                wildcard=any(name.startswith("*.") for name in sans),
                not_after=info.get("not_after", ""),
            )
        )
    return out


@router.post("/{website_id}/ssl/shared", response_model=WebsiteOut)
def install_shared_ssl(
    website_id: int,
    payload: SharedSslRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = _get_authorized_website(db, website_id, current_user)
    _block_if_source(db, website)
    if payload.source_domain == website.domain:
        raise HTTPException(status_code=400, detail="A website cannot borrow its own certificate")

    source = db.query(Website).filter(Website.domain == payload.source_domain).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source website not found")
    if source.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)

    cert_name = source.ssl_source_domain or source.domain
    info = ssl.cert_info(cert_name)
    if not info.get("sans"):
        raise HTTPException(status_code=400, detail=f"No usable certificate found for {source.domain}")
    if not ssl.cert_covers(info["sans"], website.domain):
        raise HTTPException(
            status_code=400,
            detail=f"{source.domain}'s certificate does not cover {website.domain}",
        )

    previous = (website.ssl_enabled, website.ssl_mode, website.ssl_source_domain)
    website.ssl_enabled = True
    website.ssl_mode = "shared"
    website.ssl_source_domain = cert_name
    website.ssl_cert_path = None
    website.ssl_key_path = None
    website.ssl_ca_path = None
    website.ssl_updated_at = datetime.utcnow()
    try:
        waf_result = waf.sync_website_rules(website)
        if waf_result.returncode != 0:
            raise RuntimeError(_command_error(waf_result))
        if website.http_flood_enabled:
            _sync_http_flood_zones(db)
        _rewrite_website_vhost(website)
    except Exception as exc:  # noqa: BLE001 - roll the row back on any wiring failure
        website.ssl_enabled, website.ssl_mode, website.ssl_source_domain = previous
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(website)
    log_action(db, current_user.id, "install_shared_ssl", website.domain, detail=cert_name, request=request)
    return website
