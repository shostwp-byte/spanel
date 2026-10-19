"""The Notifications addon: email over the server's SMTP, and Telegram.

Everything here is a no-op until an administrator installs the addon. The
server's own settings - the SMTP account, the Telegram bot, the thresholds and
the language - live in a JSON file beside the panel's other settings, with the
SMTP password and the bot token encrypted. What each administrator wants
lives in their notification_prefs row. Only administrators receive anything
(operator, 2026-09-27): an account that is not one is skipped wherever it is
named.

`notify()` is the one way anything is sent. It runs wherever the event happens:
the API, the backup scheduler, the malware scheduler, the 5-minute watcher. It
opens its own database session, never raises, and records every delivery in
notification_log, which is also what stops a repeating condition from
repeating the message.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets as pysecrets
import smtplib
import socket
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core import secrets as secret_box
from app.core.database import SessionLocal
from app.core.permissions import is_admin_role
from app.models.entities import LoginSource, NotificationLog, NotificationPref, User
from app.services import addons, notify_messages

logger = logging.getLogger("spanel.notifications")

DATA_DIR = Path(os.environ.get("spanel_DATA_DIR", "/var/lib/spanel"))
CONFIG_FILE = DATA_DIR / "notifications.json"
SMTP_SECURITY = ("starttls", "ssl", "none")
TELEGRAM_API = "https://api.telegram.org"
LINK_CODE_MINUTES = 15
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# A private chat or group is a number (groups and channels negative); a public
# channel can also be named.
_CHAT_ID_RE = re.compile(r"^(-?\d{3,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")

DEFAULTS: dict = {
    "language": "vi",
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "",
             "password_enc": "", "from_email": "", "from_name": "spanel"},
    "telegram": {"bot_token_enc": "", "bot_username": "", "chat_id": "", "update_offset": 0},
    "thresholds": {"disk_percent": 90, "ssl_days": 7},
}


# --- settings ---------------------------------------------------------------

def is_enabled() -> bool:
    return addons.is_installed(addons.NOTIFICATIONS)


def load_config() -> dict:
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    config = json.loads(json.dumps(DEFAULTS))
    for section, value in (stored if isinstance(stored, dict) else {}).items():
        if isinstance(value, dict) and isinstance(config.get(section), dict):
            config[section].update(value)
        else:
            config[section] = value
    return config


def _write_config(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(DATA_DIR), delete=False) as tmp:
        json.dump(config, tmp, ensure_ascii=True, indent=2, sort_keys=True)
        tmp.write("\n")
        path = Path(tmp.name)
    os.chmod(path, 0o600)
    path.replace(CONFIG_FILE)


def smtp_ready(config: dict) -> bool:
    smtp = config["smtp"]
    return bool(smtp.get("host") and smtp.get("from_email"))


def telegram_ready(config: dict) -> bool:
    return bool(config["telegram"].get("bot_token_enc"))


def admin_chat(config: dict) -> str:
    """The server's admin chat, when there is a bot to send through."""
    return config["telegram"].get("chat_id", "") if telegram_ready(config) else ""


def valid_chat_id(value: str) -> str:
    chat = str(value or "").strip()
    if chat and not _CHAT_ID_RE.match(chat):
        raise ValueError("A chat ID is a number (negative for a group) or a @channel name")
    return chat


def public_config(config: dict | None = None) -> dict:
    """The settings as the page shows them: secrets reduced to "is one set"."""
    config = config or load_config()
    smtp = {k: v for k, v in config["smtp"].items() if k != "password_enc"}
    smtp["password_set"] = bool(config["smtp"].get("password_enc"))
    telegram = {"bot_username": config["telegram"].get("bot_username", ""),
                "token_set": telegram_ready(config), "chat_id": config["telegram"].get("chat_id", "")}
    return {
        "language": config["language"],
        "smtp": smtp,
        "telegram": telegram,
        "thresholds": dict(config["thresholds"]),
        "smtp_ready": smtp_ready(config),
        "telegram_ready": telegram_ready(config),
    }


def save_config(changes: dict) -> dict:
    """Merge the page's changes in. A secret left empty keeps the stored one;
    `clear_password` / `clear_bot_token` remove it. A new bot token is checked
    with Telegram before it is kept, and the bot's name is read from it."""
    config = load_config()
    if changes.get("language") in notify_messages.LANGUAGES:
        config["language"] = changes["language"]

    smtp = changes.get("smtp") or {}
    if smtp:
        current = config["smtp"]
        for key in ("host", "username", "from_name"):
            if key in smtp and smtp[key] is not None:
                current[key] = str(smtp[key]).strip()[:255]
        if smtp.get("from_email") is not None:
            address = str(smtp["from_email"]).strip()
            if address and not _EMAIL_RE.match(address):
                raise ValueError("The sender address is not an email address")
            current["from_email"] = address
        if smtp.get("port") is not None:
            port = int(smtp["port"])
            if not 1 <= port <= 65535:
                raise ValueError("SMTP port must be between 1 and 65535")
            current["port"] = port
        if smtp.get("security") is not None:
            if smtp["security"] not in SMTP_SECURITY:
                raise ValueError("SMTP security must be starttls, ssl or none")
            current["security"] = smtp["security"]
        if smtp.get("password"):
            current["password_enc"] = secret_box.encrypt(str(smtp["password"]))
        if smtp.get("clear_password"):
            current["password_enc"] = ""

    telegram = changes.get("telegram") or {}
    if telegram.get("bot_token"):
        token = str(telegram["bot_token"]).strip()
        if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,64}", token):
            raise ValueError("That does not look like a bot token from @BotFather")
        try:
            me = telegram_call(token, "getMe", {})
        except RuntimeError as exc:
            raise ValueError(f"Telegram refused the token: {exc}") from exc
        config["telegram"].update({"bot_token_enc": secret_box.encrypt(token),
                                   "bot_username": me.get("username", ""), "update_offset": 0})
    if telegram.get("chat_id") is not None:
        config["telegram"]["chat_id"] = valid_chat_id(telegram["chat_id"])
    if telegram.get("clear_bot_token"):
        config["telegram"].update({"bot_token_enc": "", "bot_username": "", "chat_id": "", "update_offset": 0})

    thresholds = changes.get("thresholds") or {}
    for key, low, high in (("disk_percent", 50, 99), ("ssl_days", 1, 60)):
        if thresholds.get(key) is not None:
            value = int(thresholds[key])
            if not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
            config["thresholds"][key] = value

    _write_config(config)
    return public_config(config)


def server_label() -> str:
    try:
        from app.services.panel_urls import configured_panel_host
        host = configured_panel_host()
    except Exception:  # noqa: BLE001 - a label is not worth failing a send over
        host = ""
    return host or socket.gethostname()


def now_text() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


# --- channels ---------------------------------------------------------------

def send_email(config: dict, to_address: str, subject: str, body: str) -> None:
    smtp = config["smtp"]
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((smtp.get("from_name") or "spanel", smtp["from_email"]))
    message["To"] = to_address
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=smtp["from_email"].rsplit("@", 1)[-1])
    message.set_content(body)
    context = ssl.create_default_context()
    port = int(smtp.get("port") or 587)
    if smtp.get("security") == "ssl":
        server = smtplib.SMTP_SSL(smtp["host"], port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(smtp["host"], port, timeout=20)
    try:
        server.ehlo()
        if smtp.get("security") == "starttls":
            server.starttls(context=context)
            server.ehlo()
        password = secret_box.decrypt(smtp.get("password_enc"))
        if smtp.get("username"):
            server.login(smtp["username"], password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception as exc:  # noqa: BLE001 - the message is already sent or already failed
            logger.debug("SMTP quit: %s", exc)


def telegram_call(token: str, method: str, payload: dict, timeout: int = 15) -> dict:
    """One Bot API call. The token is in the URL, so nothing here ever puts
    the URL into an error message."""
    request = urllib.request.Request(  # noqa: S310 - a fixed https host
        f"{TELEGRAM_API}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            description = json.load(exc).get("description", "")
        except (ValueError, OSError):
            description = ""
        raise RuntimeError(description or f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"could not reach Telegram ({type(exc).__name__})") from None
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or "Telegram said no")
    return body.get("result") or {}


def send_telegram(config: dict, chat_id: str, title: str, body: str) -> None:
    token = secret_box.decrypt(config["telegram"].get("bot_token_enc"))
    text = f"<b>{html.escape(title)}</b>\n{html.escape(body)}"
    telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000],
                                         "parse_mode": "HTML", "disable_web_page_preview": True})


# --- people -----------------------------------------------------------------

def prefs_for(db, user_id: int) -> NotificationPref:
    pref = db.get(NotificationPref, user_id)
    if pref is None:
        pref = NotificationPref(user_id=user_id, email_enabled=True, telegram_enabled=True, muted_events="")
        db.add(pref)
        db.flush()
    return pref


def muted(pref: NotificationPref) -> set[str]:
    return {item for item in (pref.muted_events or "").split(",") if item}


def _admins(db) -> list[User]:
    return [u for u in db.query(User).filter(User.is_active.is_(True)).all() if is_admin_role(u.role)]


def _recently_sent(db, user_id: int | None, dedupe_key: str, cooldown: timedelta) -> bool:
    since = datetime.utcnow() - cooldown
    owner = NotificationLog.user_id.is_(None) if user_id is None else NotificationLog.user_id == user_id
    return db.query(NotificationLog.id).filter(
        owner,
        NotificationLog.dedupe_key == dedupe_key,
        NotificationLog.status == "sent",
        NotificationLog.created_at >= since,
    ).first() is not None


def _log(db, user_id, event, channel, status, title, detail="", dedupe_key=None) -> None:
    db.add(NotificationLog(user_id=user_id, event=event, channel=channel, status=status,
                           title=title[:255], detail=(detail or "")[:2000], dedupe_key=dedupe_key))


def notify(event: str, params: dict, *, user_ids: list[int] | None = None, admins: bool = False,
           dedupe_key: str | None = None, cooldown: timedelta = timedelta(hours=24)) -> int:
    """Send one event to the people it concerns. Returns how many messages went.

    `admins=True` adds every active administrator; `user_ids` adds those
    accounts when they are administrators - anybody else is skipped, because
    customers get no notifications. An administrator gets it on each channel
    that is set up on the server, turned on in their settings and reachable
    (an email address, a chat), unless they muted the event.

    Telegram has one more destination: the server's admin chat (token + chat
    ID in the server settings). An event about the server goes there once,
    not once per administrator, as long as one administrator still wants it;
    an administrator with no chat of their own also hears about their own
    account there. Never raises: a notification must not break the thing it
    is about.
    """
    if event not in notify_messages.EVENTS:
        raise ValueError(f"unknown notification event: {event}")
    try:
        if not is_enabled():
            return 0
        config = load_config()
        if not (smtp_ready(config) or telegram_ready(config)):
            return 0
        title, body = notify_messages.render(event, params, config.get("language", "vi"))
        subject = f"[{server_label()}] {title}"
        about_server = notify_messages.EVENTS[event]["audience"] == notify_messages.ADMIN
        server_chat = admin_chat(config)
        wanted_in_admin_chat = False
        sent = 0
        db = SessionLocal()
        try:
            people: dict[int, User] = {}
            if admins:
                people.update({u.id: u for u in _admins(db)})
            for uid in user_ids or []:
                user = db.get(User, uid)
                if user is not None and is_admin_role(user.role):
                    people[user.id] = user
            for user in people.values():
                pref = prefs_for(db, user.id)
                if event in muted(pref):
                    continue
                if about_server:
                    wanted_in_admin_chat = True
                if dedupe_key and _recently_sent(db, user.id, dedupe_key, cooldown):
                    continue
                if smtp_ready(config) and pref.email_enabled and _EMAIL_RE.match(user.email or ""):
                    try:
                        send_email(config, user.email, subject, body)
                        _log(db, user.id, event, "email", "sent", title, dedupe_key=dedupe_key)
                        sent += 1
                    except Exception as exc:  # noqa: BLE001 - recorded, never raised
                        _log(db, user.id, event, "email", "failed", title, f"{type(exc).__name__}: {exc}", dedupe_key)
                chat = pref.telegram_chat_id or server_chat
                if about_server and server_chat:
                    chat = ""  # said once in the admin chat, below
                if telegram_ready(config) and pref.telegram_enabled and chat:
                    try:
                        send_telegram(config, chat, title, f"{body}\n\n{server_label()}")
                        _log(db, user.id, event, "telegram", "sent", title, dedupe_key=dedupe_key)
                        sent += 1
                    except Exception as exc:  # noqa: BLE001 - recorded, never raised
                        _log(db, user.id, event, "telegram", "failed", title, str(exc), dedupe_key)
            if about_server and server_chat and wanted_in_admin_chat and not (
                    dedupe_key and _recently_sent(db, None, dedupe_key, cooldown)):
                try:
                    send_telegram(config, server_chat, title, f"{body}\n\n{server_label()}")
                    _log(db, None, event, "telegram", "sent", title, dedupe_key=dedupe_key)
                    sent += 1
                except Exception as exc:  # noqa: BLE001 - recorded, never raised
                    _log(db, None, event, "telegram", "failed", title, str(exc), dedupe_key)
            db.commit()
        finally:
            db.close()
        return sent
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("notification %s not sent: %s", event, exc)
        return 0


def notify_in_background(event: str, params: dict, **kwargs) -> None:
    """For request handlers: the sign-in answers now, the email goes after."""
    if not is_enabled():
        return
    threading.Thread(target=notify, args=(event, params), kwargs=kwargs,
                     name=f"spanel-notify-{event}", daemon=True).start()


def network_of(ip: str) -> str:
    """The /24 (IPv4) or /64 (IPv6) an address belongs to.

    A phone on mobile data gets a different address from the same carrier
    range every day; comparing whole addresses would call each of them new and
    turn a security notice into noise nobody reads.
    """
    import ipaddress

    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    prefix = 24 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def record_login(user_id: int, username: str, ip: str, agent: str = "") -> None:
    """Remember where an account signs in from; tell its holder about a new place.

    "New" means a network (see network_of) the account has not signed in from.
    The first address seen for an account is recorded without a message:
    otherwise every account would hear "new address" the first time it signs
    in after the addon is turned on.
    """
    if not is_enabled() or not ip:
        return

    def run() -> None:
        db = SessionLocal()
        try:
            known = db.query(LoginSource).filter(LoginSource.user_id == user_id).all()
            match = next((row for row in known if row.ip == ip), None)
            if match:
                match.last_seen_at = datetime.utcnow()
                db.commit()
                return
            seen_network = any(network_of(row.ip) == network_of(ip) for row in known)
            db.add(LoginSource(user_id=user_id, ip=ip[:64]))
            db.commit()
            if known and not seen_network:
                notify("login_new_ip", {"username": username, "ip": ip, "when": now_text(),
                                        "agent": (agent or "")[:160]}, user_ids=[user_id])
        except Exception as exc:  # noqa: BLE001 - a sign-in must never fail over this
            logger.warning("login source not recorded: %s", exc)
            db.rollback()
        finally:
            db.close()

    threading.Thread(target=run, name="spanel-notify-login", daemon=True).start()


# --- Telegram linking -------------------------------------------------------

def start_telegram_link(db, user_id: int) -> dict:
    """A one-time code the person sends to the bot as /start <code>."""
    config = load_config()
    if not telegram_ready(config):
        raise ValueError("No Telegram bot is set up on this server yet")
    pref = prefs_for(db, user_id)
    pref.telegram_link_code = pysecrets.token_hex(8)
    pref.telegram_link_expires_at = datetime.utcnow() + timedelta(minutes=LINK_CODE_MINUTES)
    db.commit()
    bot = config["telegram"].get("bot_username", "")
    return {"code": pref.telegram_link_code, "bot_username": bot,
            "link": f"https://t.me/{bot}?start={pref.telegram_link_code}" if bot else "",
            "expires_minutes": LINK_CODE_MINUTES}


def collect_telegram_links(db) -> list[int]:
    """Read the bot's new messages and link every chat that sent a pending code.

    getUpdates hands each message out once (the offset moves past it), so one
    read settles every code waiting on the server, not only the caller's.
    """
    config = load_config()
    if not telegram_ready(config):
        raise ValueError("No Telegram bot is set up on this server yet")
    token = secret_box.decrypt(config["telegram"]["bot_token_enc"])
    offset = int(config["telegram"].get("update_offset") or 0)
    updates = telegram_call(token, "getUpdates", {"offset": offset, "timeout": 0,
                                                  "allowed_updates": ["message"]}, timeout=20)
    now = datetime.utcnow()
    pending = {p.telegram_link_code: p for p in db.query(NotificationPref).filter(
        NotificationPref.telegram_link_code.isnot(None),
        NotificationPref.telegram_link_expires_at >= now).all()}
    linked = []
    for update in updates if isinstance(updates, list) else []:
        offset = max(offset, int(update.get("update_id", 0)) + 1)
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        parts = text.split()
        if len(parts) == 2 and parts[0] == "/start" and parts[1] in pending and chat.get("id") is not None:
            pref = pending.pop(parts[1])
            pref.telegram_chat_id = str(chat["id"])
            pref.telegram_link_code = None
            pref.telegram_link_expires_at = None
            pref.telegram_enabled = True
            linked.append(pref.user_id)
            try:
                telegram_call(token, "sendMessage", {"chat_id": chat["id"], "text":
                              "spanel: đã kết nối. Thông báo của bạn sẽ được gửi về đây."
                              if config.get("language", "vi") == "vi" else
                              "spanel: connected. Your notifications will arrive here."})
            except RuntimeError:
                pass
    db.commit()
    if offset != int(config["telegram"].get("update_offset") or 0):
        stored = load_config()
        stored["telegram"]["update_offset"] = offset
        _write_config(stored)
    return linked


def recent_chats() -> list[dict]:
    """Chats that recently wrote to the bot, so the admin chat can be picked
    rather than looked up. Reads without consuming: the /start links still
    find their messages afterwards."""
    config = load_config()
    if not telegram_ready(config):
        raise ValueError("Set the Telegram bot token first")
    token = secret_box.decrypt(config["telegram"]["bot_token_enc"])
    offset = int(config["telegram"].get("update_offset") or 0)
    updates = telegram_call(token, "getUpdates", {"offset": offset, "timeout": 0}, timeout=20)
    chats: dict[str, dict] = {}
    for update in updates if isinstance(updates, list) else []:
        for kind in ("message", "channel_post", "my_chat_member"):
            chat = (update.get(kind) or {}).get("chat") or {}
            if chat.get("id") is None:
                continue
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) \
                or (f"@{chat['username']}" if chat.get("username") else "")
            chats[str(chat["id"])] = {"id": str(chat["id"]), "type": chat.get("type", ""), "name": name}
    return list(chats.values())


def send_test(user: User, channel: str, db) -> str:
    """A test message to the person asking, on one channel. Raises on failure
    with what the server said, which is the point of a test."""
    config = load_config()
    title, body = (("spanel: thử thông báo", "Nếu bạn đọc được dòng này, kênh này đã hoạt động.")
                   if config.get("language", "vi") == "vi" else
                   ("spanel: test notification", "If you can read this, the channel works."))
    if channel == "email":
        if not smtp_ready(config):
            raise ValueError("Set the SMTP server and sender address first")
        if not _EMAIL_RE.match(user.email or ""):
            raise ValueError("Your account has no email address")
        send_email(config, user.email, f"[{server_label()}] {title}", body)
        _log(db, user.id, "test", "email", "sent", title)
        db.commit()
        return user.email
    if channel == "telegram_admin":
        if not admin_chat(config):
            raise ValueError("Set the bot token and the chat ID first")
        send_telegram(config, admin_chat(config), title, body)
        _log(db, None, "test", "telegram", "sent", title)
        db.commit()
        return config["telegram"]["chat_id"]
    if channel == "telegram":
        pref = prefs_for(db, user.id)
        if not telegram_ready(config):
            raise ValueError("Set the Telegram bot token first")
        if not pref.telegram_chat_id:
            raise ValueError("Connect your Telegram first")
        send_telegram(config, pref.telegram_chat_id, title, body)
        _log(db, user.id, "test", "telegram", "sent", title)
        db.commit()
        return "telegram"
    raise ValueError("Unknown channel")
