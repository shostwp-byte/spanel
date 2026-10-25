"""SPanel-to-SPanel account transfer: the source-side orchestration with the
peer mocked, and the target side's checks, upload, restore and zone claim."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from _cluster_helpers import db, pdns, state_file, write_state  # noqa: F401 - fixtures

from app.services import backup, cluster_peer, cluster_transfer  # noqa: E402

TARGET_UID = "bbbbbbbbbbbbbbbb"


@pytest.fixture()
def backup_root(tmp_path, monkeypatch):
    from app.core.config import settings

    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setattr(settings, "backup_root", str(root))
    return root


@pytest.fixture()
def account(db):
    from app.models.entities import User

    user = User(username="cltestmove", email="m@cltest.com", hashed_password="$2b$12$abcdefghijklmnopqrstuv")
    db.add(user)
    db.commit()
    return user


class FakeTarget:
    """The target node's peer endpoints, as the source sees them."""

    def __init__(self, *, conflicts=(), remote_states=("running", "done"), claim=None):
        self.calls = []
        self.conflicts = list(conflicts)
        self.remote_states = list(remote_states)
        self.claim = claim
        self.uploaded = None

    def peer(self, node, method, path, payload=None, timeout=30):
        self.calls.append((method, path, payload))
        if path.endswith("/peer/ping"):
            return {"node_uid": TARGET_UID, "backup_free_bytes": 10 ** 12}
        if path.endswith("/transfer/precheck"):
            return {"ok": not self.conflicts, "conflicts": self.conflicts, "free_bytes": 10 ** 12}
        if path.endswith("/transfer/restore"):
            return {"job_uid": "remote0000000001"}
        if "/transfer/jobs/" in path:
            state = self.remote_states.pop(0) if len(self.remote_states) > 1 else self.remote_states[0]
            return {"status": state, "message": "disk full" if state == "failed" else ""}
        if path.endswith("/transfer/claim-zones"):
            if isinstance(self.claim, Exception):
                raise self.claim
            return self.claim or {"claimed": payload["zones"], "skipped": []}
        raise AssertionError(path)

    def upload(self, node, archive, size, sha256, username):
        data = Path(archive).read_bytes()
        assert len(data) == size and hashlib.sha256(data).hexdigest() == sha256
        self.uploaded = {"size": size, "sha256": sha256, "username": username}
        return {"upload_id": f"user-{username}-transfer-0011aabb.tar.gz"}


@pytest.fixture()
def source(db, state_file, backup_root, account, monkeypatch):
    write_state(state_file, [{"id": "node_b", "name": "B", "api_token": "bp_target-token"}])
    made = {}

    def fake_backup(user, db_, use_hierarchy=True, *, backup_mode="full", passphrase=None, **kw):
        made["passphrase"] = passphrase
        made["mode"] = backup_mode
        path = backup_root / "users" / "host" / user.username / f"user-{user.username}-20261024000000.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"encrypted archive bytes " * 100)
        made["path"] = path
        return str(path)

    monkeypatch.setattr(backup, "create_user_backup", fake_backup)
    monkeypatch.setattr(cluster_transfer, "_sleep", lambda s: None)
    return made


def _job(db, account, **options):
    from app.models.entities import ClusterTransferJob

    job = ClusterTransferJob(job_uid="job0000000000001", direction="out", username=account.username,
                             node_id="node_b", node_name="B", options=json.dumps(options))
    db.add(job)
    db.commit()
    return job


def _install(monkeypatch, target):
    monkeypatch.setattr(cluster_transfer, "_peer", target.peer)
    monkeypatch.setattr(cluster_transfer, "_upload", target.upload)


# --- sealing ---------------------------------------------------------------------

def test_sealed_secret_opens_only_with_the_same_token():
    key = cluster_peer.signing_key_from_token("bp_target-token")
    sealed = cluster_transfer.seal(key, "pass phrase")
    assert "pass phrase" not in sealed
    assert cluster_transfer.unseal(key, sealed) == "pass phrase"
    with pytest.raises(cluster_transfer.TransferError):
        cluster_transfer.unseal(cluster_peer.signing_key_from_token("another"), sealed)


# --- source orchestration --------------------------------------------------------

def test_transfer_runs_every_step_and_cleans_up(db, source, account, monkeypatch):
    target = FakeTarget()
    _install(monkeypatch, target)
    _job(db, account)
    result = cluster_transfer.run_outgoing(db, "job0000000000001")
    assert result["status"] == "done", result["message"]

    # A passphrase made for this transfer, given explicitly (never the env one).
    assert source["mode"] == "full" and len(source["passphrase"]) >= 32
    restore = next(p for m, path, p in target.calls if path.endswith("/transfer/restore"))
    key = cluster_peer.signing_key_from_token("bp_target-token")
    assert cluster_transfer.unseal(key, restore["passphrase_sealed"]) == source["passphrase"]
    assert cluster_transfer.unseal(key, restore["password_hash_sealed"]) == account.hashed_password
    assert source["passphrase"] not in json.dumps(restore)
    assert target.uploaded["username"] == "cltestmove"
    paths = [path for _, path, _ in target.calls]
    assert paths.index("/api/multi-server/peer/transfer/precheck") < paths.index("/api/multi-server/peer/transfer/restore")
    assert sum(1 for p in paths if "/transfer/jobs/" in p) == 2
    # The archive is encrypted with a passphrase nobody kept: it goes.
    assert not source["path"].exists()


def test_conflict_on_the_target_stops_before_any_backup(db, source, account, monkeypatch):
    target = FakeTarget(conflicts=["an account named cltestmove"])
    _install(monkeypatch, target)
    _job(db, account)
    result = cluster_transfer.run_outgoing(db, "job0000000000001")
    assert result["status"] == "failed" and "already has" in result["message"]
    assert "passphrase" not in source and target.uploaded is None


def test_failed_restore_on_the_target_fails_the_job(db, source, account, monkeypatch):
    target = FakeTarget(remote_states=["running", "failed"])
    _install(monkeypatch, target)
    _job(db, account)
    result = cluster_transfer.run_outgoing(db, "job0000000000001")
    assert result["status"] == "failed" and "disk full" in result["message"]
    assert not source["path"].exists()


def test_dns_switch_and_suspend(db, source, account, monkeypatch):
    from app.models.entities import ClusterDnsOutbox, ClusterZoneOrigin, DnsZone
    from app.services import cluster_dns, server_network

    db.add(DnsZone(name="move-cltest.com", owner_id=account.id))
    db.add(ClusterZoneOrigin(zone="move-cltest.com", origin_uid=None))
    db.add(ClusterDnsOutbox(node_id="node_b", zone="move-cltest.com", action="upsert"))
    db.commit()
    monkeypatch.setattr(server_network, "ipv4_addresses", lambda: ["198.51.100.1"])
    monkeypatch.setattr(cluster_dns.dns, "settings", lambda: {"zone_ip": "198.51.100.1", "nameservers": [], "ttl": 3600})
    handovers = []
    monkeypatch.setattr(cluster_dns, "_sync_peers", lambda node_ids=None: [{"id": "node_b"}, {"id": "node_c", "name": "C"}])
    monkeypatch.setattr(cluster_dns, "_send_batch", lambda node, items: handovers.append((node["id"], items)) or {})
    suspended = []
    monkeypatch.setattr(cluster_transfer, "suspend_source_account", lambda db_, user, actor: suspended.append(user.username))
    target = FakeTarget()
    _install(monkeypatch, target)
    _job(db, account, switch_dns=True, suspend_source=True)

    result = cluster_transfer.run_outgoing(db, "job0000000000001")
    assert result["status"] == "done", result["message"]
    claim = next(p for m, path, p in target.calls if path.endswith("/claim-zones"))
    assert claim == {"zones": ["move-cltest.com"], "old_ips": ["198.51.100.1"]}
    row = db.query(ClusterZoneOrigin).filter_by(zone="move-cltest.com").one()
    assert row.origin_uid == TARGET_UID  # we now take this zone from B
    assert db.query(ClusterDnsOutbox).count() == 0
    # The other peer is told to take the zone from B; B itself is not.
    assert [(n, i[0]["action"], i[0]["new_origin"]) for n, i in handovers] == [("node_c", "handover", TARGET_UID)]
    assert suspended == ["cltestmove"]


def test_refused_claim_keeps_this_node_master(db, source, account, monkeypatch):
    from app.models.entities import ClusterZoneOrigin, DnsZone
    from app.services import cluster_dns, server_network

    db.add(DnsZone(name="keep-cltest.com", owner_id=account.id))
    db.commit()
    monkeypatch.setattr(server_network, "ipv4_addresses", lambda: [])
    monkeypatch.setattr(cluster_dns.dns, "settings", lambda: {"zone_ip": ""})
    monkeypatch.setattr(cluster_dns, "_sync_peers", lambda node_ids=None: [])
    target = FakeTarget(claim=RuntimeError("peer refused"))
    _install(monkeypatch, target)
    _job(db, account, switch_dns=True)
    result = cluster_transfer.run_outgoing(db, "job0000000000001")
    assert result["status"] == "failed" and result["message"].startswith("dns:")
    assert db.query(ClusterZoneOrigin).filter_by(zone="keep-cltest.com").one().origin_uid is None


def test_admin_accounts_and_dns_only_targets_are_refused(db, state_file, account, monkeypatch):
    from app.models.entities import User

    write_state(state_file, [{"id": "node_dns", "role": "dns_only"}, {"id": "node_b"}])
    monkeypatch.setattr(cluster_transfer._executor, "submit", lambda *a, **k: None)
    with pytest.raises(ValueError, match="role"):
        cluster_transfer.start_transfer(db, user_id=account.id, node_id="node_dns")
    admin = User(username="cltestadmin", email="a@cltest.com", hashed_password="x", role="admin")
    db.add(admin)
    db.commit()
    with pytest.raises(ValueError, match="Administrator"):
        cluster_transfer.start_transfer(db, user_id=admin.id, node_id="node_b")
    job = cluster_transfer.start_transfer(db, user_id=account.id, node_id="node_b", switch_dns=True)
    assert job["status"] == "queued" and job["options"]["switch_dns"] is True
    with pytest.raises(ValueError, match="already running"):
        cluster_transfer.start_transfer(db, user_id=account.id, node_id="node_b")


# --- target side -----------------------------------------------------------------

def test_precheck_reports_real_conflicts_only(db, backup_root, account):
    from app.models.entities import ClusterZoneOrigin, DnsZone, Website

    db.add(Website(domain="site-cltest.com", owner_id=account.id, root_path="/x"))
    db.add(DnsZone(name="zone-cltest.com", owner_id=None))
    db.add(DnsZone(name="copy-cltest.com", owner_id=None))
    db.add(ClusterZoneOrigin(zone="copy-cltest.com", origin_uid="aaaaaaaaaaaaaaaa"))
    db.commit()
    result = cluster_transfer.precheck(db, "cltestmove", ["site-cltest.com", "zone-cltest.com", "copy-cltest.com",
                                                          "free-cltest.com"], "aaaaaaaaaaaaaaaa")
    assert result["conflicts"] == ["an account named cltestmove", "a website site-cltest.com", "a DNS zone zone-cltest.com"]
    assert result["free_bytes"] > 0


def test_incoming_restore_validates_the_upload(db, backup_root, account):
    with pytest.raises(ValueError, match="upload id"):
        cluster_transfer.start_incoming_restore(db, upload_id="../../etc/passwd", username="x", passphrase="p",
                                                password_hash="", source_uid="a", source_name="", source_job="")
    restore_dir = Path(backup.user_restore_dir())
    restore_dir.mkdir(parents=True, exist_ok=True)
    (restore_dir / "user-cltestother-transfer-00aa11bb.tar.gz").write_bytes(b"x")
    with pytest.raises(ValueError, match="does not belong"):
        cluster_transfer.start_incoming_restore(db, upload_id="user-cltestother-transfer-00aa11bb.tar.gz",
                                                username="cltestnew", passphrase="p", password_hash="",
                                                source_uid="a", source_name="", source_job="")
    (restore_dir / "user-cltestmove-transfer-00aa11bb.tar.gz").write_bytes(b"x")
    with pytest.raises(ValueError, match="already exists"):
        cluster_transfer.start_incoming_restore(db, upload_id="user-cltestmove-transfer-00aa11bb.tar.gz",
                                                username="cltestmove", passphrase="p", password_hash="",
                                                source_uid="a", source_name="", source_job="")


def test_incoming_restore_keeps_the_password_and_removes_the_archive(db, backup_root, state_file, monkeypatch):
    from app.models.entities import ClusterTransferJob, User

    write_state(state_file, [])
    restore_dir = Path(backup.user_restore_dir())
    restore_dir.mkdir(parents=True, exist_ok=True)
    archive = restore_dir / "user-cltestin-transfer-00aa11bb.tar.gz"
    archive.write_bytes(b"x")
    seen = {}

    def fake_restore(path, db_, *, restore_mode="full", passphrase=None, **kw):
        seen["passphrase"] = passphrase
        assert kw == {"expected_username": "cltestin", "require_new_user": True}
        db_.add(User(username="cltestin", email="i@cltest.com", hashed_password="generated"))
        db_.commit()
        return {"username": "cltestin", "websites": [{"domain": "in-cltest.com"}]}

    monkeypatch.setattr(backup, "restore_user_backup", fake_restore)
    db.add(ClusterTransferJob(job_uid="in00000000000001", direction="in", username="cltestin", node_id="aaaaaaaaaaaaaaaa",
                              node_name="A", archive_name=archive.name))
    db.commit()
    result = cluster_transfer.run_incoming(db, "in00000000000001", "the-pass", "$2b$12$transferredhashvalue")
    assert result["status"] == "done" and seen["passphrase"] == "the-pass"
    assert db.query(User).filter_by(username="cltestin").one().hashed_password == "$2b$12$transferredhashvalue"
    assert not archive.exists()
    assert cluster_transfer.incoming_job(db, "in00000000000001", "aaaaaaaaaaaaaaaa")["status"] == "done"
    with pytest.raises(ValueError):
        cluster_transfer.incoming_job(db, "in00000000000001", "cccccccccccccccc")  # another node's job


def test_incoming_archive_naming_another_account_is_refused_before_restoring(db, backup_root):
    """A peer must not be able to restore over a local account by naming it
    in the archive while declaring a new username for the job."""
    import io
    import json
    import tarfile

    from app.models.entities import User

    db.add(User(username="cltestvictim", email="v@cltest.com", hashed_password="keep"))
    db.commit()
    archive = backup_root / "user-cltestnew-transfer-00cc11dd.tar.gz"
    manifest = json.dumps({"kind": "spanel_user", "user": {"username": "cltestvictim"}}).encode()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(backup.BACKUP_MANIFEST)
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    backup._write_sha256_sidecar(archive)

    with pytest.raises(ValueError, match="holds cltestvictim, not cltestnew"):
        backup.restore_user_backup(str(archive), db, expected_username="cltestnew", require_new_user=True)
    with pytest.raises(ValueError, match="already exists"):
        backup.restore_user_backup(str(archive), db, expected_username="cltestvictim", require_new_user=True)
    assert db.query(User).filter_by(username="cltestvictim").one().hashed_password == "keep"


def test_claim_takes_over_and_repoints_the_source_addresses(db, pdns, state_file, monkeypatch):
    from app.models.entities import ClusterDnsOutbox, ClusterZoneOrigin, DnsZone

    write_state(state_file, [{"id": "node_a"}], node_uid=TARGET_UID)
    pdns.add("claim-cltest.com", serial=3, rrsets=[
        {"name": "claim-cltest.com.", "type": "A", "ttl": 3600, "records": [{"content": "192.0.2.10", "disabled": False}]},
        {"name": "www.claim-cltest.com.", "type": "A", "ttl": 3600, "records": [{"content": "203.0.113.99", "disabled": False}]},
    ])
    db.add(DnsZone(name="claim-cltest.com", owner_id=None))
    db.add(ClusterZoneOrigin(zone="claim-cltest.com", origin_uid="aaaaaaaaaaaaaaaa"))
    db.add(DnsZone(name="third-cltest.com", owner_id=None))
    db.add(ClusterZoneOrigin(zone="third-cltest.com", origin_uid="cccccccccccccccc"))
    db.commit()
    from app.services import dns
    monkeypatch.setattr(dns, "settings", lambda: {"zone_ip": "198.51.100.77", "nameservers": ["ns1.x.net", "ns2.x.net"], "ttl": 3600})

    result = cluster_transfer.claim_zones(db, ["claim-cltest.com", "third-cltest.com"], ["192.0.2.10"], "aaaaaaaaaaaaaaaa")
    assert result == {"claimed": ["claim-cltest.com"], "skipped": ["third-cltest.com"]}
    db.expire_all()
    assert db.query(ClusterZoneOrigin).filter_by(zone="claim-cltest.com").one().origin_uid is None
    assert pdns.rrset("claim-cltest.com", "claim-cltest.com.", "A") == ["198.51.100.77"]
    assert pdns.rrset("claim-cltest.com", "www.claim-cltest.com.", "A") == ["203.0.113.99"]
    # Pushed to the peers (the source included) through the outbox.
    assert [(r.node_id, r.zone) for r in db.query(ClusterDnsOutbox)] == [("node_a", "claim-cltest.com")]


# --- the streamed upload endpoint ------------------------------------------------

def _upload_request(body: bytes, sha: str, length=None, user="cltestup"):
    from starlette.requests import Request

    chunks = [body[i:i + 7] for i in range(0, len(body), 7)] or [b""]
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    headers = {"content-length": str(len(body) if length is None else length),
               cluster_peer.HDR_BODY_SHA256.lower(): sha, "x-spanel-transfer-user": user}
    scope = {"type": "http", "method": "PUT", "path": "/api/multi-server/peer/transfer/upload", "query_string": b"",
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()], "client": ("192.0.2.1", 1),
             "server": ("b", 2222), "scheme": "https", "root_path": ""}
    return Request(scope, receive)


def test_upload_is_kept_only_when_complete_and_matching(backup_root, state_file):
    from fastapi import HTTPException

    from app.api.multi_server import peer_transfer_upload

    write_state(state_file, [])
    body = b"archive-content" * 50
    good = hashlib.sha256(body).hexdigest()
    result = asyncio.run(peer_transfer_upload(_upload_request(body, good), node_uid="aaaaaaaaaaaaaaaa"))
    stored = Path(backup.user_restore_dir()) / result["upload_id"]
    assert stored.read_bytes() == body and result["upload_id"].startswith("user-cltestup-transfer-")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(peer_transfer_upload(_upload_request(body, "0" * 64), node_uid="aaaaaaaaaaaaaaaa"))
    assert exc.value.status_code == 400
    assert len(list(Path(backup.user_restore_dir()).glob("*.tar.gz"))) == 1


def test_upload_over_the_limit_is_refused_before_reading(backup_root, state_file, monkeypatch):
    from fastapi import HTTPException

    from app.api.multi_server import peer_transfer_upload

    monkeypatch.setattr(backup, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(peer_transfer_upload(_upload_request(b"x" * 10, "0" * 64, length=101), node_uid="a"))
    assert exc.value.status_code == 413
    with pytest.raises(HTTPException) as exc:
        asyncio.run(peer_transfer_upload(_upload_request(b"x", "0" * 64, user="../etc"), node_uid="a"))
    assert exc.value.status_code == 400
