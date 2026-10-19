"""DNS Manager: zones and records on this server's PowerDNS (services/dns.py).

Every route needs the addon. An administrator sees and edits every zone and
the settings; a customer sees and edits the zones of their own websites, and
cannot create zones - a zone comes with a website, or from an administrator.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import User
from app.services import addons, dns, server_network
from app.services.audit import log_action

router = APIRouter(prefix="/dns", tags=["dns"], dependencies=[Depends(addons.require_dns)])


class RecordIn(BaseModel):
    name: str = Field(default="@", max_length=253)
    type: str = Field(max_length=10)
    ttl: int = Field(default=dns.DEFAULT_TTL, ge=dns.MIN_TTL, le=dns.MAX_TTL)
    content: str = Field(max_length=4096)
    priority: int | None = Field(default=None, ge=0, le=65535)


class RecordChange(BaseModel):
    original: RecordIn
    record: RecordIn


class ZoneIn(BaseModel):
    name: str = Field(max_length=253)
    owner_id: int | None = None


class SettingsIn(BaseModel):
    nameservers: list[str] = Field(default_factory=list, max_length=4)
    zone_ip: str = Field(default="", max_length=45)
    ttl: int = Field(default=dns.DEFAULT_TTL, ge=dns.MIN_TTL, le=dns.MAX_TTL)
    auto_zone: bool = True
    # Records for new zones; None keeps the one saved (an older page sends none).
    template: str | None = Field(default=None, max_length=10000)


def _answer(action):
    """Run a DNS change and turn its failures into HTTP answers."""
    try:
        return action()
    except dns.DnsInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except dns.DnsError as exc:
        raise HTTPException(status_code=404 if exc.status == 404 else 502, detail=str(exc)) from exc


@router.get("/overview")
def overview(current_user: User = Depends(get_current_user)):
    """What every account's DNS page needs first: the nameservers a registrar
    is given, the default TTL, and this server's addresses."""
    ensure_role(current_user.role, Role.end_user)
    config = dns.settings()
    ipv4 = [config["zone_ip"]] if config["zone_ip"] else []
    ipv4 += [ip for ip in server_network.ipv4_addresses() if ip not in ipv4]
    return {
        "installed": dns.active(),
        "is_admin": is_admin_role(current_user.role),
        "nameservers": config["nameservers"],
        "default_ttl": config["ttl"],
        "addresses": {"ipv4": ipv4, "ipv6": server_network.ipv6_addresses()},
    }


@router.get("/zones")
def list_zones(q: str = Query(default="", max_length=253), page: int = Query(default=1, ge=1),
               per_page: int = Query(default=50, ge=1, le=200),
               db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    listed = _answer(lambda: dns.zones_page(db, current_user, q, page, per_page))
    return {
        **listed,
        "zones": listed["items"],
        "nameservers": dns.settings()["nameservers"],
        "can_manage": is_admin_role(current_user.role),
    }


@router.post("/zones")
def create_zone(payload: ZoneIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    if payload.owner_id is not None and not db.query(User).filter(User.id == payload.owner_id).first():
        raise HTTPException(status_code=400, detail="That account does not exist.")
    zone = _answer(lambda: dns.create_zone(db, payload.name, payload.owner_id))
    log_action(db, current_user.id, "dns_create_zone", zone, request=request)
    return {"zone": zone, "message": "Zone created."}


@router.delete("/zones/{zone}")
def delete_zone(zone: str, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    # An administrator's call: every domain on the server has a zone, and a
    # customer's would come back at the next sync while its website exists.
    ensure_role(current_user.role, Role.admin)
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    _answer(lambda: dns.delete_zone(db, name))
    log_action(db, current_user.id, "dns_delete_zone", name, request=request)
    return {"zone": name, "message": "Zone deleted."}


@router.get("/zones/{zone}/records")
def list_records(zone: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    return {"zone": name, "records": _answer(lambda: dns.zone_records(db, name))}


@router.get("/zones/{zone}/delegation")
def zone_delegation(zone: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    return dns.delegation(name)


@router.post("/zones/{zone}/defaults")
def restore_zone_defaults(zone: str, request: Request, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    """Put back the records the panel manages in a zone; the owner's stay."""
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    _answer(lambda: dns.restore_defaults(db, name))
    log_action(db, current_user.id, "dns_restore_defaults", name, request=request)
    return {"zone": name, "records": _answer(lambda: dns.zone_records(db, name)), "message": "The panel's records are back."}


@router.post("/zones/{zone}/records")
def add_record(zone: str, payload: RecordIn, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    admin = is_admin_role(current_user.role)
    _answer(lambda: dns.add_record(name, payload.model_dump(), admin=admin))
    log_action(db, current_user.id, "dns_add_record", name, f"{payload.type} {payload.name}", request=request)
    return {"zone": name, "records": _answer(lambda: dns.zone_records(db, name)), "message": "Record added."}


@router.put("/zones/{zone}/records")
def update_record(zone: str, payload: RecordChange, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    admin = is_admin_role(current_user.role)
    _answer(lambda: dns.update_record(name, payload.original.model_dump(), payload.record.model_dump(), admin=admin))
    log_action(db, current_user.id, "dns_update_record", name, f"{payload.record.type} {payload.record.name}", request=request)
    return {"zone": name, "records": _answer(lambda: dns.zone_records(db, name)), "message": "Record saved."}


@router.post("/zones/{zone}/records/delete")
def delete_record(zone: str, payload: RecordIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    name = _answer(lambda: dns.may_edit(db, current_user, zone))
    admin = is_admin_role(current_user.role)
    _answer(lambda: dns.delete_record(name, payload.model_dump(), admin=admin))
    log_action(db, current_user.id, "dns_delete_record", name, f"{payload.type} {payload.name}", request=request)
    return {"zone": name, "records": _answer(lambda: dns.zone_records(db, name)), "message": "Record deleted."}


@router.get("/settings")
def read_settings(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {**dns.settings(), "server": dns.server_status(), "server_ipv4": server_network.ipv4_addresses()}


@router.put("/settings")
def save_settings(payload: SettingsIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    before = dns.settings()["nameservers"]
    saved = _answer(lambda: dns.save_settings(payload.nameservers, payload.zone_ip, payload.ttl, payload.auto_zone,
                                              payload.template))
    log_action(db, current_user.id, "dns_settings", ", ".join(saved["nameservers"]), request=request)
    # New nameservers go into the NS and SOA records of every zone.
    if saved["nameservers"] != before:
        dns.follow_nameservers(db)
    # Nameservers set for the first time, or the automatic zones switched
    # back on: the domains still without a zone get one now.
    dns.sync_quietly(db)
    return {**saved, "message": "DNS settings saved."}


@router.post("/sync")
def sync_zones(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Give every domain on the server its zone now, and hand each zone to the
    owner of its website."""
    ensure_role(current_user.role, Role.admin)
    summary = _answer(lambda: dns.sync(db))
    if not summary["ready"]:
        raise HTTPException(
            status_code=400,
            detail="Set two nameservers and the IP address for new zones, and turn on the automatic zones, first.",
        )
    log_action(db, current_user.id, "dns_sync", f"{len(summary['zones'])} zone(s)", request=request)
    return {**summary, "message": "Every domain on the server has its DNS zone."}
