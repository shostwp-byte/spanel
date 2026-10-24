import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import addons as addons_api, auth, dashboard, databases, demo_mode as demo_mode_api, dns as dns_api, fail2ban as fail2ban_api, firewall, mail as mail_api, maintenance, malware, mcp as mcp_api, notifications as notifications_api, packages, panel_settings as panel_settings_api, provisioning, services, sftp_accounts as sftp_accounts_api, site_apps as site_apps_api, terminal, updates, users, waf, websites
from app.api import migration, resellers, ips, app_installer, ai, message_templates, server_tls, processes, name_servers, message_all, custombuild, resources, multi_server, licensing
from app.api import remote_mysql as remote_mysql_api
from app.core.config import settings
from app.core.database import run_migrations
from app.core.version import APP_VERSION
from app.services import panel_settings as panel_brand_settings, auto_updater, resource_monitor, cluster_worker

run_migrations()
auto_updater.start_auto_update_daemon()
resource_monitor.start_resource_monitor_daemon()
cluster_worker.start_cluster_daemon()

logger = logging.getLogger("spanel")


def _adopt_addons_already_in_use() -> None:
    """A server running applications before the addon existed keeps them.

    Without this, the update that turned Application into an addon would take
    the feature away from everyone already using it: units still running,
    nothing in the panel to manage them with.
    """
    from sqlalchemy import func, select

    from app.core.database import SessionLocal
    from app.models.entities import SiteApp
    from app.services import addons

    try:
        with SessionLocal() as db:
            count = db.execute(select(func.count()).select_from(SiteApp)).scalar() or 0
        if addons.adopt_existing(addons.APPLICATION, count > 0):
            logger.info("Addon 'application' adopted: %s existing application(s)", count)
    except Exception:  # noqa: BLE001 - never let this stop the panel booting
        logger.warning("Could not check for existing applications", exc_info=True)
    try:
        # The same for the malware scanner, which became an addon on
        # 2026-09-27: a server where it was switched on, scheduled or
        # watching in real time keeps it.
        from app.services import panel_settings as panel_brand_settings, auto_updater

        if addons.adopt_existing(addons.MALWARE, panel_brand_settings.malware_in_use()):
            # Installed means switched on; a server adopted for its schedule
            # alone had the switch off, and the scan of uploads reads it.
            panel_brand_settings._persist_malware_enabled(True)
            logger.info("Addon 'malware' adopted: the scanner was already in use")
    except Exception:  # noqa: BLE001 - never let this stop the panel booting
        logger.warning("Could not check whether the malware scanner was in use", exc_info=True)
    try:
        from app.services import fail2ban as fail2ban_service
        f2b_info = fail2ban_service.status()
        if addons.adopt_existing(addons.FAIL2BAN, bool(f2b_info.get("installed") and f2b_info.get("running"))):
            logger.info("Addon 'fail2ban' adopted: fail2ban service was already active")
    except Exception:
        logger.warning("Could not check whether fail2ban was in use", exc_info=True)


_adopt_addons_already_in_use()


def _sync_dns_zones() -> None:
    """DNS Manager: every domain on the server has its zone (operator,
    2026-09-29), including websites that arrived while the panel was not
    looking. Then Email: the mail server gets the mailbox list again and the
    DKIM records go into those zones. In the background: startup must not
    wait on PowerDNS or the mail server."""
    import threading

    def run() -> None:
        from app.core.database import SessionLocal
        from app.services import dns, mail

        with SessionLocal() as db:
            dns.sync_quietly(db)
            mail.sync_quietly(db)

    threading.Thread(target=run, name="spanel-dns-sync", daemon=True).start()


_sync_dns_zones()

# Secure default umask: files get 644 (-rw-r--r--), dirs get 755 (rwxr-xr-x)
os.umask(0o022)

app = FastAPI(title="spanel API", version=APP_VERSION)

# Refuse to start in production with unsafe defaults.
if settings.app_env.lower() == "production":
    if settings.command_dry_run:
        raise RuntimeError(
            "COMMAND_DRY_RUN must be False in production. "
            "Set COMMAND_DRY_RUN=false in the environment."
        )

cors_origins = settings.cors_origins
if not cors_origins and settings.app_env != "production":
    cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)


def _is_potentially_trustworthy_origin(request) -> bool:
    host = (request.url.hostname or "").lower()
    return request.url.scheme == "https" or host in {"localhost", "127.0.0.1", "::1"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled request error: %s %s", request.method, request.url.path)
    if settings.app_env.lower() == "production":
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.middleware("http")
async def slide_session(request, call_next):
    """Keep an in-use panel session alive so it never expires under an admin."""
    response = await call_next(request)
    try:
        auth.maybe_renew_session_cookie(request, response)
    except Exception:  # noqa: BLE001 - session renewal must never break a response
        logger.warning("Session renewal failed", exc_info=True)
    return response


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if _is_potentially_trustworthy_origin(request):
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "accelerometer=(), autoplay=(), camera=(), display-capture=(), encrypted-media=(), "
        "fullscreen=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), midi=(), "
        "payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    if settings.app_env.lower() == "production" and request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(message_templates.router, prefix="/api")
app.include_router(resellers.router, prefix="/api")
app.include_router(packages.router, prefix="/api")
app.include_router(websites.router, prefix="/api")
app.include_router(databases.router, prefix="/api")
app.include_router(sftp_accounts_api.router, prefix="/api")
app.include_router(firewall.router, prefix="/api")
app.include_router(fail2ban_api.router, prefix="/api")
app.include_router(services.router, prefix="/api")
app.include_router(updates.router, prefix="/api")
app.include_router(waf.router, prefix="/api")
app.include_router(maintenance.router, prefix="/api")
app.include_router(panel_settings_api.router, prefix="/api")
app.include_router(malware.router, prefix="/api")
app.include_router(terminal.router, prefix="/api")
app.include_router(provisioning.router, prefix="/api")
app.include_router(addons_api.router, prefix="/api")
app.include_router(mcp_api.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(migration.router, prefix="/api")
app.include_router(notifications_api.router, prefix="/api")
# Registered whether or not the Application addon is installed: the routers
# themselves answer 409 when it is not, which tells the panel why a section is
# missing instead of leaving it looking broken.
app.include_router(site_apps_api.router, prefix="/api")
app.include_router(site_apps_api.runtime_router, prefix="/api")
app.include_router(demo_mode_api.router, prefix="/api")
app.include_router(dns_api.router, prefix="/api")
app.include_router(mail_api.router, prefix="/api")
app.include_router(ips.router, prefix="/api")
app.include_router(app_installer.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(processes.router, prefix="/api")
app.include_router(name_servers.router, prefix="/api")
app.include_router(message_all.router, prefix="/api")
app.include_router(custombuild.router, prefix="/api")
app.include_router(resources.router, prefix="/api")
app.include_router(multi_server.router, prefix="/api")
app.include_router(remote_mysql_api.router, prefix="/api")
app.include_router(licensing.router, prefix="/api")
app.include_router(server_tls.router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "name": panel_brand_settings.current_settings().get("app_name") or "spanel",
        "version": APP_VERSION,
    }


frontend_dist = Path(settings.frontend_dist)
assets_dir = frontend_dist / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/favicon.png", include_in_schema=False)
def favicon():
    custom = panel_brand_settings.current_settings().get("favicon_url") or ""
    if custom.startswith("/brand-assets/"):
        filename = custom.split("/brand-assets/", 1)[1].split("?", 1)[0]
        path, media_type = panel_brand_settings.asset_path(filename)
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    path = frontend_dist / "favicon.png"
    if path.exists():
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/brand-assets/{filename}", include_in_schema=False)
def brand_asset(filename: str):
    path, media_type = panel_brand_settings.asset_path(filename)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    """Serve the built React app directly from FastAPI on the panel port."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    requested = (frontend_dist / full_path).resolve()
    try:
        requested.relative_to(frontend_dist.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if requested.is_file():
        return FileResponse(requested)
    if full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(
            index,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return {"detail": "Frontend build not found", "path": str(index)}
