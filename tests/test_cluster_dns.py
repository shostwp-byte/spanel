"""Multi-server DNS sync: the durable outbox, zone ownership on the receiver,
monotonic SOA serials, DNSSEC handling and the periodic reconcile."""
from datetime import datetime, timedelta

import pytest

from _cluster_helpers import db, pdns, state_file, write_state  # noqa: F401 - fixtures

from app.services import cluster_dns  # noqa: E402

PEER = "bbbbbbbbbbbbbbbb"
OTHER = "cccccccccccccccc"


def _item(zone, serial=10, records=None, **extra):
    records = records if records is not None else [{"name": "@", "type": "A", "ttl": 3600, "content": "203.0.113.5"}]
    item = {"zone": zone, "action": "upsert", "serial": serial, "nameservers": ["ns1.peer.net", "ns2.peer.net"],
            "ttl": 3600, "records": records, "owner": "alice"}
    item["hash"] = cluster_dns.content_hash(records, item["nameservers"], 3600)
    item.update(extra)
    return item


# --- pure rules ------------------------------------------------------------------

@pytest.mark.parametrize("incoming,current,expected", [
    (0, 0, 1), (7, 0, 7), (3, 10, 11), (2026102405, 2026102401, 2026102405), (2026102401, 2026102401, 2026102402),
])
def test_serial_never_goes_backwards_or_resets(incoming, current, expected):
    assert cluster_dns.next_serial(incoming, current) == expected


def test_backoff_doubles_and_is_capped():
    assert [cluster_dns.backoff_seconds(n) for n in range(1, 6)] == [30, 60, 120, 240, 480]
    assert cluster_dns.backoff_seconds(50) == cluster_dns.BACKOFF_CAP


@pytest.mark.parametrize("origin,has_row,exists,adoptable,expected", [
    (None, False, False, False, "accept"),          # new zone
    (None, False, True, True, "adopt"),             # legacy replica, nothing local on it
    (None, False, True, False, "conflict"),         # legacy zone a local account uses
    (None, True, True, False, "conflict"),          # made here
    (OTHER, True, True, False, "conflict"),         # someone else's replica
    (PEER, True, True, False, "accept"),            # the sender's own replica
])
def test_ownership_decision(origin, has_row, exists, adoptable, expected):
    assert cluster_dns.ownership_decision(origin, has_row, exists, adoptable, PEER).split(":")[0] == expected


def test_content_hash_ignores_record_order():
    a = [{"name": "@", "type": "A", "ttl": 60, "content": "1.1.1.1"}, {"name": "www", "type": "A", "ttl": 60, "content": "1.1.1.1"}]
    assert cluster_dns.content_hash(a, ["ns1"], 60) == cluster_dns.content_hash(list(reversed(a)), ["ns1"], 60)
    assert cluster_dns.content_hash(a, ["ns1"], 60) != cluster_dns.content_hash(a[:1], ["ns1"], 60)


# --- receiver --------------------------------------------------------------------

@pytest.fixture()
def receiver(db, pdns, state_file):
    write_state(state_file, [])
    return db, pdns


def _apply(db, *items, source=PEER):
    return {r["zone"]: r for r in cluster_dns.apply_incoming(db, list(items), source_uid=source, source_name="node-b")["results"]}


def test_new_zone_is_created_with_the_origin_serial_and_no_local_owner(receiver):
    from app.models.entities import DnsZone

    db, fake = receiver
    result = _apply(db, _item("new-cltest.com", serial=2026102403))
    assert result["new-cltest.com"]["status"] == "applied"
    assert fake.serial("new-cltest.com") == 2026102403
    row = cluster_dns.origin_row(db, "new-cltest.com")
    assert row.origin_uid == PEER and row.origin_owner == "alice" and row.source_serial == 2026102403
    assert db.query(DnsZone).filter(DnsZone.name == "new-cltest.com").one().owner_id is None


def test_zone_owned_locally_is_never_touched(receiver):
    from app.models.entities import DnsZone, User

    db, fake = receiver
    user = User(username="cltest-owner", email="o@cltest.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.add(DnsZone(name="mine-cltest.com", owner_id=user.id))
    db.commit()
    fake.add("mine-cltest.com", serial=5)
    before = fake.zones["mine-cltest.com."]

    result = _apply(db, _item("mine-cltest.com", serial=99))
    assert result["mine-cltest.com"]["status"] == "conflict"
    assert fake.zones["mine-cltest.com."] == before
    assert not fake.writes()
    # ...and it is now known to be ours, so a delete is refused too.
    assert cluster_dns.origin_row(db, "mine-cltest.com").origin_uid is None
    result = _apply(db, {"zone": "mine-cltest.com", "action": "delete"})
    assert result["mine-cltest.com"]["status"] == "conflict"
    assert "mine-cltest.com." in fake.zones


def test_zone_made_here_after_the_upgrade_is_never_overwritten(receiver, db):
    from app.models.entities import ClusterZoneOrigin

    db, fake = receiver
    fake.add("local-cltest.com", serial=3)
    db.add(ClusterZoneOrigin(zone="local-cltest.com", origin_uid=None))
    db.commit()
    assert _apply(db, _item("local-cltest.com"))["local-cltest.com"]["status"] == "conflict"
    assert not fake.writes()


def test_replica_of_another_node_is_refused(receiver):
    from app.models.entities import ClusterZoneOrigin

    db, fake = receiver
    fake.add("theirs-cltest.com", serial=3)
    db.add(ClusterZoneOrigin(zone="theirs-cltest.com", origin_uid=OTHER))
    db.commit()
    assert _apply(db, _item("theirs-cltest.com"))["theirs-cltest.com"]["status"] == "conflict"
    assert _apply(db, {"zone": "theirs-cltest.com", "action": "delete"})["theirs-cltest.com"]["status"] == "conflict"
    assert not fake.writes()


def test_update_patches_in_place_and_serial_moves_forward(receiver):
    db, fake = receiver
    _apply(db, _item("up-cltest.com", serial=5))
    fake.zones["up-cltest.com."]["rrsets"].append(
        {"name": "old.up-cltest.com.", "type": "A", "ttl": 60, "records": [{"content": "192.0.2.1", "disabled": False}]})
    # The local serial got ahead of the origin's (soa-edit on this server).
    _apply_serial = fake.serial("up-cltest.com")
    records = [{"name": "@", "type": "A", "ttl": 3600, "content": "203.0.113.9"}]
    result = _apply(db, _item("up-cltest.com", serial=6, records=records))
    assert result["up-cltest.com"]["status"] == "applied"
    assert ("DELETE", "/zones/up-cltest.com.") not in fake.calls
    assert fake.rrset("up-cltest.com", "up-cltest.com.", "A") == ["203.0.113.9"]
    assert fake.rrset("up-cltest.com", "old.up-cltest.com.", "A") is None
    assert fake.serial("up-cltest.com") == max(6, _apply_serial + 1)

    # The origin serial lags behind the local one: still forward, never 1.
    fake.zones["up-cltest.com."]["rrsets"][0]["records"][0]["content"] = \
        "ns1.example.net. hostmaster.up-cltest.com. 500 10800 3600 604800 3600"
    records = [{"name": "@", "type": "A", "ttl": 3600, "content": "203.0.113.10"}]
    _apply(db, _item("up-cltest.com", serial=7, records=records))
    assert fake.serial("up-cltest.com") == 501


def test_stale_and_unchanged_snapshots_write_nothing(receiver):
    db, fake = receiver
    _apply(db, _item("st-cltest.com", serial=20))
    fake.calls.clear()
    assert _apply(db, _item("st-cltest.com", serial=19, records=[]))["st-cltest.com"]["status"] == "stale"
    assert _apply(db, _item("st-cltest.com", serial=21))["st-cltest.com"]["status"] == "unchanged"
    assert not fake.writes()
    assert cluster_dns.origin_row(db, "st-cltest.com").source_serial == 21


def test_delete_only_from_the_origin(receiver):
    from app.models.entities import DnsZone

    db, fake = receiver
    _apply(db, _item("del-cltest.com"))
    assert _apply(db, {"zone": "del-cltest.com", "action": "delete"}, source=OTHER)["del-cltest.com"]["status"] == "conflict"
    assert "del-cltest.com." in fake.zones
    assert _apply(db, {"zone": "del-cltest.com", "action": "delete"})["del-cltest.com"]["status"] == "deleted"
    assert "del-cltest.com." not in fake.zones
    assert cluster_dns.origin_row(db, "del-cltest.com") is None
    assert db.query(DnsZone).filter(DnsZone.name == "del-cltest.com").first() is None


def test_legacy_ownerless_zone_is_adopted_but_one_with_a_website_is_not(receiver):
    from app.models.entities import DnsZone, User, Website

    db, fake = receiver
    fake.add("legacy-cltest.com", serial=1)
    db.add(DnsZone(name="legacy-cltest.com", owner_id=None))
    fake.add("site-cltest.com", serial=1)
    db.add(DnsZone(name="site-cltest.com", owner_id=None))
    user = User(username="cltest-site", email="s@cltest.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.add(Website(domain="site-cltest.com", owner_id=user.id, root_path="/home/x"))
    db.commit()
    result = _apply(db, _item("legacy-cltest.com", serial=30), _item("site-cltest.com", serial=30))
    assert result["legacy-cltest.com"]["status"] == "applied"
    assert fake.serial("legacy-cltest.com") == 30
    assert result["site-cltest.com"]["status"] == "conflict"


def test_dnssec_zones_are_refused_and_withdrawn(receiver):
    db, fake = receiver
    assert _apply(db, _item("sig-cltest.com", dnssec=True))["sig-cltest.com"]["status"] == "conflict"
    assert "sig-cltest.com." not in fake.zones
    _apply(db, _item("sig2-cltest.com"))
    fake.zones["sig2-cltest.com."]["dnssec"] = True  # someone signed the replica here
    assert _apply(db, _item("sig2-cltest.com", serial=11, records=[]))["sig2-cltest.com"]["status"] == "conflict"
    fake.zones["sig2-cltest.com."]["dnssec"] = False
    result = _apply(db, {"zone": "sig2-cltest.com", "action": "withdraw", "reason": "dnssec"})
    assert result["sig2-cltest.com"]["status"] == "withdrawn"


def test_receiver_refuses_its_own_node_id(receiver):
    db, _ = receiver
    with pytest.raises(ValueError):
        cluster_dns.apply_incoming(db, [_item("x-cltest.com")], source_uid="aaaaaaaaaaaaaaaa")


# --- sender: snapshot ------------------------------------------------------------

def test_snapshot_of_a_signed_zone_is_a_withdrawal(db, pdns, state_file):
    write_state(state_file, [])
    pdns.add("signed-cltest.com", serial=4, dnssec=True)
    assert cluster_dns.zone_snapshot(db, "signed-cltest.com")["action"] == "withdraw"
    pdns.add("plain-cltest.com", serial=4)
    snap = cluster_dns.zone_snapshot(db, "plain-cltest.com")
    assert snap["action"] == "upsert" and snap["serial"] == 4 and snap["hash"]
    assert cluster_dns.zone_snapshot(db, "gone-cltest.com")["action"] == "delete"


# --- sender: the outbox ----------------------------------------------------------

@pytest.fixture()
def sender(db, state_file, monkeypatch):
    write_state(state_file, [{"id": "node_b"}, {"id": "node_c", "sync_dns": False}])
    monkeypatch.setattr(cluster_dns, "zone_snapshot", lambda _db, zone: _item(zone))
    return db


def _rows(db):
    from app.models.entities import ClusterDnsOutbox

    return db.query(ClusterDnsOutbox).order_by(ClusterDnsOutbox.id).all()


def test_local_change_marks_origin_and_queues_once_per_peer(sender):
    db = sender
    assert cluster_dns.note_local_change(db, "out-cltest.com", "upsert") == 1
    assert cluster_dns.note_local_change(db, "out-cltest.com", "upsert") == 1
    rows = _rows(db)
    assert [(r.node_id, r.zone, r.status) for r in rows] == [("node_b", "out-cltest.com", "pending")]
    assert cluster_dns.origin_row(db, "out-cltest.com").origin_uid is None


def test_replica_changes_are_not_pushed(sender):
    from app.models.entities import ClusterZoneOrigin

    db = sender
    db.add(ClusterZoneOrigin(zone="rep-cltest.com", origin_uid=PEER))
    db.commit()
    assert cluster_dns.note_local_change(db, "rep-cltest.com", "upsert") == 0
    assert _rows(db) == []


def test_failed_delivery_is_retried_with_backoff_then_given_up(sender):
    db = sender
    cluster_dns.note_local_change(db, "retry-cltest.com", "upsert")
    now = datetime.utcnow() + timedelta(seconds=1)

    def down(node, items):
        raise RuntimeError("connection refused")

    cluster_dns.process_outbox(db, now=now, send=down)
    row = _rows(db)[0]
    assert (row.status, row.attempts, row.next_attempt_at) == ("pending", 1, now + timedelta(seconds=30))
    assert "refused" in row.last_error

    # Not due yet: nothing is sent.
    calls = []
    cluster_dns.process_outbox(db, now=now + timedelta(seconds=10), send=lambda n, i: calls.append(i) or {})
    assert calls == []

    t = now
    for attempt in range(2, cluster_dns.MAX_ATTEMPTS + 1):
        t = _rows(db)[0].next_attempt_at
        cluster_dns.process_outbox(db, now=t, send=down)
    row = _rows(db)[0]
    assert row.attempts == cluster_dns.MAX_ATTEMPTS and row.status == "failed"


def test_successful_delivery_removes_the_row_and_survives_a_new_session(sender):
    from app.core.database import SessionLocal

    db = sender
    cluster_dns.note_local_change(db, "ok-cltest.com", "upsert")
    with SessionLocal() as other:  # e.g. after a restart
        assert len(_rows(other)) == 1
        sent = []
        result = cluster_dns.process_outbox(other, send=lambda node, items: sent.append((node["id"], items)) or {
            "results": [{"zone": "ok-cltest.com", "status": "applied"}]})
    assert result["delivered"] == 1 and sent[0][0] == "node_b"
    assert sent[0][1][0]["zone"] == "ok-cltest.com"
    db.expire_all()
    assert _rows(db) == []


def test_conflict_answer_stops_retrying(sender):
    db = sender
    cluster_dns.note_local_change(db, "cf-cltest.com", "upsert")
    cluster_dns.process_outbox(db, send=lambda n, i: {"results": [
        {"zone": "cf-cltest.com", "status": "conflict", "detail": "the zone is owned by this server"}]})
    row = _rows(db)[0]
    assert row.status == "conflict" and "owned" in row.last_error
    # A new local change queues it again.
    cluster_dns.note_local_change(db, "cf-cltest.com", "upsert")
    assert _rows(db)[0].status == "pending"


def test_rows_of_a_removed_peer_are_dropped(sender, state_file):
    db = sender
    cluster_dns.note_local_change(db, "gone-cltest.com", "upsert")
    write_state(state_file, [])
    cluster_dns.process_outbox(db, send=lambda n, i: pytest.fail("nothing to send to"))
    assert _rows(db) == []


# --- reconcile -------------------------------------------------------------------

def test_reconcile_queues_missing_changed_withdrawn_and_deleted(sender):
    from app.models.entities import ClusterZoneOrigin, DnsZone

    db = sender
    for name in ("same-cltest.com", "changed-cltest.com", "missing-cltest.com", "signed-cltest.com"):
        db.add(DnsZone(name=name, owner_id=None))
    db.add(DnsZone(name="replica-cltest.com", owner_id=None))
    db.add(ClusterZoneOrigin(zone="replica-cltest.com", origin_uid=PEER))
    db.commit()
    local = {"same-cltest.com": (5, False), "changed-cltest.com": (9, False), "missing-cltest.com": (1, False),
             "signed-cltest.com": (3, True), "replica-cltest.com": (2, False)}
    held = {"same-cltest.com": 5, "changed-cltest.com": 8, "signed-cltest.com": 3, "removed-cltest.com": 4}
    result = cluster_dns.reconcile(db, fetch_inventory=lambda node: held, local_serials=local)
    queued = {(r.zone, r.action) for r in _rows(db)}
    assert queued == {("changed-cltest.com", "upsert"), ("missing-cltest.com", "upsert"),
                      ("signed-cltest.com", "upsert"), ("removed-cltest.com", "delete")}
    assert result["queued"] == 4


def test_handover_only_from_the_origin(receiver):
    db, fake = receiver
    _apply(db, _item("ho-cltest.com"))
    refused = _apply(db, {"zone": "ho-cltest.com", "action": "handover", "new_origin": OTHER}, source=OTHER)
    assert refused["ho-cltest.com"]["status"] == "conflict"
    done = _apply(db, {"zone": "ho-cltest.com", "action": "handover", "new_origin": OTHER, "new_origin_name": "C"})
    assert done["ho-cltest.com"]["status"] == "handed_over"
    assert cluster_dns.origin_row(db, "ho-cltest.com").origin_uid == OTHER
    # From now on the new master's pushes are taken, the old one's are not.
    assert _apply(db, _item("ho-cltest.com", serial=50), source=OTHER)["ho-cltest.com"]["status"] == "applied"
    assert _apply(db, _item("ho-cltest.com", serial=60))["ho-cltest.com"]["status"] == "conflict"
