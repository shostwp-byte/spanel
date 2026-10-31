"""Per-website SFTP sub-accounts.

The credential a customer hands to someone who should reach one of their sites
and nothing else - not their panel password, and not their own Linux account,
which opens every site they own.

Ownership is checked against the website, and a site belonging to someone else
answers 404 rather than 403, so this cannot be used to enumerate website ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import is_admin_role
from app.models.entities import SftpAccount, User, Website
from app.services import panel_settings, sftp_accounts, site_users
from app.services.audit import log_action

router = APIRouter(prefix="/sftp-accounts", tags=["sftp"])


class SftpAccountCreate(BaseModel):
    website_id: int
    label: str = Field(min_length=2, max_length=32)
    # Optional: when absent the panel generates one and returns it once.
    password: Optional[str] = None


class SftpAccountPasswordUpdate(BaseModel):
    password: Optional[str] = None


class SftpAccountOut(BaseModel):
    id: int
    website_id: int
    domain: str
    label: str
    username: str
    host: str
    port: int
    path: str
    is_active: bool
    created_at: Optional[datetime] = None
    password_set_at: Optional[datetime] = None


def _sftp_host() -> str:
    """Where the customer points their SFTP client.

    The panel hostname if one is configured, because that already resolves to
    this machine and is what the customer knows. Otherwise the first IPv4 the
    box answers on.
    """
    try:
        _scheme, hostname, _port = panel_settings.parse_panel_url(
            panel_settings.configured_panel_url()
        )
        if hostname:
            return hostname
    except Exception:  # noqa: BLE001 - a malformed setting must not break listing
        pass
    try:
        from app.services import server_network

        addresses = server_network.ipv4_addresses()
        if addresses:
            return addresses[0]
    except Exception:  # noqa: BLE001 - same
        pass
    return ""


def effective_sftp_limit(user: User | None) -> int:
    from app.services import quota

    return quota.effective_limit(user, "sftp_accounts_limit")


_limit_for = effective_sftp_limit


def _owned_website(db: Session, website_id: int, current_user: User) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not is_admin_role(current_user.role) and website.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Website not found")
    return website


def _owned_account(db: Session, account_id: int, current_user: User) -> SftpAccount:
    account = db.query(SftpAccount).filter(SftpAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="SFTP account not found")
    if not is_admin_role(current_user.role) and account.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="SFTP account not found")
    return account


def _out(account: SftpAccount, domain: str, host: str) -> SftpAccountOut:
    hint = sftp_accounts.connection_hint(account.linux_user, domain, host)
    return SftpAccountOut(
        id=account.id,
        website_id=account.website_id,
        domain=domain,
        label=account.label,
        username=hint["username"],
        host=hint["host"],
        port=hint["port"],
        path=hint["path"],
        is_active=bool(account.is_active),
        created_at=account.created_at,
        password_set_at=account.password_set_at,
    )


@router.get("", response_model=list[SftpAccountOut])
def list_accounts(
    website_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(SftpAccount)
    if not is_admin_role(current_user.role):
        query = query.filter(SftpAccount.owner_id == current_user.id)
    if website_id is not None:
        query = query.filter(SftpAccount.website_id == website_id)
    accounts = query.order_by(SftpAccount.id).all()
    if not accounts:
        return []
    domains = {
        w.id: w.domain
        for w in db.query(Website)
        .filter(Website.id.in_({a.website_id for a in accounts}))
        .all()
    }
    host = _sftp_host()
    return [_out(a, domains.get(a.website_id, ""), host) for a in accounts]


@router.post("", response_model=dict)
def create_account(
    payload: SftpAccountCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = _owned_website(db, payload.website_id, current_user)
    owner_id = website.owner_id

    if not is_admin_role(current_user.role):
        limit = _limit_for(current_user)
        if limit == 0:
            raise HTTPException(
                status_code=403,
                detail="Your hosting package does not include SFTP accounts — ask your administrator to raise the limit on your package or account.",
            )
        used = db.query(SftpAccount).filter(SftpAccount.owner_id == owner_id).count()
        if limit > 0 and used >= limit:
            raise HTTPException(
                status_code=409,
                detail=f"SFTP account limit reached ({used}/{limit})",
            )

    try:
        label = sftp_accounts.validate_label(payload.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = (
        db.query(SftpAccount)
        .filter(
            SftpAccount.owner_id == owner_id,
            SftpAccount.website_id == website.id,
            SftpAccount.label == label,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409, detail=f"An SFTP account named {label!r} already exists for this website"
        )

    owner = db.query(User).filter(User.id == owner_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Website owner not found")

    password = payload.password or sftp_accounts.generate_password()
    generated = payload.password is None
    try:
        sftp_accounts.validate_password(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    linux_user = sftp_accounts.linux_user_for(owner.username, website.id, label)
    if db.query(SftpAccount).filter(SftpAccount.linux_user == linux_user).first():
        raise HTTPException(status_code=409, detail="That SFTP account already exists")

    # The website's own Linux user owns the files; fall back to the owner's
    # panel username for sites provisioned before linux_user was recorded.
    owner_linux_user = website.linux_user or site_users.linux_user_for_panel_username(
        owner.username
    )
    try:
        chroot_path = sftp_accounts.ensure_account(
            owner_linux_user, linux_user, website.root_path
        )
        sftp_accounts.set_password(linux_user, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    account = SftpAccount(
        owner_id=owner_id,
        website_id=website.id,
        label=label,
        linux_user=linux_user,
        chroot_path=chroot_path,
        is_active=True,
        password_set_at=datetime.utcnow(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    log_action(db, current_user.id, "create_sftp_account", website.domain, linux_user, request=request)

    out = _out(account, website.domain, _sftp_host())
    body = out.model_dump()
    # Returned once, and only when the panel invented it. A password the user
    # chose is never echoed back.
    body["password"] = password if generated else None
    return body


@router.post("/{account_id}/password", response_model=dict)
def reset_password(
    account_id: int,
    payload: SftpAccountPasswordUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = _owned_account(db, account_id, current_user)
    password = payload.password or sftp_accounts.generate_password()
    generated = payload.password is None
    try:
        sftp_accounts.validate_password(password)
        sftp_accounts.set_password(account.linux_user, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    account.password_set_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user.id, "reset_sftp_password", account.linux_user, request=request)
    return {"message": "SFTP password updated", "password": password if generated else None}


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = _owned_account(db, account_id, current_user)
    linux_user = account.linux_user
    sftp_accounts.delete_account(linux_user)
    db.delete(account)
    db.commit()
    log_action(db, current_user.id, "delete_sftp_account", linux_user, request=request)
    return {"message": "SFTP account removed"}


@router.get("/limits")
def account_limits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What the UI needs to decide whether to offer the button at all."""
    from app.services import quota

    limit = _limit_for(current_user)
    used = db.query(SftpAccount).filter(SftpAccount.owner_id == current_user.id).count()
    if is_admin_role(current_user.role) or quota.is_quota_unlimited(limit):
        return {"limit": None if is_admin_role(current_user.role) else -1, "used": used, "unlimited": True}
    return {"limit": limit, "used": used, "unlimited": False}
