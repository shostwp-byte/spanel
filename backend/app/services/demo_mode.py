"""Demo mode: public accounts that can look at everything and change nothing.

Supports 3 role slots so visitors can explore all 3 levels of the panel:
- Admin (slot: "admin")
- Reseller (slot: "reseller")
- End User (slot: "customer", also accepts "end_user")

A demo server hands its passwords to anyone who asks, so the rule is enforced
where every signed-in request passes (api.deps.get_current_user) rather than in
the pages: a demo account's request that would change something is refused,
and so are the few reads that hand over more than a screen shows - file
contents, downloads, database access, the terminal.
"""

from __future__ import annotations

import re
import secrets

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.permissions import Role, normalize_role
from app.core.secrets import decrypt, encrypt, is_encrypted
from app.core.security import hash_password
from app.models.entities import User
from app.services import addons, panel_settings

SETTINGS_KEY = "demo_mode"
SLOTS = ("admin", "reseller", "customer")
MIN_PASSWORD = 6
MAX_PASSWORD = 128

REFUSED_CHANGE = "This is a demo: you can look at everything, but changes are not saved."
REFUSED_READ = "This is a demo: file contents, downloads, database access and the terminal are not available."

ALLOWED_WRITES = frozenset({
    ("POST", "/api/auth/logout"),
})

BLOCKED_READS = frozenset({
    "/api/databases/{database_id}/download",
    "/api/databases/phpmyadmin-sso/{token}",
    "/api/maintenance/app-files/{app_id}/download",
    "/api/maintenance/app-files/{app_id}/read",
    "/api/maintenance/backups/{website_id}/download",
    "/api/maintenance/files/{website_id}/download",
    "/api/maintenance/files/{website_id}/read",
    "/api/maintenance/user-backups-download",
    "/api/notifications/telegram/chats",
    "/api/mail/log",
    "/api/mail/rspamd/history",
    "/api/mail/rspamd/log",
})

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3})\.\d{1,3}\.\d{1,3}\b")
_IP_FIELD = re.compile(r"\bip=\S+")


def _config() -> dict:
    raw = panel_settings._read_raw_lenient().get(SETTINGS_KEY)
    return raw if isinstance(raw, dict) else {}


def _decode_password(entry: dict) -> str:
    raw = str(entry.get("password_enc") or entry.get("password") or "")
    if not raw:
        return ""
    if is_encrypted(raw):
        try:
            return decrypt(raw)
        except Exception:
            return ""
    return raw


def accounts() -> dict[str, dict]:
    """The configured slots, whether or not the addon is on."""
    stored = _config().get("accounts")
    stored = stored if isinstance(stored, dict) else {}
    result: dict[str, dict] = {}
    for slot in SLOTS:
        entry = stored.get(slot)
        if slot == "customer" and not entry:
            entry = stored.get("end_user")
        if isinstance(entry, dict) and entry.get("username"):
            result[slot] = {
                "username": str(entry["username"]),
                "password": _decode_password(entry),
            }
    return result


def active() -> bool:
    return addons.is_installed(addons.DEMO)


def demo_usernames() -> set[str]:
    if not active():
        return set()
    return {entry["username"] for entry in accounts().values()}


def is_demo_user(user: User | None) -> bool:
    return user is not None and user.username in demo_usernames()


def is_demo_session(user: User | None, payload: dict | None) -> bool:
    if payload and payload.get("imp"):
        return False
    return is_demo_user(user)


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return request.url.path
    if not template.startswith("/api/") and request.url.path.startswith("/api/"):
        template = "/api" + template
    return template


def enforce(request: Request, user: User, payload: dict | None) -> None:
    if not is_demo_session(user, payload):
        return
    method = request.method.upper()
    path = route_template(request)
    if method in _READ_METHODS:
        if path in BLOCKED_READS:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REFUSED_READ)
        return
    if (method, path) in ALLOWED_WRITES:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REFUSED_CHANGE)


def mask_ips(text: str) -> str:
    if not text:
        return text
    text = _IP_FIELD.sub("ip=hidden", text)
    return _IPV4.sub(lambda match: f"{match.group(1)}.x.x", text)


def public_view() -> dict:
    configured = accounts() if active() else {}
    items = [{"slot": slot, **configured[slot]} for slot in SLOTS if slot in configured]
    return {"enabled": bool(items), "accounts": items}


def _retire(user: User) -> None:
    user.hashed_password = hash_password(secrets.token_urlsafe(32))
    user.token_version = (user.token_version or 0) + 1


def _publish(user: User, password: str) -> None:
    user.hashed_password = hash_password(password)


def configure(db: Session, caller: User, requested: dict) -> dict:
    chosen: dict[str, dict] = {}
    for slot in SLOTS:
        entry = requested.get(slot)
        if slot == "customer" and not entry:
            entry = requested.get("end_user")
        if not entry:
            continue
        username = str(entry.get("username") or "").strip()
        password = str(entry.get("password") or "")
        if not username:
            continue
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            raise HTTPException(status_code=400, detail="That account does not exist.")
        if user.id == caller.id:
            raise HTTPException(status_code=400, detail="You cannot make your own account a demo account.")
        user_role = normalize_role(user.role)
        if slot == "admin" and user_role != Role.admin:
            raise HTTPException(status_code=400, detail="The administrator demo account must be an administrator.")
        if slot == "reseller" and user_role != Role.reseller:
            raise HTTPException(status_code=400, detail="The reseller demo account must be a reseller.")
        if slot == "customer" and user_role != Role.end_user:
            raise HTTPException(
                status_code=400, detail="The End User demo account must be an End User account.",
            )
        if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            raise HTTPException(status_code=400, detail="A demo password must be 6 to 128 characters.")
        chosen[slot] = {"username": username, "password": password, "user": user}

    previous = {entry["username"] for entry in accounts().values()}
    kept = {entry["username"] for entry in chosen.values()}
    for username in previous - kept:
        user = db.query(User).filter(User.username == username).first()
        if user is not None:
            _retire(user)
    if active():
        for entry in chosen.values():
            _publish(entry["user"], entry["password"])
    db.commit()

    settings = panel_settings._read_raw()
    settings[SETTINGS_KEY] = {"accounts": {
        slot: {"username": entry["username"], "password_enc": encrypt(entry["password"])}
        for slot, entry in chosen.items()
    }}
    panel_settings._write_raw(settings)
    return admin_view(db)


def switch_on(db: Session) -> list[str]:
    names = []
    for entry in accounts().values():
        user = db.query(User).filter(User.username == entry["username"]).first()
        if user is not None and len(entry["password"]) >= MIN_PASSWORD:
            _publish(user, entry["password"])
            names.append(user.username)
    db.commit()
    return names


def switch_off(db: Session) -> list[str]:
    names = []
    for entry in accounts().values():
        user = db.query(User).filter(User.username == entry["username"]).first()
        if user is not None:
            _retire(user)
            names.append(user.username)
    db.commit()
    return names


def admin_view(db: Session) -> dict:
    users = db.query(User).order_by(User.username).all()
    current_accounts = accounts()
    return {
        "accounts": {slot: current_accounts.get(slot) for slot in SLOTS},
        "candidates": {
            "admin": [user.username for user in users if normalize_role(user.role) == Role.admin],
            "reseller": [user.username for user in users if normalize_role(user.role) == Role.reseller],
            "customer": [user.username for user in users if normalize_role(user.role) == Role.end_user],
            "end_user": [user.username for user in users if normalize_role(user.role) == Role.end_user],
        },
    }
