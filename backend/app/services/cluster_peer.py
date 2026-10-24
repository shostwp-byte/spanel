"""How one SPanel node talks to another: authentication and transport.

Every request between cluster nodes carries two things:

- the peer's API token as a Bearer header. The receiver looks it up exactly as
  the provisioning API does (hash, active, IP allowlist), but a node token has
  its own scopes - cluster:read, cluster:write, cluster:transfer - so a token
  pasted into a peer can sync DNS and move accounts but cannot create,
  suspend or terminate accounts through the billing API. Older peers were
  linked with a provisioning token; those still work on the peer endpoints
  (never the other way round), with a deprecation warning in the log.

- an HMAC-SHA256 signature over the method, the path with its query string, a
  timestamp, a one-time nonce, the sender's node id and the SHA-256 of the
  body. The key is the SHA-256 of the token - the value the receiver keeps in
  api_tokens.token_hash - so both ends hold it without the receiver ever
  storing the token itself. A request older than SIGNATURE_WINDOW seconds, or
  whose nonce was already seen inside that window, is refused: a captured
  request cannot be replayed, and a body cannot be swapped under a valid
  header.

TLS: a node added from now on verifies the peer's certificate by default. A
peer on a self-signed certificate is pinned instead, by the SHA-256
fingerprint of its certificate (probe_tls() reads it for the administrator to
confirm), or verified against a CA the administrator pastes. Turning all of
that off is still possible - nodes added before verification existed keep
working that way - and the page says so on the node.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import re
import secrets
import ssl
import threading
import time
import urllib.parse
from typing import Any, BinaryIO, Mapping, Optional

from app.core.version import APP_VERSION

logger = logging.getLogger("spanel.cluster")

SCOPE_READ = "cluster:read"
SCOPE_WRITE = "cluster:write"
SCOPE_TRANSFER = "cluster:transfer"
CLUSTER_SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_TRANSFER)
# What a provisioning token (the only kind there was before cluster scopes)
# may still do on the peer endpoints. Moving accounts is new, so no legacy
# token is ever good for it.
LEGACY_SCOPES = {SCOPE_READ: "provisioning:read", SCOPE_WRITE: "provisioning:write"}

HDR_TIMESTAMP = "X-SPanel-Timestamp"
HDR_NONCE = "X-SPanel-Nonce"
HDR_NODE = "X-SPanel-Node"
HDR_SIGNATURE = "X-SPanel-Signature"
HDR_BODY_SHA256 = "X-SPanel-Content-SHA256"
SIGNATURE_WINDOW = 300
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_NODE_UID_RE = re.compile(r"^[a-z0-9]{8,40}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class SignatureError(PermissionError):
    """A peer request that is unsigned, stale, replayed or forged."""


class PeerError(RuntimeError):
    """A peer request that failed: network, TLS, or an HTTP error answer."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


# --- scopes --------------------------------------------------------------------

def scope_decision(scopes: str, scope: str) -> str:
    """'ok', 'legacy' (a provisioning token standing in) or 'denied'."""
    have = {s.strip() for s in (scopes or "").split(",") if s.strip()}
    if scope in have:
        return "ok"
    legacy = LEGACY_SCOPES.get(scope)
    if legacy and legacy in have:
        return "legacy"
    return "denied"


def node_token_scopes(allow_transfer: bool = True) -> str:
    scopes = [SCOPE_READ, SCOPE_WRITE] + ([SCOPE_TRANSFER] if allow_transfer else [])
    return ",".join(scopes)


# --- signing -------------------------------------------------------------------

def signing_key_from_token(raw_token: str) -> str:
    """The HMAC key both ends hold: sha256(token), as provisioning.hash_token."""
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def canonical_request(method: str, path: str, timestamp: str, nonce: str, node_uid: str, body_sha256: str) -> bytes:
    return "\n".join([
        "SPANEL-HMAC-SHA256",
        (method or "").upper(),
        path or "/",
        str(timestamp),
        nonce,
        node_uid or "",
        (body_sha256 or EMPTY_SHA256).lower(),
    ]).encode("utf-8")


def compute_signature(key_hex: str, method: str, path: str, timestamp: str, nonce: str,
                      node_uid: str, body_sha256: str) -> str:
    message = canonical_request(method, path, timestamp, nonce, node_uid, body_sha256)
    return hmac.new(key_hex.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_headers(raw_token: str, method: str, path: str, body_sha256: str, node_uid: str,
                 *, now: Optional[float] = None, nonce: Optional[str] = None) -> dict[str, str]:
    timestamp = str(int(now if now is not None else time.time()))
    nonce = nonce or secrets.token_urlsafe(24)
    signature = compute_signature(signing_key_from_token(raw_token), method, path, timestamp,
                                  nonce, node_uid, body_sha256)
    return {
        HDR_TIMESTAMP: timestamp,
        HDR_NONCE: nonce,
        HDR_NODE: node_uid,
        HDR_SIGNATURE: signature,
        HDR_BODY_SHA256: body_sha256,
    }


class NonceCache:
    """Nonces seen inside the signature window. One API process serves the
    peer endpoints, so memory is the right place: a restart forgets them, and
    anything older than the window is refused by its timestamp anyway."""

    def __init__(self, window: int = SIGNATURE_WINDOW, max_entries: int = 100_000):
        self.window = window
        self.max_entries = max_entries
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, nonce: str, now: float) -> bool:
        """True the first time a nonce is presented, False on a replay."""
        with self._lock:
            if len(self._seen) >= self.max_entries or len(self._seen) % 512 == 0:
                cutoff = now - 2 * self.window
                for key in [k for k, ts in self._seen.items() if ts < cutoff]:
                    del self._seen[key]
                if len(self._seen) >= self.max_entries:
                    # Under a flood keep refusing new nonces rather than
                    # forgetting ones still inside the window.
                    return False
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


NONCES = NonceCache()


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return str(value or "").strip()


def verify_signature(key_hex: str, method: str, path: str, headers: Mapping[str, str], body_sha256: str,
                     *, now: Optional[float] = None, cache: Optional[NonceCache] = None) -> str:
    """Check a peer request's signature. Returns the sender's node id.

    `body_sha256` is the digest of the body as received. For a streamed
    upload the caller passes the declared digest and compares it with the
    stream once it has been read.
    """
    cache = cache if cache is not None else NONCES
    now = time.time() if now is None else now
    timestamp = _header(headers, HDR_TIMESTAMP)
    nonce = _header(headers, HDR_NONCE)
    node_uid = _header(headers, HDR_NODE)
    signature = _header(headers, HDR_SIGNATURE).lower()
    if not (timestamp and nonce and signature):
        raise SignatureError("Unsigned cluster request (update both nodes to the same SPanel version).")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("Bad request timestamp") from exc
    if abs(now - ts) > SIGNATURE_WINDOW:
        raise SignatureError("Request timestamp outside the allowed window (check the clocks of both nodes).")
    if not _NONCE_RE.fullmatch(nonce):
        raise SignatureError("Bad request nonce")
    if node_uid and not _NODE_UID_RE.fullmatch(node_uid):
        raise SignatureError("Bad node id")
    expected = compute_signature(key_hex, method, path, timestamp, nonce, node_uid, body_sha256)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("Bad request signature")
    if not cache.check_and_store(nonce, now):
        raise SignatureError("Replayed request")
    return node_uid


# --- TLS -----------------------------------------------------------------------

def normalize_fingerprint(value: str) -> str:
    """'AB:CD:...' or 'abcd...' -> 64 lowercase hex characters, or ''."""
    cleaned = re.sub(r"[\s:]", "", (value or "")).lower()
    if cleaned.startswith("sha256/") or cleaned.startswith("sha256="):
        cleaned = cleaned[7:]
    if not cleaned:
        return ""
    if not _FINGERPRINT_RE.fullmatch(cleaned):
        raise ValueError("A certificate fingerprint is the SHA-256 of the certificate: 64 hex characters.")
    return cleaned


def format_fingerprint(hex_value: str) -> str:
    return ":".join(hex_value[i:i + 2] for i in range(0, len(hex_value), 2)).upper()


def certificate_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der or b"").hexdigest()


def ssl_context_for(node: Mapping[str, Any]) -> tuple[ssl.SSLContext, str]:
    """The TLS context for a node and the fingerprint it is pinned to ('' if none)."""
    pin = normalize_fingerprint(str(node.get("tls_fingerprint") or ""))
    if pin:
        # The pin replaces chain and name validation: a self-signed panel
        # certificate has neither, and the exact certificate is checked after
        # the handshake instead.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx, pin
    if node.get("verify_tls", True):
        ca_pem = str(node.get("ca_pem") or "").strip()
        ctx = ssl.create_default_context(cadata=ca_pem) if ca_pem else ssl.create_default_context()
        return ctx, ""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx, ""


def _connection(scheme: str, host: str, port: int, timeout: float, context: Optional[ssl.SSLContext]):
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=context, blocksize=1024 * 1024)
    return http.client.HTTPConnection(host, port, timeout=timeout, blocksize=1024 * 1024)


def _check_pin(conn, pin: str) -> None:
    if not pin:
        return
    sock = getattr(conn, "sock", None)
    der = sock.getpeercert(binary_form=True) if sock is not None and hasattr(sock, "getpeercert") else None
    got = certificate_fingerprint(der or b"")
    if not der or not hmac.compare_digest(got, pin):
        raise PeerError(
            "The peer's TLS certificate does not match the pinned fingerprint "
            f"(got {format_fingerprint(got)}). If its certificate was renewed, pin the new one."
        )


def probe_tls(url: str, timeout: float = 6) -> dict[str, Any]:
    """Read the certificate a peer presents, for the administrator to pin."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only an https:// peer has a certificate to pin.")
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    conn = _connection("https", parsed.hostname, parsed.port or 443, timeout, insecure)
    try:
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True) or b""
    except (OSError, ssl.SSLError) as exc:
        raise PeerError(f"Could not reach {parsed.hostname}: {exc}") from exc
    finally:
        conn.close()
    trusted = True
    try:
        check = _connection("https", parsed.hostname, parsed.port or 443, timeout, ssl.create_default_context())
        try:
            check.connect()
        finally:
            check.close()
    except (OSError, ssl.SSLError):
        trusted = False
    info: dict[str, Any] = {"fingerprint": format_fingerprint(certificate_fingerprint(der)), "trusted_by_system": trusted}
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        info.update({
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "not_after": cert.not_valid_after_utc.isoformat(),
        })
    except Exception:  # noqa: BLE001 - the fingerprint is what matters
        pass
    return info


def local_certificate_fingerprint(cert_path: str) -> str:
    """Fingerprint of this panel's own certificate, for the other side to pin."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.serialization import Encoding

        with open(cert_path, "rb") as handle:
            cert = x509.load_pem_x509_certificate(handle.read())
        return format_fingerprint(certificate_fingerprint(cert.public_bytes(Encoding.DER)))
    except Exception:  # noqa: BLE001 - absent or unreadable: nothing to show
        return ""


# --- requests ------------------------------------------------------------------

def request(
    node: Mapping[str, Any],
    raw_token: str,
    local_uid: str,
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    timeout: float = 8,
    stream: Optional[BinaryIO] = None,
    stream_size: Optional[int] = None,
    stream_sha256: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> tuple[dict[str, Any], int]:
    """One signed request to a peer. Returns (JSON answer, latency in ms).

    `stream` sends a file as the body (an account archive) without reading it
    into memory; its SHA-256 must be given, since that is what is signed.
    """
    base = urllib.parse.urlparse(str(node.get("url") or "").rstrip("/"))
    if base.scheme not in ("http", "https") or not base.hostname:
        raise PeerError("The peer URL is not valid.")
    if not raw_token:
        raise PeerError("No usable API token is stored for this peer; enter it again.")
    if stream is not None:
        if not stream_sha256 or stream_size is None:
            raise ValueError("A streamed body needs its size and SHA-256")
        body: Any = stream
        body_sha256 = stream_sha256.lower()
        content_type = "application/octet-stream"
        length = int(stream_size)
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        body_sha256 = hashlib.sha256(body).hexdigest()
        content_type = "application/json"
        length = len(body)

    headers = {
        "Accept": "application/json",
        "Content-Type": content_type,
        "Content-Length": str(length),
        "Authorization": f"Bearer {raw_token}",
        "User-Agent": f"SPanel-Cluster/{APP_VERSION}",
    }
    headers.update(sign_headers(raw_token, method, path, body_sha256, local_uid))
    if extra_headers:
        headers.update(extra_headers)

    context, pin = ssl_context_for(node) if base.scheme == "https" else (None, "")
    port = base.port or (443 if base.scheme == "https" else 80)
    started = time.perf_counter()
    conn = _connection(base.scheme, base.hostname, port, timeout, context)
    try:
        try:
            conn.connect()
            _check_pin(conn, pin)
            conn.request(method.upper(), path, body=body if length else None, headers=headers)
            response = conn.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except PeerError:
            raise
        except ssl.SSLCertVerificationError as exc:
            raise PeerError(
                f"The certificate of {base.hostname} is not trusted ({exc.verify_message}). "
                "Pin its fingerprint or add its CA on the node."
            ) from exc
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise PeerError(f"Could not reach {base.scheme}://{base.hostname}:{port}: {exc}") from exc
    finally:
        conn.close()
    latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PeerError("The peer's answer is too large.")
    text = raw.decode("utf-8", errors="replace")
    if response.status >= 400:
        detail = ""
        try:
            parsed = json.loads(text)
            detail = str(parsed.get("detail") or parsed.get("message") or "") if isinstance(parsed, dict) else ""
        except ValueError:
            pass
        raise PeerError(detail or f"The peer answered HTTP {response.status}", status=response.status)
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        raise PeerError("The peer gave an answer that is not JSON.") from exc
    return (data if isinstance(data, dict) else {"data": data}), latency_ms
