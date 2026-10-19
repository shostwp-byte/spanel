"""Install and remove the panel's optional features.

Only an administrator sees this: an addon changes the server, not one account.
Every other user is told what exists so they know what to ask for, without being
able to turn it on.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import SiteApp, User
from app.services import addons, demo_mode, dns, fail2ban, mail, panel_settings, site_apps
from app.services.audit import log_action

router = APIRouter(prefix="/addons", tags=["addons"])


def _install_failure(exc: Exception) -> str:
    """The helper's own words, not a stack trace.

    shell.privileged wraps a failure as "Command failed: <the whole command
    line>" then a newline then stderr. The command line is noise to whoever is
    reading the panel; the stderr is the sentence the helper wrote for them.
    """
    text = str(exc).strip()
    _, separator, stderr = text.partition(chr(10))
    message = (stderr if separator else text).strip()
    # When a tool said something before the helper gave up (apt, systemctl),
    # the helper's own line is the last one, and it is the one worth reading.
    helper_lines = [line for line in message.splitlines() if line.startswith("spanel-helper: ")]
    if helper_lines:
        message = helper_lines[-1]
    for prefix in ("spanel-helper: ", "ERROR: "):
        if message.startswith(prefix):
            message = message[len(prefix):]
    return message[:500] or "The install failed and said nothing about why."


@router.get("")
def list_addons(current_user: User = Depends(get_current_user)):
    """What the panel can do, and what it is currently doing.

    Readable by every role: the sections a customer cannot see are the ones an
    addon has not been installed for, and a blank panel with no explanation is
    worse than one that says which feature is missing.
    """
    ensure_role(current_user.role, Role.end_user)
    entries = addons.state()
    if not is_admin_role(current_user.role):
        # A customer has no business knowing how the server is put together.
        for entry in entries:
            entry.pop("notes", None)
    return {"items": entries, "can_manage": is_admin_role(current_user.role)}


@router.post("/{slug}/install")
def install_addon(slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    entry = addons.known(slug)
    if slug == addons.FAIL2BAN:
        # Unlike Application, there is nothing to configure afterwards: this
        # either protects the machine or it does not. The helper proves a ban
        # reaches iptables and raises if it does not, so a failure here means
        # the addon is not recorded as installed - which is the truth.
        #
        # Say why. Letting the RuntimeError out gave the operator "500
        # internal server error" and nothing else, while the helper had
        # already written the reason - "could not install fail2ban", "a test
        # ban never reached iptables - check banaction in ...". A 500 is an
        # answer nobody can act on, and on a live report it cost an afternoon
        # of log reading to get back to a message that had existed all along.
        try:
            fail2ban.install()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_install_failure(exc)) from exc
    if slug == addons.DNS:
        # PowerDNS, port 53 and a probe query: the helper refuses to call it
        # installed unless the server really answers DNS.
        try:
            dns.install()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_install_failure(exc)) from exc
    if slug == addons.MAIL:
        # Exim, Dovecot and the webmail, each proved to answer before the
        # addon is recorded as installed.
        try:
            mail.install()
        except mail.MailError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_install_failure(exc)) from exc
    if slug == addons.MALWARE:
        # The scanner's own switch: installs LMD and the ClamAV engine in the
        # background when they are missing, and returns at once.
        try:
            panel_settings.set_malware_scan(True)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=_install_failure(exc)) from exc
    record = addons.install(slug)
    if slug == addons.DNS:
        # Every domain already on the server gets its zone now.
        dns.sync_quietly(db)
    if slug == addons.MAIL:
        # Mail domains made before the addon was last removed come back, with
        # their DKIM keys.
        try:
            mail.after_install(db)
        except mail.MailError:
            db.rollback()
    if slug == addons.DNS:
        # With Email on as well, its domains' records go into their new zones.
        mail.sync_quietly(db)
    if slug == addons.DEMO:
        # Accounts chosen before the addon was last removed get their public
        # passwords back; see demo_mode.switch_off for why they lost them.
        demo_mode.switch_on(db)
    log_action(db, current_user.id, "install_addon", slug, record.get("version", ""))
    return {
        "slug": slug,
        "name": entry["name"],
        "installed": True,
        "version": record.get("version", ""),
        # The runtimes an application needs are installed from the Application
        # page itself, which can report progress; saying so here saves someone
        # wondering why Docker did not appear.
        "next_step": "Open the Application page to install Docker or the Node.js versions you need."
        if slug == addons.APPLICATION else (
            "SSH is protected now. Banned addresses are listed on the Firewall page."
            if slug == addons.FAIL2BAN else (
                "Open Notifications to set up the SMTP server or a Telegram bot."
                if slug == addons.NOTIFICATIONS else (
                    "Open Malware scanner under Settings. If LMD and ClamAV were not installed, they are installing now (1-3 minutes)."
                    if slug == addons.MALWARE else (
                        "Choose the demo accounts on the Addons page. Until you do, the sign-in page offers none."
                        if slug == addons.DEMO else (
                            "Every domain on the server now has its DNS zone. Open DNS to check the nameservers, then point your domains at them."
                            if slug == addons.DNS else (
                                "Open Email to make the first mailbox. The webmail is on port 2096 of the panel's address."
                                if slug == addons.MAIL else ""
                            )
                        )
                    )
                )
            )
        ),
    }


@router.post("/{slug}/uninstall")
def uninstall_addon(slug: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Turn an addon off. Nothing it created is deleted.

    The units are stopped, because a panel that no longer shows a feature should
    not keep running it where nobody can see or manage it. Everything else — the
    files, the volumes, the rows — stays exactly where it is, so installing the
    addon again brings it all back.
    """
    ensure_role(current_user.role, Role.admin)
    entry = addons.known(slug)
    stopped: list[str] = []
    failed: list[str] = []
    if slug == addons.APPLICATION:
        for app in db.query(SiteApp).all():
            try:
                site_apps.control(app, "stop")
                stopped.append(app.name)
            except (RuntimeError, ValueError):
                # Already gone, or never deployed. Not a reason to refuse.
                failed.append(app.name)
    if slug == addons.FAIL2BAN:
        try:
            fail2ban.stop()
            stopped.append("fail2ban")
        except RuntimeError:
            failed.append("fail2ban")
    if slug == addons.MALWARE:
        # Stops the real-time monitor and clamd; the schedule and the scan of
        # uploads stop with the addon itself (they check it before running).
        try:
            panel_settings.set_malware_scan(False)
            stopped.append("malware scanner")
        except RuntimeError:
            failed.append("malware scanner")
    revoked = 0
    if slug == addons.MCP:
        # The one addon whose removal destroys something, and the reason its
        # catalogue entry says keeps_data_on_uninstall is false. A token is a
        # way into this server; leaving them valid against an endpoint that
        # answers again the moment someone reinstalls is not "keeping data".
        from app.api import mcp as mcp_api
        revoked = mcp_api.revoke_all(db)
    if slug == addons.DNS:
        try:
            dns.stop()
            stopped.append("PowerDNS")
        except RuntimeError:
            failed.append("PowerDNS")
    if slug == addons.MAIL:
        try:
            mail.stop()
            stopped.append("Exim, Dovecot, webmail")
        except RuntimeError:
            failed.append("Exim, Dovecot, webmail")
    retired: list[str] = []
    if slug == addons.DEMO:
        # Before the flag goes: once it has, these are ordinary accounts, and
        # their passwords are on the sign-in page and in every screenshot.
        retired = demo_mode.switch_off(db)
    addons.uninstall(slug)
    log_action(db, current_user.id, "uninstall_addon", slug, f"stopped {len(stopped)} revoked {revoked}")
    return {
        "slug": slug,
        "name": entry["name"],
        "installed": False,
        "stopped": stopped,
        "could_not_stop": failed,
        "revoked_tokens": revoked,
        "kept": ("The demo accounts now have random passwords, so the public ones no longer work. Installing the addon again restores them."  # noqa: E501
                 if retired else "No demo accounts were set.")
        if slug == addons.DEMO
        else "Every zone is kept in PowerDNS's database and is served again when you install the addon again."
        if slug == addons.DNS
        else "The mail, the mailboxes and the DKIM keys are kept, and work again when you install the addon again."
        if slug == addons.MAIL
        else "The package, the jail configuration and the ban history are all kept."
        if slug == addons.FAIL2BAN
        else "LMD, ClamAV, the scan history and the schedule settings are all kept."
        if slug == addons.MALWARE
        else (f"{revoked} MCP token(s) were revoked. Assistants using them can no longer reach the panel."
              if slug == addons.MCP
              else "Application directories, volumes and panel data are all kept."),
    }
