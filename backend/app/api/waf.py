from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role, normalize_role
from app.models.entities import User, Website
from app.schemas.schemas import WafBotBlockApply, WafGlobalBotsUpdate, WebsiteAccessLogsOut, WebsiteBotBlockUpdate
from app.services import nginx, panel_settings, waf
from app.services.audit import log_action

router = APIRouter(prefix="/waf", tags=["waf"])


class WafCustomRulesUpdate(BaseModel):
    content: str = ""


class WebsiteWafRulesUpdate(BaseModel):
    enabled_rule_ids: list[str] = Field(default_factory=list)
    custom_rules: str = ""


def _require_admin(current_user: User) -> None:
    ensure_role(current_user.role, Role.admin)


def may_manage_waf(user: User) -> bool:
    """Whether this account may work on the WAF of a website it owns.

    UserPackage.waf_enabled has existed, been editable and been displayed since
    packages were added, and was never read by anything - the same state
    terminal_enabled was in. It is the switch an admin already expects to mean
    this, so it is the one used. Its default is True, so an account with no
    package keeps access rather than silently losing a feature that is being
    granted here for the first time.
    """
    if is_admin_role(user.role):
        return True
    package = getattr(user, "package", None)
    return bool(getattr(package, "waf_enabled", True)) if package else True


def _owned_website(db: Session, website_id: int, current_user: User) -> Website:
    """A website the caller may configure: their own, or any if admin.

    404 rather than 403 for a site owned by somebody else, so this cannot be
    used to enumerate which website ids exist on the server.
    """
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if not is_admin_role(current_user.role):
        is_reseller_sub = False
        if normalize_role(current_user.role) == Role.reseller:
            owner = db.query(User).filter(User.id == website.owner_id).first()
            is_reseller_sub = bool(owner and owner.reseller_id == current_user.id)
        if website.owner_id != current_user.id and not is_reseller_sub:
            raise HTTPException(status_code=404, detail="Website not found")
        if not may_manage_waf(current_user):
            raise HTTPException(status_code=403, detail="Your hosting package does not include WAF settings")
    return website


def _website_or_404(db: Session, website_id: int) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    return website


@router.get("/status")
def get_waf_status(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return waf.status().__dict__


@router.get("/rules")
def get_waf_rules(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    status = waf.status()
    default_rules = waf.default_rules()
    custom_rules = waf.custom_rules()
    return {
        "status": status.__dict__,
        "default_rules": default_rules.stdout,
        "default_rule_definitions": waf.default_rule_definitions(),
        "custom_rules": custom_rules.stdout,
    }


@router.get("/access-logs", response_model=WebsiteAccessLogsOut)
def get_waf_access_logs(
    website_id: int | None = Query(default=None, ge=1),
    verdict: str = Query(default="all", pattern="^(all|allow|block|error)$"),
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    lines: int = Query(default=5000, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Website).order_by(Website.domain.asc())
    if not is_admin_role(current_user.role):
        if not may_manage_waf(current_user):
            raise HTTPException(status_code=403, detail="Your hosting package does not include WAF settings")
        # Without this an end user asking for no website_id would be handed
        # every site's access log on the server.
        if normalize_role(current_user.role) == Role.reseller:
            sub_ids = db.query(User.id).filter(User.reseller_id == current_user.id)
            query = query.filter((Website.owner_id == current_user.id) | (Website.owner_id.in_(sub_ids)))
        else:
            query = query.filter(Website.owner_id == current_user.id)
    if website_id is not None:
        query = query.filter(Website.id == website_id)
    websites = query.all()
    if website_id and not websites:
        raise HTTPException(status_code=404, detail="Website not found")
    try:
        return waf.access_logs(websites, verdict=verdict, query=q, limit=limit, lines=lines)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/access-logs")
def clear_waf_access_logs(
    website_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Website).order_by(Website.domain.asc())
    if not is_admin_role(current_user.role):
        if not may_manage_waf(current_user):
            raise HTTPException(status_code=403, detail="Your hosting package does not include WAF settings")
        if normalize_role(current_user.role) == Role.reseller:
            sub_ids = db.query(User.id).filter(User.reseller_id == current_user.id)
            query = query.filter((Website.owner_id == current_user.id) | (Website.owner_id.in_(sub_ids)))
        else:
            query = query.filter(Website.owner_id == current_user.id)
    if website_id is not None:
        query = query.filter(Website.id == website_id)
    websites = query.all()
    if website_id and not websites:
        raise HTTPException(status_code=404, detail="Website not found")
    try:
        cleared = waf.clear_access_logs(websites)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Cleared access logs for {cleared} website(s).", "cleared": cleared}


@router.get("/websites/{website_id}")
def get_website_waf(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _owned_website(db, website_id, current_user)
    data = waf.site_config(website)
    # The custom-rules box is admin-only to write; tell the UI so it can show it
    # read-only rather than offering an edit that will be refused.
    data["may_edit_custom_rules"] = is_admin_role(current_user.role)
    return data


@router.put("/websites/{website_id}")
def save_website_waf(payload: WebsiteWafRulesUpdate, website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _owned_website(db, website_id, current_user)
    custom_rules = payload.custom_rules
    if not is_admin_role(current_user.role):
        # Custom rules are arbitrary ModSecurity directives loaded into nginx.
        # A SecRule can run a script, or read a file the nginx worker can reach,
        # so letting a customer write them would hand out code execution on a
        # shared server. Toggling the shipped rules is safe; this is not.
        existing = waf.website_custom_rules(website)
        if (custom_rules or "").strip() != (existing or "").strip():
            raise HTTPException(
                status_code=403,
                detail="Custom WAF rules can only be changed by an administrator",
            )
        custom_rules = existing
    try:
        result = waf.save_website_config(website, payload.enabled_rule_ids, custom_rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not save WAF rules").strip())
    db.add(website)
    db.commit()
    db.refresh(website)
    if website.waf_enabled:
        try:
            nginx.update_waf_block(website.domain, True)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = waf.site_config(website)
    data["message"] = "Website WAF rules saved."
    return data


@router.get("/bots")
def list_blocked_bots(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Every website and the bot list it currently blocks.

    This is what the Bot blocking screen reads: the operator needs to see which
    sites are already covered before applying a list to more of them.
    """
    query = db.query(Website).order_by(Website.domain)
    if not is_admin_role(current_user.role):
        if not may_manage_waf(current_user):
            raise HTTPException(status_code=403, detail="Your hosting package does not include WAF settings")
        if normalize_role(current_user.role) == Role.reseller:
            sub_ids = db.query(User.id).filter(User.reseller_id == current_user.id)
            query = query.filter((Website.owner_id == current_user.id) | (Website.owner_id.in_(sub_ids)))
        else:
            query = query.filter(Website.owner_id == current_user.id)
    websites = query.all()
    return {
        "max_bots": nginx.MAX_BLOCKED_BOTS,
        "global_blocked_bots": panel_settings.global_blocked_bots(),
        "websites": [
            {
                "website_id": site.id,
                "domain": site.domain,
                # What the site adds on its own, and what it ends up enforcing.
                "blocked_bots": waf.website_blocked_bots(site),
                "effective_blocked_bots": waf.effective_blocked_bots(site),
            }
            for site in websites
        ],
    }


@router.put("/bots/global")
def save_global_bots(
    payload: WafGlobalBotsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace the server-wide bad-bot list and re-render every vhost.

    One list for the whole server is the point: adding a bot here protects
    every site at once instead of being copied into each one, where the copies
    then drift apart.
    """
    _require_admin(current_user)
    try:
        bots = panel_settings.save_global_blocked_bots(payload.blocked_bots)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    websites = db.query(Website).order_by(Website.domain).all()
    applied, failed = waf.resync_bot_blocks(websites)

    message = f"{len(bots)} bot(s) blocked globally; {len(applied)} website(s) updated."
    if failed:
        message += f" {len(failed)} failed."
    return {
        "global_blocked_bots": bots,
        "applied": applied,
        "failed": failed,
        "message": message,
    }


@router.put("/websites/{website_id}/bots")
def save_website_bots(
    payload: WebsiteBotBlockUpdate,
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = _owned_website(db, website_id, current_user)
    try:
        bots = waf.save_website_blocked_bots(website, payload.blocked_bots, mode="replace")
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(website)
    db.commit()
    db.refresh(website)
    return {
        "website_id": website.id,
        "domain": website.domain,
        "blocked_bots": bots,
        "message": f"{len(bots)} bot(s) blocked on {website.domain}.",
    }


@router.post("/bots/apply")
def apply_blocked_bots(
    payload: WafBotBlockApply,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply one list to several websites in a single call.

    Each site is written and reloaded independently, and a failure on one is
    reported without abandoning the rest - applying a list to twenty sites
    should not leave the operator guessing which of them took effect.
    """
    _require_admin(current_user)
    if not payload.website_ids:
        raise HTTPException(status_code=400, detail="Select at least one website.")

    websites = db.query(Website).filter(Website.id.in_(payload.website_ids)).all()
    found = {site.id for site in websites}
    missing = [wid for wid in payload.website_ids if wid not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Website not found: {missing}")

    applied, failed = [], []
    for site in websites:
        try:
            bots = waf.save_website_blocked_bots(site, payload.blocked_bots, mode=payload.mode)
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            failed.append({"domain": site.domain, "error": str(exc)})
            continue
        db.add(site)
        applied.append({"website_id": site.id, "domain": site.domain, "blocked_bots": bots})
    db.commit()

    message = f"Applied to {len(applied)} website(s)."
    if failed:
        message += f" {len(failed)} failed."
    return {"applied": applied, "failed": failed, "message": message}


@router.put("/rules/custom")
def save_waf_custom_rules(payload: WafCustomRulesUpdate, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        result = waf.save_custom_rules(payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not save WAF rules").strip())
    # These apply to every website with the WAF on; who changed them matters.
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        log_action(db, current_user.id, "waf_global_custom_rules", "global",
                   f"{payload.content.count('SecRule')} rule(s)")
    finally:
        db.close()
    return result.__dict__


@router.post("/install")
def install_waf(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return waf.install_engine().__dict__


@router.post("/update-rules")
def update_waf_rules(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return waf.update_rules().__dict__


@router.get("/orphans")
def scan_orphans(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """What deleted websites left behind. Touches nothing."""
    _require_admin(current_user)
    from app.services import orphans

    try:
        outcome = orphans.scan(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    outcome["message"] = (
        f"{outcome['total']} orphaned item(s) on disk." if outcome["total"]
        else "Nothing orphaned."
    )
    return outcome


@router.post("/orphans/clean")
def clean_orphans(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove them, after copying everything to /root/spanel-removed."""
    _require_admin(current_user)
    from app.services import orphans

    try:
        outcome = orphans.clean(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    outcome["message"] = orphans.describe(outcome)
    return outcome


class CrsModeUpdate(BaseModel):
    mode: str = "off"


class WebsiteCrsUpdate(BaseModel):
    enabled: bool = False


@router.get("/crs")
def get_crs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    status = waf.crs_status()
    status["modes"] = list(waf.CRS_MODES)
    websites = db.query(Website).all()
    opted_in = [w for w in websites if waf.site_uses_crs(w)]
    status["websites"] = [
        {
            "website_id": w.id,
            "domain": w.domain,
            "waf_enabled": bool(w.waf_enabled),
            "crs_enabled": bool(getattr(w, "crs_enabled", False)),
        }
        for w in websites
    ]
    status["sites_opted_in"] = len(opted_in)
    # CRS is the one WAF feature with a memory bill, and it is large enough that
    # an admin should see it before switching anything on.
    status["rss_mb_per_site"] = waf.CRS_RSS_MB_PER_SITE
    status["estimated_rss_mb"] = waf.crs_memory_estimate(len(opted_in))
    return status


@router.put("/websites/{website_id}/crs")
def set_website_crs(
    payload: WebsiteCrsUpdate,
    website_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # The site's own switch, on its WAF page: whoever may manage this site's
    # WAF decides whether it loads CRS (operator, 2026-09-25). The server-wide
    # mode stays admin-only, and while it is off nothing loads at all.
    website = _owned_website(db, website_id, current_user)
    website.crs_enabled = bool(payload.enabled)
    db.add(website)
    db.commit()
    db.refresh(website)
    result = waf.sync_website_rules(website)
    if result.returncode != 0:
        website.crs_enabled = not bool(payload.enabled)
        db.add(website)
        db.commit()
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not apply CRS").strip())
    mode = waf.active_crs_mode()
    # Every other per-site protection switch leaves an audit entry; this one did
    # not, so there was no way to tell who turned CRS on for a site or when.
    log_action(
        db,
        current_user.id,
        "update_website_crs",
        website.domain,
        "enabled" if website.crs_enabled else "disabled",
        request=request,
    )
    if not payload.enabled:
        message = f"OWASP CRS is off for {website.domain}."
    elif mode == "off":
        message = (
            f"{website.domain} is opted in, but OWASP CRS is switched off server-wide, "
            "so nothing is loaded yet."
        )
    else:
        message = (
            f"OWASP CRS is {mode} on {website.domain}. "
            f"Restart nginx to see the memory change; expect about {waf.CRS_RSS_MB_PER_SITE} MB for this site."
        )
    return {"ok": True, "message": message, "crs_enabled": bool(website.crs_enabled), "mode": mode}


@router.put("/crs")
def set_crs(payload: CrsModeUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    websites = db.query(Website).all()
    try:
        outcome = waf.set_crs_mode(payload.mode, websites)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mode = outcome["mode"]
    if mode == "off":
        message = "OWASP CRS is off."
    elif mode == "detect":
        message = "OWASP CRS is in detect mode: every rule logs, nothing is blocked."
    else:
        message = "OWASP CRS is blocking at paranoia level 1."
    if outcome["failures"]:
        names = ", ".join(item["domain"] for item in outcome["failures"][:5])
        message = f"{message} {len(outcome['failures'])} site(s) could not be updated: {names}"
    # Switching every site to blocking is the largest single change an admin can
    # make here, and it left no trace at all.
    log_action(
        db, current_user.id, "update_crs_mode", mode,
        f"{outcome.get('sites_using_crs', 0)} site(s)", request=request,
    )
    return {"ok": True, "message": message, **outcome}
