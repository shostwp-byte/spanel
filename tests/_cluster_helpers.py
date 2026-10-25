"""Shared fixtures for the cluster tests: a database with every table, a
private multi-server state file, and an in-memory stand-in for PowerDNS."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@pytest.fixture()
def db():
    from app.core.database import Base, SessionLocal, engine
    from app.models import entities

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    # Only what these tests create: the cluster tables, and names with "cltest".
    for model in (entities.ClusterDnsOutbox, entities.ClusterZoneOrigin, entities.ClusterTransferJob):
        session.query(model).delete()
    session.query(entities.WebsiteAlias).filter(entities.WebsiteAlias.domain.like("%cltest%")).delete(synchronize_session=False)
    session.query(entities.Website).filter(entities.Website.domain.like("%cltest%")).delete(synchronize_session=False)
    session.query(entities.DnsZone).filter(entities.DnsZone.name.like("%cltest%")).delete(synchronize_session=False)
    session.query(entities.User).filter(entities.User.username.like("cltest%")).delete(synchronize_session=False)
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    from app.services import multi_server

    path = tmp_path / "multi-server.json"
    monkeypatch.setattr(multi_server, "_CONFIG_PATH", path)
    return path


def write_state(path, nodes, **settings):
    from app.core.secrets import encrypt

    base = {"enabled": True, "node_name": "node-a", "node_uid": "aaaaaaaaaaaaaaaa", "auto_dns_sync": True}
    base.update(settings)
    stored = []
    for node in nodes:
        item = {"enabled": True, "sync_dns": True, "check_domain": True, "role": "full_peer",
                "verify_tls": True, "url": f"https://{node['id']}.example.com:2222", "name": node["id"]}
        item.update(node)
        item["api_token"] = encrypt(node.get("api_token", "bp_token"))
        stored.append(item)
    path.write_text(json.dumps({"settings": base, "nodes": stored}), encoding="utf-8")


class FakePdns:
    """Just enough of the PowerDNS HTTP API for the cluster code."""

    def __init__(self):
        self.zones: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def add(self, zone, serial=1, rrsets=None, dnssec=False):
        apex = zone.rstrip(".") + "."
        sets = [{"name": apex, "type": "SOA", "ttl": 3600, "records": [
            {"content": f"ns1.example.net. hostmaster.{apex} {serial} 10800 3600 604800 3600", "disabled": False}]}]
        sets += rrsets or [{"name": apex, "type": "A", "ttl": 3600,
                            "records": [{"content": "198.51.100.1", "disabled": False}]}]
        self.zones[apex] = {"name": apex, "rrsets": sets, "dnssec": dnssec, "serial": serial}

    def serial(self, zone):
        from app.services import cluster_dns

        return cluster_dns.soa_serial(self.zones[zone.rstrip(".") + "."])

    def rrset(self, zone, name, rtype):
        for rrset in self.zones[zone.rstrip(".") + "."]["rrsets"]:
            if rrset["name"] == name and rrset["type"] == rtype:
                return [r["content"] for r in rrset["records"]]
        return None

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH", "DELETE", "PUT")]

    def __call__(self, method, path, body=None):
        from app.services import dns

        self.calls.append((method, path))
        if path == "/zones" and method == "GET":
            return [{"name": z["name"], "serial": z["serial"], "dnssec": z["dnssec"]} for z in self.zones.values()]
        if path == "/zones" and method == "POST":
            apex = body["name"]
            if apex in self.zones:
                raise dns.DnsError("exists", status=409)
            sets = copy.deepcopy(body["rrsets"])
            self.zones[apex] = {"name": apex, "rrsets": sets, "dnssec": False, "serial": 0}
            self.zones[apex]["serial"] = self.serial(apex)
            return {}
        apex = path.split("/zones/", 1)[1].split("/", 1)[0]
        if apex not in self.zones:
            raise dns.DnsError("Could not find domain", status=404)
        zone = self.zones[apex]
        if method == "GET":
            return copy.deepcopy(zone)
        if method == "DELETE":
            del self.zones[apex]
            return {}
        if method == "PATCH":
            for change in body["rrsets"]:
                key = (change["name"], change["type"])
                zone["rrsets"] = [r for r in zone["rrsets"] if (r["name"], r["type"]) != key]
                if change["changetype"] == "REPLACE":
                    zone["rrsets"].append({k: copy.deepcopy(v) for k, v in change.items() if k != "changetype"})
            zone["serial"] = self.serial(apex)
            return {}
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture()
def pdns(monkeypatch):
    from app.services import dns

    fake = FakePdns()
    monkeypatch.setattr(dns, "_request", fake)
    monkeypatch.setattr(dns, "active", lambda: True)
    monkeypatch.setattr(dns, "settings", lambda: {
        "nameservers": ["ns1.example.net", "ns2.example.net"], "ttl": 3600, "zone_ip": "198.51.100.1",
        "auto_zone": True, "template": "", "spf": "",
    })
    return fake
