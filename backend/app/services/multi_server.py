from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.secrets import decrypt as _decrypt_secret, encrypt as _encrypt_secret, is_encrypted as _is_encrypted_secret
from app.core.version import APP_VERSION
from app.models.entities import DnsZone, User, Website, WebsiteAlias
from app.services import cluster_peer, dns, panel_settings, server_network, system as system_service

logger = logging.getLogger("spanel.multi_server")

_CONFIG_PATH = Path(
    os.environ.get("SPANEL_MULTI_SERVER_FILE", "/var/lib/spanel/multi-server.json")
)
_FALLBACK_CONFIG_PATH = Path(".spanel_multi_server.json")
_MAX_SYNC_LOGS = 60
_state_lock = threading.Lock()


def _resolve_config_path() -> Path:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if os.access(_CONFIG_PATH.parent, os.W_OK):
            return _CONFIG_PATH
    except OSError:
        pass
    return _FALLBACK_CONFIG_PATH


def _default_hostname() -> str:
    try:
        return socket.gethostname() or "spanel-node-1"
    except Exception:
        return "spanel-node-1"


def _default_state() -> dict[str, Any]:
    return {
        "settings": {
            "enabled": True,
            "node_name": _default_hostname(),
            # This node's identity in the cluster: what a peer records as the
            # origin of a zone it receives. Unlike node_name it never changes.
            "node_uid": "",
            "auto_dns_sync": True,
            "check_domain_conflict": True,
            "check_username_conflict": False,
            # A peer that cannot be asked: refuse the new domain/account
            # (True) or let it through as before (False).
            "conflict_fail_closed": False,
            "health_interval_sec": 60,
            "reconcile_interval_sec": 900,
        },
        "nodes": [],
        "remote_db_hosts": [],
        "sync_logs": [],
    }


def _migrate_state(state: dict[str, Any]) -> bool:
    """Bring a state file written by an older version up to date. True when
    something changed and the file should be written back.

    - Peer API tokens were stored in plaintext; they are encrypted with the
      panel's secret box now, on the first read after the update.
    - A node identity is generated once.
    """
    changed = False
    settings = state.setdefault("settings", {})
    if not str(settings.get("node_uid") or "").strip():
        settings["node_uid"] = secrets.token_hex(8)
        changed = True
    for node in state.get("nodes", []):
        if not isinstance(node, dict):
            continue
        token = str(node.get("api_token") or "").strip()
        if token and not _is_encrypted_secret(token):
            node["api_token"] = _encrypt_secret(token)
            changed = True
        # Nodes added before verification existed keep the behaviour they
        # were added with (no verification) until an administrator changes it.
        if "verify_tls" not in node:
            node["verify_tls"] = False
            changed = True
    return changed


def _read_state() -> dict[str, Any]:
    path = _resolve_config_path()
    base = _default_state()
    try:
        if not path.is_file():
            # Written at once so the node id generated here is the one kept.
            _migrate_state(base)
            _write_state(base)
            return base
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return base
        if isinstance(raw.get("settings"), dict):
            base["settings"].update(raw["settings"])
        if isinstance(raw.get("nodes"), list):
            base["nodes"] = raw["nodes"]
        if isinstance(raw.get("remote_db_hosts"), list):
            base["remote_db_hosts"] = raw["remote_db_hosts"]
        if isinstance(raw.get("sync_logs"), list):
            base["sync_logs"] = raw["sync_logs"][:_MAX_SYNC_LOGS]
        if _migrate_state(base):
            _write_state(base)
        return base
    except Exception as exc:
        logger.warning("Could not read multi-server state: %s", exc)
        return base


def _write_state(state: dict[str, Any]) -> None:
    path = _resolve_config_path()
    try:
        state["sync_logs"] = (state.get("sync_logs") or [])[:_MAX_SYNC_LOGS]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
    except Exception as exc:
        logger.warning("Could not write multi-server state: %s", exc)


def _append_log(state: dict[str, Any], action: str, target: str, status: str, detail: str) -> None:
    logs = state.setdefault("sync_logs", [])
    logs.insert(
        0,
        {
            "id": f"msl_{secrets.token_hex(5)}",
            "action": action,
            "target": target,
            "status": status,
            "detail": detail,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    del logs[_MAX_SYNC_LOGS:]


def _mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 10:
        return raw[:3] + "••••"
    return f"{raw[:6]}••••{raw[-4:]}"


def node_token(node: dict[str, Any]) -> str:
    """The peer's API token in clear, for the moment a request needs it."""
    stored = str(node.get("api_token") or "").strip()
    if not stored:
        return ""
    if not _is_encrypted_secret(stored):
        return stored  # not migrated yet (read before the first write)
    try:
        return _decrypt_secret(stored)
    except Exception:
        logger.warning("Cannot decrypt the API token of cluster node %s (SECRET_KEY changed?)", node.get("id"))
        return ""


def local_node_uid() -> str:
    with _state_lock:
        state = _read_state()
    return str((state.get("settings") or {}).get("node_uid") or "")


def _sanitize_node(node: dict[str, Any]) -> dict[str, Any]:
    out = dict(node)
    out.pop("api_token", None)
    token = node_token(node)
    out["token_masked"] = _mask_secret(token)
    out["has_token"] = bool(str(node.get("api_token") or "").strip())
    out["token_unreadable"] = bool(out["has_token"] and not token)
    pin = str(out.get("tls_fingerprint") or "")
    out["tls_mode"] = (
        "plain_http" if str(out.get("url") or "").startswith("http://")
        else "pinned" if pin
        else "verified" if out.get("verify_tls", True)
        else "insecure"
    )
    return out


def _decrypt_remote_db_password(raw_pw: str) -> str:
    val = (raw_pw or "").strip()
    if not val:
        return ""
    if _is_encrypted_secret(val):
        try:
            return _decrypt_secret(val)
        except Exception:
            return ""
    return val


def _sanitize_remote_db(host: dict[str, Any]) -> dict[str, Any]:
    out = dict(host)
    raw_pwd = str(out.pop("admin_password", "") or "")
    pwd = _decrypt_remote_db_password(raw_pwd)
    out["password_masked"] = _mask_secret(pwd or raw_pwd)
    out["has_password"] = bool(raw_pwd)
    return out


def normalize_peer_url(raw_url: str) -> str:
    cleaned = (raw_url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("URL máy chủ Peer không được để trống.")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        cleaned = f"https://{cleaned}"
    parsed = urllib.parse.urlparse(cleaned)
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost", "localhost.localdomain", "127.0.0.1", "0.0.0.0", "::1"}:
        raise ValueError("URL máy chủ Peer không được trỏ về localhost hoặc chính máy chủ nội bộ (127.0.0.1).")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_unspecified:
            raise ValueError("URL máy chủ Peer không hợp lệ (Loopback/Unspecified IP).")
    except ValueError as exc:
        if "Loopback" in str(exc):
            raise
    port = parsed.port or (2222 if parsed.scheme in {"http", "https"} else 2222)
    return f"{parsed.scheme}://{host}:{port}"


def local_node_summary(db: Session) -> dict[str, Any]:
    """Live metrics and identity of this local SPanel server."""
    res = {}
    try:
        res = system_service.resource_usage()
    except Exception:
        res = {}

    cpu_pct = round(float((res.get("cpu") or {}).get("percent") or 0.0), 1)
    mem_pct = round(float((res.get("memory") or {}).get("percent") or 0.0), 1)
    disk_pct = round(float((res.get("disk") or {}).get("percent") or 0.0), 1)
    load = (res.get("cpu") or {}).get("load") or []
    try:
        backup_free = shutil.disk_usage(app_settings.backup_root).free
    except Exception:
        backup_free = None

    websites_count = db.query(Website).count()
    users_count = db.query(User).count()
    dns_zones_count = db.query(DnsZone).count()
    p_settings = panel_settings.current_settings()
    hostname = p_settings.get("panel_hostname") or _default_hostname()

    with _state_lock:
        state = _read_state()
        node_name = (state.get("settings") or {}).get("node_name") or hostname
        node_uid = (state.get("settings") or {}).get("node_uid") or ""

    return {
        "node_name": node_name,
        "node_uid": node_uid,
        "hostname": hostname,
        "app_name": p_settings.get("app_name") or "SPanel",
        "version": APP_VERSION,
        "cpu_percent": cpu_pct,
        "ram_percent": mem_pct,
        "disk_percent": disk_pct,
        # Absolute figures and load, for placement (suggest_node).
        "cpu_cores": (res.get("cpu") or {}).get("cores"),
        "load_1": load[0] if load else None,
        "ram_available_bytes": (res.get("memory") or {}).get("available"),
        "disk_free_bytes": (res.get("disk") or {}).get("free"),
        "backup_free_bytes": backup_free,
        "websites_count": websites_count,
        "users_count": users_count,
        "dns_zones_count": dns_zones_count,
        "dns_active": dns.active(),
        "ipv4_addresses": server_network.ipv4_addresses(),
        "tls_fingerprint": cluster_peer.local_certificate_fingerprint(os.environ.get("PANEL_SSL_CERT", "")),
    }


def _peer_request(
    node: dict[str, Any],
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 8,
) -> tuple[dict[str, Any], int]:
    """Perform an authenticated, signed HTTP request to a peer SPanel node.

    Raises cluster_peer.PeerError (a RuntimeError) on any failure; its
    `status` is the HTTP status when the peer answered with an error.
    """
    return cluster_peer.request(
        node,
        node_token(node),
        local_node_uid(),
        method,
        path,
        payload,
        timeout=timeout,
    )


def get_cluster_overview(db: Session) -> dict[str, Any]:
    with _state_lock:
        state = _read_state()
    nodes = [_sanitize_node(n) for n in state.get("nodes", [])]
    remote_dbs = [_sanitize_remote_db(h) for h in state.get("remote_db_hosts", [])]
    online_nodes = sum(1 for n in nodes if n.get("status") == "online" and n.get("enabled", True))
    dns_sync_nodes = sum(1 for n in nodes if n.get("sync_dns", True) and n.get("enabled", True))
    return {
        "settings": state.get("settings", _default_state()["settings"]),
        "local_node": local_node_summary(db),
        "nodes": nodes,
        "remote_db_hosts": remote_dbs,
        "sync_logs": state.get("sync_logs", []),
        "summary": {
            "total_nodes": len(nodes),
            "online_nodes": online_nodes,
            "dns_sync_nodes": dns_sync_nodes,
            "remote_db_count": len(remote_dbs),
        },
    }


def update_cluster_settings(payload: dict[str, Any]) -> dict[str, Any]:
    with _state_lock:
        state = _read_state()
        settings = state.setdefault("settings", _default_state()["settings"])
        if "enabled" in payload and payload["enabled"] is not None:
            settings["enabled"] = bool(payload["enabled"])
        if "node_name" in payload and payload["node_name"] is not None:
            name = str(payload["node_name"]).strip()[:80]
            settings["node_name"] = name or _default_hostname()
        if "auto_dns_sync" in payload and payload["auto_dns_sync"] is not None:
            settings["auto_dns_sync"] = bool(payload["auto_dns_sync"])
        if "check_domain_conflict" in payload and payload["check_domain_conflict"] is not None:
            settings["check_domain_conflict"] = bool(payload["check_domain_conflict"])
        if "check_username_conflict" in payload and payload["check_username_conflict"] is not None:
            settings["check_username_conflict"] = bool(payload["check_username_conflict"])
        if "conflict_fail_closed" in payload and payload["conflict_fail_closed"] is not None:
            settings["conflict_fail_closed"] = bool(payload["conflict_fail_closed"])
        if payload.get("health_interval_sec") is not None:
            settings["health_interval_sec"] = max(30, min(3600, int(payload["health_interval_sec"])))
        if payload.get("reconcile_interval_sec") is not None:
            settings["reconcile_interval_sec"] = max(300, min(86400, int(payload["reconcile_interval_sec"])))
        _append_log(
            state,
            "cluster_settings",
            settings["node_name"],
            "success",
            f"Cập nhật cấu hình cụm: Auto DNS Sync={'Bật' if settings['auto_dns_sync'] else 'Tắt'}, Conflict Guard={'Bật' if settings['check_domain_conflict'] else 'Tắt'}",
        )
        _write_state(state)
        return dict(settings)


NODE_ROLES = {"full_peer", "dns_only", "db_node"}
_PEM_CERT_RE = re.compile(r"^-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----$")


def _clean_ca_pem(value: Any) -> str:
    """A CA certificate (PEM) to verify a peer against, or ''."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 20000 or not all(_PEM_CERT_RE.match(block.strip() + "-----END CERTIFICATE-----")
                                    for block in text.split("-----END CERTIFICATE-----") if block.strip()):
        raise ValueError("The CA must be one or more PEM certificates (-----BEGIN CERTIFICATE-----).")
    return text


def add_peer_node(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Vui lòng nhập tên gợi nhớ cho máy chủ Peer (VD: NS2 Node 02).")
    url = normalize_peer_url(str(payload.get("url") or ""))
    api_token = str(payload.get("api_token") or "").strip()
    if not api_token:
        raise ValueError("Vui lòng nhập API Token (bp_...) của máy chủ Peer.")
    role = str(payload.get("role") or "full_peer").strip().lower()
    if role not in NODE_ROLES:
        role = "full_peer"

    node_id = f"node_{secrets.token_hex(4)}"
    new_node = {
        "id": node_id,
        "name": name,
        "url": url,
        "api_token": _encrypt_secret(api_token),
        "role": role,
        "verify_tls": bool(payload.get("verify_tls", True)),
        "tls_fingerprint": cluster_peer.normalize_fingerprint(str(payload.get("tls_fingerprint") or "")),
        "ca_pem": _clean_ca_pem(payload.get("ca_pem")),
        "sync_dns": bool(payload.get("sync_dns", True)),
        "check_domain": bool(payload.get("check_domain", True)),
        "enabled": bool(payload.get("enabled", True)),
        "status": "unknown",
        "latency_ms": None,
        "last_seen_at": None,
        "last_sync_at": None,
        "last_error": "",
        "remote_info": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with _state_lock:
        state = _read_state()
        if any(n.get("url") == url for n in state.get("nodes", [])):
            raise ValueError(f"Máy chủ Peer có địa chỉ {url} đã tồn tại trong danh sách cụm.")
        state.setdefault("nodes", []).append(new_node)
        _append_log(state, "add_node", f"{name} ({url})", "info", "Đã thêm máy chủ Peer vào cụm.")
        _write_state(state)

    # Immediately test connection
    try:
        return ping_peer_node(node_id)
    except Exception:
        with _state_lock:
            state = _read_state()
            found = next((n for n in state.get("nodes", []) if n.get("id") == node_id), new_node)
            return _sanitize_node(found)


def update_peer_node(node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _state_lock:
        state = _read_state()
        node = next((n for n in state.get("nodes", []) if n.get("id") == node_id), None)
        if not node:
            raise ValueError("Không tìm thấy máy chủ Peer này.")

        if "name" in payload and payload["name"] is not None:
            name = str(payload["name"]).strip()[:80]
            if name:
                node["name"] = name
        if "url" in payload and payload["url"] is not None:
            url = normalize_peer_url(str(payload["url"]))
            if any(n.get("url") == url and n.get("id") != node_id for n in state.get("nodes", [])):
                raise ValueError(f"Địa chỉ {url} đã được sử dụng bởi một Node khác.")
            node["url"] = url
        if "api_token" in payload and payload["api_token"] and str(payload["api_token"]).strip():
            node["api_token"] = _encrypt_secret(str(payload["api_token"]).strip())
        if "role" in payload and payload["role"] in NODE_ROLES:
            node["role"] = payload["role"]
        if "verify_tls" in payload and payload["verify_tls"] is not None:
            node["verify_tls"] = bool(payload["verify_tls"])
        if "tls_fingerprint" in payload and payload["tls_fingerprint"] is not None:
            node["tls_fingerprint"] = cluster_peer.normalize_fingerprint(str(payload["tls_fingerprint"]))
        if "ca_pem" in payload and payload["ca_pem"] is not None:
            node["ca_pem"] = _clean_ca_pem(payload["ca_pem"])
        if "sync_dns" in payload and payload["sync_dns"] is not None:
            node["sync_dns"] = bool(payload["sync_dns"])
        if "check_domain" in payload and payload["check_domain"] is not None:
            node["check_domain"] = bool(payload["check_domain"])
        if "enabled" in payload and payload["enabled"] is not None:
            node["enabled"] = bool(payload["enabled"])

        _write_state(state)
    return ping_peer_node(node_id)


def delete_peer_node(node_id: str) -> None:
    with _state_lock:
        state = _read_state()
        nodes = state.get("nodes", [])
        target = next((n for n in nodes if n.get("id") == node_id), None)
        if not target:
            raise ValueError("Không tìm thấy máy chủ Peer này.")
        state["nodes"] = [n for n in nodes if n.get("id") != node_id]
        _append_log(state, "remove_node", f"{target.get('name')} ({target.get('url')})", "info", "Đã gỡ bỏ máy chủ Peer khỏi cụm.")
        _write_state(state)


def ping_peer_node(node_id: str) -> dict[str, Any]:
    with _state_lock:
        state = _read_state()
        node = next((n for n in state.get("nodes", []) if n.get("id") == node_id), None)
        if not node:
            raise ValueError("Không tìm thấy máy chủ Peer này.")
        node_copy = dict(node)

    try:
        remote_data, latency_ms = _peer_request(node_copy, "GET", "/api/multi-server/peer/ping", timeout=7)
        status = "online"
        last_error = ""
    except Exception as exc:
        remote_data = {}
        latency_ms = None
        status = "offline"
        last_error = str(exc)

    with _state_lock:
        state = _read_state()
        node = next((n for n in state.get("nodes", []) if n.get("id") == node_id), None)
        if not node:
            raise ValueError("Không tìm thấy máy chủ Peer này.")
        node["status"] = status
        node["latency_ms"] = latency_ms
        node["last_error"] = last_error
        if status == "online":
            node["last_seen_at"] = datetime.now(timezone.utc).isoformat()
            node["remote_info"] = {
                "node_name": remote_data.get("node_name") or remote_data.get("hostname") or "",
                "hostname": remote_data.get("hostname") or "",
                "version": remote_data.get("version") or "",
                "cpu_percent": remote_data.get("cpu_percent"),
                "ram_percent": remote_data.get("ram_percent"),
                "disk_percent": remote_data.get("disk_percent"),
                "websites_count": remote_data.get("websites_count"),
                "users_count": remote_data.get("users_count"),
                "dns_zones_count": remote_data.get("dns_zones_count"),
                "dns_active": remote_data.get("dns_active"),
                "ipv4_addresses": remote_data.get("ipv4_addresses") or [],
            }
        _write_state(state)
        return _sanitize_node(node)


def ping_all_peer_nodes() -> list[dict[str, Any]]:
    with _state_lock:
        state = _read_state()
        node_ids = [n.get("id") for n in state.get("nodes", []) if n.get("id")]
    if not node_ids:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(5, len(node_ids))) as pool:
        futures = [pool.submit(ping_peer_node, nid) for nid in node_ids]
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:
                logger.warning("Ping node error: %s", exc)
    return results


# --- Multi-Server DNS Zone Synchronization ------------------------------------
# The mechanics (outbox, origins, serials, reconcile) are in cluster_dns; these
# are the entry points dns.py and the API have always called.

def append_sync_log(action: str, target: str, status: str, detail: str) -> None:
    with _state_lock:
        state = _read_state()
        _append_log(state, action, target, status, detail)
        _write_state(state)


def record_node_sync(node_id: str, *, ok: bool, error: str = "") -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with _state_lock:
        state = _read_state()
        node = next((n for n in state.get("nodes", []) if n.get("id") == node_id), None)
        if not node:
            return
        if ok:
            node["last_sync_at"] = now_iso
            node["last_seen_at"] = now_iso
            node["last_sync_error"] = ""
        else:
            node["last_sync_error"] = (error or "")[:500]
        _write_state(state)


def sync_dns_to_peers(db: Session, zone_name: Optional[str] = None, action: str = "upsert") -> dict[str, Any]:
    """Queue one zone (or every zone this node is master of) for the DNS peers
    and deliver it now. What a peer does not take stays queued and is retried."""
    from app.services import cluster_dns

    with _state_lock:
        state = _read_state()
        if not (state.get("settings") or {}).get("enabled", True):
            return {"synced_nodes": 0, "failed_nodes": 0, "zones_count": 0, "details": [], "message": "Multi-Server Cluster đang tắt."}
    peers = cluster_dns._sync_peers()
    if not peers:
        return {"synced_nodes": 0, "failed_nodes": 0, "zones_count": 0, "details": [], "message": "Chưa có máy chủ Peer nào bật đồng bộ DNS."}
    if action == "delete" and zone_name:
        cluster_dns.note_local_change(db, zone_name, "delete")
        zones_count = 1
    else:
        queued = cluster_dns.queue_all(db, zone_name)
        zones_count = queued // max(1, len(peers))
    result = cluster_dns.process_outbox(db)
    failed = sum(1 for d in result.get("details", []) if d.get("errors"))
    synced = len(peers) - failed
    message = (
        f"Đã đồng bộ {zones_count} DNS zone sang {synced}/{len(peers)} máy chủ Peer."
        + (f" {result['retrying']} zone sẽ được thử lại tự động." if result.get("retrying") else "")
        + (f" {result['conflicts']} zone bị từ chối do xung đột quyền sở hữu." if result.get("conflicts") else "")
    )
    return {
        "synced_nodes": synced,
        "failed_nodes": failed,
        "zones_count": zones_count,
        "details": result.get("details", []),
        "dnssec_skipped": sorted(set(result.get("dnssec_skipped") or [])),
        "message": message,
    }


def trigger_background_zone_sync(zone_name: str, action: str = "upsert") -> None:
    """A local zone changed (called from dns.py). Records that this node is the
    zone's master and queues it for the peers in the durable outbox; the
    cluster worker delivers it. Never raises: DNS editing must not fail
    because of the cluster."""
    try:
        with _state_lock:
            state = _read_state()
        settings = state.get("settings") or {}
        push = bool(settings.get("enabled", True) and settings.get("auto_dns_sync", True))
        from app.core.database import SessionLocal
        from app.services import cluster_dns, cluster_worker

        with SessionLocal() as db:
            queued = cluster_dns.note_local_change(db, zone_name, action, push=push)
        if queued:
            cluster_worker.kick()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("Could not queue cluster DNS sync for %s: %s", zone_name, exc)


def apply_incoming_dns_sync(db: Session, zones_payload: list[dict[str, Any]], source_node: str = "", source_uid: str = "") -> dict[str, Any]:
    """Receive DNS zones from a peer node (see cluster_dns.apply_incoming for the rules)."""
    from app.services import cluster_dns

    return cluster_dns.apply_incoming(db, zones_payload, source_uid=source_uid, source_name=source_node)


# --- Cross-Server Domain & Username Conflict Check ----------------------------

def check_local_conflict(db: Session, domain: str = "", username: str = "", asking_node: str = "") -> dict[str, Any]:
    domain_clean = (domain or "").strip().lower().rstrip(".")
    username_clean = (username or "").strip().lower()
    domain_exists = False
    username_exists = False

    if domain_clean:
        candidates = {domain_clean, f"www.{domain_clean}"}
        if db.query(Website).filter(Website.domain.in_(list(candidates))).first():
            domain_exists = True
        elif db.query(WebsiteAlias).filter(WebsiteAlias.domain.in_(list(candidates))).first():
            domain_exists = True
        elif db.query(DnsZone).filter(DnsZone.name == domain_clean).first():
            # A zone here that is only our copy of the asking node's own zone
            # is not a conflict with it.
            from app.services import cluster_dns

            row = cluster_dns.origin_row(db, domain_clean)
            domain_exists = not (asking_node and row is not None and row.origin_uid == asking_node)

    if username_clean:
        if db.query(User).filter(User.username == username_clean).first():
            username_exists = True

    return {
        "domain_exists": domain_exists,
        "username_exists": username_exists,
        "node_hostname": _default_hostname(),
    }


def check_cluster_conflict(domain: str = "", username: str = "", *, fail_closed: Optional[bool] = None) -> dict[str, Any]:
    """Ask the peer nodes whether a domain or username already exists there.

    A peer that cannot be asked used to be skipped silently, so the same
    domain or account could be created on two nodes while one was down. With
    conflict_fail_closed on, that answer is a conflict of kind "unreachable"
    instead (HTTP 503 at the API). The peers are asked in parallel.
    """
    with _state_lock:
        state = _read_state()
        settings = state.get("settings") or {}
        if not settings.get("enabled", True):
            return {"conflict": False}
        check_dom = bool(domain and settings.get("check_domain_conflict", True))
        check_usr = bool(username and settings.get("check_username_conflict", False))
        if not check_dom and not check_usr:
            return {"conflict": False}
        if fail_closed is None:
            fail_closed = bool(settings.get("conflict_fail_closed", False))
        peers = [
            dict(n)
            for n in state.get("nodes", [])
            if n.get("enabled", True) and n.get("check_domain", True)
        ]

    if not peers:
        return {"conflict": False}

    qs = urllib.parse.urlencode(
        {
            "domain": domain if check_dom else "",
            "username": username if check_usr else "",
        }
    )

    def ask(peer: dict[str, Any]) -> tuple[dict[str, Any], Optional[dict[str, Any]], str]:
        try:
            resp, _ = _peer_request(peer, "GET", f"/api/multi-server/peer/check-conflict?{qs}", timeout=4)
            return peer, resp, ""
        except Exception as exc:  # noqa: BLE001 - reported below
            return peer, None, str(exc)

    unreachable: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, len(peers))) as pool:
        answers = list(pool.map(ask, peers))
    for peer, resp, error in answers:
        if resp is None:
            logger.warning("Cluster conflict check could not ask %s: %s", peer.get("name"), error)
            unreachable.append(str(peer.get("name") or peer.get("url")))
            continue
        if check_dom and resp.get("domain_exists"):
            return {
                "conflict": True,
                "kind": "domain",
                "node_name": peer.get("name"),
                "node_url": peer.get("url"),
                "message": f"Tên miền '{domain}' đã tồn tại trên máy chủ '{peer.get('name')}' trong cụm Multi-Server.",
            }
        if check_usr and resp.get("username_exists"):
            return {
                "conflict": True,
                "kind": "username",
                "node_name": peer.get("name"),
                "node_url": peer.get("url"),
                "message": f"Tài khoản '{username}' đã tồn tại trên máy chủ '{peer.get('name')}' trong cụm Multi-Server.",
            }

    if unreachable and fail_closed:
        return {
            "conflict": True,
            "kind": "unreachable",
            "nodes": unreachable,
            "message": (
                f"Không thể kiểm tra trùng lặp trên máy chủ Peer: {', '.join(unreachable)}. "
                "Cụm đang ở chế độ an toàn (fail-closed) nên thao tác bị chặn cho tới khi máy chủ đó phản hồi."
            ),
        }
    return {"conflict": False, "unreachable": unreachable}


def conflict_http_status(result: dict[str, Any]) -> int:
    """409 for a real duplicate, 503 when a peer could not be asked (fail-closed)."""
    return 503 if result.get("kind") == "unreachable" else 409


# --- Remote Database Hosts (Remote MySQL / MariaDB Nodes) ---------------------

_DB_IDENT_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_RESERVED_REMOTE_DB_USERS = frozenset({"root", "mysql", "mariadb.sys", "debian-sys-maint", "spanel"})


def _validate_remote_db_identifier(value: str, label: str = "identifier") -> str:
    cleaned = (value or "").strip().lower()
    if not _DB_IDENT_RE.match(cleaned):
        raise ValueError(f"Invalid remote database {label}: '{value}'")
    return cleaned


def _probe_mysql_banner(host: str, port: int, timeout: float = 4.0) -> tuple[int, str]:
    """Connect to a remote MySQL/MariaDB TCP port and parse its initial handshake version."""
    started = time.perf_counter()
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        raw = sock.recv(256)
    latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
    version_str = "MySQL / MariaDB"
    if len(raw) > 5 and raw[4] == 10:
        end_idx = raw.find(b"\x00", 5)
        if end_idx != -1:
            parsed_ver = raw[5:end_idx].decode("ascii", errors="replace").strip()
            if parsed_ver:
                version_str = parsed_ver
    return latency_ms, version_str


def _open_remote_mysql_connection(entry: dict[str, Any], timeout: int = 5):
    """Open an authenticated PyMySQL connection to a remote MySQL/MariaDB node."""
    import pymysql

    host = str(entry.get("host") or "").strip()
    port = int(entry.get("port") or 3306)
    admin_user = str(entry.get("admin_user") or "root").strip()
    admin_password = _decrypt_remote_db_password(str(entry.get("admin_password") or ""))
    use_tls = bool(entry.get("use_tls", False))

    kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "user": admin_user,
        "password": admin_password,
        "connect_timeout": timeout,
        "read_timeout": timeout,
        "write_timeout": timeout,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if use_tls:
        kwargs["ssl"] = {"ssl": {}}
    return pymysql.connect(**kwargs)


def add_remote_db_host(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:80]
    host = str(payload.get("host") or "").strip().lower()
    port = int(payload.get("port") or 3306)
    admin_user = str(payload.get("admin_user") or "root").strip()[:64]
    admin_password = str(payload.get("admin_password") or "").strip()

    if not name or not host:
        raise ValueError("Vui lòng nhập Tên gợi nhớ và Địa chỉ IP/Hostname của máy chủ Database.")
    if not (1 <= port <= 65535):
        raise ValueError("Cổng MySQL/MariaDB phải nằm trong khoảng 1 – 65535.")

    stored_password = (
        _encrypt_secret(admin_password)
        if admin_password and not _is_encrypted_secret(admin_password)
        else admin_password
    )

    host_id = f"rdb_{secrets.token_hex(4)}"
    entry = {
        "id": host_id,
        "name": name,
        "host": host,
        "port": port,
        "admin_user": admin_user,
        "admin_password": stored_password,
        "use_tls": bool(payload.get("use_tls", False)),
        "enabled": bool(payload.get("enabled", True)),
        "status": "unknown",
        "latency_ms": None,
        "server_version": "",
        "last_checked_at": None,
        "last_error": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with _state_lock:
        state = _read_state()
        if any(h.get("host") == host and int(h.get("port", 3306)) == port for h in state.get("remote_db_hosts", [])):
            raise ValueError(f"Máy chủ Database {host}:{port} đã tồn tại trong danh sách.")
        state.setdefault("remote_db_hosts", []).append(entry)
        _append_log(state, "add_remote_db", f"{name} ({host}:{port})", "info", "Đã thêm cấu hình Remote MySQL/MariaDB Node (mã hóa mật khẩu Fernet AES).")
        _write_state(state)

    return test_remote_db_host(host_id)


def test_remote_db_host(host_id: str) -> dict[str, Any]:
    with _state_lock:
        state = _read_state()
        entry = next((h for h in state.get("remote_db_hosts", []) if h.get("id") == host_id), None)
        if not entry:
            raise ValueError("Không tìm thấy máy chủ Remote Database này.")
        entry_copy = dict(entry)
        host = str(entry.get("host") or "")
        port = int(entry.get("port") or 3306)

    try:
        has_pw = bool(_decrypt_remote_db_password(str(entry_copy.get("admin_password") or "")))
        if has_pw:
            try:
                started = time.perf_counter()
                conn = _open_remote_mysql_connection(entry_copy, timeout=4)
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT VERSION()")
                        row = cur.fetchone()
                        version_str = str(row[0]) if row and row[0] else "MySQL / MariaDB"
                finally:
                    conn.close()
                latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
            except ImportError:
                latency_ms, version_str = _probe_mysql_banner(host, port)
        else:
            latency_ms, version_str = _probe_mysql_banner(host, port)
        status = "online"
        last_error = ""
    except Exception as exc:
        latency_ms = None
        version_str = ""
        status = "offline"
        last_error = str(exc)

    with _state_lock:
        state = _read_state()
        entry = next((h for h in state.get("remote_db_hosts", []) if h.get("id") == host_id), None)
        if not entry:
            raise ValueError("Không tìm thấy máy chủ Remote Database này.")
        entry["status"] = status
        entry["latency_ms"] = latency_ms
        entry["server_version"] = version_str
        entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        entry["last_error"] = last_error
        _write_state(state)
        return _sanitize_remote_db(entry)


def get_active_remote_db_host(host_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Return an active Remote DB host configuration (with decrypted admin_password) for database provisioning."""
    with _state_lock:
        state = _read_state()
        hosts = state.get("remote_db_hosts", [])
        target = None
        if host_id and host_id != "auto":
            target = next((h for h in hosts if h.get("id") == host_id and h.get("enabled", True)), None)
        elif host_id == "auto":
            target = next((h for h in hosts if h.get("enabled", True)), None)
        if not target:
            return None
        out = dict(target)
        out["admin_password_plain"] = _decrypt_remote_db_password(str(out.get("admin_password") or ""))
        return out


def provision_remote_database(
    host_entry: dict[str, Any],
    db_name: str,
    db_user: str,
    db_password: str,
    allowed_client_host: str = "%",
) -> dict[str, Any]:
    """Connect to a remote MySQL/MariaDB server via PyMySQL (or mysql CLI fallback) and provision database + user."""
    safe_db = _validate_remote_db_identifier(db_name, "db_name")
    safe_user = _validate_remote_db_identifier(db_user, "db_user")
    if safe_user in _RESERVED_REMOTE_DB_USERS:
        raise ValueError(f"'{safe_user}' is a reserved database account and cannot be provisioned.")
    client_host = (allowed_client_host or "%").strip()
    if any(ch in client_host for ch in ("'", '"', ";", "`", "\\")):
        raise ValueError("Invalid allowed_client_host")

    host = str(host_entry.get("host") or "").strip()
    port = int(host_entry.get("port") or 3306)
    admin_user = str(host_entry.get("admin_user") or "root").strip()
    admin_pw = str(
        host_entry.get("admin_password_plain")
        or _decrypt_remote_db_password(str(host_entry.get("admin_password") or ""))
    )

    try:
        conn = _open_remote_mysql_connection(host_entry, timeout=8)
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS `{safe_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (safe_user, client_host, db_password))
                cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (safe_user, client_host, db_password))
                cur.execute(f"GRANT ALL PRIVILEGES ON `{safe_db}`.* TO %s@%s", (safe_user, client_host))
                cur.execute("FLUSH PRIVILEGES")
        finally:
            conn.close()
    except ImportError:
        escaped_pw = db_password.replace("\\", "\\\\").replace("'", "''")
        sql = (
            f"CREATE DATABASE IF NOT EXISTS `{safe_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER IF NOT EXISTS '{safe_user}'@'{client_host}' IDENTIFIED BY '{escaped_pw}';\n"
            f"ALTER USER '{safe_user}'@'{client_host}' IDENTIFIED BY '{escaped_pw}';\n"
            f"GRANT ALL PRIVILEGES ON `{safe_db}`.* TO '{safe_user}'@'{client_host}';\n"
            "FLUSH PRIVILEGES;\n"
        )
        env = os.environ.copy()
        if admin_pw:
            env["MYSQL_PWD"] = admin_pw
        cmd = ["mysql", "-h", host, "-P", str(port), "-u", admin_user]
        if host_entry.get("use_tls"):
            cmd.append("--ssl")
        subprocess.run(cmd, input=sql, text=True, capture_output=True, env=env, timeout=10, check=True)

    with _state_lock:
        state = _read_state()
        _append_log(
            state,
            "provision_remote_db",
            f"{safe_db}@{host}:{port}",
            "success",
            f"Đã khởi tạo CSDL '{safe_db}' và user '{safe_user}' trên Remote MySQL {host}:{port}.",
        )
        _write_state(state)

    return {
        "host_id": host_entry.get("id"),
        "host": host,
        "port": port,
        "db_name": safe_db,
        "db_user": safe_user,
    }


def drop_remote_database(
    host_entry: dict[str, Any],
    db_name: str,
    db_user: str,
    allowed_client_host: str = "%",
) -> dict[str, Any]:
    """Drop a database and user on a remote MySQL/MariaDB server."""
    safe_db = _validate_remote_db_identifier(db_name, "db_name")
    safe_user = _validate_remote_db_identifier(db_user, "db_user")
    if safe_user in _RESERVED_REMOTE_DB_USERS:
        raise ValueError(f"'{safe_user}' is a reserved database account and cannot be dropped.")
    client_host = (allowed_client_host or "%").strip()
    if any(ch in client_host for ch in ("'", '"', ";", "`", "\\")):
        raise ValueError("Invalid allowed_client_host")

    host = str(host_entry.get("host") or "").strip()
    port = int(host_entry.get("port") or 3306)
    admin_user = str(host_entry.get("admin_user") or "root").strip()
    admin_pw = str(
        host_entry.get("admin_password_plain")
        or _decrypt_remote_db_password(str(host_entry.get("admin_password") or ""))
    )

    try:
        conn = _open_remote_mysql_connection(host_entry, timeout=8)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{safe_db}`")
                cur.execute("DROP USER IF EXISTS %s@%s", (safe_user, client_host))
                cur.execute("FLUSH PRIVILEGES")
        finally:
            conn.close()
    except ImportError:
        sql = (
            f"DROP DATABASE IF EXISTS `{safe_db}`;\n"
            f"DROP USER IF EXISTS '{safe_user}'@'{client_host}';\n"
            "FLUSH PRIVILEGES;\n"
        )
        env = os.environ.copy()
        if admin_pw:
            env["MYSQL_PWD"] = admin_pw
        cmd = ["mysql", "-h", host, "-P", str(port), "-u", admin_user]
        subprocess.run(cmd, input=sql, text=True, capture_output=True, env=env, timeout=10, check=True)

    return {"host_id": host_entry.get("id"), "host": host, "port": port, "dropped_db": safe_db}


def delete_remote_db_host(host_id: str) -> None:
    with _state_lock:
        state = _read_state()
        hosts = state.get("remote_db_hosts", [])
        target = next((h for h in hosts if h.get("id") == host_id), None)
        if not target:
            raise ValueError("Không tìm thấy máy chủ Remote Database này.")
        state["remote_db_hosts"] = [h for h in hosts if h.get("id") != host_id]
        _append_log(
            state,
            "remove_remote_db",
            f"{target.get('name')} ({target.get('host')}:{target.get('port')})",
            "info",
            "Đã xóa máy chủ Remote Database.",
        )
        _write_state(state)
