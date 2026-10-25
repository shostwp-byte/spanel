"""Placement hints: which node a new or moved account should go to."""
from datetime import datetime, timedelta, timezone

import pytest

from _cluster_helpers import db, state_file, write_state  # noqa: F401 - fixtures

from app.services import cluster_placement  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(minutes=1)).isoformat()


def _node(node_id, *, role="full_peer", status="online", enabled=True, seen=FRESH, **metrics):
    base = {"disk_percent": 40, "ram_percent": 40, "cpu_percent": 20, "load_1": 0.5, "cpu_cores": 4,
            "disk_free_bytes": 500 * 10 ** 9, "websites_count": 10}
    base.update(metrics)
    return {"id": node_id, "name": node_id, "role": role, "status": status, "enabled": enabled,
            "last_seen_at": seen, "metrics": base}


def test_score_prefers_free_resources():
    idle, _ = cluster_placement.score_node({"disk_percent": 10, "ram_percent": 10, "cpu_percent": 5, "load_1": 0.1,
                                            "cpu_cores": 8}, role="full_peer", status="online", enabled=True,
                                           last_seen_at=FRESH, now=NOW)
    busy, _ = cluster_placement.score_node({"disk_percent": 80, "ram_percent": 85, "cpu_percent": 90, "load_1": 12,
                                            "cpu_cores": 4}, role="full_peer", status="online", enabled=True,
                                           last_seen_at=FRESH, now=NOW)
    assert 0 <= busy < idle <= 100


@pytest.mark.parametrize("node,reason", [
    (_node("a", role="dns_only"), "role"),
    (_node("b", role="db_node"), "role"),
    (_node("c", status="offline"), "offline"),
    (_node("d", enabled=False), "disabled"),
    (_node("e", seen=(NOW - timedelta(hours=1)).isoformat()), "recent"),
    (_node("f", disk_percent=95), "disk"),
    (_node("g", ram_percent=97), "memory"),
    (_node("h", disk_free_bytes=10 * 10 ** 9), "needed"),
])
def test_ineligible_nodes_say_why(node, reason):
    ranked = cluster_placement.rank([node], required_bytes=9 * 10 ** 9, now=NOW)
    assert ranked[0]["eligible"] is False and reason in ranked[0]["reasons"][0]


def test_rank_puts_the_best_eligible_node_first_and_breaks_ties_by_websites():
    nodes = [
        _node("busy", disk_percent=85, ram_percent=80, cpu_percent=70),
        _node("down", status="offline", disk_percent=1),
        _node("calm-many", websites_count=300),
        _node("calm-few", websites_count=3),
    ]
    order = [c["id"] for c in cluster_placement.rank(nodes, now=NOW)]
    assert order == ["calm-few", "calm-many", "busy", "down"]


def test_suggest_node_uses_stored_health_and_never_suggests_moving_to_itself(db, state_file, monkeypatch):
    from app.services import multi_server

    fresh = datetime.now(timezone.utc).isoformat()
    write_state(state_file, [
        {"id": "node_b", "status": "online", "last_seen_at": fresh,
         "remote_info": {"disk_percent": 30, "ram_percent": 30, "cpu_percent": 10, "disk_free_bytes": 10 ** 12}},
        {"id": "node_c", "status": "online", "last_seen_at": fresh, "role": "dns_only",
         "remote_info": {"disk_percent": 1, "ram_percent": 1, "cpu_percent": 1}},
    ])
    monkeypatch.setattr(multi_server, "local_node_summary", lambda db_: {
        "node_name": "A", "disk_percent": 92, "ram_percent": 50, "cpu_percent": 50})
    for_account = cluster_placement.suggest_node(db, purpose="account")
    assert for_account["suggested"]["id"] == "node_b"
    assert {c["id"] for c in for_account["candidates"]} == {"node_b", "node_c", "local"}
    for_transfer = cluster_placement.suggest_node(db, purpose="transfer")
    assert "local" not in {c["id"] for c in for_transfer["candidates"]}
