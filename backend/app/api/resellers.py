import ipaddress
import re
from typing import List, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.database import get_db
from app.services import storage_quota
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import User, WhiteLabelConfig, WebhookEndpoint, UserPackage, Website, DatabaseAccount

router = APIRouter(prefix="/resellers", tags=["resellers"])

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_SAFE_BRAND_ASSET_RE = re.compile(r"^/brand-assets/(?:logo|favicon|login_background)\.(?:png|jpg|jpeg|webp|ico)$")

class WhiteLabelPublicSchema(BaseModel):
    company_name: str
    logo_url: Optional[str] = None
    primary_color: str
    support_url: Optional[str] = None

class WhiteLabelConfigSchema(BaseModel):
    company_name: str
    panel_domain: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: str
    support_url: Optional[str] = None

class WebhookEndpointCreate(BaseModel):
    url: str
    secret: Optional[str] = None
    events: str = "*"
    is_active: bool = True

class WebhookEndpointUpdate(BaseModel):
    url: Optional[str] = None
    secret: Optional[str] = None
    events: Optional[str] = None
    is_active: Optional[bool] = None

class WebhookEndpointOut(BaseModel):
    id: int
    url: str
    events: str
    is_active: bool

class ResellerStatsOut(BaseModel):
    total_users: int
    total_packages: int
    total_websites: int
    total_databases: int
    total_storage_used_bytes: int = 0
    total_storage_limit_bytes: int = 0
    total_bandwidth_used_bytes: int = 0
    total_bandwidth_limit_bytes: int = 0

@router.get("/stats", response_model=ResellerStatsOut)
def get_reseller_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    users = db.query(User).filter(User.reseller_id == current_user.id).all()
    total_users = len(users)
    total_packages = db.query(UserPackage).filter(UserPackage.owner_id == current_user.id).count()
    total_websites = db.query(Website).join(User, Website.owner_id == User.id).filter(User.reseller_id == current_user.id).count()
    total_databases = db.query(DatabaseAccount).join(User, DatabaseAccount.owner_id == User.id).filter(User.reseller_id == current_user.id).count()

    total_storage_used = 0
    total_storage_limit = 0
    for u in users:
        summary = storage_quota.storage_usage_summary(db, u, use_cache=True)
        total_storage_used += summary.get("storage_used_bytes", 0)
        limit = summary.get("storage_limit_bytes")
        if limit is not None:
            total_storage_limit += limit

    return {
        "total_users": total_users,
        "total_packages": total_packages,
        "total_websites": total_websites,
        "total_databases": total_databases,
        "total_storage_used_bytes": total_storage_used,
        "total_storage_limit_bytes": total_storage_limit,
        "total_bandwidth_used_bytes": 0,
        "total_bandwidth_limit_bytes": 0,
    }


def _validate_whitelabel_url(value: Optional[str], field_name: str, *, is_logo: bool = False) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if any(ch in cleaned for ch in ("<", ">", '"', "'", "\r", "\n", " ")):
        raise HTTPException(status_code=400, detail=f"{field_name} contains invalid characters")
    if cleaned.startswith("/brand-assets/"):
        if not _SAFE_BRAND_ASSET_RE.fullmatch(cleaned):
            raise HTTPException(status_code=400, detail=f"Invalid brand asset path for {field_name}")
        return cleaned
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must start with http://, https://, or /brand-assets/",
        )
    parsed = urlparse(cleaned)
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"Invalid URL for {field_name}")
    if is_logo and parsed.path.lower().endswith(".svg"):
        raise HTTPException(
            status_code=400,
            detail="SVG logos are not permitted for security reasons. Use PNG, JPG, WEBP, or ICO.",
        )
    return cleaned


def _validate_webhook_url(raw_url: str) -> str:
    cleaned = (raw_url or "").strip()
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise HTTPException(status_code=400, detail="Webhook URL must start with http:// or https://")
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost", "localhost.localdomain", "0.0.0.0"}:
        raise HTTPException(status_code=400, detail="Webhook URL cannot target localhost or internal addresses")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(status_code=400, detail="Webhook URL cannot target private or link-local IP addresses")
    except ValueError:
        pass
    return cleaned


@router.get("/whitelabel/public", response_model=WhiteLabelPublicSchema)
def get_whitelabel_public(request: Request, db: Session = Depends(get_db)):
    host = request.headers.get("host", "").split(":")[0]
    config = db.query(WhiteLabelConfig).filter(WhiteLabelConfig.panel_domain == host).first()
    if not config:
        admin_user = db.query(User).filter(User.role == Role.admin).first()
        if admin_user:
            config = db.query(WhiteLabelConfig).filter(WhiteLabelConfig.reseller_id == admin_user.id).first()
    if not config:
        return WhiteLabelPublicSchema(company_name="SPanel", primary_color="#4f46e5")
    return config

@router.get("/whitelabel", response_model=WhiteLabelConfigSchema)
def get_whitelabel(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    config = db.query(WhiteLabelConfig).filter(WhiteLabelConfig.reseller_id == current_user.id).first()
    if not config:
        return WhiteLabelConfigSchema(
            company_name="Control Panel",
            primary_color="#4f46e5"
        )
    return config

@router.put("/whitelabel", response_model=WhiteLabelConfigSchema)
def update_whitelabel(payload: WhiteLabelConfigSchema, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)

    panel_domain = (payload.panel_domain or "").strip().lower() or None
    if panel_domain is not None and not _DOMAIN_RE.fullmatch(panel_domain):
        raise HTTPException(status_code=400, detail="Invalid panel domain format")

    primary_color = (payload.primary_color or "#4f46e5").strip()
    if not _HEX_COLOR_RE.fullmatch(primary_color):
        raise HTTPException(status_code=400, detail="Primary color must be a valid hex color (e.g. #4f46e5)")

    logo_url = _validate_whitelabel_url(payload.logo_url, "logo_url", is_logo=True)
    support_url = _validate_whitelabel_url(payload.support_url, "support_url", is_logo=False)

    if panel_domain is not None:
        duplicate = (
            db.query(WhiteLabelConfig)
            .filter(
                WhiteLabelConfig.panel_domain == panel_domain,
                WhiteLabelConfig.reseller_id != current_user.id,
            )
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Panel domain is already in use")

    config = db.query(WhiteLabelConfig).filter(WhiteLabelConfig.reseller_id == current_user.id).first()
    if not config:
        config = WhiteLabelConfig(reseller_id=current_user.id)
        db.add(config)

    config.company_name = (payload.company_name or "Control Panel").strip()[:120]
    config.panel_domain = panel_domain
    config.logo_url = logo_url
    config.primary_color = primary_color
    config.support_url = support_url

    db.commit()
    db.refresh(config)
    return config


@router.get("/webhooks", response_model=List[WebhookEndpointOut])
def list_webhooks(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    return db.query(WebhookEndpoint).filter(WebhookEndpoint.user_id == current_user.id).all()

@router.post("/webhooks", response_model=WebhookEndpointOut)
def create_webhook(payload: WebhookEndpointCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    safe_url = _validate_webhook_url(payload.url)

    webhook = WebhookEndpoint(
        user_id=current_user.id,
        url=safe_url,
        secret=payload.secret,
        events=payload.events,
        is_active=payload.is_active
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook

@router.put("/webhooks/{webhook_id}", response_model=WebhookEndpointOut)
def update_webhook(webhook_id: int, payload: WebhookEndpointUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)

    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == current_user.id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if payload.url is not None:
        webhook.url = _validate_webhook_url(payload.url)
    if payload.secret is not None:
        webhook.secret = payload.secret
    if payload.events is not None:
        webhook.events = payload.events
    if payload.is_active is not None:
        webhook.is_active = payload.is_active

    db.commit()
    db.refresh(webhook)
    return webhook

@router.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)

    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == current_user.id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    db.delete(webhook)
    db.commit()
    return {"ok": True}
