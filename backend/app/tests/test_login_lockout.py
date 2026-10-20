"""The login lockout has to survive the attacker's own successful login.

Two keys guard POST /auth/login: the source address and the username. Only the
source key carries a hard lockout - the username key is deliberately built
without one so nobody can lock a victim out of their own account by guessing at
it. That makes the source lockout the only hard stop there is.

_record_success used to wipe all three counters (attempts, failures, lockout)
for *both* keys on any successful login. A tenant with one ordinary account
could therefore guess at the admin, log into their own account before the 20th
failure, and start again from zero - forever, and with a fresh burst window too.

The source key now keeps its failure history and lockout. They expire on their
own TTLs instead of being cleared by a success that says nothing about them.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AUTH = PROJECT_ROOT / "backend" / "app" / "api" / "auth.py"
SERVE = PROJECT_ROOT / "backend" / "app" / "serve.py"


def _auth_source() -> str:
    return AUTH.read_text(encoding="utf-8")


def test_a_successful_login_does_not_clear_the_source_lockout():
    src = _auth_source()
    assert "_clear_short_window(ip_key)" in src, (
        "the source key must keep its failures and lockout after a success"
    )
    assert "_record_success(ip_key)" not in src, (
        "_record_success deletes attempts, failures AND lockout - calling it on "
        "the source key is what let a tenant launder another account's failures"
    )
    # The username key is the attacker's own account; clearing it fully is right.
    assert "_record_success(user_key)" in src


def test_the_short_window_clear_keeps_failures_and_lockout():
    """Covers both backends: the redis helper and the in-memory path."""
    src = _auth_source()
    start = src.index("def _redis_clear_window(")
    end = src.index("def _issue_login_session(", start)
    body = src[start:end]

    assert '_rate_limit_key("attempts"' in body
    for forbidden in (
        '_rate_limit_key("failures"',
        '_rate_limit_key("lockout"',
        "_login_failures.pop",
        "_login_lockouts.pop",
    ):
        assert forbidden not in body, (
            f"the source-key clear must not touch {forbidden} - that is the "
            "lockout a tenant was laundering"
        )


def test_the_username_key_still_has_no_lockout():
    """Guards the deliberate decision the fix must not disturb.

    Putting a lockout on the username key would let anyone lock the admin out
    by guessing at them. The short window stays; the lockout must not appear.
    """
    src = _auth_source()
    assert "apply_lockout=False" in src


def test_nothing_is_trusted_to_set_the_client_address():
    """The rate-limit key and the audit log IP both come from request.client.

    uvicorn's ProxyHeadersMiddleware rewrites that from X-Forwarded-For for any
    peer in forwarded_allow_ips. There is no reverse proxy in front of the panel
    - it terminates TLS itself - so trusting 127.0.0.1 meant trusting every
    local process, which on a hosting box is every tenant's PHP and Node.
    """
    serve = SERVE.read_text(encoding="utf-8")
    assert "TRUSTED_FORWARDERS: list[str] = []" in serve
    assert '"proxy_headers": False' in serve
    assert '"forwarded_allow_ips": "127.0.0.1"' not in serve
