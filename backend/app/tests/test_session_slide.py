import time
import types

from jose import jwt
from starlette.responses import Response

from app.api import auth
from app.core.config import settings
from app.core.security import ALGORITHM, create_access_token


class _Url:
    scheme = "https"


def _request(cookies, payload=None, token=None):
    req = types.SimpleNamespace()
    req.cookies = cookies
    req.headers = {}
    req.url = _Url()
    req.state = types.SimpleNamespace()
    if payload is not None:
        req.state.jwt_payload = payload
    if token is not None:
        req.state.jwt_token = token
    return req


def _payload(sub="admin", *, age_seconds, lifetime_seconds, **extra):
    now = int(time.time())
    return {"sub": sub, "iat": now - age_seconds, "exp": now - age_seconds + lifetime_seconds, **extra}


def _session_cookie(response: Response) -> str | None:
    for name, value in response.raw_headers:
        if name.lower() == b"set-cookie" and value.startswith(b"spanel_session="):
            return value.decode("latin-1")
    return None


def _new_token_from(response: Response) -> str:
    header = _session_cookie(response)
    assert header is not None
    return header.split("spanel_session=", 1)[1].split(";", 1)[0]


def test_access_token_honours_explicit_lifetime():
    token = create_access_token("admin", {"role": "admin"}, expires_minutes=60 * 24 * 30)
    claims = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    assert abs((claims["exp"] - claims["iat"]) - 30 * 24 * 3600) < 5


def test_fresh_session_is_not_renewed():
    payload = _payload(age_seconds=10, lifetime_seconds=7200, role="admin", tv=0)
    token = "fresh-token"
    response = Response()
    auth.maybe_renew_session_cookie(_request({"spanel_session": token}, payload, token), response)
    assert _session_cookie(response) is None


def test_half_spent_session_is_renewed_at_the_same_lifetime():
    payload = _payload(age_seconds=3700, lifetime_seconds=7200, role="admin", tv=3)
    token = "old-token"
    response = Response()
    auth.maybe_renew_session_cookie(
        _request({"spanel_session": token, "spanel_csrf": "csrf-value"}, payload, token), response
    )
    claims = jwt.decode(_new_token_from(response), settings.secret_key, algorithms=[ALGORITHM])
    assert claims["sub"] == "admin"
    assert claims["tv"] == 3
    assert abs((claims["exp"] - claims["iat"]) - 7200) < 5
    # CSRF value is kept so an in-flight POST still matches.
    assert "spanel_csrf=csrf-value" in "\n".join(
        v.decode("latin-1") for n, v in response.raw_headers if n.lower() == b"set-cookie"
    )


def test_remember_me_lifetime_is_preserved_on_renewal():
    month = 30 * 24 * 3600
    payload = _payload(age_seconds=16 * 24 * 3600, lifetime_seconds=month, role="admin", tv=0)
    token = "remembered"
    response = Response()
    auth.maybe_renew_session_cookie(_request({"spanel_session": token}, payload, token), response)
    claims = jwt.decode(_new_token_from(response), settings.secret_key, algorithms=[ALGORITHM])
    assert abs((claims["exp"] - claims["iat"]) - month) < 60


def test_bearer_token_is_not_slid():
    payload = _payload(age_seconds=3700, lifetime_seconds=7200, role="admin", tv=0)
    token = "bearer-token"
    response = Response()
    # No matching session cookie -> this came in as an Authorization header.
    auth.maybe_renew_session_cookie(_request({}, payload, token), response)
    assert _session_cookie(response) is None


def test_no_renewal_when_handler_already_set_a_session_cookie():
    payload = _payload(age_seconds=3700, lifetime_seconds=7200, role="admin", tv=0)
    token = "old-token"
    response = Response()
    response.set_cookie("spanel_session", "handler-issued", path="/")
    auth.maybe_renew_session_cookie(_request({"spanel_session": token}, payload, token), response)
    assert _new_token_from(response) == "handler-issued"


def test_impersonation_session_is_not_slid():
    # An impersonation session keeps its hard expiry; the admin must re-initiate
    # "Login as" rather than stay in it indefinitely by keeping the tab busy.
    payload = _payload(age_seconds=3700, lifetime_seconds=7200, role="end_user", tv=0, imp=True)
    token = "imp-token"
    response = Response()
    auth.maybe_renew_session_cookie(_request({"spanel_session": token}, payload, token), response)
    assert _session_cookie(response) is None


def test_expired_session_is_left_for_the_auth_layer_to_reject():
    payload = _payload(age_seconds=8000, lifetime_seconds=7200, role="admin", tv=0)
    token = "expired"
    response = Response()
    auth.maybe_renew_session_cookie(_request({"spanel_session": token}, payload, token), response)
    assert _session_cookie(response) is None
