import pyotp
import pytest
from fastapi import HTTPException

from app.core import step_up
from app.core.security import hash_password
from app.core.secrets import encrypt
from app.models.entities import User


def _user(password: str = "correct-password", totp_enabled: bool = False, secret: str | None = None) -> User:
    return User(
        id=1,
        username="admin",
        email="admin@example.test",
        hashed_password=hash_password(password),
        role="admin",
        is_active=True,
        totp_enabled=totp_enabled,
        totp_secret=encrypt(secret) if secret else None,
    )


def test_sensitive_step_up_rejects_missing_current_password():
    with pytest.raises(HTTPException) as exc:
        step_up.require_sensitive_action_step_up(_user(), None)

    assert exc.value.status_code == 401


def test_sensitive_step_up_requires_totp_when_enabled():
    secret = pyotp.random_base32()
    user = _user(totp_enabled=True, secret=secret)

    with pytest.raises(HTTPException) as exc:
        step_up.require_sensitive_action_step_up(user, "correct-password", "000000")

    assert exc.value.status_code == 401


def test_sensitive_step_up_accepts_current_password_and_valid_totp():
    secret = pyotp.random_base32()
    user = _user(totp_enabled=True, secret=secret)
    code = pyotp.TOTP(secret).now()

    step_up.require_sensitive_action_step_up(user, "correct-password", code)


def test_sensitive_step_up_accepts_password_only_when_totp_disabled():
    step_up.require_sensitive_action_step_up(_user(), "correct-password")
