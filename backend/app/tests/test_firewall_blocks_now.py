"""Blocking an address has to stop an attack that is already under way.

A customer blocked 140.245.96.0/19 on a live server and watched 140.245.105.90
keep POSTing /wp-login.php for half an hour afterwards. The rule was right and
the ipset held the address; the packets never reached the rule. spanel-INPUT
returned on ESTABLISHED,RELATED before it consulted the deny sets, so every
connection the attacker had already opened stayed usable, and an HTTP
keep-alive is open for as long as the client wants it.

These tests pin the two halves of the answer: the deny sets are consulted
before the conntrack return, and applying the firewall closes sockets that the
sets now cover.
"""

import re
from pathlib import Path

import pytest

from app.services import firewall

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


@pytest.fixture(scope="module")
def helper_source():
    return HELPER_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chain_body(helper_source):
    """The body of firewall_apply_family, where the chain is built."""
    start = helper_source.index("firewall_apply_family() {")
    end = helper_source.index("\nfirewall_kill_blocked_connections() {", start)
    return helper_source[start:end]


def _rule_order(chain_body, needle):
    index = chain_body.find(needle)
    assert index != -1, f"no rule matching {needle!r} is appended to the chain"
    return index


# --- the ordering that let the attack continue ------------------------------

def test_deny_sets_are_consulted_before_the_established_return(chain_body):
    """The whole bug, in one assertion."""
    deny = _rule_order(chain_body, '--match-set "$sd" src -j DROP')
    established = _rule_order(chain_body, "--ctstate ESTABLISHED,RELATED -j RETURN")
    assert deny < established, (
        "a connection opened before the block was added must not outlive it"
    )


def test_the_url_blocklist_is_also_consulted_before_the_established_return(chain_body):
    blocklist = _rule_order(chain_body, '--match-set "$sb" src -j DROP')
    established = _rule_order(chain_body, "--ctstate ESTABLISHED,RELATED -j RETURN")
    assert blocklist < established


def test_allow_still_wins_over_deny(chain_body):
    allow = _rule_order(chain_body, '--match-set "$sa" src -j RETURN')
    deny = _rule_order(chain_body, '--match-set "$sd" src -j DROP')
    assert allow < deny, "an explicit allow has to beat the blocklists"


def test_loopback_is_still_first(chain_body):
    loopback = _rule_order(chain_body, "-i lo -j RETURN")
    assert loopback < _rule_order(chain_body, '--match-set "$sa" src -j RETURN')


def test_the_established_return_still_precedes_the_port_rules(chain_body):
    """Established traffic on any port must keep flowing once it is allowed."""
    established = _rule_order(chain_body, "--ctstate ESTABLISHED,RELATED -j RETURN")
    ports = _rule_order(chain_body, '-p tcp --dport "$port" -j RETURN')
    assert established < ports


def test_the_documented_chain_layout_matches_the_chain(helper_source):
    """The comment above the engine is the thing people read first."""
    layout = helper_source[helper_source.index("# Chain layout"):]
    layout = layout[:layout.index("##########")]
    deny_line = layout.index("deny sets")
    established_line = layout.index("ESTABLISHED,RELATED")
    assert deny_line < established_line, (
        "the documented order still shows the old chain"
    )


# --- closing what is already open -------------------------------------------

def test_applying_the_firewall_closes_connections_from_blocked_addresses(helper_source):
    apply_body = helper_source[helper_source.index("firewall_apply() {"):]
    apply_body = apply_body[:apply_body.index("\nfirewall_flush() {")]
    assert "firewall_kill_blocked_connections" in apply_body, (
        "a DROP rule stops packets but leaves the socket open; the connection "
        "has to be closed for the block to mean anything right away"
    )


def test_the_kill_walks_sockets_not_the_blocklist(helper_source):
    """182k addresses in the set, a few hundred sockets on the box."""
    body = helper_source[helper_source.index("firewall_kill_blocked_connections() {"):]
    body = body[:body.index("\nfirewall_apply() {")]
    assert "ss -tnH state established" in body
    assert "ipset test" in body
    assert "ss -K dst" in body


def test_the_kill_covers_every_deny_set(helper_source):
    body = helper_source[helper_source.index("firewall_kill_blocked_connections() {"):]
    body = body[:body.index("\nfirewall_apply() {")]
    for name in ("spanel-deny4", "spanel-block4", "spanel-deny6", "spanel-block6"):
        assert name in body, f"{name} is not swept for live connections"


def test_the_kill_survives_a_kernel_without_socket_destroy(helper_source):
    """ss -K needs CONFIG_INET_DIAG_DESTROY. Not every kernel has it."""
    body = helper_source[helper_source.index("firewall_kill_blocked_connections() {"):]
    body = body[:body.index("\nfirewall_apply() {")]
    assert "ss -K dst \"$ip\" >/dev/null 2>&1" in body
    assert body.rstrip().endswith("return 0\n}") or "return 0" in body


# --- not locking the operator out -------------------------------------------

@pytest.mark.parametrize(
    "network,address",
    [
        ("140.245.96.0/19", "140.245.105.90"),
        ("203.0.113.7/32", "203.0.113.7"),
        ("10.0.0.0/8", "10.1.2.3"),
        ("2001:db8::/32", "2001:db8::1"),
    ],
)
def test_a_rule_covering_your_own_address_is_refused(network, address):
    with pytest.raises(ValueError) as exc:
        firewall.block_ip(network, requester=address)
    assert address in str(exc.value)
    assert "lock you out" in str(exc.value)


@pytest.mark.parametrize(
    "network,address",
    [
        ("140.245.96.0/19", "140.245.200.1"),
        ("203.0.113.7/32", "203.0.113.8"),
        ("2001:db8::/32", "2001:dead::1"),
        ("140.245.96.0/19", None),
        ("140.245.96.0/19", "not-an-address"),
    ],
)
def test_an_unrelated_address_does_not_block_the_rule(network, address, monkeypatch):
    calls = []
    monkeypatch.setattr(
        firewall.shell, "privileged",
        lambda *args, **kwargs: calls.append(args) or object(),
    )
    firewall.block_ip(network, requester=address)
    assert calls, "the rule should have reached the helper"


def test_covers_address_says_no_to_nonsense():
    assert firewall.covers_address("10.0.0.0/8", "") is False
    assert firewall.covers_address("10.0.0.0/8", None) is False
    assert firewall.covers_address("not-a-network", "10.0.0.1") is False
    assert firewall.covers_address("10.0.0.0/8", "10.0.0.1 ") is True
