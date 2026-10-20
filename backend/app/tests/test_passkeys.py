"""Passkeys, and why they do not replace the authenticator app.

WebAuthn binds a credential to a Relying Party ID, which is a hostname, and a
browser will not so much as admit a credential exists to any other name.
app/serve.py answers on every hostname on the machine that has a certificate,
so the panel derives the RP ID from the request and stores it on the
credential: one account, one passkey per name it signs in through.

That binding is exactly why TOTP stays live beside it. A passkey registered for
panel.example.com is not offered when the same customer reaches the panel by
its IP, and a second factor that can strand somebody on the wrong hostname is
not a security feature. The login offers the passkey first and falls through.

What the tests below actually guard is the trust boundary: the RP ID and the
origin are derived on the server, the challenge is one the server issued and
can only be spent once, and a counter that goes backwards is a cloned key.
"""

import ast
import re
from pathlib import Path

import pytest

from app.services import passkeys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AUTH_API = PROJECT_ROOT / "backend" / "app" / "api" / "auth.py"


# --- what a hostname may be -------------------------------------------------

@pytest.mark.parametrize("host,expected", [
    ("panel.example.com:2222", "panel.example.com"),
    ("panel.example.com", "panel.example.com"),
    ("PANEL.Example.COM.", "panel.example.com"),
    ("localhost:5173", "localhost"),
])
def test_a_domain_becomes_the_relying_party_id(host, expected):
    assert passkeys.rp_id_for_host(host) == expected


@pytest.mark.parametrize("host", ["163.61.72.88:2222", "10.0.0.1", "[::1]:2222", "nodots", "", "   "])
def test_an_address_is_not_a_relying_party_id(host):
    """WebAuthn requires a domain.

    Reaching the panel by IP has to answer "passkeys are not available here"
    rather than produce a failure the customer cannot interpret.
    """
    assert passkeys.rp_id_for_host(host) == ""
    assert passkeys.supported(host) is False


def test_the_origin_keeps_the_port():
    """The browser reports the origin with its port; a mismatch fails the check."""
    assert passkeys.origin_for_request("https", "panel.example.com:2222") == "https://panel.example.com:2222"


# --- what is derived rather than accepted -----------------------------------

def test_the_relying_party_id_never_comes_from_the_request_body():
    """It is half of what a signature is checked against.

    A caller who could name the RP ID could choose which credential their
    assertion appears to satisfy.
    """
    src = (PROJECT_ROOT / "backend" / "app" / "services" / "passkeys.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef,)):
            continue
        if node.name not in {"verify_registration", "verify_authentication"}:
            continue
        body = ast.unparse(node)
        assert "rp_id = rp_id_for_host(host)" in body, (
            f"{node.name} must derive the RP ID from the request host"
        )
        assert "expected_origin=origin_for_request(" in body


def test_a_passkey_for_another_hostname_is_refused(monkeypatch):
    class _Stored:
        rp_id = "other.example.com"
        public_key = passkeys.b64(b"x")
        sign_count = 0

    with pytest.raises(ValueError, match="different hostname"):
        passkeys.verify_authentication(
            credential_json="{}",
            challenge=b"c",
            stored=_Stored(),
            scheme="https",
            host="panel.example.com:2222",
        )


# --- the challenge ----------------------------------------------------------

def test_a_challenge_is_random_and_long_enough():
    seen = {passkeys.new_challenge() for _ in range(50)}
    assert len(seen) == 50
    assert all(len(c) >= 32 for c in seen)


def test_challenges_are_scoped_to_the_hostname_and_the_subject():
    a = passkeys.challenge_key("auth", "7", "panel.example.com")
    b = passkeys.challenge_key("auth", "7", "other.example.com")
    c = passkeys.challenge_key("auth", "8", "panel.example.com")
    assert a != b != c and a != c


def test_a_challenge_is_spent_when_it_is_read():
    """Read-and-delete. One that can be spent twice is not a nonce."""
    from app.api import auth as auth_api

    key = passkeys.challenge_key("auth", "test-user", "panel.example.com")
    auth_api._challenge_store(key, b"a-challenge-value-32-bytes-long!!")
    assert auth_api._challenge_take(key) == b"a-challenge-value-32-bytes-long!!"
    assert auth_api._challenge_take(key) is None, "a challenge was accepted twice"


def test_a_challenge_does_not_live_long():
    """Long enough to pick a key up off the desk, short enough to be worthless."""
    assert 30 <= passkeys.CHALLENGE_TTL_SECONDS <= 600


# --- the counter ------------------------------------------------------------

def test_the_source_of_truth_for_the_counter_rule_is_written_down():
    """A counter that goes backwards means the credential was cloned.

    An authenticator that does not count reports 0 forever, which the spec
    allows and which must not be mistaken for a clone - the rule has to say
    both halves.
    """
    src = (PROJECT_ROOT / "backend" / "app" / "services" / "passkeys.py").read_text(encoding="utf-8")
    body = src.split("def verify_authentication(", 1)[1].split("\ndef ", 1)[0]
    assert "went backwards" in body
    assert "new_count and" in body, (
        "an authenticator reporting 0 every time is legal and must still work"
    )


# --- the fallback, which is the point --------------------------------------

def test_login_offers_the_passkey_first_and_keeps_the_app_available():
    src = AUTH_API.read_text(encoding="utf-8")
    body = src.split("def login(", 1)[1].split("\n@router", 1)[0]
    assert "requires_passkey=True" in body
    assert "requires_2fa=bool(user.totp_enabled)" in body, (
        "the passkey challenge must also say whether an authenticator app is "
        "available, or a customer on the wrong hostname has no way in"
    )


def test_a_second_factor_that_cannot_be_checked_is_refused_not_ignored():
    """Sending an otp or a passkey to an account with neither must fail."""
    src = AUTH_API.read_text(encoding="utf-8")
    body = src.split("def login(", 1)[1].split("\n@router", 1)[0]
    assert "no second factor configured" in body


def test_a_failed_passkey_counts_against_the_rate_limit():
    """Otherwise it is an unlimited oracle next to a limited one."""
    src = AUTH_API.read_text(encoding="utf-8")
    body = src.split("if passkey and site_passkeys:", 1)[1].split("elif site_passkeys", 1)[0]
    assert body.count("_record_failure(ip_key, apply_lockout=True)") >= 2, (
        "both the expired-challenge and rejected-assertion paths have to record "
        "a failure"
    )


# --- lifecycle --------------------------------------------------------------

def test_deleting_a_user_takes_their_passkeys():
    """A row pointing at a freed user id is a way in for whoever inherits it."""
    src = (PROJECT_ROOT / "backend" / "app" / "services" / "teardown.py").read_text(encoding="utf-8")
    assert "purge_owner_passkeys" in src
    assert '"passkeys": purge_owner_passkeys(db, owner_id)' in src


def test_registering_a_passkey_needs_the_same_step_up_as_setting_up_totp():
    src = AUTH_API.read_text(encoding="utf-8")
    body = src.split("def passkey_register_options(", 1)[1].split("\n@router", 1)[0]
    assert "require_sensitive_action_step_up" in body


# --- the ceremonies, driven for real ----------------------------------------
#
# Everything above reads the source. These build the bytes a security key would
# actually produce - clientDataJSON, authenticator data, a COSE public key and
# an ECDSA signature - and put them through the panel's own verification path.
#
# The registration and authentication cases prove it works. The five after them
# are the ones worth having: they prove it refuses.

from app.tests._soft_authenticator import SoftAuthenticator  # noqa: E402

HOST = "panel.example.com:2222"
ORIGIN = "https://panel.example.com:2222"
RP = "panel.example.com"


class _User:
    id = 42
    username = "probe"


class _Stored:
    def __init__(self, record):
        self.credential_id = record["credential_id"]
        self.public_key = record["public_key"]
        self.sign_count = record["sign_count"]
        self.rp_id = record["rp_id"]


def _register(authenticator):
    _options, challenge = passkeys.registration_options(_User(), HOST, [])
    attestation = authenticator.register(rp_id=RP, challenge=challenge, origin=ORIGIN)
    return passkeys.verify_registration(
        credential_json=attestation, challenge=challenge, scheme="https", host=HOST
    )


def test_a_real_attestation_verifies_and_binds_to_the_hostname():
    record = _register(SoftAuthenticator())
    assert record["public_key"]
    assert record["rp_id"] == RP
    assert record["transports"] == "internal"


def test_a_real_assertion_verifies_and_advances_the_counter():
    authenticator = SoftAuthenticator()
    stored = _Stored(_register(authenticator))
    _options, challenge = passkeys.authentication_options([stored], HOST)
    assertion = authenticator.assert_(rp_id=RP, challenge=challenge, origin=ORIGIN)
    assert passkeys.verify_authentication(
        credential_json=assertion, challenge=challenge, stored=stored, scheme="https", host=HOST
    ) == 1
    assert passkeys.credential_id_from(assertion) == stored.credential_id


def test_a_captured_assertion_cannot_be_replayed():
    """The signature is valid; the challenge is not the one we just issued."""
    authenticator = SoftAuthenticator()
    stored = _Stored(_register(authenticator))
    _o, first = passkeys.authentication_options([stored], HOST)
    assertion = authenticator.assert_(rp_id=RP, challenge=first, origin=ORIGIN)
    _o, second = passkeys.authentication_options([stored], HOST)
    with pytest.raises(Exception):
        passkeys.verify_authentication(
            credential_json=assertion, challenge=second, stored=stored, scheme="https", host=HOST
        )


def test_an_assertion_signed_for_another_site_is_refused():
    """The origin is in what gets signed, and the server derives its own."""
    authenticator = SoftAuthenticator()
    stored = _Stored(_register(authenticator))
    _o, challenge = passkeys.authentication_options([stored], HOST)
    elsewhere = authenticator.assert_(rp_id=RP, challenge=challenge, origin="https://evil.example.com")
    with pytest.raises(Exception):
        passkeys.verify_authentication(
            credential_json=elsewhere, challenge=challenge, stored=stored, scheme="https", host=HOST
        )


def test_an_assertion_signed_for_another_hostname_is_refused():
    authenticator = SoftAuthenticator()
    stored = _Stored(_register(authenticator))
    _o, challenge = passkeys.authentication_options([stored], HOST)
    elsewhere = authenticator.assert_(rp_id="other.example.com", challenge=challenge, origin=ORIGIN)
    with pytest.raises(Exception):
        passkeys.verify_authentication(
            credential_json=elsewhere, challenge=challenge, stored=stored, scheme="https", host=HOST
        )


def test_a_cloned_key_is_caught_by_the_counter():
    """Same private key, same credential id, a counter that went backwards."""
    authenticator = SoftAuthenticator()
    stored = _Stored(_register(authenticator))
    clone = SoftAuthenticator()
    clone.key = authenticator.key
    clone.credential_id = authenticator.credential_id
    stored.sign_count = 50
    _o, challenge = passkeys.authentication_options([stored], HOST)
    with pytest.raises(Exception):
        passkeys.verify_authentication(
            credential_json=clone.assert_(rp_id=RP, challenge=challenge, origin=ORIGIN),
            challenge=challenge, stored=stored, scheme="https", host=HOST,
        )


def test_an_authenticator_that_never_counts_still_works():
    """Reporting 0 forever is legal, and must not look like a clone."""
    flat = SoftAuthenticator(counts=False)
    stored = _Stored(_register(flat))
    _o, challenge = passkeys.authentication_options([stored], HOST)
    assert passkeys.verify_authentication(
        credential_json=flat.assert_(rp_id=RP, challenge=challenge, origin=ORIGIN),
        challenge=challenge, stored=stored, scheme="https", host=HOST,
    ) == 0


def test_the_step_up_password_is_not_asked_for_with_a_prompt():
    """prompt() renders a password in clear text in a browser dialog.

    It is also a modal that blocks everything else on the page. The passkey
    flow asks for the current password in a masked field instead, the way the
    admin account form already does.
    """
    src = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    body = src.split("async function addPasskey()", 1)[1].split("\n  async function ", 1)[0]
    assert "prompt(" not in body, "the passkey step-up still uses a browser prompt"
    assert "passkeyPassword" in body


# --- the second-factor decision table ---------------------------------------
#
# Who is owed a second factor, and which one, given what the account holds and
# which hostname the browser is talking to. Driven through login() itself
# rather than read off the source: the branch that was wrong here read as
# perfectly reasonable.

from types import SimpleNamespace  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api import auth as auth_api  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.entities import Base, User, WebauthnCredential  # noqa: E402

PASSWORD = "correct-horse-battery"


class _FakeRequest:
    def __init__(self, host):
        self.headers = {"host": host}
        self.url = SimpleNamespace(scheme="https", netloc=host, path="/api/auth/login")
        self.client = SimpleNamespace(host="203.0.113.7")
        self.cookies = {}


class _FakeResponse:
    def __init__(self):
        self.cookies = {}

    def set_cookie(self, key, value, **kw):
        self.cookies[key] = value

    def delete_cookie(self, *a, **kw):
        pass


@pytest.fixture()
def login_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _account(db, *, totp=False, passkey_hosts=()):
    user = User(
        username="tenant",
        email="t@example.test",
        hashed_password=hash_password(PASSWORD),
        role="end_user",
        totp_enabled=totp,
        totp_secret="x" if totp else None,
    )
    db.add(user)
    db.flush()
    for host in passkey_hosts:
        db.add(WebauthnCredential(
            user_id=user.id,
            credential_id=f"cred-{host}",
            public_key="k",
            rp_id=host,
            name=host,
        ))
    db.commit()
    return user


def _attempt(db, host, *, otp="", passkey="", monkeypatch=None):
    form = SimpleNamespace(username="tenant", password=PASSWORD)
    return auth_api.login(
        request=_FakeRequest(host),
        response=_FakeResponse(),
        form=form,
        otp=otp,
        passkey=passkey,
        remember="",
        db=db,
    )


def test_a_passkey_for_this_hostname_is_demanded(login_db, monkeypatch):
    monkeypatch.setattr(auth_api, "_record_success", lambda *a, **k: None)
    _account(login_db, passkey_hosts=["panel.example.com"])
    result = _attempt(login_db, "panel.example.com:2222")
    assert result.requires_passkey is True
    assert result.passkey_options
    assert result.access_token is None


def test_the_authenticator_app_is_advertised_beside_it(login_db, monkeypatch):
    """So the login page can offer a way out when the key will not cooperate."""
    monkeypatch.setattr(auth_api, "_record_success", lambda *a, **k: None)
    _account(login_db, totp=True, passkey_hosts=["panel.example.com"])
    result = _attempt(login_db, "panel.example.com:2222")
    assert result.requires_passkey is True
    assert result.requires_2fa is True, (
        "a customer whose passkey fails has to be told the app is available"
    )


def test_sending_a_code_instead_skips_straight_to_the_app(login_db, monkeypatch):
    """Choosing the fallback must not be blocked by the passkey being offered."""
    monkeypatch.setattr(auth_api, "_record_success", lambda *a, **k: None)
    monkeypatch.setattr(auth_api, "_verify_totp", lambda user, code: code == "123456")
    _account(login_db, totp=True, passkey_hosts=["panel.example.com"])
    result = _attempt(login_db, "panel.example.com:2222", otp="123456")
    assert result.access_token, "the authenticator code was not accepted as a fallback"


def test_a_wrong_code_in_the_fallback_is_still_refused(login_db, monkeypatch):
    monkeypatch.setattr(auth_api, "_verify_totp", lambda user, code: False)
    _account(login_db, totp=True, passkey_hosts=["panel.example.com"])
    with pytest.raises(HTTPException) as exc:
        _attempt(login_db, "panel.example.com:2222", otp="000000")
    assert exc.value.status_code == 401


def test_another_hostname_falls_back_to_the_app(login_db, monkeypatch):
    """The passkey is invisible there, so the app is what is asked for."""
    monkeypatch.setattr(auth_api, "_record_success", lambda *a, **k: None)
    _account(login_db, totp=True, passkey_hosts=["panel.example.com"])
    result = _attempt(login_db, "other.example.com:2222")
    assert result.requires_passkey is False
    assert result.requires_2fa is True


def test_another_hostname_with_no_app_is_refused_not_waved_through(login_db):
    """The bypass this test exists for.

    An account with a passkey for one name and no authenticator app used to
    fall through every branch at any other name and be let in on the password
    alone - a second factor the customer had switched on, silently not
    applying. serve.py answers on every hostname with a certificate, so
    reaching a second name is ordinary, not an edge case.
    """
    _account(login_db, passkey_hosts=["panel.example.com"])
    with pytest.raises(HTTPException) as exc:
        _attempt(login_db, "other.example.com:2222")
    assert exc.value.status_code == 401
    # Whole token, not substring: a message naming "notpanel.example.com"
    # would be telling them the wrong place, and must not pass this.
    named = [word.strip(" ,.") for word in str(exc.value.detail).split()]
    assert any(word == "panel.example.com" for word in named), (
        "tell them where their passkey does work"
    )


def test_by_ip_with_only_a_passkey_is_refused_too(login_db):
    """An address cannot be a Relying Party ID, so no passkey is usable."""
    _account(login_db, passkey_hosts=["panel.example.com"])
    with pytest.raises(HTTPException) as exc:
        _attempt(login_db, "203.0.113.9:2222")
    assert exc.value.status_code == 401


def test_an_account_with_neither_still_signs_in_on_the_password(login_db, monkeypatch):
    """Closing the bypass must not start demanding a factor nobody has."""
    monkeypatch.setattr(auth_api, "_record_success", lambda *a, **k: None)
    _account(login_db)
    result = _attempt(login_db, "panel.example.com:2222")
    assert result.access_token


def test_clicking_login_does_not_send_the_click_as_a_passkey():
    """login(passkeyAssertion) was wired as onClick={login}, so React handed it
    the click event: every click posted passkey=[object Object], and an account
    without two-factor was refused with "This account has no second factor
    configured". Only pressing Enter in a field - login() with no argument -
    ever signed anyone in."""
    app = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    code = re.sub(r"\{/\*.*?\*/\}", "", app, flags=re.S)
    assert "onClick={login}" not in code
    assert "onClick={() => login()}" in code
