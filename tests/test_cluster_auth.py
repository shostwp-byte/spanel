"""Cluster node authentication: request signing, token scopes, stored peer
tokens and TLS verification between SPanel nodes."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import cluster_peer  # noqa: E402


TOKEN = "bp_example-cluster-token-value"
KEY = hashlib.sha256(TOKEN.encode()).hexdigest()
NODE = "a1b2c3d4e5f60718"


def _signed(method="POST", path="/api/multi-server/peer/dns-sync", body=b'{"zones":[]}', now=1_800_000_000, nonce=None):
    digest = hashlib.sha256(body).hexdigest()
    return cluster_peer.sign_headers(TOKEN, method, path, digest, NODE, now=now, nonce=nonce), digest


# --- signing -------------------------------------------------------------------

def test_signed_request_verifies_and_names_the_sender():
    headers, digest = _signed()
    cache = cluster_peer.NonceCache()
    node = cluster_peer.verify_signature(KEY, "POST", "/api/multi-server/peer/dns-sync", headers, digest,
                                         now=1_800_000_010, cache=cache)
    assert node == NODE


def test_signing_key_is_the_stored_token_hash():
    from app.services.provisioning import hash_token

    assert cluster_peer.signing_key_from_token(TOKEN) == hash_token(TOKEN)


@pytest.mark.parametrize("change", ["body", "path", "method", "node", "key"])
def test_any_tampering_breaks_the_signature(change):
    headers, digest = _signed()
    method, path, key = "POST", "/api/multi-server/peer/dns-sync", KEY
    if change == "body":
        digest = hashlib.sha256(b'{"zones":[{"zone":"evil.com","action":"delete"}]}').hexdigest()
    elif change == "path":
        path = "/api/multi-server/peer/transfer/restore"
    elif change == "method":
        method = "PUT"
    elif change == "node":
        headers = dict(headers, **{cluster_peer.HDR_NODE: "ffffffffffffffff"})
    elif change == "key":
        key = hashlib.sha256(b"another token").hexdigest()
    with pytest.raises(cluster_peer.SignatureError, match="signature"):
        cluster_peer.verify_signature(key, method, path, headers, digest, now=1_800_000_000,
                                      cache=cluster_peer.NonceCache())


def test_query_string_is_part_of_what_is_signed():
    path = "/api/multi-server/peer/check-conflict?domain=a.com&username="
    headers, digest = _signed(method="GET", path=path, body=b"")
    with pytest.raises(cluster_peer.SignatureError):
        cluster_peer.verify_signature(KEY, "GET", path.replace("a.com", "b.com"), headers, digest,
                                      now=1_800_000_000, cache=cluster_peer.NonceCache())


def test_replayed_request_is_refused():
    headers, digest = _signed()
    cache = cluster_peer.NonceCache()
    args = (KEY, "POST", "/api/multi-server/peer/dns-sync", headers, digest)
    cluster_peer.verify_signature(*args, now=1_800_000_000, cache=cache)
    with pytest.raises(cluster_peer.SignatureError, match="Replayed"):
        cluster_peer.verify_signature(*args, now=1_800_000_005, cache=cache)


@pytest.mark.parametrize("offset", [-301, 301, 3600])
def test_stale_or_future_timestamp_is_refused(offset):
    headers, digest = _signed(now=1_800_000_000)
    with pytest.raises(cluster_peer.SignatureError, match="window"):
        cluster_peer.verify_signature(KEY, "POST", "/api/multi-server/peer/dns-sync", headers, digest,
                                      now=1_800_000_000 + offset, cache=cluster_peer.NonceCache())


def test_forged_signature_does_not_burn_the_nonce():
    """A bad signature must be rejected before its nonce is remembered, or an
    attacker could pre-spend the nonces of requests in flight."""
    headers, digest = _signed(nonce="n" * 24)
    cache = cluster_peer.NonceCache()
    forged = dict(headers, **{cluster_peer.HDR_SIGNATURE: "0" * 64})
    with pytest.raises(cluster_peer.SignatureError):
        cluster_peer.verify_signature(KEY, "POST", "/api/multi-server/peer/dns-sync", forged, digest,
                                      now=1_800_000_000, cache=cache)
    assert cluster_peer.verify_signature(KEY, "POST", "/api/multi-server/peer/dns-sync", headers, digest,
                                         now=1_800_000_000, cache=cache) == NODE


def test_unsigned_request_is_refused():
    with pytest.raises(cluster_peer.SignatureError, match="Unsigned"):
        cluster_peer.verify_signature(KEY, "GET", "/api/multi-server/peer/ping", {}, cluster_peer.EMPTY_SHA256,
                                      cache=cluster_peer.NonceCache())


# --- scopes --------------------------------------------------------------------

def test_cluster_scopes_and_legacy_fallback():
    node_scopes = cluster_peer.node_token_scopes(allow_transfer=True)
    assert cluster_peer.scope_decision(node_scopes, "cluster:write") == "ok"
    assert cluster_peer.scope_decision(node_scopes, "cluster:transfer") == "ok"
    assert "provisioning" not in node_scopes
    assert cluster_peer.scope_decision(cluster_peer.node_token_scopes(False), "cluster:transfer") == "denied"
    legacy = "provisioning:read,provisioning:write"
    assert cluster_peer.scope_decision(legacy, "cluster:read") == "legacy"
    assert cluster_peer.scope_decision(legacy, "cluster:write") == "legacy"
    # Moving accounts is new: a billing token is never good for it.
    assert cluster_peer.scope_decision(legacy, "cluster:transfer") == "denied"
    assert cluster_peer.scope_decision("provisioning:read", "cluster:write") == "denied"


def test_node_token_cannot_use_the_provisioning_api():
    from fastapi import HTTPException

    from app.api.provisioning import _require_scope

    token = SimpleNamespace(scopes=cluster_peer.node_token_scopes())
    with pytest.raises(HTTPException) as exc:
        _require_scope(token, "provisioning:write")
    assert exc.value.status_code == 403


# --- the peer endpoint dependency, end to end with a real token row ---------

@pytest.fixture()
def db():
    from app.core.database import Base, SessionLocal, engine
    import app.models.entities  # noqa: F401 - register every table

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _request(method, path, headers, query=b""):
    from starlette.requests import Request

    scope = {
        "type": "http", "method": method, "path": path, "query_string": query, "scheme": "https",
        "server": ("panel.test", 2222), "client": ("203.0.113.9", 40000), "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def _make_token(db, scopes):
    from app.services.provisioning import create_api_token

    raw, row = create_api_token(db, name=f"t-{scopes}", scopes=scopes, allowed_ips="")
    return raw


def test_peer_auth_accepts_signed_node_token(db):
    from app.api.multi_server import authenticate_peer

    raw = _make_token(db, cluster_peer.node_token_scopes())
    path = "/api/multi-server/peer/ping"
    headers = cluster_peer.sign_headers(raw, "GET", path, cluster_peer.EMPTY_SHA256, NODE)
    headers["Authorization"] = f"Bearer {raw}"
    _token, node = authenticate_peer(_request("GET", path, headers), db, "cluster:read", cluster_peer.EMPTY_SHA256)
    assert node == NODE


def test_peer_auth_refuses_unsigned_bearer(db):
    from fastapi import HTTPException

    from app.api.multi_server import authenticate_peer

    raw = _make_token(db, cluster_peer.node_token_scopes())
    path = "/api/multi-server/peer/ping"
    with pytest.raises(HTTPException) as exc:
        authenticate_peer(_request("GET", path, {"Authorization": f"Bearer {raw}"}), db, "cluster:read",
                          cluster_peer.EMPTY_SHA256)
    assert exc.value.status_code == 401


def test_peer_auth_legacy_token_warns_and_cannot_transfer(db, caplog):
    from fastapi import HTTPException

    from app.api.multi_server import authenticate_peer

    raw = _make_token(db, "provisioning:read,provisioning:write")
    path = "/api/multi-server/peer/dns-sync"
    body = json.dumps({"zones": []}).encode()
    digest = hashlib.sha256(body).hexdigest()
    headers = cluster_peer.sign_headers(raw, "POST", path, digest, NODE)
    headers["Authorization"] = f"Bearer {raw}"
    with caplog.at_level("WARNING", logger="spanel.cluster"):
        authenticate_peer(_request("POST", path, headers), db, "cluster:write", digest)
    assert "deprecated" in caplog.text

    headers = cluster_peer.sign_headers(raw, "POST", path, digest, NODE)
    headers["Authorization"] = f"Bearer {raw}"
    with pytest.raises(HTTPException) as exc:
        authenticate_peer(_request("POST", path, headers), db, "cluster:transfer", digest)
    assert exc.value.status_code == 403


# --- peer tokens at rest ---------------------------------------------------------

@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    from app.services import multi_server

    path = tmp_path / "multi-server.json"
    monkeypatch.setattr(multi_server, "_CONFIG_PATH", path)
    return path


def test_plaintext_peer_token_is_encrypted_on_first_read(state_file):
    from app.services import multi_server

    state_file.write_text(json.dumps({
        "settings": {"node_name": "n1"},
        "nodes": [{"id": "node_1", "name": "ns2", "url": "https://ns2.example.com:2222",
                   "api_token": "bp_plaintext-token-from-1.0.7"}],
    }), encoding="utf-8")

    with multi_server._state_lock:
        state = multi_server._read_state()
    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    stored = on_disk["nodes"][0]["api_token"]
    assert stored.startswith("fernet:")
    assert "bp_plaintext" not in state_file.read_text(encoding="utf-8")
    assert multi_server.node_token(state["nodes"][0]) == "bp_plaintext-token-from-1.0.7"
    # Nodes from before keep the (unverified) TLS they were added with.
    assert on_disk["nodes"][0]["verify_tls"] is False
    assert len(on_disk["settings"]["node_uid"]) == 16

    sanitized = multi_server._sanitize_node(state["nodes"][0])
    assert "api_token" not in sanitized
    assert sanitized["has_token"] and sanitized["token_masked"].startswith("bp_pla")
    assert sanitized["tls_mode"] == "insecure"


def test_new_node_token_is_stored_encrypted_and_tls_verified(state_file, monkeypatch):
    from app.services import multi_server

    monkeypatch.setattr(multi_server, "ping_peer_node", lambda node_id: {"id": node_id})
    multi_server.add_peer_node({"name": "ns2", "url": "ns2.example.com", "api_token": "bp_new-token"})
    node = json.loads(state_file.read_text(encoding="utf-8"))["nodes"][0]
    assert node["api_token"].startswith("fernet:")
    assert node["verify_tls"] is True
    assert multi_server.node_token(node) == "bp_new-token"


def test_node_uid_is_stable_across_reads(state_file):
    from app.services import multi_server

    first = multi_server.local_node_uid()
    assert first and multi_server.local_node_uid() == first


# --- TLS -------------------------------------------------------------------------

def test_fingerprint_normalisation():
    hexfp = "ab" * 32
    assert cluster_peer.normalize_fingerprint(":".join(["AB"] * 32)) == hexfp
    assert cluster_peer.normalize_fingerprint(" " + hexfp.upper() + " ") == hexfp
    assert cluster_peer.normalize_fingerprint("") == ""
    with pytest.raises(ValueError):
        cluster_peer.normalize_fingerprint("abcd")


def test_tls_context_modes():
    import ssl

    ctx, pin = cluster_peer.ssl_context_for({"verify_tls": True})
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname and pin == ""
    ctx, pin = cluster_peer.ssl_context_for({})  # a node with no setting verifies
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    ctx, pin = cluster_peer.ssl_context_for({"verify_tls": True, "tls_fingerprint": "cd" * 32})
    assert pin == "cd" * 32 and ctx.verify_mode == ssl.CERT_NONE
    ctx, pin = cluster_peer.ssl_context_for({"verify_tls": False})
    assert ctx.verify_mode == ssl.CERT_NONE and pin == ""


def test_pinned_certificate_must_match():
    der = b"certificate-bytes"
    good = hashlib.sha256(der).hexdigest()

    class Sock:
        def getpeercert(self, binary_form=False):
            return der

    conn = SimpleNamespace(sock=Sock())
    cluster_peer._check_pin(conn, good)
    with pytest.raises(cluster_peer.PeerError, match="pinned"):
        cluster_peer._check_pin(conn, "00" * 32)


def test_request_signs_what_it_sends(monkeypatch):
    """The transport sends the bearer token and a signature the receiver's
    verify_signature accepts for exactly that body and path."""
    sent = {}

    class FakeResponse:
        status = 200

        def read(self, limit=None):
            return b'{"ok": true}'

    class FakeConn:
        sock = None

        def connect(self):
            pass

        def request(self, method, path, body=None, headers=None):
            sent.update(method=method, path=path, body=body, headers=headers)

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(cluster_peer, "_connection", lambda *a, **k: FakeConn())
    data, _latency = cluster_peer.request({"url": "https://ns2.example.com:2222", "verify_tls": True},
                                          TOKEN, NODE, "POST", "/api/multi-server/peer/dns-sync", {"zones": []})
    assert data == {"ok": True}
    assert sent["headers"]["Authorization"] == f"Bearer {TOKEN}"
    digest = hashlib.sha256(sent["body"]).hexdigest()
    assert cluster_peer.verify_signature(KEY, "POST", sent["path"], sent["headers"], digest,
                                         cache=cluster_peer.NonceCache()) == NODE


def test_request_surfaces_peer_error_detail(monkeypatch):
    class FakeResponse:
        status = 409

        def read(self, limit=None):
            return b'{"detail": "zone is owned locally"}'

    class FakeConn:
        sock = None

        def connect(self):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(cluster_peer, "_connection", lambda *a, **k: FakeConn())
    with pytest.raises(cluster_peer.PeerError) as exc:
        cluster_peer.request({"url": "https://ns2.example.com:2222"}, TOKEN, NODE, "GET", "/x")
    assert exc.value.status == 409 and "owned locally" in str(exc.value)
