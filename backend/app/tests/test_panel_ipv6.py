"""The IPv6 switch: off unless somebody turns it on, and only where it can work."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import nginx, panel_ipv6

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def helper(monkeypatch):
    """Stand in for the helper, recording what the panel asked it to do."""
    calls: list[str] = []
    replies: dict[str, _Result] = {}

    def fake(command, **kwargs):
        calls.append(command)
        return replies.get(command, _Result())

    monkeypatch.setattr(panel_ipv6.shell, "privileged", fake)
    return calls, replies


def test_status_reads_what_the_server_reports(helper):
    calls, replies = helper
    replies["ipv6-status"] = _Result(
        stdout="available=yes\nenabled=yes\naddresses=2001:db8::1,2001:db8::2\n"
    )

    state = panel_ipv6.status()

    assert calls == ["ipv6-status"]
    assert state["available"] is True
    assert state["enabled"] is True
    assert state["addresses"] == ["2001:db8::1", "2001:db8::2"]


def test_a_server_without_ipv6_says_so(helper):
    _, replies = helper
    replies["ipv6-status"] = _Result(stdout="available=no\nenabled=no\naddresses=\n")

    state = panel_ipv6.status()

    assert state == {
        "available": False,
        "enabled": False,
        "addresses": [],
        "detail": panel_ipv6.NO_IPV6_MESSAGE,
    }


def test_enabling_is_refused_when_the_server_has_no_ipv6(helper):
    calls, replies = helper
    replies["ipv6-status"] = _Result(stdout="available=no\nenabled=no\naddresses=\n")

    with pytest.raises(HTTPException) as raised:
        panel_ipv6.set_enabled(True)

    assert raised.value.status_code == 400
    assert "no IPv6 address" in raised.value.detail
    # Refused before the helper was ever asked to change anything.
    assert "ipv6-enable" not in calls


def test_enabling_asks_the_helper_when_the_server_has_ipv6(helper):
    calls, replies = helper
    replies["ipv6-status"] = _Result(stdout="available=yes\nenabled=yes\naddresses=2001:db8::1\n")

    state = panel_ipv6.set_enabled(True)

    assert "ipv6-enable" in calls
    assert state["enabled"] is True
    assert state["message"]


def test_a_helper_failure_becomes_a_readable_error(helper):
    _, replies = helper
    replies["ipv6-status"] = _Result(stdout="available=yes\nenabled=no\naddresses=2001:db8::1\n")
    replies["ipv6-enable"] = _Result(
        returncode=1, stderr="nginx refused the IPv6 configuration; nothing was changed"
    )

    with pytest.raises(HTTPException) as raised:
        panel_ipv6.set_enabled(True)

    assert "nginx refused" in raised.value.detail


def test_turning_it_off_never_needs_ipv6_to_be_present(helper):
    calls, replies = helper
    replies["ipv6-status"] = _Result(stdout="available=no\nenabled=no\naddresses=\n")

    panel_ipv6.set_enabled(False)

    assert "ipv6-disable" in calls


def test_the_marker_file_is_what_says_it_is_on(monkeypatch, tmp_path):
    marker = tmp_path / "ipv6-enabled"
    monkeypatch.setattr(panel_ipv6, "MARKER", marker)
    assert panel_ipv6.is_enabled() is False
    marker.write_text("", encoding="utf-8")
    assert panel_ipv6.is_enabled() is True


@pytest.mark.parametrize("app_type", ["wordpress", "php", "static"])
def test_a_new_website_listens_on_ipv6_only_when_the_switch_is_on(monkeypatch, app_type):
    monkeypatch.setattr(nginx.panel_ipv6, "is_enabled", lambda: False)
    off = nginx.render_vhost("example.test", "/home/bp_example/example.test", app_type=app_type)
    assert "listen 80;" in off
    assert "listen [::]:80;" not in off

    monkeypatch.setattr(nginx.panel_ipv6, "is_enabled", lambda: True)
    on = nginx.render_vhost("example.test", "/home/bp_example/example.test", app_type=app_type)
    assert "listen 80;" in on
    assert "listen [::]:80;" in on


def test_the_helper_checks_the_server_before_turning_it_on():
    helper_text = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "ipv6-enable)" in helper_text
    assert "ipv6_available || deny" in helper_text
    # A global address only: loopback and link-local carry nothing from outside.
    assert "ip -6 -o addr show scope global" in helper_text


def test_the_helper_puts_every_file_back_if_nginx_refuses():
    helper_text = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper_text.split("nginx_ipv6_apply() {", 1)[1].split("\n}", 1)[0]
    assert 'cp -a "$file" "${backup}/$(basename "$file")"' in body
    assert "nginx -t" in body
    assert 'cp -a "$file" "/etc/nginx/conf.d/$(basename "$file")"' in body
    assert "return 1" in body
    # And the marker goes away again, so the switch never claims to be on.
    assert '''      rm -f "$PANEL_IPV6_MARKER"''' in helper_text


def test_certbot_https_lines_get_an_ipv6_twin():
    helper_text = HELPER_SCRIPT.read_text(encoding="utf-8")
    # certbot writes 'listen 443 ssl; # managed by Certbot'. Without the
    # trailing comment in the pattern every HTTPS vhost would be skipped.
    assert r'listen\s+((?:\d{1,3}\.){3}\d{1,3}:)?(\d+)([^;]*);\s*(#.*)?$' in helper_text


def test_an_update_re_applies_the_switch():
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "/usr/local/sbin/spanel-helper ipv6-apply" in update


def test_the_switch_turns_itself_off_if_the_address_disappears():
    helper_text = HELPER_SCRIPT.read_text(encoding="utf-8")
    block = helper_text.split("ipv6-apply)", 1)[1].split(";;", 1)[0]
    assert "ipv6_is_enabled && ! ipv6_available" in block
    assert 'rm -f "$PANEL_IPV6_MARKER"' in block


def test_the_panel_socket_still_answers_ipv4(monkeypatch):
    """The panel's socket must serve both families, not IPv6 alone.

    asyncio sets IPV6_V6ONLY on every AF_INET6 socket it binds itself, so
    simply asking uvicorn to listen on :: took IPv4 away: the panel answered
    over IPv6 and refused every IPv4 client, on a live server.
    """
    import socket

    from app import serve

    monkeypatch.setattr(serve.panel_ipv6, "is_enabled", lambda: True)
    listener = serve.dual_stack_socket(0)
    if listener is None:
        pytest.skip("this machine cannot open an IPv6 socket")
    with listener:
        assert listener.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 0
        assert listener.family == socket.AF_INET6


def test_no_ipv6_socket_is_built_while_the_switch_is_off(monkeypatch):
    from app import serve

    monkeypatch.setattr(serve.panel_ipv6, "is_enabled", lambda: False)
    assert serve.dual_stack_socket(0) is None


def test_the_settings_page_is_told_the_server_addresses(monkeypatch):
    """The page shows IPv4 and IPv6, so both have to reach it."""
    from app.services import panel_settings, server_network

    monkeypatch.setattr(server_network, "ipv4_addresses", lambda: ["64.118.132.44"])
    monkeypatch.setattr(
        panel_ipv6,
        "status",
        lambda: {
            "available": True,
            "enabled": True,
            "addresses": ["2404:c140:1f00:32::1e:c2e"],
            "detail": "ok",
        },
    )

    state = panel_settings.current_settings()

    assert state["server_ipv4"] == ["64.118.132.44"]
    assert state["ipv6"]["addresses"] == ["2404:c140:1f00:32::1e:c2e"]


def test_only_reachable_addresses_are_reported(monkeypatch):
    """Loopback and link-local are real addresses that reach nobody."""
    from app.services import server_network

    class _Result:
        returncode = 0
        stdout = (
            "1: lo    inet 127.0.0.1/8 scope host lo\  valid_lft forever\n"
            "2: eth0    inet 64.118.132.44/22 brd 64.118.135.255 scope global eth0\n"
            "2: eth0    inet6 fe80::f843:38ff:fef7:9a00/64 scope link\n"
            "2: eth0    inet6 2404:c140:1f00:32::1e:c2e/64 scope global\n"
        )
        stderr = ""

    monkeypatch.setattr(server_network.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(server_network.subprocess, "run", lambda *a, **k: _Result())

    found = server_network._query("-4") + server_network._query("-6")

    assert found == ["64.118.132.44", "2404:c140:1f00:32::1e:c2e"]


def test_a_machine_without_the_ip_command_reports_nothing(monkeypatch):
    from app.services import server_network

    monkeypatch.setattr(server_network.shutil, "which", lambda name: None)
    assert server_network.addresses() == {"ipv4": [], "ipv6": []}


INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"


def test_a_fresh_install_switches_ipv6_on_when_the_server_has_it():
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "enable_ipv6_when_available() {" in install
    assert "  enable_ipv6_when_available" in install
    block = install.split("enable_ipv6_when_available() {", 1)[1].split("\nmain() {", 1)[0]
    # It asks the server first, and only then turns anything on.
    assert 'spanel-helper ipv6-status' in block
    assert '"$status" != *"available=yes"*' in block
    assert "spanel-helper ipv6-enable" in block


def test_detection_never_fails_the_install():
    """The installer runs under set -e; a server without IPv6 is not an error."""
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    block = install.split("enable_ipv6_when_available() {", 1)[1].split("\nmain() {", 1)[0]
    assert "|| true" in block
    assert ">/dev/null 2>&1" in block
    # And the summary says what was decided rather than leaving it a mystery.
    assert 'echo "IPv6: enabled on ${IPV6_RESULT#on:}"' in install


def test_an_update_never_turns_ipv6_on_by_itself():
    """An existing server keeps whatever its admin chose."""
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "ipv6-enable" not in update
    # It only re-applies the switch as it already stands.
    assert "spanel-helper ipv6-apply" in update


def test_ipv6_detection_reads_everything_before_deciding():
    """A server with two IPv6 addresses was intermittently told it had none.

    The helper runs under `set -o pipefail`, and `ip ... | grep -q inet6` fails
    whenever grep exits on the first address while iproute2 is still flushing
    the second: ip takes SIGPIPE and pipefail hands 141 up as the answer.
    """
    helper_text = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper_text.split("ipv6_available() {", 1)[1].split("\n}", 1)[0]
    assert 'found="$(ip -6 -o addr show scope global 2>/dev/null || true)"' in body
    assert '[[ -n "$found" ]]' in body
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))
    assert "grep -q" not in code


def test_the_helper_never_pipes_into_a_grep_that_exits_early():
    """Same trap, anywhere in the helper: pipefail turns it into a false answer."""
    helper_text = HELPER_SCRIPT.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in helper_text.splitlines()
        if "| grep -q" in line and not line.strip().startswith("#")
    ]
    assert offenders == []
