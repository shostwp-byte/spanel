import json
import os
import tempfile
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import server_network
from app.services.audit import log_action

router = APIRouter(prefix="/name-servers", tags=["settings"])

DATA_DIR = Path(os.environ.get("spanel_DATA_DIR", "/var/lib/spanel"))
CONFIG_FILE = DATA_DIR / "name_servers.json"
FALLBACK_CONFIG_FILE = Path(tempfile.gettempdir()) / "spanel" / "name_servers.json"

class NameServersUpdate(BaseModel):
    ns1: str
    ns2: str

def get_name_servers() -> dict:
    data = {"ns1": "ns1.yourdomain.com", "ns2": "ns2.yourdomain.com"}
    for candidate in (CONFIG_FILE, FALLBACK_CONFIG_FILE):
        if candidate.exists():
            try:
                data.update(json.loads(candidate.read_text(encoding="utf-8")))
                break
            except Exception:
                pass
            
    # Add server IP for frontend display
    try:
        ips = server_network.ipv4_addresses()
        data["server_ip"] = ips[0] if ips else "127.0.0.1"
    except Exception:
        data["server_ip"] = "127.0.0.1"
        
    return data

@router.get("")
def read_name_servers(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return get_name_servers()

@router.post("")
def update_name_servers(
    payload: NameServersUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_role(current_user.role, Role.admin)
    
    if not payload.ns1 or not payload.ns2:
        raise HTTPException(status_code=400, detail="NS1 and NS2 are required")
        
    try:
        content = json.dumps({"ns1": payload.ns1, "ns2": payload.ns2}, indent=4)
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(content, encoding="utf-8")
        except OSError:
            FALLBACK_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            FALLBACK_CONFIG_FILE.write_text(content, encoding="utf-8")
        
        log_action(db, current_user.id, "update_name_servers", f"{payload.ns1}, {payload.ns2}")
        return {"success": True, "message": "Name servers updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {e}")
