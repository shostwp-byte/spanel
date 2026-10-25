import ipaddress
import re
from typing import List, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.database import get_db
from app.services import panel_settings, storage_quota, webhooks as webhooks_service
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import ApiToken, User, WhiteLabelConfig, WebhookEndpoint, UserPackage, Website, DatabaseAccount

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
    has_secret: bool = False

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


_EVENT_NAME_RE = re.compile(r"^(\*|[a-z][a-z0-9_]*(\.[a-z0-9_]+)*(\.\*)?)$")


def _validate_events(raw: str) -> str:
    """Comma-separated event names; "*" for all, "account.*" for a family."""
    items = []
    for item in (raw or "").split(","):
        name = item.strip()
        if not name:
            continue
        if len(name) > 100 or not _EVENT_NAME_RE.fullmatch(name):
            raise HTTPException(status_code=400, detail=f"Invalid event name: {name}")
        if name not in items:
            items.append(name)
    return ",".join(items) or "*"


@router.get("/webhooks/events")
def list_webhook_events(current_user: User = Depends(get_current_user)):
    """The event catalogue for the endpoint form."""
    ensure_role(current_user.role, Role.reseller)
    return {
        "events": [{"name": name, "description": desc} for name, desc in webhooks_service.EVENT_CATALOGUE.items()],
        "signature": {
            "timestamp_header": "X-SPanel-Timestamp",
            "signature_header": "X-SPanel-Signature-256",
            "legacy_signature_header": "X-SPanel-Signature",
            "scheme": "sha256=HMAC_SHA256(secret, timestamp + '.' + body)",
            "tolerance_seconds": webhooks_service.SIGNATURE_TOLERANCE_SECONDS,
        },
    }


def _webhook_to_out(webhook: WebhookEndpoint) -> dict:
    return {
        "id": webhook.id,
        "url": webhook.url,
        "events": webhook.events,
        "is_active": bool(webhook.is_active),
        "has_secret": bool(webhook.secret),
    }


@router.get("/webhooks", response_model=List[WebhookEndpointOut])
def list_webhooks(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    rows = db.query(WebhookEndpoint).filter(WebhookEndpoint.user_id == current_user.id).all()
    return [_webhook_to_out(row) for row in rows]

@router.post("/webhooks", response_model=WebhookEndpointOut)
def create_webhook(payload: WebhookEndpointCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    safe_url = _validate_webhook_url(payload.url)

    webhook = WebhookEndpoint(
        user_id=current_user.id,
        url=safe_url,
        secret=webhooks_service.protect_secret(payload.secret),
        events=_validate_events(payload.events),
        is_active=payload.is_active
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return _webhook_to_out(webhook)

@router.put("/webhooks/{webhook_id}", response_model=WebhookEndpointOut)
def update_webhook(webhook_id: int, payload: WebhookEndpointUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)

    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == current_user.id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if payload.url is not None:
        webhook.url = _validate_webhook_url(payload.url)
    if payload.secret is not None and payload.secret.strip():
        webhook.secret = webhooks_service.protect_secret(payload.secret)
    if payload.events is not None:
        webhook.events = _validate_events(payload.events)
    if payload.is_active is not None:
        webhook.is_active = payload.is_active

    db.commit()
    db.refresh(webhook)
    return _webhook_to_out(webhook)


# Declared before /webhooks/{webhook_id}: routes match in order and
# "deliveries" fits that path too, so clearing the history used to fail
# validation on webhook_id (422) and never reach this handler.
@router.delete("/webhooks/deliveries")
def clear_webhook_deliveries(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    removed = webhooks_service.clear_deliveries(
        user_id=current_user.id,
        is_admin=is_admin_role(current_user.role),
    )
    return {"ok": True, "removed": removed, "message": f"Đã xóa {removed} bản ghi lịch sử Webhook."}


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)

    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == current_user.id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    db.delete(webhook)
    db.commit()
    return {"ok": True}


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == current_user.id).first()
    if not webhook and is_admin_role(current_user.role):
        webhook = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == webhook_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    test_payload = {
        "event": "webhook_test",
        "user_id": current_user.id,
        "username": current_user.username,
        "target": "spanel_diagnostic",
        "detail": "Gói tin kiểm tra kết nối Outbound Webhook từ SPanel",
    }
    try:
        secret = webhooks_service.endpoint_secret(webhook)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="Webhook secret cannot be read; set it again") from exc
    delivery = webhooks_service.send_webhook_sync(
        webhook.url,
        secret,
        test_payload,
        webhook_id=webhook.id,
        user_id=current_user.id,
    )
    if delivery.get("status") != "success":
        raise HTTPException(
            status_code=502,
            detail=f"Máy chủ đích phản hồi lỗi ({delivery.get('status_code') or 'Timeout'}): {delivery.get('response_preview')}",
        )
    return delivery


@router.get("/webhooks/deliveries")
def list_webhook_deliveries(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    return {
        "items": webhooks_service.list_deliveries(
            user_id=current_user.id,
            is_admin=is_admin_role(current_user.role),
            limit=50,
        )
    }


@router.post("/webhooks/deliveries/{delivery_id}/replay")
def replay_webhook_delivery(delivery_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    try:
        delivery = webhooks_service.replay_delivery(
            db,
            delivery_id,
            user_id=current_user.id,
            is_admin=is_admin_role(current_user.role),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return delivery


@router.get("/whmcs-diagnostic")
def whmcs_connection_diagnostic(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Comprehensive diagnostic for WHMCS, HostBill, Blesta & Perfex CRM integration."""
    ensure_role(current_user.role, Role.admin)
    from datetime import datetime as _dt

    from app.services.provisioning import has_scope

    now = _dt.utcnow()
    active_tokens = [
        t for t in db.query(ApiToken).filter(ApiToken.is_active == True).all()  # noqa: E712
        if t.revoked_at is None and (t.expires_at is None or t.expires_at > now)
    ]
    write_tokens = [
        t for t in active_tokens
        if has_scope(t, "accounts:write") and has_scope(t, "accounts:read")
    ]
    packages_count = db.query(UserPackage).count()
    p_settings = panel_settings.current_settings()
    ssl_enabled = bool(p_settings.get("ssl_enabled"))
    hostname = p_settings.get("panel_hostname") or ""
    port = int(p_settings.get("panel_port") or 2222)
    deliveries = webhooks_service.list_deliveries(is_admin=True, limit=20)
    # A pending row that has not been tried yet is not a failure.
    failed_deliveries = [
        d for d in deliveries
        if d.get("status") == "failed" or (d.get("status") == "pending" and (d.get("attempts") or 0) > 0)
    ]

    checks = [
        {
            "key": "api_token",
            "title": "Khóa Provisioning API Token (Đọc & Ghi)",
            "ok": len(write_tokens) > 0,
            "status": "ready" if len(write_tokens) > 0 else "warn",
            "detail": (
                f"Đang có {len(write_tokens)} API Token hoạt động đủ quyền provisioning:read & provisioning:write."
                if len(write_tokens) > 0
                else "Chưa có API Token nào có quyền ghi (provisioning:write). Hãy tạo 1 Token ở khung bên dưới để dán vào WHMCS."
            ),
        },
        {
            "key": "packages",
            "title": "Gói dịch vụ Hosting (Hosting Plans / Packages)",
            "ok": packages_count > 0,
            "status": "ready" if packages_count > 0 else "warn",
            "detail": (
                f"Đã cấu hình {packages_count} gói Hosting sẵn sàng đồng bộ sang WHMCS/HostBill qua /api/provisioning/v1/plans."
                if packages_count > 0
                else "Chưa có gói Hosting nào trong Manage User Packages. WHMCS cần ít nhất 1 Package để tạo tài khoản."
            ),
        },
        {
            "key": "panel_https",
            "title": "Giao thức kết nối Panel & Cổng API (Port 2222)",
            "ok": True,
            "status": "ready" if ssl_enabled else "info",
            "detail": (
                f"HTTPS SSL đã bật trên {hostname}:{port} — kết nối WHMCS mã hóa TLS an toàn."
                if ssl_enabled and hostname
                else f"Đang chạy trên cổng {port} (HTTP). Khuyến nghị bật Panel SSL nếu kết nối WHMCS qua Internet."
            ),
        },
        {
            "key": "webhooks_health",
            "title": "Trạng thái Outbound Webhooks & Đồng bộ Sự kiện",
            "ok": len(failed_deliveries) == 0,
            "status": "ready" if len(failed_deliveries) == 0 else "warn",
            "detail": (
                f"Tất cả {len(deliveries)} sự kiện Webhook gần nhất đều gửi thành công 100%."
                if deliveries and len(failed_deliveries) == 0
                else (
                    f"Phát hiện {len(failed_deliveries)}/{len(deliveries)} gói tin Webhook lỗi gần đây — có thể nhấn nút 'Gửi lại (Replay)' ở bảng lịch sử bên dưới."
                    if failed_deliveries
                    else "Sẵn sàng gửi sự kiện (chưa phát sinh gói tin lỗi nào)."
                )
            ),
        },
    ]
    ready_count = sum(1 for c in checks if c["ok"])
    return {
        "ready": ready_count >= 3 and len(write_tokens) > 0 and packages_count > 0,
        "score": f"{ready_count}/{len(checks)}",
        "active_tokens_count": len(active_tokens),
        "write_tokens_count": len(write_tokens),
        "packages_count": packages_count,
        "ssl_enabled": ssl_enabled,
        "panel_port": port,
        "checks": checks,
    }
