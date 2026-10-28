#!/usr/bin/env python3
"""SPanel Webmail Service (Email Add-on).

Runs behind Nginx on 127.0.0.1:8096 (public port 2096 and webmail.<domain>).
Reads configuration from /etc/SPanel-webmail.env (or environment variables):
  - AUTH_SECRET
  - IMAP_HOST (127.0.0.1), IMAP_PORT (143), IMAP_SECURE (false)
  - SMTP_HOST (127.0.0.1), SMTP_PORT (587), SMTP_SECURE (false)
  - SSO_SECRET, SSO_MASTER_USER (spanel-webmail), SSO_MASTER_PASSWORD, SSO_MASTER_SEPARATOR (*)
  - DATA_DIR (/opt/SPanel-webmail/data), HOST (127.0.0.1), PORT (8096)
"""

from __future__ import annotations

import base64
import email
import email.header
import email.policy
import email.utils
import hashlib
import hmac
import html
import imaplib
import json
import mimetypes
import os
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

ENV_FILE = Path(os.environ.get("WEBMAIL_ENV_FILE", "/etc/SPanel-webmail.env"))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


_load_env_file(ENV_FILE)


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _cfg_bool(name: str, default: bool = False) -> bool:
    val = _cfg(name, "true" if default else "false").lower()
    return val in ("1", "true", "yes", "on")


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(_cfg(name, str(default)))
    except ValueError:
        return default


COOKIE_NAME = "spanel_webmail_session"
SESSION_TTL_SECONDS = 86400 * 7  # 7 days (password login)
MASTER_SESSION_TTL_SECONDS = 3600 * 2  # 2 hours (SSO master login)
SSO_MAX_SKEW_SECONDS = 300
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+=-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9._ /+-]{1,128}$")
UID_RE = re.compile(r"^[0-9]{1,12}$")

STANDARD_FOLDERS = [
    ("INBOX", "Hộp thư đến", "Inbox"),
    ("Starred", "Đã gắn sao", "Starred"),
    ("Sent", "Đã gửi", "Sent"),
    ("Drafts", "Thư nháp", "Drafts"),
    ("Junk", "Thư rác (Spam)", "Junk / Spam"),
    ("Trash", "Thùng rác", "Trash"),
]
IMAP_SYSTEM_FOLDERS = ("INBOX", "Sent", "Drafts", "Junk", "Trash")

# Replay cache for single-use SSO nonces: {nonce: exp_timestamp}
_used_sso_nonces: dict[str, int] = {}

LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILURES_PER_PAIR = 5
LOGIN_MAX_FAILURES_PER_IP = 15
LOGIN_MAX_FAILURES_PER_EMAIL = 20
_login_failures_pair: dict[tuple[str, str], list[float]] = {}
_login_failures_ip: dict[str, list[float]] = {}
_login_failures_email: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        # Nginx overwrites X-Forwarded-For with $remote_addr; take the last hop
        last = xff.split(",")[-1].strip()
        if last:
            return last
    xri = (request.headers.get("x-real-ip") or "").strip()
    if xri:
        return xri
    if request.client and request.client.host:
        return str(request.client.host)
    return "127.0.0.1"


def _prune_window(buckets: dict[Any, list[float]], key: Any, cutoff: float) -> list[float]:
    entries = [ts for ts in buckets.get(key, []) if ts >= cutoff]
    if entries:
        buckets[key] = entries
    else:
        buckets.pop(key, None)
    return entries


def _check_login_rate_limit(client_ip: str, address: str, *, now: float | None = None) -> None:
    current = float(now if now is not None else time.time())
    cutoff = current - LOGIN_WINDOW_SECONDS
    pair_key = (client_ip, address)
    if (
        len(_prune_window(_login_failures_pair, pair_key, cutoff)) >= LOGIN_MAX_FAILURES_PER_PAIR
        or len(_prune_window(_login_failures_ip, client_ip, cutoff)) >= LOGIN_MAX_FAILURES_PER_IP
        or len(_prune_window(_login_failures_email, address, cutoff)) >= LOGIN_MAX_FAILURES_PER_EMAIL
    ):
        raise HTTPException(
            status_code=429,
            detail="Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau vài phút.",
            headers={"Retry-After": "60"},
        )


def _record_login_failure(client_ip: str, address: str, *, now: float | None = None) -> None:
    current = float(now if now is not None else time.time())
    cutoff = current - LOGIN_WINDOW_SECONDS
    pair_key = (client_ip, address)
    _prune_window(_login_failures_pair, pair_key, cutoff)
    _login_failures_pair.setdefault(pair_key, []).append(current)
    _prune_window(_login_failures_ip, client_ip, cutoff)
    _login_failures_ip.setdefault(client_ip, []).append(current)
    _prune_window(_login_failures_email, address, cutoff)
    _login_failures_email.setdefault(address, []).append(current)


def _clear_login_failures(client_ip: str, address: str) -> None:
    _login_failures_pair.pop((client_ip, address), None)


def _data_dir() -> Path:
    d = Path(_cfg("DATA_DIR", "/opt/SPanel-webmail/data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sessions_dir() -> Path:
    d = _data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _prefs_dir() -> Path:
    d = _data_dir() / "prefs"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _auth_secret() -> bytes:
    secret = _cfg("AUTH_SECRET") or _cfg("SSO_SECRET") or "spanel-webmail-default-secret"
    return secret.encode("utf-8")


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def _xor_Subtle(data: bytes, key_material: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    offset = 0
    counter = 0
    while offset < len(data):
        block = hmac.new(
            key_material,
            nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        chunk = min(len(block), len(data) - offset)
        for i in range(chunk):
            out[offset + i] = data[offset + i] ^ block[i]
        offset += chunk
        counter += 1
    return bytes(out)


def _encrypt_secret(plaintext: str) -> str:
    nonce = secrets.token_bytes(16)
    cipher = _xor_Subtle(plaintext.encode("utf-8"), _auth_secret(), nonce)
    mac = hmac.new(_auth_secret(), nonce + cipher, hashlib.sha256).digest()
    return f"{_b64u_encode(nonce)}.{_b64u_encode(cipher)}.{_b64u_encode(mac)}"


def _decrypt_secret(token: str) -> str:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return ""
    try:
        nonce = _b64u_decode(parts[0])
        cipher = _b64u_decode(parts[1])
        mac = _b64u_decode(parts[2])
        expected = hmac.new(_auth_secret(), nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            return ""
        return _xor_Subtle(cipher, _auth_secret(), nonce).decode("utf-8")
    except Exception:
        return ""


def _sign_session_id(session_id: str) -> str:
    sig = _b64u_encode(hmac.new(_auth_secret(), session_id.encode("utf-8"), hashlib.sha256).digest())
    return f"{session_id}.{sig}"


def _verify_session_cookie(cookie_val: str | None) -> str | None:
    if not cookie_val or "." not in cookie_val:
        return None
    session_id, _, sig = cookie_val.partition(".")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", session_id):
        return None
    expected = _b64u_encode(hmac.new(_auth_secret(), session_id.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    return session_id


def _generations_file() -> Path:
    return _data_dir() / "mailbox_generations.json"


def _expected_mailbox_generation(address: str) -> tuple[bool, str]:
    path = _generations_file()
    if not path.is_file():
        return False, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return True, str(raw.get(address.lower().strip()) or "").strip()
    except Exception:
        pass
    return False, ""


def _record_mailbox_generation(address: str, gen: str) -> None:
    clean_addr = address.lower().strip()
    clean_gen = str(gen or "").strip()
    if not clean_addr or not clean_gen:
        return
    path = _generations_file()
    mapping: dict[str, str] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                mapping = {str(k).lower().strip(): str(v).strip() for k, v in loaded.items()}
        except Exception:
            mapping = {}
    if mapping.get(clean_addr) != clean_gen:
        mapping[clean_addr] = clean_gen
        try:
            path.write_text(json.dumps(mapping), encoding="utf-8")
        except OSError:
            pass


def _purge_expired_sessions(now: int | None = None) -> int:
    current_ts = int(now if now is not None else time.time())
    removed = 0
    for path in _sessions_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("exp") or 0) < current_ts:
                path.unlink(missing_ok=True)
                removed += 1
        except Exception:
            pass
    return removed


def purge_sessions_for_email(address: str) -> int:
    target = (address or "").strip().lower()
    if not target:
        return 0
    removed = 0
    for path in _sessions_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("email") or "").strip().lower() == target:
                path.unlink(missing_ok=True)
                removed += 1
        except Exception:
            pass
    return removed


def purge_sessions_for_domain(domain: str) -> int:
    suffix = "@" + (domain or "").strip().lower().lstrip("@")
    if suffix == "@":
        return 0
    removed = 0
    for path in _sessions_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("email") or "").strip().lower().endswith(suffix):
                path.unlink(missing_ok=True)
                removed += 1
        except Exception:
            pass
    return removed


def _create_session(address: str, *, mode: str = "master", password: str = "", gen: str = "") -> str:
    _purge_expired_sessions()
    clean_addr = address.lower().strip()
    clean_gen = str(gen or "").strip()
    if not clean_gen:
        _, clean_gen = _expected_mailbox_generation(clean_addr)
    else:
        # If a new generation is presented for this address, invalidate older generation sessions
        for old_path in _sessions_dir().glob("*.json"):
            try:
                old_data = json.loads(old_path.read_text(encoding="utf-8"))
                if str(old_data.get("email") or "").strip().lower() == clean_addr:
                    old_gen = str(old_data.get("gen") or "").strip()
                    if old_gen and old_gen != clean_gen:
                        old_path.unlink(missing_ok=True)
            except Exception:
                pass
        _record_mailbox_generation(clean_addr, clean_gen)

    session_id = secrets.token_urlsafe(24)
    now = int(time.time())
    ttl = MASTER_SESSION_TTL_SECONDS if mode == "master" else SESSION_TTL_SECONDS
    payload = {
        "email": clean_addr,
        "mode": mode,
        "password_enc": _encrypt_secret(password) if password else "",
        "gen": clean_gen,
        "created_at": now,
        "exp": now + ttl,
    }
    path = _sessions_dir() / f"{session_id}.json"
    tmp = _sessions_dir() / f".{session_id}.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return _sign_session_id(session_id)


def _get_session(request: Request) -> dict[str, Any] | None:
    cookie_val = request.cookies.get(COOKIE_NAME)
    session_id = _verify_session_cookie(cookie_val)
    if not session_id:
        return None
    path = _sessions_dir() / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        now = int(time.time())
        if int(data.get("exp") or 0) < now:
            path.unlink(missing_ok=True)
            return None
        email_addr = str(data.get("email") or "").strip().lower()
        sess_gen = str(data.get("gen") or "").strip()
        has_gen_map, expected_gen = _expected_mailbox_generation(email_addr)
        if has_gen_map:
            if not expected_gen or (sess_gen and sess_gen != expected_gen):
                path.unlink(missing_ok=True)
                return None
        data["session_id"] = session_id
        return data
    except Exception:
        return None


def _verify_csrf_request(request: Request) -> None:
    if request.method.upper() not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    origin = (request.headers.get("origin") or "").strip()
    xrw = (request.headers.get("x-requested-with") or "").strip()
    expected_host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or ""
    ).split(",")[0].strip().lower()

    if origin:
        if origin.lower() == "null":
            raise HTTPException(status_code=403, detail="Yêu cầu bị từ chối (CSRF Origin không hợp lệ).")
        parsed_origin = urlsplit(origin)
        origin_host = (parsed_origin.netloc or "").strip().lower()
        if not origin_host or (expected_host and origin_host != expected_host):
            raise HTTPException(status_code=403, detail="Yêu cầu bị từ chối (CSRF Origin không khớp).")
        return

    if xrw.lower() != "xmlhttprequest":
        raise HTTPException(status_code=403, detail="Yêu cầu bị từ chối (thiếu xác thực CSRF).")


def _require_session(request: Request) -> dict[str, Any]:
    _verify_csrf_request(request)
    sess = _get_session(request)
    if not sess:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    return sess


def _verify_sso_token_with_gen(token: str, *, secret: str | None = None, now: int | None = None) -> tuple[str, str]:
    """Verify SPanel's HMAC-SHA256 SSO token and return (address, generation)."""
    sso_secret = (secret if secret is not None else _cfg("SSO_SECRET")).strip()
    if not sso_secret:
        raise ValueError("SSO_SECRET is not configured")
    if not token or "." not in token:
        raise ValueError("Invalid SSO token format")
    body, _, signature = token.partition(".")
    if not body or not signature:
        raise ValueError("Malformed SSO token")
    expected = _b64u_encode(hmac.new(sso_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid SSO token signature")
    try:
        payload = json.loads(_b64u_decode(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid SSO token payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid SSO token structure")
    current_ts = int(now if now is not None else time.time())
    exp = int(payload.get("exp") or 0)
    if exp < current_ts or exp > current_ts + SSO_MAX_SKEW_SECONDS:
        raise ValueError("SSO token has expired")
    nonce = str(payload.get("nonce") or "").strip()
    if not nonce or len(nonce) < 8:
        raise ValueError("Missing SSO nonce")
    # Purge expired nonces
    expired_nonces = [k for k, v in _used_sso_nonces.items() if v < current_ts]
    for k in expired_nonces:
        _used_sso_nonces.pop(k, None)
    if nonce in _used_sso_nonces:
        raise ValueError("SSO token has already been used")
    address = str(payload.get("email") or "").strip().lower()
    if not EMAIL_RE.fullmatch(address):
        raise ValueError("Invalid email address in SSO token")
    gen = str(payload.get("gen") or "").strip()
    _used_sso_nonces[nonce] = exp + 60
    return address, gen


def _verify_sso_token(token: str, *, secret: str | None = None, now: int | None = None) -> str:
    """Verify SPanel's HMAC-SHA256 SSO token (payload.signature in base64url)."""
    address, _ = _verify_sso_token_with_gen(token, secret=secret, now=now)
    return address


def _session_credentials(session: dict[str, Any]) -> tuple[str, str]:
    address = str(session["email"]).strip().lower()
    mode = session.get("mode", "master")
    if mode == "password" and session.get("password_enc"):
        pw = _decrypt_secret(str(session["password_enc"]))
        if pw:
            return address, pw
    master_user = _cfg("SSO_MASTER_USER", "spanel-webmail")
    master_pass = _cfg("SSO_MASTER_PASSWORD")
    sep = _cfg("SSO_MASTER_SEPARATOR", "*") or "*"
    if master_user and master_pass:
        return f"{address}{sep}{master_user}", master_pass
    raise HTTPException(status_code=503, detail="Chưa cấu hình thông tin xác thực hộp thư.")


def _imap_authenticate(conn: imaplib.IMAP4, auth_user: str, auth_pass: str) -> None:
    """Authenticate over IMAP using UTF-8 SASL PLAIN so non-ASCII passwords work."""
    conn._encoding = "utf-8"
    if "\x00" in auth_user or "\x00" in auth_pass or "\r" in auth_user or "\n" in auth_user:
        raise imaplib.IMAP4.error("Invalid credentials")
    if hasattr(conn, "authenticate"):
        auth_bytes = f"\x00{auth_user}\x00{auth_pass}".encode("utf-8")
        conn.authenticate("PLAIN", lambda _: auth_bytes)
    else:
        conn.login(auth_user, auth_pass)


def _open_imap_with_creds(auth_user: str, auth_pass: str) -> imaplib.IMAP4:
    host = _cfg("IMAP_HOST", "127.0.0.1")
    port = _cfg_int("IMAP_PORT", 143)
    secure = _cfg_bool("IMAP_SECURE", False)
    try:
        if secure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=15)
        else:
            conn = imaplib.IMAP4(host, port, timeout=15)
        try:
            _imap_authenticate(conn, auth_user, auth_pass)
        except Exception:
            try:
                conn.logout()
            except Exception:
                pass
            raise
        return conn
    except (imaplib.IMAP4.error, UnicodeError) as exc:
        raise HTTPException(status_code=401, detail="Tài khoản email hoặc mật khẩu không chính xác (hoặc hộp thư đang tạm khóa).") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Không thể kết nối tới máy chủ IMAP ({host}:{port}).") from exc


def _open_imap(session: dict[str, Any]) -> imaplib.IMAP4:
    auth_user, auth_pass = _session_credentials(session)
    return _open_imap_with_creds(auth_user, auth_pass)


def _ensure_standard_folders(conn: imaplib.IMAP4) -> None:
    for folder in ("Drafts", "Sent", "Junk", "Trash"):
        try:
            typ, _ = conn.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                conn.create(f'"{folder}"')
                conn.subscribe(f'"{folder}"')
        except Exception:
            pass


def _decode_header_value(raw: Any) -> str:
    if not raw:
        return ""
    text = str(raw).strip()
    if "=?" not in text:
        return text
    try:
        parts = email.header.decode_header(text)
        decoded_chunks: list[str] = []
        for content, charset in parts:
            if isinstance(content, bytes):
                enc = (charset or "utf-8").strip().lower()
                if enc in ("unknown-8bit", "default", ""):
                    enc = "utf-8"
                try:
                    decoded_chunks.append(content.decode(enc, errors="replace"))
                except LookupError:
                    decoded_chunks.append(content.decode("utf-8", errors="replace"))
            else:
                decoded_chunks.append(str(content))
        return "".join(decoded_chunks).strip()
    except Exception:
        return text


def _format_address_list(raw: str | None) -> str:
    if not raw:
        return ""
    decoded = _decode_header_value(raw)
    pairs = email.utils.getaddresses([decoded])
    if not pairs:
        return decoded
    formatted = []
    for name, addr in pairs:
        clean_name = _decode_header_value(name).strip()
        clean_addr = (addr or "").strip()
        if clean_name and clean_addr and clean_name.lower() != clean_addr.lower():
            formatted.append(f"{clean_name} <{clean_addr}>")
        elif clean_addr:
            formatted.append(clean_addr)
        elif clean_name:
            formatted.append(clean_name)
    return ", ".join(formatted)


def _extract_sender_display(raw: str | None) -> tuple[str, str]:
    decoded = _decode_header_value(raw)
    pairs = email.utils.getaddresses([decoded])
    if not pairs:
        return decoded, ""
    name, addr = pairs[0]
    clean_name = _decode_header_value(name).strip()
    clean_addr = (addr or "").strip()
    return (clean_name or clean_addr), clean_addr


def _parse_imap_status(data: list[Any]) -> tuple[int, int]:
    total = 0
    unseen = 0
    for item in data or []:
        if not item:
            continue
        text = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
        m_total = re.search(r"MESSAGES\s+(\d+)", text, re.I)
        m_unseen = re.search(r"UNSEEN\s+(\d+)", text, re.I)
        if m_total:
            total = int(m_total.group(1))
        if m_unseen:
            unseen = int(m_unseen.group(1))
    return total, unseen


def _get_quota(conn: imaplib.IMAP4) -> dict[str, int]:
    used_kb = 0
    limit_kb = 0
    try:
        typ, data = conn.getquotaroot('"INBOX"')
        if typ == "OK" and data:
            for group in data:
                items = group if isinstance(group, list) else [group]
                for item in items:
                    text = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
                    m = re.search(r"STORAGE\s+(\d+)\s+(\d+)", text, re.I)
                    if m:
                        used_kb = int(m.group(1))
                        limit_kb = int(m.group(2))
    except Exception:
        pass
    return {"used_kb": used_kb, "limit_kb": limit_kb}


def _sanitize_folder(folder: str) -> str:
    clean = (folder or "INBOX").strip()
    if not FOLDER_NAME_RE.fullmatch(clean) or ".." in clean:
        raise HTTPException(status_code=400, detail="Tên thư mục không hợp lệ.")
    return clean


def _user_prefs_path(address: str) -> Path:
    digest = hashlib.sha256(address.lower().encode("utf-8")).hexdigest()
    return _prefs_dir() / f"{digest}.json"


def _load_user_prefs(address: str) -> dict[str, Any]:
    defaults = {
        "display_name": "",
        "reply_to": "",
        "signature": "",
        "language": "vi",
        "theme": "light",
    }
    path = _user_prefs_path(address)
    if not path.is_file():
        return defaults
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            defaults.update({k: v for k, v in stored.items() if k in defaults})
    except Exception:
        pass
    return defaults


def _save_user_prefs(address: str, updates: dict[str, Any]) -> dict[str, Any]:
    prefs = _load_user_prefs(address)
    if "display_name" in updates:
        clean_dn = re.sub(r"[\r\n\x00]+", " ", str(updates["display_name"] or "")).strip()[:120]
        prefs["display_name"] = clean_dn
    if "reply_to" in updates:
        rt = re.sub(r"[\r\n\x00]+", "", str(updates["reply_to"] or "")).strip()
        if rt and not EMAIL_RE.fullmatch(rt):
            raise HTTPException(status_code=400, detail="Địa chỉ Reply-To không hợp lệ.")
        prefs["reply_to"] = rt
    if "signature" in updates:
        prefs["signature"] = str(updates["signature"] or "")[:4000]
    if updates.get("language") in ("vi", "en"):
        prefs["language"] = updates["language"]
    if updates.get("theme") in ("light", "dark"):
        prefs["theme"] = updates["theme"]
    path = _user_prefs_path(address)
    path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    return prefs


def _sanitize_html_email(raw_html: str) -> str:
    """Remove active scripts/event handlers while preserving email layout/styles."""
    cleaned = re.sub(r"<\s*(script|iframe|object|embed|applet|meta|base)\b[^>]*>.*?<\s*/\s*\1\s*>", "", raw_html, flags=re.I | re.S)
    cleaned = re.sub(r"<\s*(script|iframe|object|embed|applet|meta|base)\b[^>]*/?\s*>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bon[a-z0-9_]+\s*=\s*(['\"]).*?\1", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"\bon[a-z0-9_]+\s*=\s*[^\s>]+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"javascript\s*:", "blocked:", cleaned, flags=re.I)
    return cleaned


def _parse_message_detail(raw_bytes: bytes, uid: str, flags: list[str], folder: str) -> dict[str, Any]:
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    subject = _decode_header_value(msg.get("Subject")) or "(Không có tiêu đề)"
    from_name, from_addr = _extract_sender_display(msg.get("From"))
    from_full = _format_address_list(msg.get("From"))
    to_full = _format_address_list(msg.get("To"))
    cc_full = _format_address_list(msg.get("Cc"))
    reply_to = _format_address_list(msg.get("Reply-To"))
    date_raw = _decode_header_value(msg.get("Date"))
    message_id = _decode_header_value(msg.get("Message-ID"))
    in_reply_to = _decode_header_value(msg.get("In-Reply-To"))
    references = _decode_header_value(msg.get("References"))

    timestamp = 0
    if date_raw:
        try:
            dt = email.utils.parsedate_to_datetime(date_raw)
            timestamp = int(dt.timestamp())
        except Exception:
            pass

    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    cid_map: dict[str, str] = {}

    part_index = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        part_index += 1
        ctype = (part.get_content_type() or "application/octet-stream").lower()
        disp = str(part.get_content_disposition() or "").lower()
        filename = _decode_header_value(part.get_filename())
        cid = str(part.get("Content-ID") or "").strip().strip("<>")
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            payload = b""

        if cid and ctype.startswith("image/") and len(payload) <= 2 * 1024 * 1024:
            b64_img = base64.b64encode(payload).decode("ascii")
            cid_map[cid] = f"data:{ctype};base64,{b64_img}"

        if disp == "attachment" or (filename and ctype not in ("text/plain", "text/html")):
            attachments.append({
                "part_id": part_index,
                "filename": filename or f"attachment-{part_index}",
                "content_type": ctype,
                "size": len(payload),
            })
            continue

        charset = part.get_content_charset() or "utf-8"
        try:
            text_val = payload.decode(charset, errors="replace")
        except LookupError:
            text_val = payload.decode("utf-8", errors="replace")

        if ctype == "text/html":
            html_parts.append(text_val)
        elif ctype == "text/plain":
            text_parts.append(text_val)
        elif filename:
            attachments.append({
                "part_id": part_index,
                "filename": filename,
                "content_type": ctype,
                "size": len(payload),
            })

    body_text = "\n\n".join(text_parts).strip()
    body_html = "\n".join(html_parts).strip()
    if body_html:
        for cid_key, data_uri in cid_map.items():
            body_html = body_html.replace(f"cid:{cid_key}", data_uri)
        body_html = _sanitize_html_email(body_html)
    elif body_text:
        escaped = html.escape(body_text)
        escaped = re.sub(
            r"(https?://[^\s<\"']+)",
            r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
            escaped,
        )
        body_html = f'<div style="white-space:pre-wrap;font-family:system-ui,-apple-system,sans-serif;line-height:1.6">{escaped}</div>'

    return {
        "uid": str(uid),
        "folder": folder,
        "subject": subject,
        "from": from_full,
        "from_name": from_name,
        "from_address": from_addr,
        "to": to_full,
        "cc": cc_full,
        "reply_to": reply_to,
        "date": date_raw,
        "timestamp": timestamp,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "seen": "\\Seen" in flags,
        "flagged": "\\Flagged" in flags,
        "answered": "\\Answered" in flags,
        "draft": "\\Draft" in flags,
        "size": len(raw_bytes),
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
    }


def _smtp_authenticate(smtp: smtplib.SMTP, auth_user: str, auth_pass: str) -> None:
    """Authenticate over SMTP using SASL PLAIN with authzid=authcid=auth_user.

    Exim's dovecot_plain authenticator uses `server_set_id = ${sg{$auth1}{\\N\\*.*$\\N}{}}`
    where $auth1 is the first field (authzid) in SASL PLAIN (`authzid\\0authcid\\0passwd`).
    Setting both authzid and authcid to auth_user guarantees $auth1 carries the
    mailbox address for Exim's senders check and DKIM signing.
    """
    plain_raw = f"{auth_user}\0{auth_user}\0{auth_pass}".encode("utf-8")
    plain_b64 = base64.b64encode(plain_raw).decode("ascii")
    code, resp = smtp.docmd("AUTH", f"PLAIN {plain_b64}")
    if code in (235, 503):
        return
    # Fallback to standard smtplib login if needed
    smtp.login(auth_user, auth_pass)


app = FastAPI(title="SPanel Webmail", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    """Healthcheck & current session status.
    Returns HTTP 200 even when unauthenticated so `curl -fsS http://127.0.0.1:8096/api/auth/me`
    in spanel-helper's install_mail check succeeds.
    """
    sess = _get_session(request)
    if not sess:
        return {"authenticated": False, "email": None}
    address = sess["email"]
    return {
        "authenticated": True,
        "email": address,
        "settings": _load_user_prefs(address),
    }


@app.get("/api/auth/sso")
def api_auth_sso(request: Request, token: str = Query(default="")):
    """Single sign-on from SPanel (`GET /api/auth/sso?token=<body.signature>`)."""
    try:
        address, token_gen = _verify_sso_token_with_gen(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"Liên kết đăng nhập nhanh không hợp lệ hoặc đã hết hạn: {exc}") from exc

    master_user = _cfg("SSO_MASTER_USER", "spanel-webmail")
    master_pass = _cfg("SSO_MASTER_PASSWORD")
    sep = _cfg("SSO_MASTER_SEPARATOR", "*") or "*"
    if not master_user or not master_pass:
        raise HTTPException(status_code=503, detail="Máy chủ Webmail chưa cấu hình khóa SSO Master.")

    # Verify mailbox is active in Dovecot (also enforces /etc/dovecot/spanel/denied)
    conn = _open_imap_with_creds(f"{address}{sep}{master_user}", master_pass)
    try:
        _ensure_standard_folders(conn)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    cookie_val = _create_session(address, mode="master", gen=token_gen)
    is_https = (request.headers.get("x-forwarded-proto") or request.url.scheme).lower() == "https"
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_val,
        max_age=MASTER_SESSION_TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=is_https,
    )
    return response


@app.post("/api/auth/login")
async def api_auth_login(request: Request, response: Response):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Dữ liệu đăng nhập không hợp lệ.") from exc

    address = str((body or {}).get("email") or "").strip().lower()
    password = str((body or {}).get("password") or "")
    if not EMAIL_RE.fullmatch(address) or not password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập đầy đủ địa chỉ email và mật khẩu.")

    client_ip = _client_ip(request)
    _check_login_rate_limit(client_ip, address)

    try:
        conn = _open_imap_with_creds(address, password)
    except HTTPException as exc:
        if exc.status_code == 401:
            _record_login_failure(client_ip, address)
        raise

    _clear_login_failures(client_ip, address)
    try:
        _ensure_standard_folders(conn)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    cookie_val = _create_session(address, mode="password", password=password)
    is_https = (request.headers.get("x-forwarded-proto") or request.url.scheme).lower() == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_val,
        max_age=SESSION_TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=is_https,
    )
    return {
        "authenticated": True,
        "email": address,
        "settings": _load_user_prefs(address),
    }


@app.post("/api/auth/logout")
def api_auth_logout(request: Request, response: Response):
    _verify_csrf_request(request)
    sess = _get_session(request)
    if sess and sess.get("session_id"):
        (_sessions_dir() / f"{sess['session_id']}.json").unlink(missing_ok=True)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/settings")
def api_get_settings(request: Request):
    sess = _require_session(request)
    return _load_user_prefs(sess["email"])


@app.put("/api/settings")
async def api_put_settings(request: Request):
    sess = _require_session(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Dữ liệu cài đặt không hợp lệ.") from exc
    return _save_user_prefs(sess["email"], body if isinstance(body, dict) else {})


@app.get("/api/folders")
def api_list_folders(request: Request):
    sess = _require_session(request)
    conn = _open_imap(sess)
    try:
        _ensure_standard_folders(conn)
        folders = []
        for folder_id, label_vi, label_en in STANDARD_FOLDERS:
            if folder_id == "Starred":
                # Virtual folder backed by FLAGGED search in INBOX
                total = 0
                try:
                    typ, _ = conn.select('"INBOX"', readonly=True)
                    if typ == "OK":
                        st, sdata = conn.uid("SEARCH", None, "FLAGGED")
                        if st == "OK" and sdata and sdata[0]:
                            total = len(sdata[0].split())
                except Exception:
                    pass
                folders.append({
                    "id": "Starred",
                    "label_vi": label_vi,
                    "label_en": label_en,
                    "total": total,
                    "unseen": 0,
                })
                continue

            total, unseen = 0, 0
            try:
                typ, data = conn.status(f'"{folder_id}"', "(MESSAGES UNSEEN)")
                if typ == "OK":
                    total, unseen = _parse_imap_status(data)
            except Exception:
                pass
            folders.append({
                "id": folder_id,
                "label_vi": label_vi,
                "label_en": label_en,
                "total": total,
                "unseen": unseen,
            })

        quota = _get_quota(conn)
        return {"folders": folders, "quota": quota}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _parse_fetch_Block(meta_line: bytes, header_bytes: bytes) -> dict[str, Any]:
    meta_str = meta_line.decode("utf-8", errors="replace")
    m_uid = re.search(r"UID\s+(\d+)", meta_str, re.I)
    uid = m_uid.group(1) if m_uid else ""
    m_size = re.search(r"RFC822\.SIZE\s+(\d+)", meta_str, re.I)
    size = int(m_size.group(1)) if m_size else 0
    m_flags = re.search(r"FLAGS\s*\(([^)]*)\)", meta_str, re.I)
    flags = m_flags.group(1).split() if m_flags else []

    msg = email.message_from_bytes(header_bytes, policy=email.policy.default)
    subject = _decode_header_value(msg.get("Subject")) or "(Không có tiêu đề)"
    from_name, from_addr = _extract_sender_display(msg.get("From"))
    to_full = _format_address_list(msg.get("To"))
    date_raw = _decode_header_value(msg.get("Date"))
    ctype = (msg.get_content_type() or "").lower()
    has_attachment = "multipart/mixed" in ctype

    timestamp = 0
    if date_raw:
        try:
            dt = email.utils.parsedate_to_datetime(date_raw)
            timestamp = int(dt.timestamp())
        except Exception:
            pass

    return {
        "uid": uid,
        "subject": subject,
        "from_name": from_name,
        "from_address": from_addr,
        "from": _format_address_list(msg.get("From")),
        "to": to_full,
        "date": date_raw,
        "timestamp": timestamp,
        "size": size,
        "seen": "\\Seen" in flags,
        "flagged": "\\Flagged" in flags,
        "answered": "\\Answered" in flags,
        "draft": "\\Draft" in flags,
        "has_attachment": has_attachment,
    }


@app.get("/api/messages")
def api_list_messages(
    request: Request,
    folder: str = Query(default="INBOX"),
    page: int = Query(default=1, ge=1, le=10000),
    per_page: int = Query(default=40, ge=1, le=100),
    q: str = Query(default=""),
    filter_unseen: bool = Query(default=False),
):
    sess = _require_session(request)
    clean_folder = _sanitize_folder(folder)
    actual_folder = "INBOX" if clean_folder == "Starred" else clean_folder

    conn = _open_imap(sess)
    try:
        _ensure_standard_folders(conn)
        typ, _ = conn.select(f'"{actual_folder}"', readonly=True)
        if typ != "OK":
            return {"items": [], "total": 0, "page": page, "per_page": per_page, "folder": clean_folder}

        criteria = "FLAGGED" if clean_folder == "Starred" else ("UNSEEN" if filter_unseen else "ALL")
        typ, data = conn.uid("SEARCH", None, criteria)
        if typ != "OK" or not data or not data[0]:
            return {"items": [], "total": 0, "page": page, "per_page": per_page, "folder": clean_folder}

        all_uids = [u.decode("ascii") for u in data[0].split() if u]
        # Newest UIDs first
        all_uids.reverse()

        search_term = (q or "").strip().lower()
        if not search_term:
            total = len(all_uids)
            start = (page - 1) * per_page
            page_uids = all_uids[start : start + per_page]
        else:
            # Scan latest 250 messages when searching so UTF-8/Vietnamese header search works reliably
            page_uids = all_uids[:250]
            total = len(page_uids)

        if not page_uids:
            return {"items": [], "total": total, "page": page, "per_page": per_page, "folder": clean_folder}

        uid_set = ",".join(page_uids)
        ftyp, fdata = conn.uid(
            "FETCH",
            uid_set,
            "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE MESSAGE-ID CONTENT-TYPE)])",
        )
        if ftyp != "OK" or not fdata:
            return {"items": [], "total": 0, "page": page, "per_page": per_page, "folder": clean_folder}

        by_uid: dict[str, dict[str, Any]] = {}
        for item in fdata:
            if isinstance(item, tuple) and len(item) >= 2:
                parsed = _parse_fetch_Block(item[0], item[1])
                if parsed["uid"]:
                    by_uid[parsed["uid"]] = parsed

        ordered = [by_uid[u] for u in page_uids if u in by_uid]
        if search_term:
            ordered = [
                row
                for row in ordered
                if search_term
                in f"{row['subject']} {row['from']} {row['to']} {row['from_address']}".lower()
            ]
            total = len(ordered)
            start = (page - 1) * per_page
            ordered = ordered[start : start + per_page]

        return {
            "items": ordered,
            "total": total,
            "page": page,
            "per_page": per_page,
            "folder": clean_folder,
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _fetch_raw_message(conn: imaplib.IMAP4, folder: str, uid: str, *, mark_seen: bool = False) -> tuple[bytes, list[str]]:
    if not UID_RE.fullmatch(str(uid or "")):
        raise HTTPException(status_code=400, detail="UID thư không hợp lệ.")
    actual_folder = "INBOX" if folder == "Starred" else _sanitize_folder(folder)
    typ, _ = conn.select(f'"{actual_folder}"', readonly=not mark_seen)
    if typ != "OK":
        raise HTTPException(status_code=404, detail="Không tìm thấy thư mục.")
    ftyp, fdata = conn.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
    if ftyp != "OK" or not fdata:
        raise HTTPException(status_code=404, detail="Không tìm thấy email.")

    raw_bytes = b""
    flags: list[str] = []
    for item in fdata:
        if isinstance(item, tuple) and len(item) >= 2:
            meta_str = item[0].decode("utf-8", errors="replace")
            m_flags = re.search(r"FLAGS\s*\(([^)]*)\)", meta_str, re.I)
            if m_flags:
                flags = m_flags.group(1).split()
            raw_bytes = item[1]
            break

    if not raw_bytes:
        raise HTTPException(status_code=404, detail="Nội dung email không tồn tại.")

    if mark_seen and "\\Seen" not in flags:
        try:
            conn.uid("STORE", uid, "+FLAGS.SILENT", "(\\Seen)")
            flags.append("\\Seen")
        except Exception:
            pass

    return raw_bytes, flags


@app.get("/api/messages/{uid}")
def api_get_message(
    request: Request,
    uid: str,
    folder: str = Query(default="INBOX"),
):
    sess = _require_session(request)
    clean_folder = _sanitize_folder(folder)
    conn = _open_imap(sess)
    try:
        raw_bytes, flags = _fetch_raw_message(conn, clean_folder, uid, mark_seen=True)
        return _parse_message_detail(raw_bytes, uid, flags, clean_folder)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


@app.get("/api/messages/{uid}/raw")
def api_get_message_raw(
    request: Request,
    uid: str,
    folder: str = Query(default="INBOX"),
):
    sess = _require_session(request)
    clean_folder = _sanitize_folder(folder)
    conn = _open_imap(sess)
    try:
        raw_bytes, _ = _fetch_raw_message(conn, clean_folder, uid, mark_seen=False)
        return Response(
            content=raw_bytes,
            media_type="message/rfc822",
            headers={"Content-Disposition": f'attachment; filename="message-{uid}.eml"'},
        )
    finally:
        try:
            conn.logout()
        except Exception:
            pass


@app.get("/api/messages/{uid}/attachments/{part_id}")
def api_download_attachment(
    request: Request,
    uid: str,
    part_id: int,
    folder: str = Query(default="INBOX"),
):
    sess = _require_session(request)
    clean_folder = _sanitize_folder(folder)
    conn = _open_imap(sess)
    try:
        raw_bytes, _ = _fetch_raw_message(conn, clean_folder, uid, mark_seen=False)
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
        idx = 0
        for part in msg.walk():
            if part.is_multipart():
                continue
            idx += 1
            if idx == part_id:
                filename = _decode_header_value(part.get_filename()) or f"attachment-{part_id}"
                ctype = (part.get_content_type() or "application/octet-stream").lower()
                payload = part.get_payload(decode=True) or b""
                safe_ascii = re.sub(r'[^A-Za-z0-9._-]', '_', filename)
                encoded_utf8 = quote(filename)
                return Response(
                    content=payload,
                    media_type=ctype,
                    headers={
                        "Content-Disposition": f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded_utf8}"
                    },
                )
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


async def _build_email_from_request(request: Request, sender_address: str) -> tuple[EmailMessage, list[str], str | None]:
    prefs = _load_user_prefs(sender_address)
    display_name = re.sub(r"[\r\n\x00]+", " ", str(prefs.get("display_name") or "")).strip()
    reply_to_pref = re.sub(r"[\r\n\x00]+", "", str(prefs.get("reply_to") or "")).strip()

    ctype = (request.headers.get("content-type") or "").lower()
    uploads: list[tuple[str, str, bytes]] = []
    if "multipart/form-data" in ctype:
        form = await request.form()
        to_raw = re.sub(r"[\r\n\x00]+", " ", str(form.get("to") or "")).strip()
        cc_raw = re.sub(r"[\r\n\x00]+", " ", str(form.get("cc") or "")).strip()
        bcc_raw = re.sub(r"[\r\n\x00]+", " ", str(form.get("bcc") or "")).strip()
        subject = re.sub(r"[\r\n\x00]+", " ", str(form.get("subject") or "")).strip()
        body_text = str(form.get("body_text") or "")
        body_html = str(form.get("body_html") or "")
        in_reply_to = re.sub(r"[\r\n\x00]+", " ", str(form.get("in_reply_to") or "")).strip()
        references = re.sub(r"[\r\n\x00]+", " ", str(form.get("references") or "")).strip()
        draft_uid = str(form.get("draft_uid") or "").strip() or None
        for file_item in form.getlist("attachments"):
            if hasattr(file_item, "filename") and getattr(file_item, "filename", ""):
                data = await file_item.read()
                if data:
                    uploads.append((
                        str(file_item.filename),
                        str(getattr(file_item, "content_type", "") or "application/octet-stream"),
                        data,
                    ))
    else:
        try:
            data_json = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Dữ liệu gửi thư không hợp lệ.") from exc
        body = data_json if isinstance(data_json, dict) else {}
        to_raw = re.sub(r"[\r\n\x00]+", " ", str(body.get("to") or "")).strip()
        cc_raw = re.sub(r"[\r\n\x00]+", " ", str(body.get("cc") or "")).strip()
        bcc_raw = re.sub(r"[\r\n\x00]+", " ", str(body.get("bcc") or "")).strip()
        subject = re.sub(r"[\r\n\x00]+", " ", str(body.get("subject") or "")).strip()
        body_text = str(body.get("body_text") or "")
        body_html = str(body.get("body_html") or "")
        in_reply_to = re.sub(r"[\r\n\x00]+", " ", str(body.get("in_reply_to") or "")).strip()
        references = re.sub(r"[\r\n\x00]+", " ", str(body.get("references") or "")).strip()
        draft_uid = str(body.get("draft_uid") or "").strip() or None

    msg = EmailMessage()
    from_header = email.utils.formataddr((display_name, sender_address)) if display_name else sender_address
    msg["From"] = from_header
    if to_raw:
        msg["To"] = to_raw
    if cc_raw:
        msg["Cc"] = cc_raw
    if reply_to_pref:
        msg["Reply-To"] = reply_to_pref
    msg["Subject"] = subject or "(Không có tiêu đề)"
    msg["Date"] = email.utils.formatdate(localtime=True)
    domain_part = sender_address.partition("@")[2] or "localhost"
    msg["Message-ID"] = email.utils.make_msgid(domain=domain_part)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    plain_content = body_text or ""
    msg.set_content(plain_content)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    for fname, ftype, fbytes in uploads:
        guessed_type, _ = mimetypes.guess_type(fname)
        final_type = ftype if "/" in ftype and ftype != "application/octet-stream" else (guessed_type or "application/octet-stream")
        maintype, _, subtype = final_type.partition("/")
        msg.add_attachment(fbytes, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=fname)

    all_rcpts: list[str] = []
    for _, addr in email.utils.getaddresses([to_raw, cc_raw, bcc_raw]):
        clean_addr = (addr or "").strip()
        if clean_addr:
            if not EMAIL_RE.fullmatch(clean_addr):
                raise HTTPException(status_code=400, detail=f"Địa chỉ người nhận không hợp lệ: {clean_addr}")
            if clean_addr not in all_rcpts:
                all_rcpts.append(clean_addr)

    return msg, all_rcpts, draft_uid


@app.post("/api/messages/send")
async def api_send_message(request: Request):
    sess = _require_session(request)
    sender_address = sess["email"]
    auth_user, auth_pass = _session_credentials(sess)
    msg, recipients, draft_uid = await _build_email_from_request(request, sender_address)
    if not recipients:
        raise HTTPException(status_code=400, detail="Vui lòng nhập ít nhất một địa chỉ người nhận (To/Cc/Bcc).")

    smtp_host = _cfg("SMTP_HOST", "127.0.0.1")
    smtp_port = _cfg_int("SMTP_PORT", 587)
    smtp_secure = _cfg_bool("SMTP_SECURE", False)

    raw_bytes = msg.as_bytes()
    try:
        if smtp_secure and smtp_port == 465:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=30) as smtp:
                smtp.ehlo()
                _smtp_authenticate(smtp, auth_user, auth_pass)
                smtp.sendmail(sender_address, recipients, raw_bytes)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if smtp_secure and smtp.has_extn("starttls"):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    smtp.starttls(context=ctx)
                    smtp.ehlo()
                _smtp_authenticate(smtp, auth_user, auth_pass)
                smtp.sendmail(sender_address, recipients, raw_bytes)
    except smtplib.SMTPRecipientsRefused as exc:
        raise HTTPException(status_code=400, detail=f"Máy chủ từ chối địa chỉ người nhận: {exc}") from exc
    except smtplib.SMTPResponseException as exc:
        err_msg = exc.smtp_error.decode("utf-8", errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        raise HTTPException(status_code=400, detail=f"Không thể gửi email ({exc.smtp_code}): {err_msg}") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Không thể kết nối tới máy chủ SMTP ({smtp_host}:{smtp_port}).") from exc

    # Append sent copy to Sent folder and remove draft if applicable
    try:
        conn = _open_imap(sess)
        try:
            _ensure_standard_folders(conn)
            conn.append('"Sent"', "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw_bytes)
            if draft_uid and UID_RE.fullmatch(draft_uid):
                typ, _ = conn.select('"Drafts"', readonly=False)
                if typ == "OK":
                    conn.uid("STORE", draft_uid, "+FLAGS.SILENT", "(\\Deleted)")
                    conn.expunge()
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception:
        pass

    return {"ok": True, "message_id": str(msg.get("Message-ID") or "")}


@app.post("/api/messages/draft")
async def api_save_draft(request: Request):
    sess = _require_session(request)
    sender_address = sess["email"]
    msg, _, draft_uid = await _build_email_from_request(request, sender_address)
    raw_bytes = msg.as_bytes()

    conn = _open_imap(sess)
    try:
        _ensure_standard_folders(conn)
        atyp, _ = conn.append('"Drafts"', "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()), raw_bytes)
        if atyp != "OK":
            raise HTTPException(status_code=500, detail="Không thể lưu thư nháp (hộp thư có thể đã đầy).")
        if draft_uid and UID_RE.fullmatch(draft_uid):
            typ, _ = conn.select('"Drafts"', readonly=False)
            if typ == "OK":
                conn.uid("STORE", draft_uid, "+FLAGS.SILENT", "(\\Deleted)")
                conn.expunge()
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/messages/action")
async def api_messages_action(request: Request):
    sess = _require_session(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Yêu cầu thao tác không hợp lệ.") from exc

    data = body if isinstance(body, dict) else {}
    action = str(data.get("action") or "").strip()
    folder = _sanitize_folder(str(data.get("folder") or "INBOX"))
    actual_folder = "INBOX" if folder == "Starred" else folder
    uids_raw = data.get("uids") or []
    if isinstance(uids_raw, (str, int)):
        uids_raw = [str(uids_raw)]
    uids = [str(u).strip() for u in uids_raw if UID_RE.fullmatch(str(u).strip())]

    conn = _open_imap(sess)
    try:
        _ensure_standard_folders(conn)
        if action in ("empty_trash", "empty_junk"):
            target_box = "Trash" if action == "empty_trash" else "Junk"
            typ, _ = conn.select(f'"{target_box}"', readonly=False)
            if typ == "OK":
                styp, sdata = conn.uid("SEARCH", None, "ALL")
                if styp == "OK" and sdata and sdata[0]:
                    all_set = b",".join(sdata[0].split()).decode("ascii")
                    conn.uid("STORE", all_set, "+FLAGS.SILENT", "(\\Deleted)")
                    conn.expunge()
            return {"ok": True}

        if not uids:
            raise HTTPException(status_code=400, detail="Chưa chọn email để thao tác.")

        typ, _ = conn.select(f'"{actual_folder}"', readonly=False)
        if typ != "OK":
            raise HTTPException(status_code=404, detail="Không mở được thư mục.")

        uid_set = ",".join(uids)
        if action == "mark_read":
            conn.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Seen)")
        elif action == "mark_unread":
            conn.uid("STORE", uid_set, "-FLAGS.SILENT", "(\\Seen)")
        elif action == "star":
            conn.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Flagged)")
        elif action == "unstar":
            conn.uid("STORE", uid_set, "-FLAGS.SILENT", "(\\Flagged)")
        elif action == "move":
            target = _sanitize_folder(str(data.get("target_folder") or "INBOX"))
            if target != actual_folder:
                ctyp, _ = conn.uid("COPY", uid_set, f'"{target}"')
                if ctyp != "OK":
                    raise HTTPException(status_code=400, detail=f"Không thể di chuyển thư tới {target}.")
                conn.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
                conn.expunge()
        elif action == "delete":
            permanent = bool(data.get("permanent")) or actual_folder == "Trash"
            if not permanent:
                ctyp, _ = conn.uid("COPY", uid_set, '"Trash"')
                if ctyp != "OK":
                    raise HTTPException(status_code=400, detail="Không thể chuyển thư vào Thùng rác.")
            conn.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
            conn.expunge()
        else:
            raise HTTPException(status_code=400, detail=f"Thao tác không được hỗ trợ: {action}")

        return {"ok": True, "count": len(uids)}
    finally:
        try:
            conn.logout()
        except Exception:
            pass


WEBMAIL_HTML = r"""<!DOCTYPE html>
<html lang="vi" data-theme="light">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SPanel Webmail</title>
  <style>
    :root, [data-theme="light"] {
      --bg: #f4f6fb;
      --surface: #ffffff;
      --surface-2: #f8fafc;
      --surface-hover: #f1f5f9;
      --surface-active: #eff6ff;
      --border: #e2e8f0;
      --text: #0f172a;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-soft: rgba(37, 99, 235, 0.1);
      --danger: #dc2626;
      --danger-soft: rgba(220, 38, 38, 0.1);
      --warning: #d97706;
      --success: #16a34a;
      --shadow: 0 4px 20px rgba(15, 23, 42, 0.06);
    }
    [data-theme="dark"] {
      --bg: #0b1120;
      --surface: #111827;
      --surface-2: #1e293b;
      --surface-hover: #1f2937;
      --surface-active: rgba(59, 130, 246, 0.16);
      --border: #1e293b;
      --text: #f8fafc;
      --text-secondary: #cbd5e1;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --primary-soft: rgba(59, 130, 246, 0.18);
      --danger: #ef4444;
      --danger-soft: rgba(239, 68, 68, 0.18);
      --warning: #f59e0b;
      --success: #22c55e;
      --shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      overflow: hidden;
      font-size: 14px;
    }
    button, input, textarea, select { font: inherit; color: inherit; }
    .hidden { display: none !important; }

    /* Login View */
    .login-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      padding: 20px;
      background: radial-gradient(circle at top right, rgba(37, 99, 235, 0.12), transparent 55%), var(--bg);
    }
    .login-card {
      width: 100%;
      max-width: 410px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 32px;
      box-shadow: var(--shadow);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
    }
    .brand-logo {
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: linear-gradient(135deg, #2563eb, #0ea5e9);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 18px;
    }
    .brand-title { font-size: 18px; font-weight: 700; }
    .brand-sub { font-size: 12.5px; color: var(--text-muted); }
    .field { margin-bottom: 16px; }
    .field label { display: block; font-size: 12.5px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; }
    .input {
      width: 100%;
      padding: 10px 12px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 9px;
      outline: none;
      transition: border-color .15s;
    }
    .input:focus { border-color: var(--primary); }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      padding: 9px 15px;
      border-radius: 9px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      font-weight: 600;
      font-size: 13px;
      cursor: pointer;
      transition: all .15s;
      white-space: nowrap;
    }
    .btn:hover { background: var(--surface-hover); }
    .btn-primary {
      background: var(--primary);
      border-color: var(--primary);
      color: #fff;
    }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-danger {
      color: var(--danger);
      border-color: var(--danger-soft);
      background: var(--danger-soft);
    }
    .btn-sm { padding: 6px 10px; font-size: 12.5px; border-radius: 7px; }
    .alert-error {
      padding: 10px 12px;
      border-radius: 8px;
      background: var(--danger-soft);
      color: var(--danger);
      font-size: 13px;
      margin-bottom: 14px;
    }

    /* Main App Layout */
    .app-shell {
      display: grid;
      grid-template-rows: 58px 1fr;
      height: 100vh;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 18px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      gap: 16px;
    }
    .search-box {
      flex: 1;
      max-width: 520px;
      display: flex;
      gap: 8px;
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .user-pill {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-secondary);
      padding: 6px 12px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 999px;
    }
    .workspace {
      display: grid;
      grid-template-columns: 240px 380px 1fr;
      height: calc(100vh - 58px);
      overflow: hidden;
    }
    @media (max-width: 1100px) {
      .workspace { grid-template-columns: 200px 320px 1fr; }
    }

    /* Sidebar */
    .sidebar {
      background: var(--surface);
      border-right: 1px solid var(--border);
      padding: 16px 12px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      overflow-y: auto;
    }
    .compose-btn {
      width: 100%;
      padding: 11px 16px;
      font-size: 14px;
      margin-bottom: 16px;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.22);
    }
    .folder-list { list-style: none; display: flex; flex-direction: column; gap: 4px; }
    .folder-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 9px 12px;
      border-radius: 9px;
      cursor: pointer;
      color: var(--text-secondary);
      font-weight: 500;
      transition: background .12s;
    }
    .folder-item:hover { background: var(--surface-hover); color: var(--text); }
    .folder-item.active {
      background: var(--surface-active);
      color: var(--primary);
      font-weight: 600;
    }
    .badge {
      font-size: 11.5px;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 999px;
      background: var(--primary-soft);
      color: var(--primary);
    }
    .quota-box {
      padding: 12px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 12px;
      color: var(--text-muted);
    }
    .quota-bar {
      height: 6px;
      background: var(--border);
      border-radius: 999px;
      overflow: hidden;
      margin: 7px 0;
    }
    .quota-fill {
      height: 100%;
      background: var(--primary);
      width: 0%;
      transition: width .25s;
    }

    /* Message List Column */
    .list-pane {
      background: var(--surface);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .list-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      gap: 8px;
      background: var(--surface-2);
    }
    .msg-list {
      flex: 1;
      overflow-y: auto;
    }
    .msg-row {
      display: grid;
      grid-template-columns: 26px 1fr auto;
      gap: 10px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
      transition: background .12s;
      align-items: start;
    }
    .msg-row:hover { background: var(--surface-hover); }
    .msg-row.active { background: var(--surface-active); }
    .msg-row.unread .msg-from, .msg-row.unread .msg-subj {
      font-weight: 700;
      color: var(--text);
    }
    .msg-from {
      font-size: 13.5px;
      color: var(--text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .msg-subj {
      font-size: 13px;
      color: var(--text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-top: 3px;
    }
    .msg-meta {
      text-align: right;
      font-size: 11.5px;
      color: var(--text-muted);
      white-space: nowrap;
    }
    .star-btn {
      background: none;
      border: none;
      cursor: pointer;
      font-size: 15px;
      color: var(--text-muted);
      padding: 0 3px;
    }
    .star-btn.starred { color: #f59e0b; }
    .list-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 14px;
      border-top: 1px solid var(--border);
      font-size: 12px;
      color: var(--text-muted);
      background: var(--surface-2);
    }

    /* Reading Pane */
    .read-pane {
      background: var(--bg);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .read-empty {
      margin: auto;
      text-align: center;
      color: var(--text-muted);
      padding: 32px;
    }
    .read-header {
      background: var(--surface);
      padding: 18px 24px;
      border-bottom: 1px solid var(--border);
    }
    .read-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }
    .read-subject {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 10px;
      line-height: 1.35;
    }
    .read-meta {
      font-size: 12.5px;
      color: var(--text-secondary);
      line-height: 1.6;
    }
    .attach-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px dashed var(--border);
    }
    .attach-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 7px;
      font-size: 12px;
      text-decoration: none;
      color: var(--primary);
      font-weight: 600;
    }
    .read-body {
      flex: 1;
      background: var(--surface);
      margin: 14px;
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
    }
    .read-iframe {
      width: 100%;
      height: 100%;
      border: none;
      background: #fff;
    }

    /* Modals */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.55);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      z-index: 100;
    }
    .modal {
      width: 100%;
      max-width: 680px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: var(--shadow);
      display: flex;
      flex-direction: column;
      max-height: 90vh;
      overflow: hidden;
    }
    .modal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 20px;
      border-bottom: 1px solid var(--border);
      font-weight: 700;
      font-size: 15px;
    }
    .modal-body {
      padding: 18px 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .modal-foot {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 20px;
      border-top: 1px solid var(--border);
      background: var(--surface-2);
    }
    .toast {
      position: fixed;
      bottom: 20px;
      right: 20px;
      padding: 11px 16px;
      border-radius: 10px;
      background: #0f172a;
      color: #fff;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 8px 24px rgba(0,0,0,.25);
      z-index: 200;
    }
  </style>
</head>
<body>
  <!-- Login Screen -->
  <div id="loginView" class="login-wrap hidden">
    <div class="login-card">
      <div class="brand">
        <div class="brand-logo">S</div>
        <div>
          <div class="brand-title">SPanel Webmail</div>
          <div class="brand-sub" data-i18n="login_sub">Đăng nhập hộp thư doanh nghiệp</div>
        </div>
      </div>
      <div id="loginError" class="alert-error hidden"></div>
      <form id="loginForm">
        <div class="field">
          <label data-i18n="email_label">Địa chỉ Email</label>
          <input id="loginEmail" type="email" class="input" placeholder="you@domain.com" required autocomplete="username" />
        </div>
        <div class="field">
          <label data-i18n="password_label">Mật khẩu</label>
          <input id="loginPassword" type="password" class="input" placeholder="••••••••" required autocomplete="current-password" />
        </div>
        <button id="loginSubmitBtn" type="submit" class="btn btn-primary" style="width:100%;padding:11px">
          <span data-i18n="sign_in">Đăng nhập</span>
        </button>
      </form>
    </div>
  </div>

  <!-- Main Webmail Shell -->
  <div id="appView" class="app-shell hidden">
    <header class="topbar">
      <div class="brand" style="margin-bottom:0">
        <div class="brand-logo" style="width:34px;height:34px;font-size:15px">S</div>
        <div>
          <div class="brand-title" style="font-size:15.5px">SPanel Webmail</div>
        </div>
      </div>
      <form id="searchForm" class="search-box">
        <input id="searchInput" type="search" class="input" placeholder="Tìm kiếm tiêu đề, người gửi, người nhận..." />
        <button type="submit" class="btn" data-i18n="search_btn">Tìm</button>
      </form>
      <div class="topbar-right">
        <span id="userEmailBadge" class="user-pill"></span>
        <button id="langBtn" type="button" class="btn btn-sm">VI</button>
        <button id="themeBtn" type="button" class="btn btn-sm" title="Giao diện Sáng / Tối">☀️</button>
        <button id="settingsBtn" type="button" class="btn btn-sm" data-i18n="settings_btn">Cài đặt</button>
        <button id="logoutBtn" type="button" class="btn btn-sm btn-danger" data-i18n="logout_btn">Đăng xuất</button>
      </div>
    </header>

    <div class="workspace">
      <!-- Left Folder Sidebar -->
      <aside class="sidebar">
        <div>
          <button id="composeBtn" type="button" class="btn btn-primary compose-btn">
            ✉️ <span data-i18n="compose_btn">Soạn thư mới</span>
          </button>
          <ul id="folderList" class="folder-list"></ul>
        </div>
        <div class="quota-box">
          <div style="display:flex;justify-content:space-between">
            <span data-i18n="storage_label">Dung lượng hộp thư</span>
            <span id="quotaPct">0%</span>
          </div>
          <div class="quota-bar"><div id="quotaFill" class="quota-fill"></div></div>
          <div id="quotaText">0 MB</div>
        </div>
      </aside>

      <!-- Middle Message List -->
      <section class="list-pane">
        <div class="list-toolbar">
          <div style="display:flex;align-items:center;gap:6px">
            <input id="selectAllBox" type="checkbox" title="Chọn tất cả" />
            <button id="refreshBtn" type="button" class="btn btn-sm" title="Làm mới">↻</button>
            <button id="bulkReadBtn" type="button" class="btn btn-sm" title="Đánh dấu đã đọc">✓ Đã đọc</button>
            <button id="bulkSpamBtn" type="button" class="btn btn-sm" title="Báo cáo Spam">Spam</button>
            <button id="bulkDeleteBtn" type="button" class="btn btn-sm btn-danger" title="Xóa thư đã chọn">Xóa</button>
          </div>
          <button id="emptyFolderBtn" type="button" class="btn btn-sm btn-danger hidden">Dọn sạch</button>
        </div>
        <div id="msgList" class="msg-list"></div>
        <div class="list-footer">
          <span id="pageInfo">0 thư</span>
          <div style="display:flex;gap:6px">
            <button id="prevPageBtn" type="button" class="btn btn-sm">‹</button>
            <button id="nextPageBtn" type="button" class="btn btn-sm">›</button>
          </div>
        </div>
      </section>

      <!-- Right Reading Pane -->
      <main class="read-pane">
        <div id="readEmpty" class="read-empty" data-i18n="select_prompt">
          Chọn một email từ danh sách để xem nội dung
        </div>
        <div id="readContent" class="hidden" style="display:flex;flex-direction:column;height:100%">
          <div class="read-header">
            <div class="read-actions">
              <button id="replyBtn" type="button" class="btn btn-sm">↩️ <span data-i18n="reply">Trả lời</span></button>
              <button id="replyAllBtn" type="button" class="btn btn-sm">↩️ <span data-i18n="reply_all">Trả lời tất cả</span></button>
              <button id="forwardBtn" type="button" class="btn btn-sm">↪️ <span data-i18n="forward">Chuyển tiếp</span></button>
              <button id="editDraftBtn" type="button" class="btn btn-sm btn-primary hidden">✏️ Sửa thư nháp</button>
              <button id="markUnreadBtn" type="button" class="btn btn-sm" data-i18n="mark_unread">Chưa đọc</button>
              <button id="moveJunkBtn" type="button" class="btn btn-sm">Spam</button>
              <a id="downloadEmlBtn" href="#" class="btn btn-sm" style="text-decoration:none">.EML</a>
              <button id="deleteMsgBtn" type="button" class="btn btn-sm btn-danger" data-i18n="delete">Xóa</button>
            </div>
            <div id="readSubject" class="read-subject"></div>
            <div id="readMeta" class="read-meta"></div>
            <div id="readAttachments" class="attach-bar hidden"></div>
          </div>
          <div class="read-body">
            <iframe id="readFrame" class="read-iframe" sandbox="allow-popups allow-popups-to-escape-sandbox"></iframe>
          </div>
        </div>
      </main>
    </div>
  </div>

  <!-- Compose Modal -->
  <div id="composeModal" class="modal-backdrop hidden">
    <div class="modal">
      <div class="modal-head">
        <span id="composeTitle" data-i18n="compose_btn">Soạn thư mới</span>
        <button id="closeComposeBtn" type="button" class="btn btn-sm">✕</button>
      </div>
      <form id="composeForm" class="modal-body">
        <input id="composeDraftUid" type="hidden" value="" />
        <input id="composeInReplyTo" type="hidden" value="" />
        <input id="composeReferences" type="hidden" value="" />
        <div>
          <input id="composeTo" type="text" class="input" placeholder="Người nhận (To): user@example.com, ..." required />
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <input id="composeCc" type="text" class="input" placeholder="Cc (tùy chọn)" />
          <input id="composeBcc" type="text" class="input" placeholder="Bcc (tùy chọn)" />
        </div>
        <div>
          <input id="composeSubject" type="text" class="input" placeholder="Tiêu đề email" required />
        </div>
        <div>
          <textarea id="composeBody" class="input" rows="11" style="resize:vertical;font-family:inherit;line-height:1.5" placeholder="Nhập nội dung email..."></textarea>
        </div>
        <div>
          <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">Tệp đính kèm (tối đa 50 MB):</label>
          <input id="composeFiles" type="file" multiple class="input" style="padding:7px" />
        </div>
      </form>
      <div class="modal-foot">
        <button id="saveDraftBtn" type="button" class="btn" data-i18n="save_draft">Lưu nháp</button>
        <div style="display:flex;gap:8px">
          <button id="cancelComposeBtn" type="button" class="btn" data-i18n="cancel">Hủy</button>
          <button id="sendMailBtn" type="button" class="btn btn-primary">🚀 <span data-i18n="send_now">Gửi email</span></button>
        </div>
      </div>
    </div>
  </div>

  <!-- Settings Modal -->
  <div id="settingsModal" class="modal-backdrop hidden">
    <div class="modal" style="max-width:500px">
      <div class="modal-head">
        <span data-i18n="settings_title">Cài đặt hộp thư</span>
        <button id="closeSettingsBtn" type="button" class="btn btn-sm">✕</button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label data-i18n="display_name_label">Tên hiển thị khi gửi thư</label>
          <input id="prefDisplayName" type="text" class="input" placeholder="Nguyễn Văn A" />
        </div>
        <div class="field">
          <label>Địa chỉ Reply-To (tùy chọn)</label>
          <input id="prefReplyTo" type="email" class="input" placeholder="reply@domain.com" />
        </div>
        <div class="field" style="margin-bottom:0">
          <label data-i18n="signature_label">Chữ ký email tự động</label>
          <textarea id="prefSignature" class="input" rows="5" placeholder="--&#10;Trân trọng,"></textarea>
        </div>
      </div>
      <div class="modal-foot" style="justify-content:flex-end;gap:8px">
        <button id="saveSettingsBtn" type="button" class="btn btn-primary" data-i18n="save_btn">Lưu cài đặt</button>
      </div>
    </div>
  </div>

  <div id="toast" class="toast hidden"></div>

  <script>
    const I18N = {
      vi: {
        login_sub: "Đăng nhập hộp thư doanh nghiệp",
        email_label: "Địa chỉ Email",
        password_label: "Mật khẩu",
        sign_in: "Đăng nhập",
        search_btn: "Tìm",
        settings_btn: "Cài đặt",
        logout_btn: "Đăng xuất",
        compose_btn: "Soạn thư mới",
        storage_label: "Dung lượng",
        select_prompt: "Chọn một email từ danh sách để xem nội dung",
        reply: "Trả lời",
        reply_all: "Trả lời tất cả",
        forward: "Chuyển tiếp",
        mark_unread: "Chưa đọc",
        delete: "Xóa",
        save_draft: "Lưu nháp",
        cancel: "Hủy",
        send_now: "Gửi email",
        settings_title: "Cài đặt hộp thư",
        display_name_label: "Tên hiển thị khi gửi thư",
        signature_label: "Chữ ký email tự động",
        save_btn: "Lưu cài đặt"
      },
      en: {
        login_sub: "Sign in to your mailbox",
        email_label: "Email Address",
        password_label: "Password",
        sign_in: "Sign In",
        search_btn: "Search",
        settings_btn: "Settings",
        logout_btn: "Sign Out",
        compose_btn: "Compose",
        storage_label: "Storage",
        select_prompt: "Select an email from the list to read",
        reply: "Reply",
        reply_all: "Reply All",
        forward: "Forward",
        mark_unread: "Mark Unread",
        delete: "Delete",
        save_draft: "Save Draft",
        cancel: "Cancel",
        send_now: "Send Email",
        settings_title: "Mailbox Settings",
        display_name_label: "Sender Display Name",
        signature_label: "Email Signature",
        save_btn: "Save Settings"
      }
    };

    const state = {
      email: null,
      settings: { display_name: "", reply_to: "", signature: "", language: "vi", theme: "light" },
      folders: [],
      currentFolder: "INBOX",
      messages: [],
      totalMessages: 0,
      page: 1,
      perPage: 40,
      searchQuery: "",
      activeMsg: null,
      selectedUids: new Set()
    };

    function showToast(msg) {
      const el = document.getElementById("toast");
      el.textContent = msg;
      el.classList.remove("hidden");
      clearTimeout(el._timer);
      el._timer = setTimeout(() => el.classList.add("hidden"), 3200);
    }

    function applyThemeAndLang() {
      const theme = state.settings.theme || "light";
      const lang = state.settings.language || "vi";
      document.documentElement.setAttribute("data-theme", theme);
      document.getElementById("themeBtn").textContent = theme === "dark" ? "🌙" : "☀️";
      document.getElementById("langBtn").textContent = lang.toUpperCase();
      const dict = I18N[lang] || I18N.vi;
      document.querySelectorAll("[data-i18n]").forEach(el => {
        const key = el.getAttribute("data-i18n");
        if (dict[key]) el.textContent = dict[key];
      });
      renderFolders();
    }

    async function api(path, options = {}) {
      const headers = { "X-Requested-With": "XMLHttpRequest", ...(options.headers || {}) };
      const res = await fetch(path, { credentials: "same-origin", ...options, headers });
      let data = null;
      const ctype = res.headers.get("content-type") || "";
      if (ctype.includes("application/json")) {
        data = await res.json();
      }
      if (!res.ok) {
        if (res.status === 401 && path !== "/api/auth/login") {
          showLogin();
        }
        throw new Error((data && data.detail) || `HTTP ${res.status}`);
      }
      return data;
    }

    function formatSize(bytes) {
      if (!bytes || bytes <= 0) return "0 KB";
      if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    function formatShortDate(ts, rawDate) {
      if (!ts) return (rawDate || "").slice(0, 16);
      const d = new Date(ts * 1000);
      const now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }
      return d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
    }

    function showLogin() {
      document.getElementById("appView").classList.add("hidden");
      document.getElementById("loginView").classList.remove("hidden");
    }

    function showApp() {
      document.getElementById("loginView").classList.add("hidden");
      document.getElementById("appView").classList.remove("hidden");
      document.getElementById("userEmailBadge").textContent = state.email || "";
      applyThemeAndLang();
      loadFolders();
      loadMessages();
    }

    function renderFolders() {
      const ul = document.getElementById("folderList");
      ul.innerHTML = "";
      const lang = state.settings.language || "vi";
      state.folders.forEach(f => {
        const li = document.createElement("li");
        li.className = "folder-item" + (f.id === state.currentFolder ? " active" : "");
        const label = lang === "en" ? f.label_en : f.label_vi;
        const badgeCount = f.id === "Drafts" ? f.total : f.unseen;
        li.innerHTML = `
          <span>${label}</span>
          ${badgeCount > 0 ? `<span class="badge">${badgeCount}</span>` : ""}
        `;
        li.onclick = () => {
          state.currentFolder = f.id;
          state.page = 1;
          state.activeMsg = null;
          state.selectedUids.clear();
          document.getElementById("readContent").classList.add("hidden");
          document.getElementById("readEmpty").classList.remove("hidden");
          renderFolders();
          loadMessages();
        };
        ul.appendChild(li);
      });

      const emptyBtn = document.getElementById("emptyFolderBtn");
      if (state.currentFolder === "Trash" || state.currentFolder === "Junk") {
        emptyBtn.classList.remove("hidden");
      } else {
        emptyBtn.classList.add("hidden");
      }
    }

    async function loadFolders() {
      try {
        const data = await api("/api/folders");
        state.folders = data.folders || [];
        renderFolders();
        const usedKb = (data.quota && data.quota.used_kb) || 0;
        const limitKb = (data.quota && data.quota.limit_kb) || 0;
        const usedMb = (usedKb / 1024).toFixed(1);
        if (limitKb > 0) {
          const limitMb = Math.round(limitKb / 1024);
          const pct = Math.min(100, Math.round((usedKb / limitKb) * 100));
          document.getElementById("quotaPct").textContent = `${pct}%`;
          document.getElementById("quotaFill").style.width = `${pct}%`;
          document.getElementById("quotaText").textContent = `${usedMb} MB / ${limitMb} MB`;
        } else {
          document.getElementById("quotaPct").textContent = "";
          document.getElementById("quotaFill").style.width = "8%";
          document.getElementById("quotaText").textContent = `${usedMb} MB`;
        }
      } catch (e) {
        console.error(e);
      }
    }

    function renderMessageList() {
      const container = document.getElementById("msgList");
      container.innerHTML = "";
      document.getElementById("selectAllBox").checked = false;

      if (!state.messages.length) {
        container.innerHTML = `<div style="padding:36px 16px;text-align:center;color:var(--text-muted)">Không có email nào trong thư mục này.</div>`;
      } else {
        state.messages.forEach(m => {
          const row = document.createElement("div");
          row.className = "msg-row" + (!m.seen ? " unread" : "") + (state.activeMsg && state.activeMsg.uid === m.uid ? " active" : "");

          const leftCol = document.createElement("div");
          leftCol.style.display = "flex";
          leftCol.style.flexDirection = "column";
          leftCol.style.alignItems = "center";
          leftCol.style.gap = "6px";

          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = state.selectedUids.has(m.uid);
          cb.onclick = ev => {
            ev.stopPropagation();
            if (cb.checked) state.selectedUids.add(m.uid);
            else state.selectedUids.delete(m.uid);
          };

          const star = document.createElement("button");
          star.type = "button";
          star.className = "star-btn" + (m.flagged ? " starred" : "");
          star.textContent = m.flagged ? "★" : "☆";
          star.onclick = async ev => {
            ev.stopPropagation();
            const act = m.flagged ? "unstar" : "star";
            await api("/api/messages/action", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ action: act, folder: state.currentFolder, uids: [m.uid] })
            });
            m.flagged = !m.flagged;
            renderMessageList();
            loadFolders();
          };

          leftCol.appendChild(cb);
          leftCol.appendChild(star);

          const midCol = document.createElement("div");
          midCol.style.minWidth = "0";
          const fromDiv = document.createElement("div");
          fromDiv.className = "msg-from";
          fromDiv.textContent = state.currentFolder === "Sent" ? `To: ${m.to || ""}` : (m.from_name || m.from || "");
          const subjDiv = document.createElement("div");
          subjDiv.className = "msg-subj";
          subjDiv.textContent = (m.has_attachment ? "📎 " : "") + (m.subject || "");
          midCol.appendChild(fromDiv);
          midCol.appendChild(subjDiv);

          const rightCol = document.createElement("div");
          rightCol.className = "msg-meta";
          rightCol.textContent = formatShortDate(m.timestamp, m.date);

          row.appendChild(leftCol);
          row.appendChild(midCol);
          row.appendChild(rightCol);

          row.onclick = () => openMessage(m.uid);
          container.appendChild(row);
        });
      }

      const start = state.totalMessages === 0 ? 0 : (state.page - 1) * state.perPage + 1;
      const end = Math.min(state.totalMessages, state.page * state.perPage);
      document.getElementById("pageInfo").textContent = `${start}-${end} / ${state.totalMessages}`;
      document.getElementById("prevPageBtn").disabled = state.page <= 1;
      document.getElementById("nextPageBtn").disabled = end >= state.totalMessages;
    }

    async function loadMessages() {
      try {
        const params = new URLSearchParams({
          folder: state.currentFolder,
          page: String(state.page),
          per_page: String(state.perPage),
          q: state.searchQuery
        });
        const data = await api(`/api/messages?${params.toString()}`);
        state.messages = data.items || [];
        state.totalMessages = data.total || 0;
        renderMessageList();
      } catch (e) {
        showToast(e.message);
      }
    }

    async function openMessage(uid) {
      try {
        const params = new URLSearchParams({ folder: state.currentFolder });
        const msg = await api(`/api/messages/${encodeURIComponent(uid)}?${params.toString()}`);
        state.activeMsg = msg;
        const item = state.messages.find(x => x.uid === uid);
        if (item) item.seen = true;
        renderMessageList();
        loadFolders();

        document.getElementById("readEmpty").classList.add("hidden");
        const contentEl = document.getElementById("readContent");
        contentEl.classList.remove("hidden");
        document.getElementById("readSubject").textContent = msg.subject || "";

        const metaLines = [];
        metaLines.push(`<strong>Từ:</strong> ${escapeHtml(msg.from || "")}`);
        if (msg.to) metaLines.push(`<strong>Đến:</strong> ${escapeHtml(msg.to)}`);
        if (msg.cc) metaLines.push(`<strong>Cc:</strong> ${escapeHtml(msg.cc)}`);
        if (msg.date) metaLines.push(`<strong>Thời gian:</strong> ${escapeHtml(msg.date)}`);
        document.getElementById("readMeta").innerHTML = metaLines.join(" &nbsp;•&nbsp; ");

        const editDraftBtn = document.getElementById("editDraftBtn");
        if (state.currentFolder === "Drafts" || msg.draft) {
          editDraftBtn.classList.remove("hidden");
        } else {
          editDraftBtn.classList.add("hidden");
        }

        document.getElementById("downloadEmlBtn").href = `/api/messages/${encodeURIComponent(uid)}/raw?folder=${encodeURIComponent(state.currentFolder)}`;

        const attachEl = document.getElementById("readAttachments");
        attachEl.innerHTML = "";
        if (msg.attachments && msg.attachments.length) {
          attachEl.classList.remove("hidden");
          msg.attachments.forEach(a => {
            const link = document.createElement("a");
            link.className = "attach-chip";
            link.href = `/api/messages/${encodeURIComponent(uid)}/attachments/${a.part_id}?folder=${encodeURIComponent(state.currentFolder)}`;
            link.textContent = `📎 ${a.filename} (${formatSize(a.size)})`;
            attachEl.appendChild(link);
          });
        } else {
          attachEl.classList.add("hidden");
        }

        const frame = document.getElementById("readFrame");
        frame.srcdoc = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{font-family:system-ui,-apple-system,sans-serif;font-size:14px;color:#0f172a;margin:18px;line-height:1.6;word-wrap:break-word}img{max-width:100%;height:auto}blockquote{border-left:3px solid #cbd5e1;margin:8px 0;padding-left:12px;color:#475569}</style></head><body>${msg.body_html || ""}</body></html>`;
      } catch (e) {
        showToast(e.message);
      }
    }

    function escapeHtml(str) {
      return String(str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function openCompose(opts = {}) {
      document.getElementById("composeDraftUid").value = opts.draftUid || "";
      document.getElementById("composeInReplyTo").value = opts.inReplyTo || "";
      document.getElementById("composeReferences").value = opts.references || "";
      document.getElementById("composeTo").value = opts.to || "";
      document.getElementById("composeCc").value = opts.cc || "";
      document.getElementById("composeBcc").value = "";
      document.getElementById("composeSubject").value = opts.subject || "";
      document.getElementById("composeFiles").value = "";

      const sig = (state.settings.signature || "").trim();
      const baseBody = opts.body || "";
      document.getElementById("composeBody").value = sig && !opts.draftUid ? `${baseBody}\n\n${sig}` : baseBody;
      document.getElementById("composeModal").classList.remove("hidden");
    }

    async function submitCompose(endpoint) {
      const fd = new FormData();
      fd.append("to", document.getElementById("composeTo").value.trim());
      fd.append("cc", document.getElementById("composeCc").value.trim());
      fd.append("bcc", document.getElementById("composeBcc").value.trim());
      fd.append("subject", document.getElementById("composeSubject").value.trim());
      fd.append("body_text", document.getElementById("composeBody").value);
      fd.append("in_reply_to", document.getElementById("composeInReplyTo").value);
      fd.append("references", document.getElementById("composeReferences").value);
      fd.append("draft_uid", document.getElementById("composeDraftUid").value);
      const files = document.getElementById("composeFiles").files;
      for (let i = 0; i < files.length; i++) {
        fd.append("attachments", files[i]);
      }
      await api(endpoint, { method: "POST", body: fd });
      document.getElementById("composeModal").classList.add("hidden");
      loadFolders();
      loadMessages();
    }

    // Event bindings
    document.getElementById("loginForm").onsubmit = async ev => {
      ev.preventDefault();
      const errEl = document.getElementById("loginError");
      errEl.classList.add("hidden");
      try {
        const res = await api("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: document.getElementById("loginEmail").value.trim(),
            password: document.getElementById("loginPassword").value
          })
        });
        state.email = res.email;
        state.settings = res.settings || state.settings;
        showApp();
      } catch (e) {
        errEl.textContent = e.message;
        errEl.classList.remove("hidden");
      }
    };

    document.getElementById("logoutBtn").onclick = async () => {
      await api("/api/auth/logout", { method: "POST" });
      state.email = null;
      showLogin();
    };

    document.getElementById("searchForm").onsubmit = ev => {
      ev.preventDefault();
      state.searchQuery = document.getElementById("searchInput").value.trim();
      state.page = 1;
      loadMessages();
    };

    document.getElementById("refreshBtn").onclick = () => {
      loadFolders();
      loadMessages();
    };

    document.getElementById("selectAllBox").onchange = ev => {
      state.selectedUids.clear();
      if (ev.target.checked) {
        state.messages.forEach(m => state.selectedUids.add(m.uid));
      }
      renderMessageList();
      document.getElementById("selectAllBox").checked = ev.target.checked;
    };

    async function runBulkAction(action, extra = {}) {
      const uids = Array.from(state.selectedUids);
      if (!uids.length) {
        showToast("Vui lòng chọn ít nhất một email.");
        return;
      }
      try {
        await api("/api/messages/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, folder: state.currentFolder, uids, ...extra })
        });
        state.selectedUids.clear();
        loadFolders();
        loadMessages();
      } catch (e) {
        showToast(e.message);
      }
    }

    document.getElementById("bulkReadBtn").onclick = () => runBulkAction("mark_read");
    document.getElementById("bulkSpamBtn").onclick = () => runBulkAction("move", { target_folder: "Junk" });
    document.getElementById("bulkDeleteBtn").onclick = () => runBulkAction("delete");

    document.getElementById("emptyFolderBtn").onclick = async () => {
      const act = state.currentFolder === "Trash" ? "empty_trash" : "empty_junk";
      await api("/api/messages/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: act, folder: state.currentFolder })
      });
      state.activeMsg = null;
      document.getElementById("readContent").classList.add("hidden");
      document.getElementById("readEmpty").classList.remove("hidden");
      loadFolders();
      loadMessages();
      showToast("Đã dọn sạch thư mục.");
    };

    document.getElementById("prevPageBtn").onclick = () => {
      if (state.page > 1) { state.page--; loadMessages(); }
    };
    document.getElementById("nextPageBtn").onclick = () => {
      state.page++; loadMessages();
    };

    document.getElementById("composeBtn").onclick = () => openCompose();
    document.getElementById("closeComposeBtn").onclick = () => document.getElementById("composeModal").classList.add("hidden");
    document.getElementById("cancelComposeBtn").onclick = () => document.getElementById("composeModal").classList.add("hidden");

    document.getElementById("sendMailBtn").onclick = async () => {
      try {
        await submitCompose("/api/messages/send");
        showToast("Đã gửi email thành công!");
      } catch (e) {
        showToast(e.message);
      }
    };

    document.getElementById("saveDraftBtn").onclick = async () => {
      try {
        await submitCompose("/api/messages/draft");
        showToast("Đã lưu thư nháp.");
      } catch (e) {
        showToast(e.message);
      }
    };

    document.getElementById("replyBtn").onclick = () => {
      const m = state.activeMsg;
      if (!m) return;
      const subj = /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`;
      const quoted = `\n\n--- Vào ${m.date || ""}, ${m.from || ""} đã viết: ---\n` + (m.body_text || "").split("\n").map(l => `> ${l}`).join("\n");
      openCompose({
        to: m.reply_to || m.from_address || m.from,
        subject: subj,
        body: quoted,
        inReplyTo: m.message_id,
        references: [m.references, m.message_id].filter(Boolean).join(" ")
      });
    };

    document.getElementById("replyAllBtn").onclick = () => {
      const m = state.activeMsg;
      if (!m) return;
      const subj = /^re:/i.test(m.subject) ? m.subject : `Re: ${m.subject}`;
      const quoted = `\n\n--- Vào ${m.date || ""}, ${m.from || ""} đã viết: ---\n` + (m.body_text || "").split("\n").map(l => `> ${l}`).join("\n");
      openCompose({
        to: m.reply_to || m.from_address || m.from,
        cc: m.cc || "",
        subject: subj,
        body: quoted,
        inReplyTo: m.message_id,
        references: [m.references, m.message_id].filter(Boolean).join(" ")
      });
    };

    document.getElementById("forwardBtn").onclick = () => {
      const m = state.activeMsg;
      if (!m) return;
      const subj = /^fwd:/i.test(m.subject) ? m.subject : `Fwd: ${m.subject}`;
      const fwdHeader = `\n\n---------- Thư chuyển tiếp ----------\nTừ: ${m.from || ""}\nNgày: ${m.date || ""}\nTiêu đề: ${m.subject || ""}\nĐến: ${m.to || ""}\n\n${m.body_text || ""}`;
      openCompose({ subject: subj, body: fwdHeader });
    };

    document.getElementById("editDraftBtn").onclick = () => {
      const m = state.activeMsg;
      if (!m) return;
      openCompose({
        draftUid: m.uid,
        to: m.to || "",
        cc: m.cc || "",
        subject: m.subject || "",
        body: m.body_text || ""
      });
    };

    document.getElementById("markUnreadBtn").onclick = async () => {
      if (!state.activeMsg) return;
      await api("/api/messages/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "mark_unread", folder: state.currentFolder, uids: [state.activeMsg.uid] })
      });
      loadFolders();
      loadMessages();
      showToast("Đã đánh dấu chưa đọc.");
    };

    document.getElementById("moveJunkBtn").onclick = async () => {
      if (!state.activeMsg) return;
      const target = state.currentFolder === "Junk" ? "INBOX" : "Junk";
      await api("/api/messages/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "move", folder: state.currentFolder, target_folder: target, uids: [state.activeMsg.uid] })
      });
      state.activeMsg = null;
      document.getElementById("readContent").classList.add("hidden");
      document.getElementById("readEmpty").classList.remove("hidden");
      loadFolders();
      loadMessages();
    };

    document.getElementById("deleteMsgBtn").onclick = async () => {
      if (!state.activeMsg) return;
      await api("/api/messages/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", folder: state.currentFolder, uids: [state.activeMsg.uid] })
      });
      state.activeMsg = null;
      document.getElementById("readContent").classList.add("hidden");
      document.getElementById("readEmpty").classList.remove("hidden");
      loadFolders();
      loadMessages();
      showToast("Đã xóa email.");
    };

    document.getElementById("themeBtn").onclick = async () => {
      const next = state.settings.theme === "dark" ? "light" : "dark";
      state.settings = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: next })
      });
      applyThemeAndLang();
    };

    document.getElementById("langBtn").onclick = async () => {
      const next = state.settings.language === "en" ? "vi" : "en";
      state.settings = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: next })
      });
      applyThemeAndLang();
    };

    document.getElementById("settingsBtn").onclick = () => {
      document.getElementById("prefDisplayName").value = state.settings.display_name || "";
      document.getElementById("prefReplyTo").value = state.settings.reply_to || "";
      document.getElementById("prefSignature").value = state.settings.signature || "";
      document.getElementById("settingsModal").classList.remove("hidden");
    };
    document.getElementById("closeSettingsBtn").onclick = () => document.getElementById("settingsModal").classList.add("hidden");
    document.getElementById("saveSettingsBtn").onclick = async () => {
      try {
        state.settings = await api("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: document.getElementById("prefDisplayName").value.trim(),
            reply_to: document.getElementById("prefReplyTo").value.trim(),
            signature: document.getElementById("prefSignature").value
          })
        });
        document.getElementById("settingsModal").classList.add("hidden");
        showToast("Đã lưu cài đặt hộp thư.");
      } catch (e) {
        showToast(e.message);
      }
    };

    // Bootstrap
    (async () => {
      try {
        const me = await api("/api/auth/me");
        if (me && me.authenticated) {
          state.email = me.email;
          state.settings = me.settings || state.settings;
          showApp();
        } else {
          showLogin();
        }
      } catch (e) {
        showLogin();
      }
    })();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def webmail_index():
    return HTMLResponse(content=WEBMAIL_HTML)


if __name__ == "__main__":
    import uvicorn

    host = _cfg("HOST", "127.0.0.1")
    port = _cfg_int("PORT", 8096)
    uvicorn.run(app, host=host, port=port, log_level="warning")
