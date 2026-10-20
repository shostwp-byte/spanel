"""A link back into the panel follows the hostname the request came in on."""

import pytest
from starlette.requests import Request

from app.core import config, panel_sni
from app.services import panel_settings, panel_urls


class _FakeStore:
    """Stands in for the certificates the helper copies onto the machine."""

    def __init__(self, names):
        self.names = set(names)

    def context_for(self, hostname):
        return object() if panel_sni.normalize_hostname(hostname) in self.names else None

    def hostnames(self):
        return sorted(self.names)


@pytest.fixture
def served(monkeypatch):
    store = _FakeStore({"alpha.test", "beta.test"})
    monkeypatch.setattr(panel_sni, "store", lambda *a, **k: store)
    monkeypatch.setattr(panel_sni, "available_hostnames", store.hostnames)
    monkeypatch.setattr(panel_settings, "configured_panel_url", lambda: "https://panel.test:2222")
    monkeypatch.setattr(panel_urls, "has_panel_certificate", lambda: True)
    monkeypatch.setattr(config.settings, "panel_port", 2222)
    monkeypatch.setattr(config.settings, "panel_domain", "panel.test")
    return store


def _request(headers: dict) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/provisioning/accounts/1/login",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_link_uses_the_domain_the_request_arrived_on(served):
    request = _request({"host": "alpha.test:2222"})
    assert panel_urls.panel_base_url(request) == "https://alpha.test:2222"


def test_link_uses_the_forwarded_host_behind_nginx(served):
    request = _request({"host": "127.0.0.1:2222", "x-forwarded-host": "beta.test"})
    assert panel_urls.panel_base_url(request) == "https://beta.test:2222"


def test_link_keeps_the_panel_port_whatever_port_was_used(served):
    request = _request({"host": "alpha.test:443"})
    assert panel_urls.panel_base_url(request) == "https://alpha.test:2222"


def test_the_configured_hostname_is_always_good_enough(served):
    # panel.test has no certificate of its own here: it is the default one.
    request = _request({"host": "panel.test:2222"})
    assert panel_urls.panel_base_url(request) == "https://panel.test:2222"


def test_a_hostname_this_panel_does_not_serve_is_ignored(served):
    """A spoofed Host header must not end up inside a login link."""
    request = _request({"host": "evil.example.com"})
    assert panel_urls.panel_base_url(request) == "https://panel.test:2222"


def test_without_a_request_the_configured_url_is_used(served):
    assert panel_urls.panel_base_url() == "https://panel.test:2222"


def test_hostnames_list_starts_with_the_configured_one(served):
    assert panel_urls.panel_hostnames() == ["panel.test", "alpha.test", "beta.test"]


def test_http_panel_builds_http_links(served, monkeypatch):
    monkeypatch.setattr(panel_urls, "has_panel_certificate", lambda: False)
    request = _request({"host": "alpha.test:2222"})
    assert panel_urls.panel_base_url(request) == "http://alpha.test:2222"
