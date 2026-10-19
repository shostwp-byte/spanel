"""Demo mode settings, and the demo accounts the sign-in page offers.

The rules themselves are in services/demo_mode.py and are applied to every
signed-in request by api.deps; this is only where an administrator picks the
accounts and their public passwords across the 3 panel roles:
Admin, Reseller, and End User.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import addons, demo_mode
from app.services.audit import log_action

router = APIRouter(prefix="/demo-mode", tags=["demo-mode"])


class DemoAccount(BaseModel):
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=demo_mode.MAX_PASSWORD)


class DemoAccounts(BaseModel):
    admin: DemoAccount | None = None
    reseller: DemoAccount | None = None
    customer: DemoAccount | None = None
    end_user: DemoAccount | None = None


@router.get("/public")
def public_accounts():
    """No sign-in needed: the login page shows these as one-click buttons.

    Empty while the addon is off, so an ordinary panel's login page shows
    nothing and says nothing about demo accounts.
    """
    return demo_mode.public_view()


@router.get("", dependencies=[Depends(addons.require_demo)])
def read_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return demo_mode.admin_view(db)


@router.put("", dependencies=[Depends(addons.require_demo)])
def save_settings(
    payload: DemoAccounts,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    view = demo_mode.configure(db, current_user, payload.model_dump())
    names = ", ".join(entry["username"] for entry in view["accounts"].values() if entry) or "none"
    log_action(db, current_user.id, "demo_mode_accounts", names, request=request)
    return {**view, "message": "Demo accounts saved."}
