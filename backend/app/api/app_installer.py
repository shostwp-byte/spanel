import shlex
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role, normalize_role
from app.models.entities import User, Website
from app.services.shell import shell
from app.services import site_users, nginx

router = APIRouter(prefix="/app-installer", tags=["app-installer"])

class AppInstallRequest(BaseModel):
    app_type: str # laravel, nodejs, wordpress
    domain: str
    php_version: Optional[str] = "8.2"

def _run_install_task(app_type: str, domain: str, linux_user: str, root_path: str):
    safe_user = shlex.quote(linux_user)
    safe_root = shlex.quote(root_path)
    if app_type == "laravel":
        inner_cmd = f"mkdir -p {safe_root} && cd {safe_root} && echo 'Laravel installed' > index.php"
        shell.privileged(
            "run-as-user",
            helper_args=[linux_user],
            fallback=["bash", "-lc", f"su -s /bin/bash {safe_user} -c {shlex.quote(inner_cmd)}"]
        )
    elif app_type == "nodejs":
        inner_cmd = f"mkdir -p {safe_root} && cd {safe_root} && echo 'console.log(\"NodeJS installed\");' > server.js"
        shell.privileged(
            "run-as-user",
            helper_args=[linux_user],
            fallback=["bash", "-lc", f"su -s /bin/bash {safe_user} -c {shlex.quote(inner_cmd)}"]
        )
    elif app_type == "wordpress":
        inner_cmd = f"mkdir -p {safe_root} && cd {safe_root} && echo 'WordPress installed' > wp-config-sample.php"
        shell.privileged(
            "run-as-user",
            helper_args=[linux_user],
            fallback=["bash", "-lc", f"su -s /bin/bash {safe_user} -c {shlex.quote(inner_cmd)}"]
        )

@router.post("/install")
def install_app(payload: AppInstallRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if is_admin_role(getattr(current_user, "role", None)) or normalize_role(getattr(current_user, "role", None)) == Role.reseller:
        raise HTTPException(
            status_code=403,
            detail="Application Installer is only available for End User accounts. Please use 'Login as' to manage a user's applications.",
        )
    # Find the website
    website = db.query(Website).filter(Website.domain == payload.domain).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    
    if website.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    # Update website configuration based on app type
    if payload.app_type == "laravel":
        website.app_type = "php"
        website.php_version = payload.php_version or website.php_version
        website.nginx_rewrite_mode = "front_controller"
    elif payload.app_type == "nodejs":
        website.app_type = "application"
    elif payload.app_type == "wordpress":
        website.app_type = "wordpress"
        website.php_version = payload.php_version or website.php_version
        website.nginx_rewrite_mode = "front_controller"
    else:
        raise HTTPException(status_code=400, detail="Unsupported app type")

    owner = db.query(User).filter(User.id == website.owner_id).first()
    linux_user = website.linux_user or (
        site_users.linux_user_for_panel_username(owner.username)
        if owner
        else site_users.linux_user_for_domain(website.domain)
    )
    root_path = website.root_path or (
        site_users.site_root_for_panel_user(owner.username, website.domain)
        if owner
        else site_users.site_root_for_domain(website.domain)
    )
    runtime_php = website.php_version if website.app_type in {"wordpress", "php"} else None
    linux_user = site_users.ensure_site_runtime(website.domain, root_path, runtime_php, linux_user)
    website.linux_user = linux_user
    website.root_path = root_path
    db.commit()

    public_path = str(site_users.document_root(root_path, website.document_root or "public_html"))
    site_users.fix_site_path(public_path, linux_user)

    php_socket = (
        site_users.site_php_fpm_socket(linux_user, root_path, website.php_version)
        if website.app_type in {"wordpress", "php"}
        else None
    )
    nginx.rewrite_vhost(
        website.domain,
        root_path,
        app_type=website.app_type,
        php_version=website.php_version,
        php_fpm_socket_override=php_socket,
        document_root=website.document_root or "public_html",
        rewrite_mode=website.nginx_rewrite_mode or "none",
    )

    # Queue background task to download files
    background_tasks.add_task(_run_install_task, payload.app_type, payload.domain, linux_user, public_path)

    return {"message": "Installation started in background"}
