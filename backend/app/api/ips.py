import ipaddress
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import ServerIP, User, Website
from app.services import server_network

router = APIRouter(prefix="/ips", tags=["ips"])

_META_PATH = Path("/opt/spanel/backend/data/ip_metadata.json")


def _load_meta() -> Dict[str, Dict[str, Any]]:
    try:
        if _META_PATH.exists():
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_meta(meta: Dict[str, Dict[str, Any]]) -> None:
    try:
        _META_PATH.parent.mkdir(parents=True, exist_ok=True)
        _META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        pass


def _detect_host_interfaces() -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    try:
        proc = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            ifaces = json.loads(proc.stdout)
            for iface in ifaces:
                ifname = iface.get("ifname") or "eth0"
                if ifname == "lo":
                    continue
                for addr in iface.get("addr_info", []):
                    local_ip = addr.get("local")
                    prefixlen = addr.get("prefixlen")
                    if local_ip:
                        mapping[local_ip] = {
                            "device": ifname,
                            "netmask": f"/{prefixlen}" if prefixlen is not None else "/24",
                        }
    except Exception:
        pass
    return mapping


def _default_interface() -> str:
    try:
        proc = subprocess.run(
            ["ip", "-j", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            routes = json.loads(proc.stdout)
            if routes and routes[0].get("dev"):
                return str(routes[0]["dev"])
    except Exception:
        pass
    return "eth0"


class ServerIPBase(BaseModel):
    ip_address: str
    is_shared: bool = False
    assigned_user_id: Optional[int] = None


class ServerIPOut(ServerIPBase):
    id: int
    version: int = 4
    status: str = "free"
    device: str = "eth0"
    netmask: str = "/24"
    reseller_username: str = ""
    assigned_username: str = ""
    users_count: int = 0
    domains_count: int = 0
    nameserver: str = ""
    is_global: bool = False
    is_main_ip: bool = False
    notes: str = ""


class ServerIPCreate(BaseModel):
    ip_address: str
    is_shared: bool = False
    netmask: Optional[str] = "/24"
    device: Optional[str] = None
    assigned_user_id: Optional[int] = None
    nameserver: Optional[str] = ""
    is_global: Optional[bool] = False
    notes: Optional[str] = ""
    bind_to_interface: Optional[bool] = False


class ServerIPAssign(BaseModel):
    assigned_user_id: Optional[int] = None


class ServerIPUpdate(BaseModel):
    is_shared: Optional[bool] = None
    assigned_user_id: Optional[int] = None
    clear_assigned_user: Optional[bool] = False
    netmask: Optional[str] = None
    device: Optional[str] = None
    nameserver: Optional[str] = None
    is_global: Optional[bool] = None
    notes: Optional[str] = None


def _enrich_ip(
    ip: ServerIP,
    main_ip_addr: str,
    iface_map: Dict[str, Dict[str, str]],
    default_dev: str,
    meta: Dict[str, Dict[str, Any]],
    users_by_id: Dict[int, User],
    shared_users_count: int,
    shared_domains_count: int,
    user_domains_count: Dict[int, int],
) -> ServerIPOut:
    addr = (ip.ip_address or "").strip()
    try:
        ip_obj = ipaddress.ip_address(addr)
        ver = ip_obj.version
    except ValueError:
        ver = 6 if ":" in addr else 4

    os_info = iface_map.get(addr, {})
    ip_meta = meta.get(addr, {})

    default_mask = "/64" if ver == 6 else "/24"
    netmask = ip_meta.get("netmask") or os_info.get("netmask") or default_mask
    if not str(netmask).startswith("/"):
        netmask = f"/{netmask}"
    device = ip_meta.get("device") or os_info.get("device") or default_dev
    nameserver = ip_meta.get("nameserver", "")
    is_global = bool(ip_meta.get("is_global", False))
    notes = ip_meta.get("notes", "")
    is_main = addr == main_ip_addr

    assigned_user = users_by_id.get(ip.assigned_user_id) if ip.assigned_user_id else None
    assigned_username = assigned_user.username if assigned_user else ""
    reseller_username = ""
    if assigned_user:
        if assigned_user.role == Role.reseller.value:
            reseller_username = assigned_user.username
        elif assigned_user.reseller_id and assigned_user.reseller_id in users_by_id:
            reseller_username = users_by_id[assigned_user.reseller_id].username

    if is_main and ip.is_shared:
        status_label = "server"
    elif ip.is_shared:
        status_label = "shared"
    elif ip.assigned_user_id:
        status_label = "owned"
    else:
        status_label = "free"

    if ip.is_shared:
        u_count = shared_users_count
        d_count = shared_domains_count
    elif ip.assigned_user_id:
        if assigned_user and assigned_user.role == Role.reseller.value:
            sub_ids = [u.id for u in users_by_id.values() if u.reseller_id == assigned_user.id or u.id == assigned_user.id]
            u_count = len(sub_ids)
            d_count = sum(user_domains_count.get(uid, 0) for uid in sub_ids)
        else:
            u_count = 1
            d_count = user_domains_count.get(ip.assigned_user_id, 0)
    else:
        u_count = 0
        d_count = 0

    return ServerIPOut(
        id=ip.id,
        ip_address=addr,
        is_shared=bool(ip.is_shared),
        assigned_user_id=ip.assigned_user_id,
        version=ver,
        status=status_label,
        device=device,
        netmask=netmask,
        reseller_username=reseller_username,
        assigned_username=assigned_username,
        users_count=u_count,
        domains_count=d_count,
        nameserver=nameserver,
        is_global=is_global,
        is_main_ip=is_main,
        notes=notes,
    )


@router.get("", response_model=List[ServerIPOut])
def get_ips(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ips = db.query(ServerIP).all()
    net_ips: List[str] = []
    try:
        net_ips = server_network.ipv4_addresses()
    except Exception:
        pass

    if not ips and net_ips:
        try:
            main_ip = ServerIP(ip_address=net_ips[0], is_shared=True)
            db.add(main_ip)
            db.commit()
            db.refresh(main_ip)
            ips = [main_ip]
        except Exception:
            pass

    main_ip_addr = net_ips[0] if net_ips else (ips[0].ip_address if ips else "")
    iface_map = _detect_host_interfaces()
    default_dev = _default_interface()
    meta = _load_meta()

    all_users = db.query(User).all()
    users_by_id = {u.id: u for u in all_users}
    all_websites = db.query(Website).all()
    user_domains_count: Dict[int, int] = {}
    for w in all_websites:
        if w.owner_id:
            user_domains_count[w.owner_id] = user_domains_count.get(w.owner_id, 0) + 1

    dedicated_user_ids = {ip.assigned_user_id for ip in ips if ip.assigned_user_id and not ip.is_shared}
    shared_users_count = sum(1 for u in all_users if u.id not in dedicated_user_ids)
    shared_domains_count = sum(1 for w in all_websites if w.owner_id not in dedicated_user_ids)

    visible_ips: List[ServerIP] = []
    if is_admin_role(current_user.role):
        visible_ips = ips
    else:
        ensure_role(current_user.role, Role.reseller)
        for ip in ips:
            if ip.is_shared or ip.assigned_user_id == current_user.id or ip.assigned_user_id is None:
                visible_ips.append(ip)
            else:
                owner = users_by_id.get(ip.assigned_user_id)
                if owner and owner.reseller_id == current_user.id:
                    visible_ips.append(ip)

    return [
        _enrich_ip(
            ip,
            main_ip_addr=main_ip_addr,
            iface_map=iface_map,
            default_dev=default_dev,
            meta=meta,
            users_by_id=users_by_id,
            shared_users_count=shared_users_count,
            shared_domains_count=shared_domains_count,
            user_domains_count=user_domains_count,
        )
        for ip in visible_ips
    ]


@router.post("/sync", response_model=List[ServerIPOut])
def sync_host_ips(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    iface_map = _detect_host_interfaces()
    meta = _load_meta()
    existing_addrs = {ip.ip_address for ip in db.query(ServerIP).all()}

    added = 0
    for addr, info in iface_map.items():
        try:
            ip_obj = ipaddress.ip_address(addr)
            if ip_obj.is_loopback or ip_obj.is_link_local:
                continue
        except ValueError:
            continue
        if addr not in existing_addrs:
            db.add(ServerIP(ip_address=addr, is_shared=(added == 0 and not existing_addrs)))
            existing_addrs.add(addr)
            added += 1
        meta.setdefault(addr, {})
        meta[addr]["device"] = info.get("device", "eth0")
        meta[addr]["netmask"] = info.get("netmask", "/24")

    if added > 0:
        db.commit()
    _save_meta(meta)
    return get_ips(db=db, current_user=current_user)


@router.post("", response_model=ServerIPOut)
def create_ip(payload: ServerIPCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    clean_ip = (payload.ip_address or "").strip()
    try:
        ip_obj = ipaddress.ip_address(clean_ip)
        clean_ip = str(ip_obj)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IPv4/IPv6 address: {clean_ip}") from exc

    existing = db.query(ServerIP).filter(ServerIP.ip_address == clean_ip).first()
    if existing:
        raise HTTPException(status_code=400, detail="IP address already exists in pool")

    new_ip = ServerIP(
        ip_address=clean_ip,
        is_shared=bool(payload.is_shared),
        assigned_user_id=None if payload.is_shared else payload.assigned_user_id,
    )
    db.add(new_ip)
    db.commit()
    db.refresh(new_ip)

    meta = _load_meta()
    default_mask = "/64" if ip_obj.version == 6 else "/24"
    mask = (payload.netmask or default_mask).strip()
    if not mask.startswith("/"):
        mask = f"/{mask}"
    dev = (payload.device or "").strip() or _default_interface()
    meta[clean_ip] = {
        "netmask": mask,
        "device": dev,
        "nameserver": (payload.nameserver or "").strip(),
        "is_global": bool(payload.is_global),
        "notes": (payload.notes or "").strip(),
    }
    _save_meta(meta)

    if payload.bind_to_interface:
        try:
            subprocess.run(
                ["sudo", "/usr/local/libexec/spanel-helper", "network-ip-add", clean_ip, mask.lstrip("/"), dev],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            pass

    all_ips = get_ips(db=db, current_user=current_user)
    for item in all_ips:
        if item.id == new_ip.id:
            return item
    raise HTTPException(status_code=500, detail="Failed to load created IP")


@router.patch("/{ip_id}", response_model=ServerIPOut)
def update_ip(
    ip_id: int,
    payload: ServerIPUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.reseller)
    ip = db.query(ServerIP).filter(ServerIP.id == ip_id).first()
    if not ip:
        raise HTTPException(status_code=404, detail="IP not found")

    is_admin = is_admin_role(current_user.role)
    if not is_admin:
        if ip.assigned_user_id != current_user.id:
            owner = db.query(User).filter(User.id == ip.assigned_user_id).first() if ip.assigned_user_id else None
            if not owner or owner.reseller_id != current_user.id:
                raise HTTPException(status_code=403, detail="You can only modify IPs in your reseller pool")

    if payload.is_shared is not None:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only administrators can change shared/free status")
        ip.is_shared = bool(payload.is_shared)

    if payload.clear_assigned_user:
        ip.assigned_user_id = None if is_admin else current_user.id
    elif payload.assigned_user_id is not None:
        target_user = db.query(User).filter(User.id == payload.assigned_user_id).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="Assigned user not found")
        if not is_admin and target_user.id != current_user.id and target_user.reseller_id != current_user.id:
            raise HTTPException(status_code=403, detail="User does not belong to your reseller account")
        ip.assigned_user_id = target_user.id

    db.commit()
    db.refresh(ip)

    meta = _load_meta()
    entry = meta.setdefault(ip.ip_address, {})
    if payload.netmask is not None and is_admin:
        m = payload.netmask.strip()
        entry["netmask"] = m if m.startswith("/") else f"/{m}"
    if payload.device is not None and is_admin:
        entry["device"] = payload.device.strip() or _default_interface()
    if payload.nameserver is not None:
        entry["nameserver"] = payload.nameserver.strip()
    if payload.is_global is not None and is_admin:
        entry["is_global"] = bool(payload.is_global)
    if payload.notes is not None:
        entry["notes"] = payload.notes.strip()
    _save_meta(meta)

    all_ips = get_ips(db=db, current_user=current_user)
    for item in all_ips:
        if item.id == ip.id:
            return item
    raise HTTPException(status_code=404, detail="IP not found after update")


@router.delete("/{ip_id}")
def delete_ip(ip_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    ip = db.query(ServerIP).filter(ServerIP.id == ip_id).first()
    if not ip:
        raise HTTPException(status_code=404, detail="IP not found")

    try:
        net_ips = server_network.ipv4_addresses()
        if net_ips and ip.ip_address == net_ips[0]:
            raise HTTPException(status_code=400, detail="Cannot delete the server's primary IPv4 address")
    except HTTPException:
        raise
    except Exception:
        pass

    addr = ip.ip_address
    db.delete(ip)
    db.commit()

    meta = _load_meta()
    if addr in meta:
        del meta[addr]
        _save_meta(meta)

    return {"ok": True}


@router.put("/{ip_id}/assign", response_model=ServerIPOut)
def assign_ip(ip_id: int, payload: ServerIPAssign, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_ip(
        ip_id=ip_id,
        payload=ServerIPUpdate(
            assigned_user_id=payload.assigned_user_id,
            clear_assigned_user=(payload.assigned_user_id is None),
        ),
        db=db,
        current_user=current_user,
    )
