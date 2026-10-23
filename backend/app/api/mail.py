"""Email: mail domains, mailboxes, forwarders, relays, the spam filter and the
server's mail settings (services/mail.py). The routes follow OPanel's.

Every route needs the addon. A customer sees and changes the mail of their own
domains; an administrator sees every domain, and alone chooses relays, sets a
domain's own DNS records and reads the logs.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import addons, mail
from app.services.audit import log_action

router = APIRouter(prefix="/mail", tags=["mail"], dependencies=[Depends(addons.require_mail)])


def _answer(action):
    """Run a mail change and turn its failures into HTTP answers."""
    try:
        return action()
    except mail.MailError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


class DomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    owner_id: int | None = Field(default=None, gt=0)


class DomainUpdate(BaseModel):
    catch_all: str = Field(default="", max_length=254)


class WebmailHostIn(BaseModel):
    enabled: bool


class MailboxIn(BaseModel):
    domain_id: int = Field(gt=0)
    local_part: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    quota_mb: int | None = Field(default=None, ge=0, le=mail.MAX_QUOTA_MB)


class MailboxUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    quota_mb: int | None = Field(default=None, ge=0, le=mail.MAX_QUOTA_MB)
    enabled: bool | None = None


class ForwarderIn(BaseModel):
    domain_id: int = Field(gt=0)
    local_part: str = Field(min_length=1, max_length=64)
    destinations: list[str] = Field(min_length=1, max_length=mail.MAX_DESTINATIONS)


class ForwarderUpdate(BaseModel):
    destinations: list[str] = Field(min_length=1, max_length=mail.MAX_DESTINATIONS)


class AutoresponderIn(BaseModel):
    domain_id: int = Field(gt=0)
    local_part: str = Field(min_length=1, max_length=64)
    subject: str = Field(max_length=255)
    body: str = Field()


class AutoresponderUpdate(BaseModel):
    subject: str = Field(max_length=255)
    body: str = Field()


class DnsRecordIn(BaseModel):
    type: str = Field(min_length=1, max_length=8)
    name: str = Field(default="@", max_length=253)
    value: str = Field(min_length=1, max_length=2048)
    priority: int | None = Field(default=None, ge=0, le=65535)


class DomainRelayIn(BaseModel):
    relay: str = Field(default="", max_length=40)


class RelayIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    tls: str | None = Field(default=None, max_length=10)
    username: str = Field(default="", max_length=255)
    # Empty keeps the saved one.
    password: str = Field(default="", max_length=255)
    spf_include: str = Field(default="", max_length=200)
    dns_records: list[DnsRecordIn] = Field(default_factory=list, max_length=mail.MAX_RELAY_RECORDS)
    make_default: bool = False


class DefaultRelayIn(BaseModel):
    relay_id: str = Field(default="", max_length=40)


class RelayTestIn(BaseModel):
    to: str = Field(max_length=254)


class MailSettingsIn(BaseModel):
    auth_rate_per_hour: int | None = Field(default=None, ge=0, le=100000)
    local_rate_per_hour: int | None = Field(default=None, ge=0, le=100000)
    max_message_mb: int | None = Field(default=None, ge=1, le=200)
    spam_enabled: bool | None = None
    spam_header_score: float | None = Field(default=None, ge=1, le=100)
    spam_reject_score: float | None = Field(default=None, ge=1, le=100)
    greylisting: bool | None = None
    default_quota_mb: int | None = Field(default=None, ge=1, le=mail.MAX_USER_QUOTA_MB)
    allow: list[str] | None = Field(default=None, max_length=mail.MAX_ALLOW)


class AllowIn(BaseModel):
    value: str = Field(min_length=3, max_length=254)


# --- overview and domains ----------------------------------------------------------------

@router.get("/overview")
def get_overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return _answer(lambda: mail.overview(db, current_user))


@router.post("/domains")
def post_domain(payload: DomainIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.add_domain(db, current_user, payload.domain, payload.owner_id))
    log_action(db, current_user.id, "mail_domain_add", row.domain, f"owner={row.owner_id}", request=request)
    return mail.domain_out(db, row)


@router.put("/domains/{domain_id}")
def put_domain(domain_id: int, payload: DomainUpdate, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.set_catch_all(db, current_user, domain_id, payload.catch_all))
    log_action(db, current_user.id, "mail_catch_all", row.domain, row.catch_all or "off", request=request)
    return mail.domain_out(db, row)


@router.delete("/domains/{domain_id}")
def delete_domain(domain_id: int, request: Request, confirm: str = "", db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.get_domain(db, current_user, domain_id))
    # Every mailbox of the domain goes with it, so the name has to be typed.
    if confirm.strip().lower() != row.domain:
        raise HTTPException(status_code=400, detail="Type the domain name to confirm.")
    name = _answer(lambda: mail.delete_domain(db, current_user, domain_id))
    log_action(db, current_user.id, "mail_domain_delete", name, "", request=request)
    return {"ok": True, "domain": name}


@router.get("/domains/{domain_id}/dns")
def get_domain_dns(domain_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.get_domain(db, current_user, domain_id))
    return mail.dns_view(db, row, current_user)


@router.post("/domains/{domain_id}/dns/publish")
def publish_domain_dns(domain_id: int, request: Request, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Put the records on the page into this server's zone for the domain."""
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.get_domain(db, current_user, domain_id))
    names = _answer(lambda: mail.publish_dns(db, row, replace=True))
    log_action(db, current_user.id, "mail_dns_publish", row.domain, ", ".join(names), request=request)
    return {"published": names, **mail.dns_view(db, row, current_user)}


@router.put("/domains/{domain_id}/relay")
def put_domain_relay(domain_id: int, payload: DomainRelayIn, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.set_domain_relay(db, current_user, domain_id, payload.relay))
    log_action(db, current_user.id, "mail_domain_relay", row.domain, row.relay or "default", request=request)
    return mail.dns_view(db, row, current_user)


@router.post("/domains/{domain_id}/dkim/rotate")
def post_rotate_dkim(domain_id: int, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.rotate_dkim(db, current_user, domain_id))
    log_action(db, current_user.id, "mail_dkim_rotate", row.domain, "", request=request)
    return {"domain": row.domain, "records": mail.dns_records(row)}


@router.post("/domains/{domain_id}/webmail-host")
def post_webmail_host(domain_id: int, payload: WebmailHostIn, request: Request, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.set_webmail_host(db, current_user, domain_id, payload.enabled))
    log_action(db, current_user.id, "mail_webmail_host", f"webmail.{row.domain}", "on" if payload.enabled else "off",
               request=request)
    return mail.domain_out(db, row)


# --- mailboxes ----------------------------------------------------------------------------------

@router.get("/mailboxes")
def get_mailboxes(domain_id: int | None = None, q: str = "", page: int = 1, per_page: int = 50,
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return _answer(lambda: mail.list_accounts(db, current_user, domain_id, q, page, per_page))


@router.post("/mailboxes")
def post_mailbox(payload: MailboxIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    account = _answer(lambda: mail.create_account(db, current_user, payload.domain_id, payload.local_part,
                                                  payload.password, payload.quota_mb))
    log_action(db, current_user.id, "mail_create", account.address, f"quota {account.quota_mb} MB", request=request)
    return {"id": account.id, "address": account.address, "quota_mb": account.quota_mb}


@router.put("/mailboxes/{mailbox_id}")
def put_mailbox(mailbox_id: int, payload: MailboxUpdate, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    account = _answer(lambda: mail.update_account(db, current_user, mailbox_id, password=payload.password,
                                                  quota_mb=payload.quota_mb, enabled=payload.enabled))
    changed = [name for name, value in (("password", payload.password), ("quota", payload.quota_mb),
                                        ("enabled", payload.enabled)) if value is not None]
    log_action(db, current_user.id, "mail_update", account.address, ",".join(changed), request=request)
    return {"id": account.id, "address": account.address, "quota_mb": account.quota_mb, "enabled": account.enabled}


@router.delete("/mailboxes/{mailbox_id}")
def delete_mailbox(mailbox_id: int, request: Request, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    address = _answer(lambda: mail.delete_account(db, current_user, mailbox_id))
    log_action(db, current_user.id, "mail_delete", address, "", request=request)
    return {"ok": True, "address": address}


@router.post("/mailboxes/{mailbox_id}/webmail")
def open_webmail(mailbox_id: int, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """A one-time link that opens this mailbox in the webmail, signed in."""
    ensure_role(current_user.role, Role.end_user)
    url = _answer(lambda: mail.sso_url(db, current_user, mailbox_id))
    account = mail.get_account(db, current_user, mailbox_id)
    log_action(db, current_user.id, "mail_webmail_sso", account.address, "", request=request)
    return {"url": url, "expires_in": mail.SSO_TOKEN_SECONDS}


# --- forwarders ------------------------------------------------------------------------------------

@router.get("/forwarders")
def get_forwarders(domain_id: int | None = None, q: str = "", page: int = 1, per_page: int = 50,
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return _answer(lambda: mail.list_forwarders(db, current_user, domain_id, q, page, per_page))


@router.post("/forwarders")
def post_forwarder(payload: ForwarderIn, request: Request, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.create_forwarder(db, current_user, payload.domain_id, payload.local_part,
                                                payload.destinations))
    log_action(db, current_user.id, "mail_forwarder_create", row.address, ", ".join(row.destination_list), request=request)
    return {"id": row.id, "address": row.address, "destinations": row.destination_list}


@router.put("/forwarders/{forwarder_id}")
def put_forwarder(forwarder_id: int, payload: ForwarderUpdate, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.update_forwarder(db, current_user, forwarder_id, payload.destinations))
    log_action(db, current_user.id, "mail_forwarder_update", row.address, ", ".join(row.destination_list), request=request)
    return {"id": row.id, "address": row.address, "destinations": row.destination_list}


@router.delete("/forwarders/{forwarder_id}")
def delete_forwarder(forwarder_id: int, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    address = _answer(lambda: mail.delete_forwarder(db, current_user, forwarder_id))
    log_action(db, current_user.id, "mail_forwarder_delete", address, "", request=request)
    return {"ok": True, "address": address}


@router.get("/autoresponders")
def get_autoresponders(domain_id: int | None = None, q: str = "", page: int = 1, per_page: int = 50,
                       db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return _answer(lambda: mail.list_autoresponders(db, current_user, domain_id, q, page, per_page))


@router.post("/autoresponders")
def post_autoresponder(payload: AutoresponderIn, request: Request, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.create_autoresponder(db, current_user, payload.domain_id, payload.local_part,
                                                    payload.subject, payload.body))
    log_action(db, current_user.id, "mail_autoresponder_create", row.address, row.subject, request=request)
    return {"id": row.id, "address": row.address, "subject": row.subject}


@router.put("/autoresponders/{autoresponder_id}")
def put_autoresponder(autoresponder_id: int, payload: AutoresponderUpdate, request: Request, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    row = _answer(lambda: mail.update_autoresponder(db, current_user, autoresponder_id, payload.subject, payload.body))
    log_action(db, current_user.id, "mail_autoresponder_update", row.address, row.subject, request=request)
    return {"id": row.id, "address": row.address, "subject": row.subject}


@router.delete("/autoresponders/{autoresponder_id}")
def delete_autoresponder(autoresponder_id: int, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    address = _answer(lambda: mail.delete_autoresponder(db, current_user, autoresponder_id))
    log_action(db, current_user.id, "mail_autoresponder_delete", address, "", request=request)
    return {"ok": True, "address": address}


# --- server settings (administrators) -------------------------------------------------------------

@router.get("/settings")
def get_settings(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {
        "settings": mail.current_settings(),
        "queue": mail.queue_size(),
        "hostname": mail._hostname_or_blank(),
        "status": {**mail.server_status(), "outbound_smtp": mail.outbound_smtp_open()},
    }


@router.put("/settings")
def put_settings(payload: MailSettingsIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    values = {key: value for key, value in payload.model_dump().items() if value is not None}
    result = _answer(lambda: mail.save_settings(values))
    log_action(db, current_user.id, "mail_settings", ", ".join(sorted(values)), "", request=request)
    return {"settings": result}


# --- relays (administrators) ------------------------------------------------------------------------

@router.get("/relays")
def get_relays(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return mail.list_relays(db)


@router.post("/relays")
def post_relay(payload: RelayIn, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    _answer(lambda: mail.save_relay(db, payload.model_dump()))
    # From the request's own name and host: never from what the password went into.
    log_action(db, current_user.id, "mail_relay_add", payload.name, f"{payload.host}:{payload.port}", request=request)
    return mail.list_relays(db)


@router.put("/relays/{relay_id}")
def put_relay(relay_id: str, payload: RelayIn, request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    _answer(lambda: mail.save_relay(db, payload.model_dump(), relay_id))
    log_action(db, current_user.id, "mail_relay_update", payload.name, f"{payload.host}:{payload.port}", request=request)
    return mail.list_relays(db)


@router.delete("/relays/{relay_id}")
def delete_relay(relay_id: str, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    name = _answer(lambda: mail.delete_relay(db, relay_id))
    log_action(db, current_user.id, "mail_relay_delete", name, "", request=request)
    return mail.list_relays(db)


@router.put("/default-relay")
def put_default_relay(payload: DefaultRelayIn, request: Request, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    relay_id = _answer(lambda: mail.set_default_relay(db, payload.relay_id))
    log_action(db, current_user.id, "mail_default_relay", relay_id or "none", "", request=request)
    return mail.list_relays(db)


@router.post("/relays/test")
def relay_test(payload: RelayTestIn, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """Send one message now, and say what happened to it."""
    ensure_role(current_user.role, Role.admin)
    lines = _answer(lambda: mail.relay_test(payload.to))
    log_action(db, current_user.id, "mail_relay_test", payload.to, "", request=request)
    return {"lines": lines}


# --- logs, queue and Rspamd (administrators) ----------------------------------------------------------

@router.get("/log")
def get_log(lines: int = 200, q: str = "", current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"lines": _answer(lambda: mail.mail_log(lines, q)), "queue": mail.queue_size()}


@router.get("/rspamd/stat")
def get_rspamd_stat(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return _answer(mail.rspamd_stat)


@router.get("/rspamd/history")
def get_rspamd_history(page: int = 1, per_page: int = 50, q: str = "", action: str = "",
                       current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return _answer(lambda: mail.rspamd_history(page, per_page, q, action))


@router.get("/rspamd/log")
def get_rspamd_log(lines: int = 300, q: str = "", current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"lines": _answer(lambda: mail.rspamd_log(lines, q))}


@router.post("/rspamd/allow")
def post_rspamd_allow(payload: AllowIn, request: Request, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """A sender or a domain the spam filter never blocks, from the history."""
    ensure_role(current_user.role, Role.admin)
    result = _answer(lambda: mail.allow_sender(payload.value))
    log_action(db, current_user.id, "mail_spam_allow", payload.value, "", request=request)
    return {"settings": result}
