"""Cross-node domain/username conflict checks: fail-open vs fail-closed, and
the username guard on every way an account is created."""
from types import SimpleNamespace

import pytest

from _cluster_helpers import db, state_file, write_state  # noqa: F401 - fixtures

from app.services import multi_server  # noqa: E402


@pytest.fixture()
def two_peers(state_file):
    write_state(state_file, [{"id": "node_b", "name": "B"}, {"id": "node_c", "name": "C"}],
                check_username_conflict=True)
    return state_file


def _peers_answer(monkeypatch, answers):
    asked = []

    def fake(node, method, path, payload=None, timeout=8):
        asked.append((node["id"], path))
        answer = answers[node["id"]]
        if isinstance(answer, Exception):
            raise answer
        return answer, 5

    monkeypatch.setattr(multi_server, "_peer_request", fake)
    return asked


def test_unreachable_peer_is_skipped_when_fail_open(two_peers, monkeypatch):
    _peers_answer(monkeypatch, {"node_b": RuntimeError("timed out"), "node_c": {"domain_exists": False}})
    result = multi_server.check_cluster_conflict(domain="new-cltest.com")
    assert result["conflict"] is False and result["unreachable"] == ["B"]


def test_unreachable_peer_blocks_when_fail_closed(two_peers, monkeypatch):
    write_state(two_peers, [{"id": "node_b", "name": "B"}, {"id": "node_c", "name": "C"}],
                check_username_conflict=True, conflict_fail_closed=True)
    _peers_answer(monkeypatch, {"node_b": RuntimeError("timed out"), "node_c": {"domain_exists": False}})
    result = multi_server.check_cluster_conflict(domain="new-cltest.com")
    assert result["conflict"] is True and result["kind"] == "unreachable"
    assert multi_server.conflict_http_status(result) == 503


def test_a_real_duplicate_wins_over_an_unreachable_peer(two_peers, monkeypatch):
    write_state(two_peers, [{"id": "node_b", "name": "B"}, {"id": "node_c", "name": "C"}],
                check_username_conflict=True, conflict_fail_closed=True)
    _peers_answer(monkeypatch, {"node_b": RuntimeError("down"), "node_c": {"username_exists": True}})
    result = multi_server.check_cluster_conflict(username="cltestbob")
    assert result["kind"] == "username" and result["node_name"] == "C"
    assert multi_server.conflict_http_status(result) == 409


def test_username_guard_off_asks_nobody(state_file, monkeypatch):
    write_state(state_file, [{"id": "node_b"}], check_username_conflict=False)
    asked = _peers_answer(monkeypatch, {"node_b": {"username_exists": True}})
    assert multi_server.check_cluster_conflict(username="cltestbob") == {"conflict": False}
    assert asked == []


def test_peer_ignores_its_copy_of_the_askers_zone(db, state_file):
    from app.models.entities import ClusterZoneOrigin, DnsZone

    write_state(state_file, [])
    db.add(DnsZone(name="asker-cltest.com", owner_id=None))
    db.add(ClusterZoneOrigin(zone="asker-cltest.com", origin_uid="bbbbbbbbbbbbbbbb"))
    db.commit()
    assert multi_server.check_local_conflict(db, domain="asker-cltest.com", asking_node="bbbbbbbbbbbbbbbb")["domain_exists"] is False
    assert multi_server.check_local_conflict(db, domain="asker-cltest.com", asking_node="cccccccccccccccc")["domain_exists"] is True


# --- wired into account creation ---------------------------------------------------

def _conflict(monkeypatch, calls):
    def fake(domain="", username="", **_):
        calls.append({"domain": domain, "username": username})
        return {"conflict": True, "kind": "username", "message": "exists on B"}

    monkeypatch.setattr(multi_server, "check_cluster_conflict", fake)


def test_admin_and_reseller_user_creation_checks_the_cluster(db, monkeypatch):
    from fastapi import HTTPException

    from app.api import users
    from app.schemas.schemas import UserCreate

    calls = []
    _conflict(monkeypatch, calls)
    monkeypatch.setattr(users.site_users, "ensure_panel_user",
                        lambda *a, **k: pytest.fail("no Linux account may be made for a conflicting name"))
    payload = UserCreate(username="cltestnew", email="n@cltest.com", password="a-long-password-1")
    admin = SimpleNamespace(id=1, role="admin")
    with pytest.raises(HTTPException) as exc:
        users.create_user(payload, request=None, db=db, current_user=admin)
    assert exc.value.status_code == 409 and calls == [{"domain": "", "username": "cltestnew"}]


def test_provisioning_create_checks_username_and_domain(db, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.api import provisioning
    from app.schemas.schemas import ProvisioningAccountCreate
    from app.services.provisioning import create_api_token

    raw, _row = create_api_token(db, "billing", "provisioning:read,provisioning:write", "")
    request = Request({"type": "http", "method": "POST", "path": "/api/provisioning/v1/accounts", "query_string": b"",
                       "headers": [(b"authorization", f"Bearer {raw}".encode())], "client": ("192.0.2.4", 1),
                       "server": ("panel", 443), "scheme": "https", "root_path": ""})
    monkeypatch.setattr(provisioning.nginx, "vhost_exists", lambda domain: False)
    calls = []
    _conflict(monkeypatch, calls)
    payload = ProvisioningAccountCreate(external_id="cltest-1", username="cltestbill", password="a-long-password-1",
                                        package_id=1, domain="bill-cltest.com")
    with pytest.raises(HTTPException) as exc:
        provisioning.create_account(payload, request, db)
    assert exc.value.status_code == 409
    assert calls == [{"domain": "bill-cltest.com", "username": "cltestbill"}]
