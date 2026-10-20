from types import SimpleNamespace

import pytest

from app.api.provisioning import _provisioning_app_type, _provisioning_email
from app.schemas.schemas import ProvisioningAccountOut
from app.services import provisioning as provisioning_service
from app.services.provisioning import account_to_dict


def test_provisioning_email_is_stable_per_username():
    assert _provisioning_email("bp_123") == "bp_123@users.spanel.dev"


def test_provisioning_email_ignores_customer_email():
    assert _provisioning_email("bp456_abcd") != "customer@example.com"


def test_provisioning_without_domain_is_panel_user_only():
    payload = SimpleNamespace(domain=None, install_wordpress=False, app_type="wordpress")

    assert _provisioning_app_type(payload) == "php"


def test_account_to_dict_uses_package_and_whmcs_service_id_label():
    account = SimpleNamespace(
        external_id="whmcs:123",
        user=SimpleNamespace(username="bp_123", email="bp_123@users.spanel.dev"),
        primary_website=None,
        package=SimpleNamespace(name="Starter"),
        package_id=2,
        status="active",
        created_at=None,
    )

    data = account_to_dict(account, None)

    assert data["domain"] is None
    assert data["service_label"] == "Starter #123"


def _terminated_account():
    return SimpleNamespace(
        external_id="whmcs:123",
        user=None,
        primary_website=None,
        package=SimpleNamespace(name="Starter"),
        package_id=2,
        status="terminated",
        created_at=None,
    )


def test_terminated_account_still_serializes():
    """The billing record outlives the panel user it pointed at.

    Returning None for username/email failed ProvisioningAccountOut validation,
    so WHMCS got a 500 when rendering a terminated service.
    """
    data = account_to_dict(_terminated_account(), None)

    assert data["username"] == ""
    assert data["email"] == ""
    out = ProvisioningAccountOut(**data)
    assert out.status == "terminated"


def test_account_dict_exposes_panel_url(monkeypatch):
    monkeypatch.setattr(provisioning_service, "panel_base_url", lambda: "https://panel.example.com")

    data = account_to_dict(_terminated_account(), None)

    assert data["panel_url"] == "https://panel.example.com"


@pytest.mark.parametrize(
    "panel_url,panel_domain,expected",
    [
        ("https://panel.example.com/", "", "https://panel.example.com"),
        # No URL configured, only a domain: the link still has to name the port
        # the panel listens on, or it points at Nginx instead of at the panel.
        ("", "panel.example.com", "https://panel.example.com:2222"),
        ("", "", ""),
    ],
)
def test_panel_base_url_prefers_configured_url(monkeypatch, panel_url, panel_domain, expected):
    from app.core import config
    from app.services import panel_settings

    monkeypatch.setattr(panel_settings, "configured_panel_url", lambda: panel_url.rstrip("/"))
    monkeypatch.setattr(config.settings, "panel_url", panel_url)
    monkeypatch.setattr(config.settings, "panel_domain", panel_domain)
    monkeypatch.setattr(config.settings, "panel_port", 2222)

    assert provisioning_service.panel_base_url() == expected
