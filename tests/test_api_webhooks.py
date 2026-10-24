"""Webhook outbox: retries with backoff, timestamped signatures, the
account.* lifecycle events and encrypted endpoint secrets."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import api_testkit as kit
from app.core.database import SessionLocal
from app.models.entities import WebhookEndpoint, WebhookOutbox
from app.services import webhooks

BASE = "/api/provisioning/v1"


@pytest.fixture(autouse=True)
def _clean_db():
    kit.reset_db()
    with patch("app.services.audit._write_audit_anchor"), \
            patch("app.services.resource_monitor.apply_user_cgroup_limits_async"):
        yield


class Receiver:
    """Stands in for the HTTP transport and records what was sent."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, "OK")]
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": dict(headers), "payload": json.loads(body)})
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    def events(self):
        return [c["payload"]["event"] for c in self.calls]


def _endpoint(db, owner_id, url="https://hooks.example.com/in", events="*", secret=None):
    ep = WebhookEndpoint(user_id=owner_id, url=url, events=events, is_active=True,
                         secret=webhooks.protect_secret(secret) if secret else None)
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


# --- signatures ------------------------------------------------------------------

def test_timestamped_signature_round_trip_and_replay_window():
    body = b'{"event":"account.created"}'
    headers = webhooks.signed_headers("s3cret", "account.created", "whd_1", body, timestamp=1_700_000_000)
    assert headers["X-SPanel-Timestamp"] == "1700000000"
    expected = "sha256=" + hmac.new(b"s3cret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert headers["X-SPanel-Signature-256"] == expected
    # The old header is unchanged for receivers written against it.
    assert headers["X-SPanel-Signature"] == hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert headers["X-SPanel-Delivery"] == "whd_1"

    sig = headers["X-SPanel-Signature-256"]
    assert webhooks.verify_signature("s3cret", "1700000000", body, sig, now=1_700_000_100)
    assert not webhooks.verify_signature("s3cret", "1700000000", body + b" ", sig, now=1_700_000_100)
    assert not webhooks.verify_signature("wrong", "1700000000", body, sig, now=1_700_000_100)
    # Captured and replayed ten minutes later: refused.
    assert not webhooks.verify_signature("s3cret", "1700000000", body, sig, now=1_700_000_600)


def test_unsigned_endpoint_sends_no_signature():
    headers = webhooks.signed_headers(None, "x", "whd_2", b"{}")
    assert "X-SPanel-Signature" not in headers and "X-SPanel-Signature-256" not in headers


# --- outbox and retries ----------------------------------------------------------

def test_failed_delivery_is_retried_with_backoff_until_success():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        _endpoint(db, admin.id, secret="topsecret")
        webhooks.emit_event(db, "account.created", {"external_id": "whmcs:1"})
        row = db.query(WebhookOutbox).one()
        assert row.status == "pending" and row.attempts == 0

        receiver = Receiver(ConnectionError("refused"), (503, "busy"), (200, "OK"))
        with patch.object(webhooks, "transport", receiver), patch.object(webhooks.random, "uniform", return_value=1.0):
            start = datetime.utcnow()
            assert webhooks.process_due(db, now=start) == 1
            db.refresh(row)
            assert (row.status, row.attempts) == ("pending", 1)
            assert row.next_attempt_at - start >= timedelta(seconds=29)

            # Not due yet: nothing is sent.
            assert webhooks.process_due(db, now=start + timedelta(seconds=10)) == 0
            assert webhooks.process_due(db, now=start + timedelta(seconds=31)) == 1
            db.refresh(row)
            assert (row.status, row.attempts, row.last_status_code) == ("pending", 2, 503)
            assert row.next_attempt_at - (start + timedelta(seconds=31)) >= timedelta(seconds=119)

            assert webhooks.process_due(db, now=start + timedelta(hours=1)) == 1
            db.refresh(row)
            assert (row.status, row.attempts) == ("success", 3)
            assert row.delivered_at is not None

    # The same delivery id on every attempt, a fresh signature each time.
    assert len({c["headers"]["X-SPanel-Delivery"] for c in receiver.calls}) == 1
    for call in receiver.calls:
        assert webhooks.verify_signature(
            "topsecret", call["headers"]["X-SPanel-Timestamp"], call["body"],
            call["headers"]["X-SPanel-Signature-256"],
        )


def test_delivery_gives_up_after_max_attempts():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        _endpoint(db, admin.id)
        webhooks.emit_event(db, "account.suspended", {"external_id": "whmcs:1"})
        row = db.query(WebhookOutbox).one()
        now = datetime.utcnow()
        with patch.object(webhooks, "transport", Receiver((500, "down"))):
            for i in range(webhooks.MAX_ATTEMPTS):
                now += timedelta(days=1)
                webhooks.process_due(db, now=now)
        db.refresh(row)
        assert (row.status, row.attempts) == ("failed", webhooks.MAX_ATTEMPTS)
        assert row.next_attempt_at is None


def test_row_left_delivering_by_a_dead_worker_is_retried():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        _endpoint(db, admin.id)
        webhooks.emit_event(db, "account.created", {"external_id": "whmcs:1"})
        row = db.query(WebhookOutbox).one()
        row.status = "delivering"
        row.updated_at = datetime.utcnow() - timedelta(minutes=10)
        db.commit()
        receiver = Receiver()
        with patch.object(webhooks, "transport", receiver):
            webhooks.process_due(db)
        db.refresh(row)
        assert row.status == "success" and len(receiver.calls) == 1


def test_event_filters_and_reseller_routing():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        reseller = kit.make_user(db, "resell1", role="reseller")
        other = kit.make_user(db, "resell2", role="reseller")
        customer = kit.make_user(db, "cust1", role="end_user", reseller_id=reseller.id)
        _endpoint(db, admin.id, url="https://a.example.com/all")
        _endpoint(db, admin.id, url="https://a.example.com/accounts", events="account.*")
        _endpoint(db, admin.id, url="https://a.example.com/web", events="create_website")
        _endpoint(db, reseller.id, url="https://r.example.com/in", events="account.created")
        _endpoint(db, other.id, url="https://o.example.com/in")
        webhooks.emit_event(db, "account.created", {"external_id": "whmcs:3"}, subject=customer)
        urls = sorted(row.url for row in db.query(WebhookOutbox).all())
    assert urls == ["https://a.example.com/accounts", "https://a.example.com/all", "https://r.example.com/in"]


def test_history_clear_keeps_pending_retries_and_replay_resends():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        _endpoint(db, admin.id)
        webhooks.emit_event(db, "account.created", {"external_id": "whmcs:1"})
        webhooks.emit_event(db, "account.suspended", {"external_id": "whmcs:1"})
        first = db.query(WebhookOutbox).order_by(WebhookOutbox.id).first()
        with patch.object(webhooks, "transport", Receiver((200, "OK"), ConnectionError("x"))):
            webhooks.process_due(db)
        delivered_id = first.delivery_id
        admin_id = admin.id

    c = kit.client(admin_id)
    items = c.get("/api/resellers/webhooks/deliveries").json()["items"]
    assert sorted(i["status"] for i in items) == ["pending", "success"]

    receiver = Receiver()
    with patch.object(webhooks, "transport", receiver):
        replay = c.post(f"/api/resellers/webhooks/deliveries/{delivered_id}/replay")
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed_from"] == delivered_id
    assert receiver.events() == ["account.created"]

    cleared = c.delete("/api/resellers/webhooks/deliveries").json()
    assert cleared["removed"] == 2
    assert [i["status"] for i in c.get("/api/resellers/webhooks/deliveries").json()["items"]] == ["pending"]


def test_old_history_is_purged():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        _endpoint(db, admin.id)
        webhooks.emit_event(db, "account.created", {"external_id": "whmcs:1"})
        row = db.query(WebhookOutbox).one()
        row.status = "success"
        db.commit()
        assert webhooks.purge_history(db, now=datetime.utcnow() + timedelta(days=31)) == 1


# --- provisioning lifecycle events ---------------------------------------------------

def test_provisioning_lifecycle_emits_account_events_without_secrets():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        small = kit.make_package(db, name="Small")
        big = kit.make_package(db, name="Big")
        _endpoint(db, admin.id, events="account.*")
        raw = kit.make_token(db)
    c = kit.client()
    h = kit.bearer(raw)
    password = "first-password-1234"
    with patch("app.api.provisioning.linux_user_exists", return_value=False), \
            patch("app.services.site_users.delete_panel_user"):
        assert c.post(f"{BASE}/accounts", headers=h, json={
            "external_id": "whmcs:77", "username": "lifecyc", "password": password, "package_id": small.id,
        }).status_code == 200
        assert c.post(f"{BASE}/accounts/whmcs:77/suspend", headers=h, json={"reason": "unpaid"}).status_code == 200
        assert c.post(f"{BASE}/accounts/whmcs:77/unsuspend", headers=h).status_code == 200
        assert c.patch(f"{BASE}/accounts/whmcs:77/password", headers=h, json={"password": "second-password-1234"}).status_code == 200
        assert c.patch(f"{BASE}/accounts/whmcs:77/package", headers=h, json={"package_id": big.id}).status_code == 200
        assert c.delete(f"{BASE}/accounts/whmcs:77", headers=h).status_code == 200

    receiver = Receiver()
    with SessionLocal() as db, patch.object(webhooks, "transport", receiver):
        webhooks.process_due(db)
    assert receiver.events() == [
        "account.created", "account.suspended", "account.unsuspended",
        "account.password_changed", "account.package_changed", "account.terminated",
    ]
    by_event = {c["payload"]["event"]: c["payload"] for c in receiver.calls}
    assert by_event["account.created"]["data"]["username"] == "lifecyc"
    assert by_event["account.suspended"]["data"]["reason"] == "unpaid"
    assert by_event["account.package_changed"]["data"]["package_id"] == big.id
    assert by_event["account.terminated"]["data"]["status"] == "terminated"
    assert by_event["account.terminated"]["data"]["username"] == "lifecyc"
    for call in receiver.calls:
        text = call["body"].decode("utf-8")
        assert password not in text and "second-password-1234" not in text
        assert "sftp_password\"" not in text


# --- secrets and destinations -------------------------------------------------------

def test_endpoint_secret_is_encrypted_and_still_signs():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
    c = kit.client(admin.id)
    created = c.post("/api/resellers/webhooks", json={"url": "https://hooks.example.com/x", "secret": "plain-secret"})
    assert created.status_code == 200, created.text
    assert created.json()["has_secret"] is True
    with SessionLocal() as db:
        stored = db.query(WebhookEndpoint).one().secret
    assert stored.startswith("fernet:") and "plain-secret" not in stored

    receiver = Receiver()
    with patch.object(webhooks, "transport", receiver):
        test = c.post(f"/api/resellers/webhooks/{created.json()['id']}/test")
    assert test.status_code == 200, test.text
    sent = receiver.calls[0]
    assert webhooks.verify_signature("plain-secret", sent["headers"]["X-SPanel-Timestamp"], sent["body"],
                                     sent["headers"]["X-SPanel-Signature-256"])


def test_legacy_plaintext_secrets_are_encrypted_on_start():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
        db.add(WebhookEndpoint(user_id=admin.id, url="https://h.example.com", secret="legacy", events="*"))
        db.commit()
        assert webhooks.encrypt_legacy_secrets(db) == 1
        ep = db.query(WebhookEndpoint).one()
        assert ep.secret.startswith("fernet:")
        assert webhooks.endpoint_secret(ep) == "legacy"
        assert webhooks.encrypt_legacy_secrets(db) == 0


def test_invalid_event_names_are_refused():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
    resp = kit.client(admin.id).post("/api/resellers/webhooks", json={"url": "https://h.example.com", "events": "account.*,DROP TABLE"})
    assert resp.status_code == 400


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "::ffff:192.168.0.1"])
def test_hostnames_resolving_inside_are_blocked(address):
    fake = [(None, None, None, None, (address, 443))]
    with patch.object(webhooks.socket, "getaddrinfo", return_value=fake):
        with pytest.raises(ValueError):
            webhooks.check_destination("https://innocent.example.com/hook")


def test_public_destination_is_allowed_and_redirects_are_not_followed():
    fake = [(None, None, None, None, ("93.184.216.34", 443))]
    with patch.object(webhooks.socket, "getaddrinfo", return_value=fake):
        webhooks.check_destination("https://example.com/hook")
    assert webhooks._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/") is None


def test_event_catalogue_is_served():
    with SessionLocal() as db:
        admin = kit.make_user(db, "admin1", role="admin")
    body = kit.client(admin.id).get("/api/resellers/webhooks/events").json()
    names = {e["name"] for e in body["events"]}
    assert {"account.created", "account.terminated", "account.password_changed"} <= names
    assert body["signature"]["signature_header"] == "X-SPanel-Signature-256"
