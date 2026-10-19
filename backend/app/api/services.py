from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import ServiceAction
from app.services.system import install_wordpress_stack, list_services, resource_usage, service_action, system_info

router = APIRouter(prefix="/services", tags=["services"])

# Everything here describes the machine, not a customer's slice of it: the
# kernel and hostname, the whole box's CPU and memory, and which system units
# are running. An end user has no business with any of it - it is the
# operator's server, and knowing what is installed and how loaded it is helps
# nobody but someone sizing up the host.
#
# The reads were open to end users and the status action with them. Hiding the
# page in the frontend is not access control; these are the checks that are.


@router.get("/system-info")
def get_system_info(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return system_info()


@router.get("/resource-usage")
def get_resource_usage(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return resource_usage()


@router.get("/list")
def get_services(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"services": list_services()}


@router.post("/action")
def run_service_action(payload: ServiceAction, current_user: User = Depends(get_current_user)):
    # Reading a unit's status says as much about the host as listing them does.
    ensure_role(current_user.role, Role.admin)
    try:
        result = service_action(payload.name, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.__dict__


@router.post("/install-wordpress-stack")
def install_stack(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    result = install_wordpress_stack()
    return result.__dict__
