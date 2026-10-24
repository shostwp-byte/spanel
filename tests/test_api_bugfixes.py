"""Routing and dispatch faults in the billing integration.

Each test names the fault it guards against.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import api_testkit as kit
from app.core.database import SessionLocal
from app.models.entities import ProvisioningAccount, WebhookEndpoint


@pytest.fixture(autouse=True)
def _clean_db():
    kit.reset_db()
    yield


def test_bulk_usage_is_not_shadowed_by_the_external_id_route():
    """GET /accounts/usage was declared after /accounts/{external_id}, so
    WHMCS UsageUpdate always got 404 "Account not found"."""
    with SessionLocal() as db:
        package = kit.make_package(db)
        user = kit.make_user(db, "custone", role="end_user", package_id=package.id)
        db.add(ProvisioningAccount(external_id="whmcs:7", user_id=user.id, package_id=package.id, status="active"))
        db.commit()
        raw = kit.make_token(db)

    with patch("app.services.storage_quota.storage_usage_summary", return_value={
        "storage_used_bytes": 5, "storage_limit_bytes": 10, "storage_percent": 50.0,
    }):
        resp = kit.client().get("/api/provisioning/v1/accounts/usage", headers=kit.bearer(raw))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert [row["external_id"] for row in body] == ["whmcs:7"]
    assert body[0]["storage_used_bytes"] == 5


def test_clearing_webhook_history_is_not_shadowed_by_the_webhook_id_route():
    """DELETE /resellers/webhooks/deliveries matched /webhooks/{webhook_id}
    first and failed int validation with 422."""
    with SessionLocal() as db:
        admin = kit.make_user(db, "rootadmin", role="admin")
        admin_id = admin.id

    with patch("app.services.webhooks.clear_deliveries", return_value=3) as clear:
        resp = kit.client(admin_id).delete("/api/resellers/webhooks/deliveries")

    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 3
    clear.assert_called_once()


def test_system_events_reach_admin_webhooks():
    """dispatch_webhook returned early for user_id=None, and every
    provisioning call is audited with user_id=None, so billing events never
    produced a webhook."""
    from app.services import audit, webhooks

    with SessionLocal() as db:
        admin = kit.make_user(db, "hookadmin", role="admin")
        reseller = kit.make_user(db, "hookreseller", role="reseller")
        db.add(WebhookEndpoint(user_id=admin.id, url="https://billing.example.com/hook", events="*", is_active=True))
        db.add(WebhookEndpoint(user_id=reseller.id, url="https://reseller.example.com/hook", events="*", is_active=True))
        db.commit()

        class _InlineThread:
            def __init__(self, target, args=(), daemon=None):
                self._target, self._args = target, args

            def start(self):
                self._target(*self._args)

        with patch.object(webhooks, "send_webhook_sync") as send, \
                patch.object(webhooks, "threading", SimpleNamespace(Thread=_InlineThread)), \
                patch.object(audit, "_write_audit_anchor"):
            audit.log_action(db, None, "provisioning_create", "whmcs:9", detail="example.com")

    urls = sorted(call.args[0] for call in send.call_args_list)
    # The administrator's endpoint receives it; a reseller cannot see
    # system-wide provisioning events.
    assert urls == ["https://billing.example.com/hook"]
    payload = send.call_args_list[0].args[2]
    assert payload["event"] == "provisioning_create"
    assert payload["user_id"] is None
