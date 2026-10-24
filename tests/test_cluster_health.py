"""Cluster node health: periodic pings, stored metrics and down/up alerts."""
import json

import pytest

from _cluster_helpers import state_file, write_state  # noqa: F401 - fixtures

from app.services import multi_server  # noqa: E402


def test_down_is_reported_once_after_two_failures_and_up_once_after():
    node = {}
    events = []
    for ok in (False, False, False, True, True, False, True):
        events.append(multi_server.apply_health_result(
            node, ok=ok, latency_ms=12 if ok else None, error="" if ok else "timed out",
            remote_data={"cpu_percent": 5} if ok else None, now_iso="2026-10-24T00:00:00+00:00"))
    # One failure (often a restart) is not an outage, and a single failure
    # after recovering is not reported either.
    assert events == [None, "down", None, "up", None, None, None]
    assert node["status"] == "online" and node["consecutive_failures"] == 0
    assert len(node["health_history"]) == 7


def test_metrics_for_placement_are_kept():
    node = {}
    multi_server.apply_health_result(node, ok=True, latency_ms=9, error="", now_iso="t", remote_data={
        "hostname": "b.example.com", "cpu_percent": 30.0, "ram_percent": 40.0, "disk_percent": 50.0,
        "disk_free_bytes": 10 ** 11, "ram_available_bytes": 4 * 10 ** 9, "load_1": 0.5, "cpu_cores": 4,
        "node_uid": "bbbbbbbbbbbbbbbb", "ipv4_addresses": ["203.0.113.7"]})
    info = node["remote_info"]
    assert info["disk_free_bytes"] == 10 ** 11 and info["cpu_cores"] == 4 and info["node_uid"] == "bbbbbbbbbbbbbbbb"
    assert info["node_name"] == "b.example.com" and info["ipv4_addresses"] == ["203.0.113.7"]


def test_ping_sends_one_alert_per_transition(state_file, monkeypatch):
    from app.services import notifications

    write_state(state_file, [{"id": "node_b", "name": "B"}])
    sent = []
    monkeypatch.setattr(notifications, "notify_in_background", lambda event, params, **kw: sent.append((event, params, kw)))
    answers = iter([RuntimeError("connection refused"), RuntimeError("connection refused"),
                    RuntimeError("connection refused"), ({"hostname": "b", "cpu_percent": 1}, 7)])

    def fake(node, method, path, payload=None, timeout=8):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(multi_server, "_peer_request", fake)
    for _ in range(4):
        multi_server.ping_peer_node("node_b")
    assert [(e, p["recovered"]) for e, p, _ in sent] == [("cluster_node_down", False), ("cluster_node_down", True)]
    assert all(kw.get("admins") for _, _, kw in sent)
    stored = json.loads(state_file.read_text(encoding="utf-8"))
    assert stored["nodes"][0]["status"] == "online"
    assert [log["action"] for log in stored["sync_logs"][:2]] == ["node_up", "node_down"]


def test_alert_message_renders_in_both_languages():
    from app.services import notify_messages

    assert "cluster_node_down" in notify_messages.EVENTS
    title, body = notify_messages.render("cluster_node_down", {"node": "B", "url": "https://b:2222", "error": "x", "failures": 2}, "en")
    assert "B" in title and "2 checks" in body
    title, _ = notify_messages.render("cluster_node_down", {"node": "B", "recovered": True}, "vi")
    assert "B" in title


def test_worker_pings_at_the_health_interval(state_file, monkeypatch):
    from app.services import cluster_dns, cluster_worker

    write_state(state_file, [{"id": "node_b"}], health_interval_sec=60, reconcile_interval_sec=900, auto_dns_sync=False)
    pings = []
    monkeypatch.setattr(multi_server, "ping_all_peer_nodes", lambda enabled_only=False: pings.append(enabled_only) or [])
    monkeypatch.setattr(cluster_dns, "process_outbox", lambda db: {"delivered": 0})
    monkeypatch.setattr(cluster_worker, "_last_run", {})
    cluster_worker.run_once(now=1000.0)
    cluster_worker.run_once(now=1030.0)
    cluster_worker.run_once(now=1061.0)
    assert pings == [True, True]


def test_worker_idles_without_nodes(state_file, monkeypatch):
    from app.services import cluster_worker

    write_state(state_file, [])
    monkeypatch.setattr(multi_server, "ping_all_peer_nodes", lambda **k: pytest.fail("no nodes to ping"))
    assert cluster_worker.run_once(now=5.0) == {}
