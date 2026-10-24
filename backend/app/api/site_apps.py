"""Applications: install, run and keep alive.

An app belongs to a panel user, not to a website. It gets its own directory, its
own loopback port and its own systemd unit — a Node process or a container. A
website set to "application" mode points at one, and nginx proxies the domain to
that port; that side lives in app/api/websites.py.
"""

from pathlib import Path
from typing import Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import SiteApp, User
from app.schemas.schemas import (
    ComposeValidateRequest,
    NodeInstallRequest,
    SiteAppControl,
    SiteAppCreate,
    SiteAppOut,
    SiteAppUpdate,
)
from app.services import addons, site_apps
from app.services.audit import log_action

# The whole feature is an addon, so nothing here answers on a panel that has not
# installed it. One guard on the router rather than a check per handler, which is
# the kind of thing that gets forgotten on the next route someone adds.
router = APIRouter(
    prefix="/site-apps",
    tags=["site-apps"],
    dependencies=[Depends(addons.require_application)],
)
# Separate prefix on purpose: under /site-apps these would be shadowed by
# /{app_id}/status and answer 422 instead of dispatching here.
runtime_router = APIRouter(
    prefix="/site-runtimes",
    tags=["site-apps"],
)


def _owned_app(db: Session, current_user: User, app_id: int) -> SiteApp:
    app = db.query(SiteApp).filter(SiteApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    return app


def _resolve_owner(db: Session, current_user: User, owner_id: int | None) -> User:
    if owner_id is None or owner_id == current_user.id:
        return current_user
    ensure_role(current_user.role, Role.admin)
    owner = db.query(User).filter(User.id == owner_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    return owner


def _app_out(app: SiteApp) -> dict:
    payload = SiteAppOut.model_validate(app).model_dump()
    # Where the customer uploads code. Their SFTP is chrooted to their home, so
    # the path is reachable without going through the panel.
    try:
        payload["directory"] = str(site_apps.directory_for(app))
    except (ValueError, OSError):
        payload["directory"] = ""
    try:
        payload["unit"] = site_apps.unit_name(app)
    except ValueError:
        payload["unit"] = ""
    try:
        token = site_apps.webhook_token_for(app)
        payload["webhook_token"] = token
        payload["webhook_path"] = f"/api/site-apps/webhook/{app.id}/{token}"
    except Exception:
        payload["webhook_token"] = ""
        payload["webhook_path"] = ""
    payload["websites"] = [website.domain for website in app.websites]
    return payload


@router.get("")
def list_site_apps(
    owner_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.end_user)
    query = db.query(SiteApp)
    if is_admin_role(current_user.role):
        if owner_id is not None:
            query = query.filter(SiteApp.owner_id == owner_id)
    else:
        query = query.filter(SiteApp.owner_id == current_user.id)
    apps = query.order_by(SiteApp.id).all()
    return {
        "items": [_app_out(app) for app in apps],
        "limit": site_apps.app_limit_for(current_user),
        "used": site_apps.count_apps_for_owner(db, current_user.id),
        "memory_ceiling_mb": site_apps.memory_ceiling_for(current_user),
        "port_range": [site_apps.PORT_RANGE_START, site_apps.PORT_RANGE_END],
    }


@router.get("/suggest-port")
def suggest_port(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    try:
        return {"port": site_apps.allocate_port(db)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/compose/validate")
def validate_compose(payload: ComposeValidateRequest, current_user: User = Depends(get_current_user)):
    """Report what the panel can run from a pasted compose file.

    A dry run: nothing is stored, so the customer can paste, read the issues and
    fix them before committing to an application.
    """
    ensure_role(current_user.role, Role.end_user)
    from app.services import compose

    # Same registry rule as a container application: admins may reach a registry
    # the panel does not ship in its allowlist, customers may not.
    enforce = not is_admin_role(current_user.role)
    variables = compose.read_variables(payload.env or "")
    # A dry run cannot know which website will point here, so the two panel names
    # stand in for themselves; the real values are filled in at deploy.
    variables.setdefault("spanel_URL", "https://<domain>")
    variables.setdefault("spanel_DOMAIN", "<domain>")
    return compose.analyse(payload.compose_source or "", payload.web_service or "",
                           enforce, variables, web_port=payload.web_port).as_dict()


@router.post("")
def create_site_app(payload: SiteAppCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    owner = _resolve_owner(db, current_user, payload.owner_id)
    is_admin = is_admin_role(current_user.role)
    try:
        site_apps.ensure_app_quota(db, owner, is_admin)
        name = site_apps.validate_name(payload.name)
        kind = site_apps.validate_kind(payload.kind)
        memory_limit_mb = site_apps.validate_memory_mb(
            payload.memory_limit_mb,
            None if is_admin else site_apps.memory_ceiling_for(owner),
        )
        start_kind, start_arg, node_major = (None, None, None)
        image, container_port, cpu_limit = (None, 3000, "1")
        compose_source, web_service = ("", None)
        if kind == "node":
            start_kind, start_arg = site_apps.validate_start(payload.start_kind, payload.start_arg)
            node_major = site_apps.validate_node_major(payload.node_major) or "22"
        elif kind == "python":
            start_kind, start_arg = site_apps.validate_start(
                payload.start_kind or "uvicorn",
                payload.start_arg or "main:app",
            )
        elif kind == "docker":
            image = site_apps.validate_image(payload.image, enforce_registry=not is_admin)
            container_port = site_apps.validate_container_port(payload.container_port)
            cpu_limit = site_apps.validate_cpu_limit(payload.cpu_limit)
        else:
            from app.services import compose as compose_service

            cpu_limit = site_apps.validate_cpu_limit(payload.cpu_limit)
            variables = compose_service.read_variables(payload.env or "")
            variables.setdefault("spanel_URL", "https://<domain>")
            variables.setdefault("spanel_DOMAIN", "<domain>")
            plan = compose_service.analyse(payload.compose_source or "", payload.web_service or "",
                                           enforce_registry=not is_admin, variables=variables,
                                           web_port=payload.container_port)
            if not plan.ok:
                raise HTTPException(
                    status_code=400,
                    detail={"message": "Compose file cannot be imported as it is", "issues": [i.as_dict() for i in plan.issues]},
                )
            compose_source = payload.compose_source or ""
            web_service = plan.web_service
            container_port = next(s.container_port for s in plan.services if s.name == plan.web_service)
        env = site_apps.validate_env(payload.env)
        port = site_apps.allocate_port(db, payload.port)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    app = SiteApp(
        owner_id=owner.id,
        name=name,
        kind=kind,
        start_kind=start_kind,
        start_arg=start_arg,
        node_major=node_major,
        image=image,
        container_port=container_port,
        cpu_limit=cpu_limit,
        env=env,
        compose_source=compose_source,
        web_service=web_service,
        port=port,
        memory_limit_mb=memory_limit_mb,
        autostart=bool(payload.autostart),
    )
    db.add(app)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="You already have an application with that name") from exc
    db.refresh(app)
    # So the customer can upload code straight away instead of having to deploy
    # an empty application first.
    try:
        site_apps.ensure_directory(app)
    except (RuntimeError, ValueError):
        pass
    log_action(db, current_user.id, "create_site_app", owner.username, f"{name} :{port}")
    return _app_out(app)


def _reapply_runtime(db: Session, app: SiteApp, previous_name: str | None = None) -> None:
    """Rewrite the unit and restart, but only for an app already deployed."""
    units = Path("/etc/systemd/system")
    if not (units / f"{site_apps.unit_name(app, previous_name)}.service").exists():
        return
    if previous_name and previous_name != app.name:
        # A rename moves the unit, so the old one has to go or it keeps running
        # the app under a name nothing points at any more.
        try:
            site_apps.delete_runtime(app, previous_name)
        except (RuntimeError, ValueError):
            pass
    try:
        site_apps.write_runtime(app)
        # Fetch first, so the containers currently serving stay up while the new
        # image downloads. A bad tag then fails here, with the old ones running.
        site_apps.fetch_images(app)
        site_apps.control(app, "restart")
    except (RuntimeError, ValueError) as exc:
        app.status = "error"
        app.last_error = f"Could not restart on the new port: {exc}"[-2000:]
        db.commit()
        return
    running = site_apps.settled_state(app) == "active"
    app.status = "running" if running else "error"
    app.last_error = "" if running else "The application did not come back on the new port. Check the log."
    db.commit()


def _resync_websites(app: SiteApp) -> None:
    """Every domain serving this app has to follow its port."""
    from app.api.websites import _rewrite_website_vhost  # circular at import time

    for website in app.websites:
        try:
            _rewrite_website_vhost(website, app_port=app.port)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot write Nginx config for {website.domain}: {exc}",
            ) from exc


@router.put("/{app_id}")
def update_site_app(app_id: int, payload: SiteAppUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    owner = db.query(User).filter(User.id == app.owner_id).first() or current_user
    is_admin = is_admin_role(current_user.role)
    previous_port = app.port
    # Everything the systemd unit is built from. Change any of these on a
    # deployed app and the running process is out of date until it is rewritten.
    UNIT_FIELDS = ("port", "memory_limit_mb", "cpu_limit", "image", "container_port",
                   "start_kind", "start_arg", "node_major", "env", "name",
                   "compose_source", "web_service")
    before = {field: getattr(app, field) for field in UNIT_FIELDS}
    try:
        if payload.name is not None:
            app.name = site_apps.validate_name(payload.name)
        if payload.memory_limit_mb is not None:
            app.memory_limit_mb = site_apps.validate_memory_mb(
                payload.memory_limit_mb,
                None if is_admin else site_apps.memory_ceiling_for(owner),
            )
        if payload.autostart is not None:
            app.autostart = bool(payload.autostart)
        if payload.node_major is not None:
            app.node_major = site_apps.validate_node_major(payload.node_major)
        if payload.image is not None:
            app.image = site_apps.validate_image(payload.image, enforce_registry=not is_admin)
        if payload.container_port is not None:
            app.container_port = site_apps.validate_container_port(payload.container_port)
        if payload.cpu_limit is not None:
            app.cpu_limit = site_apps.validate_cpu_limit(payload.cpu_limit)
        if payload.env is not None:
            app.env = site_apps.validate_env(payload.env)
        if app.kind == "compose" and (payload.compose_source is not None or payload.web_service is not None
                                      or payload.env is not None):
            from app.services import compose as compose_service

            source = payload.compose_source if payload.compose_source is not None else app.compose_source
            wanted = payload.web_service if payload.web_service is not None else app.web_service
            variables = site_apps.compose_variables(app)
            variables.setdefault("spanel_URL", "https://<domain>")
            variables.setdefault("spanel_DOMAIN", "<domain>")
            plan = compose_service.analyse(source or "", wanted or "", variables=variables,
                                           web_port=payload.container_port)
            if not plan.ok:
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail={"message": "Compose file cannot be imported as it is", "issues": [i.as_dict() for i in plan.issues]},
                )
            app.compose_source = source or ""
            app.web_service = plan.web_service
            app.container_port = next(s.container_port for s in plan.services if s.name == plan.web_service)
        if payload.start_kind is not None or payload.start_arg is not None:
            app.start_kind, app.start_arg = site_apps.validate_start(
                payload.start_kind or app.start_kind,
                payload.start_arg or app.start_arg,
            )
        if payload.port is not None and int(payload.port) != previous_port:
            app.port = site_apps.allocate_port(db, payload.port, exclude_app_id=app.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An application with that name or port already exists") from exc
    db.refresh(app)

    if app.name != before["name"]:
        try:
            site_apps.rename_directory(app, before["name"])
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if app.kind in ("docker", "compose") or any(getattr(app, field) != before[field] for field in UNIT_FIELDS):
        # The unit still carries the old port, memory cap or start command until
        # it is rewritten, so a change that only touched the database would leave
        # the running process stale until someone thought to press Deploy.
        #
        # A container app re-applies even when nothing in the file changed: with
        # a tag like :latest the version that should run moves without the text
        # moving, and saving is how someone asks for what the tag points at now.
        _reapply_runtime(db, app, before["name"])
    if app.port != previous_port:
        _resync_websites(app)

    log_action(db, current_user.id, "update_site_app", app.name, f":{app.port}")
    return _app_out(app)


@router.delete("/{app_id}")
def delete_site_app(app_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    if app.websites:
        domains = ", ".join(website.domain for website in app.websites)
        raise HTTPException(
            status_code=409,
            detail=f"This application still serves {domains}. Point those websites at something else first.",
        )
    name, port = app.name, app.port
    try:
        site_apps.delete_runtime(app)
    except (RuntimeError, ValueError):
        pass
    db.delete(app)
    db.commit()
    log_action(db, current_user.id, "delete_site_app", name, f":{port}")
    return {"deleted": name, "port": port}


# --- runtime lifecycle ------------------------------------------------------

def _record_status(db: Session, app: SiteApp, status: str, error: str = "") -> None:
    app.status = status
    app.last_error = (error or "")[-2000:]
    db.commit()


@router.post("/{app_id}/deploy")
def deploy_site_app(app_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Fetch what the app needs, write its unit, and start it."""
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    steps: list[str] = []
    try:
        if app.kind == "compose":
            # The unit and the generated file come first: pulling reads that file.
            unit = site_apps.write_runtime(app)
            steps.append(site_apps.fetch_images(app))
        else:
            if app.kind == "docker":
                steps.append(site_apps.fetch_images(app))
            else:
                try:
                    steps.append(site_apps.install_dependencies(app))
                except RuntimeError as exc:
                    # No package.json is normal for a single-file entry point.
                    if "no package.json" not in str(exc).lower():
                        raise
            unit = site_apps.write_runtime(app)
        site_apps.control(app, "enable" if app.autostart else "disable")
        site_apps.control(app, "restart")
    except (RuntimeError, ValueError) as exc:
        _record_status(db, app, "error", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Give it a few seconds to fall over before calling the deploy a success.
    state = site_apps.settled_state(app)
    running = state == "active"
    trouble = ""
    if running and app.kind == "compose":
        # `docker compose up` stays attached through a container's crash loop, so
        # the unit being active says nothing about the containers themselves.
        trouble = site_apps.compose_trouble(app)
        running = not trouble
    _record_status(
        db,
        app,
        "running" if running else "error",
        "" if running
        else trouble or f"The application did not stay running (systemd reports '{state}'). Check the log.",
    )
    log_action(db, current_user.id, "deploy_site_app", app.name, f":{app.port} {state}")
    return {
        "unit": unit,
        "status": app.status,
        "running": running,
        "output": "\n".join(step for step in steps if step)[-4000:],
    }


@router.post("/{app_id}/control")
def control_site_app(app_id: int, payload: SiteAppControl, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    try:
        output = site_apps.control(app, payload.action)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    running = site_apps.is_running(app)
    _record_status(db, app, "running" if running else "stopped")
    log_action(db, current_user.id, f"{payload.action}_site_app", app.name, "")
    return {"action": payload.action, "running": running, "status": app.status, "output": output[-2000:]}


@router.get("/{app_id}/status")
def site_app_status(app_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    running = site_apps.is_running(app)
    trouble = site_apps.compose_trouble(app) if running and app.kind == "compose" else ""
    return {
        "running": running and not trouble,
        "unit": site_apps.unit_name(app),
        "status": "error" if trouble else app.status,
        "last_error": trouble or app.last_error,
    }


@router.get("/{app_id}/logs")
def site_app_logs(app_id: int, lines: int = 200, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    try:
        return {"log": site_apps.logs(app, lines)}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{app_id}/git-deploy")
def trigger_git_deploy(
    app_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.end_user)
    app = _owned_app(db, current_user, app_id)
    try:
        result = site_apps.git_auto_deploy(app)
    except (RuntimeError, ValueError) as exc:
        _record_status(db, app, "error", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_status(db, app, result.get("status", "running"))
    log_action(db, current_user.id, "git_deploy_site_app", app.name, f":{app.port}")
    return {
        "ok": result.get("ok", True),
        "status": app.status,
        "git_output": result.get("git_output", ""),
        "deps_output": result.get("deps_output", ""),
        "message": f"Git auto-deploy completed for {app.name}.",
    }


@router.post("/webhook/{app_id}/{token}")
async def git_webhook_auto_deploy(
    app_id: int,
    token: str,
    request: Request,
    branch: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Unauthenticated HMAC-verified webhook endpoint for GitHub / GitLab push auto-deploy."""
    app = db.query(SiteApp).filter(SiteApp.id == app_id).first()
    if not app or not site_apps.verify_webhook_token(app, token):
        raise HTTPException(status_code=403, detail="Invalid or expired webhook token")

    raw_body = await request.body()
    gh_sig = request.headers.get("X-Hub-Signature-256")
    if gh_sig is not None and not site_apps.verify_github_webhook_signature(app, raw_body, gh_sig):
        raise HTTPException(status_code=403, detail="Invalid X-Hub-Signature-256 HMAC signature")

    gl_token = request.headers.get("X-Gitlab-Token")
    if gl_token is not None and not site_apps.verify_webhook_token(app, gl_token):
        raise HTTPException(status_code=403, detail="Invalid X-Gitlab-Token header")

    gh_event = (request.headers.get("X-GitHub-Event") or "").strip().lower()
    if gh_event == "ping":
        return {"ok": True, "event": "ping", "app": app.name, "message": "Webhook ping acknowledged"}
    if gh_event and gh_event not in {"push", "release", "workflow_run"}:
        return {"ok": True, "skipped": True, "app": app.name, "reason": f"Ignored event {gh_event}"}

    pushed_branch = ""
    if raw_body:
        try:
            payload_json = json.loads(raw_body.decode("utf-8"))
            if isinstance(payload_json, dict):
                ref = str(payload_json.get("ref") or "")
                if ref.startswith("refs/heads/"):
                    pushed_branch = ref[len("refs/heads/"):]
        except Exception:
            pass
    if branch and pushed_branch and pushed_branch != branch.strip():
        return {
            "ok": True,
            "skipped": True,
            "app": app.name,
            "reason": f"Push to {pushed_branch} does not match target branch {branch.strip()}",
        }

    try:
        result = site_apps.git_auto_deploy(app)
    except (RuntimeError, ValueError) as exc:
        _record_status(db, app, "error", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_status(db, app, result.get("status", "running"))
    log_action(db, app.owner_id, "webhook_deploy_site_app", app.name, f":{app.port}")
    return {
        "ok": result.get("ok", True),
        "app": app.name,
        "branch": pushed_branch or branch or "default",
        "status": app.status,
        "git_output": result.get("git_output", ""),
    }


# --- server side runtimes ---------------------------------------------------

@runtime_router.get("")
@runtime_router.get("/status")
def runtime_status(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    node_majors = site_apps.installed_node_majors()
    return {
        "docker": site_apps.docker_status(),
        "node_majors": node_majors,
        "node": {
            "installed": node_majors,
            "supported": [18, 20, 22],
        },
        "allowed_registries": list(site_apps.allowed_registries()),
    }


@runtime_router.post("/docker-install")
def runtime_install_docker(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        output = site_apps.install_docker()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Docker is ready.", "output": output, "docker": site_apps.docker_status()}


@runtime_router.post("/docker-prune")
def runtime_prune_docker(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Reclaim what pulling images left behind.

    Server-wide, so it is an administrator's button: images are shared between
    tenants and cannot be charged to one customer's quota.
    """
    ensure_role(current_user.role, Role.admin)
    try:
        output = site_apps.prune_docker()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "prune_docker", "docker", "")
    return {"message": "Unused layers and build cache pruned.", "output": output,
            "docker": site_apps.docker_status()}


@runtime_router.post("/node-install")
def runtime_install_node(payload: NodeInstallRequest, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        output = site_apps.install_node(payload.major)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Node {payload.major} is ready.", "output": output, "node_majors": site_apps.installed_node_majors()}
