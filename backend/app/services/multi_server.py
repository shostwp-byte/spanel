from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.version import APP_VERSION
from app.models.entities import DnsZone, User, Website, WebsiteAlias
from app.services import dns, panel_settings, resources as resources_service, server_network

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
            "auto_dns_sync": True,
            "check_domain_conflict": True,
            "check_username_conflict": False,
        },
        "nodes": [],
        "remote_db_hosts": [],
        "sync_logs": [],
    }


def _read_state() -> dict[str, Any]:
    path = _resolve_config_path()
    base = _default_state()
    try:
        if not path.is_file():
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


def _sanitize_node(node: dict[str, Any]) -> dict[str, Any]:
    out = dict(node)
    token = str(out.pop("api_token", "") or "")
    out["token_masked"] = _mask_secret(token)
    out["has_token"] = bool(token)
    return out


def _sanitize_remote_db(host: dict[str, Any]) -> dict[str, Any]:
    out = dict(host)
    pwd = str(out.pop("admin_password", "") or "")
    out["password_masked"] = _mask_secret(pwd)
    out["has_password"] = bool(pwd)
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
        res = resources_service.system_resources()
    except Exception:
        res = {}

    cpu_pct = round(float((res.get("cpu") or {}).get("percent") or 0.0), 1)
    mem_pct = round(float((res.get("memory") or {}).get("percent") or 0.0), 1)
    disk_pct = round(float((res.get("disk") or {}).get("percent") or 0.0), 1)

    websites_count = db.query(Website).count()
    users_count = db.query(User).count()
    dns_zones_count = db.query(DnsZone).count()
    p_settings = panel_settings.current_settings()
    hostname = p_settings.get("panel_hostname") or _default_hostname()

    with _state_lock:
        state = _read_state()
        node_name = (state.get("settings") or {}).get("node_name") or hostname

    return {
        "node_name": node_name,
        "hostname": hostname,
        "app_name": p_settings.get("app_name") or "SPanel",
        "version": APP_VERSION,
        "cpu_percent": cpu_pct,
        "ram_percent": mem_pct,
        "disk_percent": disk_pct,
        "websites_count": websites_count,
        "users_count": users_count,
        "dns_zones_count": dns_zones_count,
        "dns_active": dns.active(),
        "ipv4_addresses": server_network.ipv4_addresses(),
    }


def _peer_request(
    node: dict[str, Any],
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 8,
) -> tuple[dict[str, Any], int]:
    """Perform an authenticated HTTP request to a peer SPanel node."""
    base_url = str(node.get("url") or "").rstrip("/")
    token = str(node.get("api_token") or "").strip()
    verify_tls = bool(node.get("verify_tls", False))

    url = f"{base_url}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": f"SPanel-Cluster/{APP_VERSION}",
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())

    ctx = None
    if url.startswith("https://") and not verify_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            latency_ms = max(1, int(round((time.perf_counter() - started) * 1000)))
            data = json.loads(raw) if raw.strip() else {}
            return (data if isinstance(data, dict) else {"data": data}), latency_ms
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            err_json = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = str(err_json.get("detail") or err_json.get("message") or "")
        except Exception:
            pass
        raise RuntimeError(detail or f"Peer trả về mã lỗi HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"Không thể kết nối tới {base_url}: {exc}") from exc


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
        _append_log(
            state,
            "cluster_settings",
            settings["node_name"],
            "success",
            f"Cập nhật cấu hình cụm: Auto DNS Sync={'Bật' if settings['auto_dns_sync'] else 'Tắt'}, Conflict Guard={'Bật' if settings['check_domain_conflict'] else 'Tắt'}",
        )
        _write_state(state)
        return dict(settings)


def add_peer_node(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("Vui lòng nhập tên gợi nhớ cho máy chủ Peer (VD: NS2 Node 02).")
    url = normalize_peer_url(str(payload.get("url") or ""))
    api_token = str(payload.get("api_token") or "").strip()
    if not api_token:
        raise ValueError("Vui lòng nhập API Token (bp_...) của máy chủ Peer.")
    role = str(payload.get("role") or "full_peer").strip().lower()
    if role not in {"full_peer", "dns_only", "db_node"}:
        role = "full_peer"

    node_id = f"node_{secrets.token_hex(4)}"
    new_node = {
        "id": node_id,
        "name": name,
        "url": url,
        "api_token": api_token,
        "role": role,
        "verify_tls": bool(payload.get("verify_tls", False)),
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
            node["api_token"] = str(payload["api_token"]).strip()
        if "role" in payload and payload["role"] in {"full_peer", "dns_only", "db_node"}:
            node["role"] = payload["role"]
        if "verify_tls" in payload and payload["verify_tls"] is not None:
            node["verify_tls"] = bool(payload["verify_tls"])
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

def _collect_zones_payload(db: Session, zone_name: Optional[str] = None, action: str = "upsert") -> list[dict[str, Any]]:
    cfg = dns.settings()
    nameservers = cfg.get("nameservers") or []
    default_ttl = int(cfg.get("ttl") or 3600)

    if action == "delete" and zone_name:
        return [{"zone": dns.normalize_zone(zone_name), "action": "delete", "records": [], "nameservers": nameservers, "ttl": default_ttl}]

    if zone_name:
        normalized = dns.normalize_zone(zone_name)
        zone_names = [normalized]
    else:
        zone_names = [row.name for row in db.query(DnsZone).order_by(DnsZone.name).all()]

    payload_items: list[dict[str, Any]] = []
    for zname in zone_names:
        try:
            recs = dns.records(zname)
            payload_items.append(
                {
                    "zone": zname,
                    "action": "upsert",
                    "nameservers": nameservers,
                    "ttl": default_ttl,
                    "records": recs,
                }
            )
        except Exception as exc:
            logger.warning("Could not read local DNS zone %s for cluster sync: %s", zname, exc)
    return payload_items


def sync_dns_to_peers(db: Session, zone_name: Optional[str] = None, action: str = "upsert") -> dict[str, Any]:
    """Push local DNS zones & records to all active DNS peer nodes in the cluster."""
    with _state_lock:
        state = _read_state()
        cluster_settings = state.get("settings") or {}
        if not cluster_settings.get("enabled", True):
            return {"synced_nodes": 0, "failed_nodes": 0, "zones_count": 0, "details": [], "message": "Multi-Server Cluster đang tắt."}
        active_peers = [
            dict(n)
            for n in state.get("nodes", [])
            if n.get("enabled", True) and n.get("sync_dns", True)
        ]
        source_node = cluster_settings.get("node_name") or _default_hostname()

    if not active_peers:
        return {"synced_nodes": 0, "failed_nodes": 0, "zones_count": 0, "details": [], "message": "Chưa có máy chủ Peer nào bật đồng bộ DNS."}

    zones_payload = _collect_zones_payload(db, zone_name=zone_name, action=action)
    if not zones_payload:
        return {"synced_nodes": 0, "failed_nodes": 0, "zones_count": 0, "details": [], "message": "Không có DNS Zone nào cần đồng bộ."}

    synced_nodes = 0
    failed_nodes = 0
    details = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for peer in active_peers:
        try:
            resp, latency_ms = _peer_request(
                peer,
                "POST",
                "/api/multi-server/peer/dns-sync",
                payload={"source_node": source_node, "zones": zones_payload},
                timeout=15,
            )
            synced_nodes += 1
            details.append(
                {
                    "node_id": peer["id"],
                    "node_name": peer["name"],
                    "status": "success",
                    "latency_ms": latency_ms,
                    "zones_applied": resp.get("applied", len(zones_payload)),
                }
            )
            with _state_lock:
                st = _read_state()
                target_n = next((n for n in st.get("nodes", []) if n.get("id") == peer["id"]), None)
                if target_n:
                    target_n["status"] = "online"
                    target_n["latency_ms"] = latency_ms
                    target_n["last_seen_at"] = now_iso
                    target_n["last_sync_at"] = now_iso
                    target_n["last_error"] = ""
                _write_state(st)
        except Exception as exc:
            failed_nodes += 1
            err_msg = str(exc)
            details.append(
                {
                    "node_id": peer["id"],
                    "node_name": peer["name"],
                    "status": "failed",
                    "error": err_msg,
                }
            )
            with _state_lock:
                st = _read_state()
                target_n = next((n for n in st.get("nodes", []) if n.get("id") == peer["id"]), None)
                if target_n:
                    target_n["last_error"] = err_msg
                _write_state(st)

    with _state_lock:
        st = _read_state()
        target_label = zone_name or f"{len(zones_payload)} zones"
        _append_log(
            st,
            f"dns_sync_{action}",
            target_label,
            "success" if failed_nodes == 0 else "warn",
            f"Đồng bộ {len(zones_payload)} DNS zone tới {synced_nodes}/{len(active_peers)} máy chủ Peer.",
        )
        _write_state(st)

    return {
        "synced_nodes": synced_nodes,
        "failed_nodes": failed_nodes,
        "zones_count": len(zones_payload),
        "details": details,
        "message": f"Đã đồng bộ {len(zones_payload)} DNS zone sang {synced_nodes}/{len(active_peers)} máy chủ Peer.",
    }


def trigger_background_zone_sync(zone_name: str, action: str = "upsert") -> None:
    """Non-blocking background DNS zone push to cluster peers when a local zone changes."""
    with _state_lock:
        state = _read_state()
        settings = state.get("settings") or {}
        if not (settings.get("enabled", True) and settings.get("auto_dns_sync", True)):
            return
        has_peers = any(n.get("enabled", True) and n.get("sync_dns", True) for n in state.get("nodes", []))
    if not has_peers:
        return

    def _worker() -> None:
        from app.core.database import SessionLocal

        try:
            with SessionLocal() as db:
                sync_dns_to_peers(db, zone_name=zone_name, action=action)
        except Exception as exc:
            logger.warning("Background cluster DNS sync failed for %s: %s", zone_name, exc)

    threading.Thread(target=_worker, name=f"spanel-cluster-dns-{zone_name}", daemon=True).start()


def apply_incoming_dns_sync(db: Session, zones_payload: list[dict[str, Any]], source_node: str = "") -> dict[str, Any]:
    """Receive DNS zones & records from a peer SPanel node and apply them to local PowerDNS."""
    if not dns.active():
        raise ValueError("Add-on DNS Manager (PowerDNS) chưa được cài đặt hoặc bật trên máy chủ Peer này.")

    applied = 0
    deleted = 0
    errors: list[str] = []

    for item in zones_payload or []:
        if not isinstance(item, dict):
            continue
        raw_zone = str(item.get("zone") or "").strip()
        action = str(item.get("action") or "upsert").strip().lower()
        if not raw_zone:
            continue
        try:
            zone = dns.normalize_zone(raw_zone)
            if action == "delete":
                dns.delete_zone(db, zone, sync_cluster=False)
                deleted += 1
                continue

            local_cfg = dns.settings()
            nameservers = [
                str(ns).strip().lower().rstrip(".")
                for ns in (item.get("nameservers") or local_cfg.get("nameservers") or [])
                if str(ns).strip()
            ]
            if len(nameservers) < 2:
                nameservers = local_cfg.get("nameservers") or [f"ns1.{zone}", f"ns2.{zone}"]

            ttl = int(item.get("ttl") or local_cfg.get("ttl") or dns.DEFAULT_TTL)
            apex = dns._absolute(zone)
            rrsets = [
                {
                    "name": apex,
                    "type": "SOA",
                    "ttl": ttl,
                    "records": [
                        {
                            "content": f"{dns._absolute(nameservers[0])} hostmaster.{apex} 1 10800 3600 604800 3600",
                            "disabled": False,
                        }
                    ],
                },
                {
                    "name": apex,
                    "type": "NS",
                    "ttl": ttl,
                    "records": [{"content": dns._absolute(ns), "disabled": False} for ns in nameservers],
                },
            ]

            grouped: dict[tuple[str, str], tuple[int, list[str]]] = {}
            for rec in item.get("records") or []:
                if not isinstance(rec, dict):
                    continue
                name, rtype, rec_ttl, content = dns.to_pdns(zone, rec)
                if rtype == "NS" and name == apex:
                    continue
                _, existing_contents = grouped.get((name, rtype), (rec_ttl, []))
                if content not in existing_contents:
                    existing_contents.append(content)
                grouped[(name, rtype)] = (rec_ttl, existing_contents)

            for (name, rtype), (rec_ttl, contents) in grouped.items():
                rrsets.append(
                    {
                        "name": name,
                        "type": rtype,
                        "ttl": rec_ttl,
                        "records": [{"content": c, "disabled": False} for c in contents],
                    }
                )

            # Recreate or replace zone in PowerDNS
            try:
                dns._request("DELETE", f"/zones/{apex}")
            except dns.DnsError as exc:
                if exc.status != 404:
                    raise
            dns._request(
                "POST",
                "/zones",
                {
                    "name": apex,
                    "kind": "Native",
                    "soa_edit_api": dns.SOA_EDIT_API,
                    "nameservers": [],
                    "rrsets": rrsets,
                },
            )
            if not db.query(DnsZone).filter(DnsZone.name == zone).first():
                db.add(DnsZone(name=zone, owner_id=None))
                db.commit()
            applied += 1
        except Exception as exc:
            errors.append(f"{raw_zone}: {exc}")

    with _state_lock:
        state = _read_state()
        _append_log(
            state,
            "receive_dns_sync",
            source_node or "peer",
            "success" if not errors else "warn",
            f"Nhận đồng bộ từ {source_node or 'Peer'}: cập nhật {applied} zone, xóa {deleted} zone.",
        )
        _write_state(state)

    return {"applied": applied, "deleted": deleted, "errors": errors}


# --- Cross-Server Domain & Username Conflict Check ----------------------------

def check_local_conflict(db: Session, domain: str = "", username: str = "") -> dict[str, Any]:
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
            domain_exists = True

    if username_clean:
        if db.query(User).filter(User.username == username_clean).first():
            username_exists = True

    return {
        "domain_exists": domain_exists,
        "username_exists": username_exists,
        "node_hostname": _default_hostname(),
    }


def check_cluster_conflict(domain: str = "", username: str = "") -> dict[str, Any]:
    """Query active peer nodes in the cluster to see if domain or username already exists."""
    with _state_lock:
        state = _read_state()
        settings = state.get("settings") or {}
        if not settings.get("enabled", True):
            return {"conflict": False}
        check_dom = bool(domain and settings.get("check_domain_conflict", True))
        check_usr = bool(username and settings.get("check_username_conflict", False))
        if not check_dom and not check_usr:
            return {"conflict": False}
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
    for peer in peers:
        try:
            resp, _ = _peer_request(peer, "GET", f"/api/multi-server/peer/check-conflict?{qs}", timeout=4)
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
        except Exception as exc:
            logger.debug("Cluster conflict check skipped unreachable peer %s: %s", peer.get("name"), exc)

    return {"conflict": False}


# --- Remote Database Hosts (Remote MySQL / MariaDB Nodes) ---------------------

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

    host_id = f"rdb_{secrets.token_hex(4)}"
    entry = {
        "id": host_id,
        "name": name,
        "host": host,
        "port": port,
        "admin_user": admin_user,
        "admin_password": admin_password,
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
        _append_log(state, "add_remote_db", f"{name} ({host}:{port})", "info", "Đã thêm cấu hình Remote MySQL/MariaDB Node.")
        _write_state(state)

    return test_remote_db_host(host_id)


def test_remote_db_host(host_id: str) -> dict[str, Any]:
    with _state_lock:
        state = _read_state()
        entry = next((h for h in state.get("remote_db_hosts", []) if h.get("id") == host_id), None)
        if not entry:
            raise ValueError("Không tìm thấy máy chủ Remote Database này.")
        host = str(entry.get("host") or "")
        port = int(entry.get("port") or 3306)

    try:
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
