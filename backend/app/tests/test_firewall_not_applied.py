"""A firewall that is on as a setting must not be shown as on while nothing
enforces it.

A server built from a July image was updated straight to v1.0.170. The update
ran the July updater, which predates the iptables + ipset firewall: ipset was
never installed, UFW kept filtering, and the panel's chain was never applied.
The admin pressed "Turn on", which saved "enabled" before failing on the
missing ipset. From then on the dashboard and the alert said the firewall was
off (the chain is not active) while the Firewall page said "On" (the saved
setting). Both pages now read the chain, and the page offers the repair that
the missing update step would have done.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import firewall

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head:text.index(end, head)]


def test_turning_on_needs_the_tools_before_it_saves_on():
    verb = _between(HELPER, "  firewall-enable|ufw-enable)", ";;")
    assert verb.index("firewall_require_tools") < verb.index("firewall_set_state enabled")


def test_the_repair_installs_what_is_missing_then_takes_over_from_ufw():
    body = _between(HELPER, "firewall_repair() {", "\n}\n")
    assert "apt-get -o DPkg::Lock::Timeout=120 install -y iptables ipset" in body
    # Lists first: an image may carry stale ones.
    assert body.index("update --allow-releaseinfo-change") < body.index("install -y iptables ipset")
    assert body.rstrip().endswith("firewall_migrate")
    assert "  firewall-repair)\n    firewall_repair\n    ;;" in HELPER


@pytest.mark.parametrize("output, expected", [
    ("Status: enabled\nEngine: iptables + ipset\nChain active: no\n", False),
    ("Status: enabled\nEngine: iptables + ipset\nChain active: yes\n", True),
    ("Status: disabled\nChain active: no\n", False),
])
def test_the_dashboard_counts_an_unapplied_chain_as_off(monkeypatch, output, expected):
    monkeypatch.setattr(firewall, "status", lambda: SimpleNamespace(stdout=output, stderr="", returncode=0))
    assert firewall.is_enabled() is expected


def test_the_page_reads_the_chain_as_the_dashboard_does():
    page = _between(APP, "  function renderFirewall() {", "\n  function ")
    assert "line.startsWith('Chain active:')" in page
    assert "const notApplied = enabled && chainLine.includes('no');" in page
    assert "t('On, not applied')" in page
    assert "onClick={repairFirewall}" in page
    assert "runFirewallAction('/firewall/repair', { method: 'POST' }" in APP


def test_the_repair_route_is_for_administrators(monkeypatch):
    from fastapi import HTTPException

    from app.api import firewall as firewall_api

    calls = []
    monkeypatch.setattr(firewall, "repair", lambda: calls.append("repair") or SimpleNamespace(
        stdout="Firewall applied (enabled)\n", stderr="", returncode=0))
    with pytest.raises(HTTPException) as refused:
        firewall_api.repair_firewall(current_user=SimpleNamespace(id=2, role="end_user", username="khach"))
    assert refused.value.status_code == 403 and calls == []
    result = firewall_api.repair_firewall(current_user=SimpleNamespace(id=1, role="admin", username="admin"))
    assert calls == ["repair"] and "Firewall applied" in result["stdout"]
