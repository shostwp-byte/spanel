import pyotp
from fastapi import HTTPException, status

from app.core.security import verify_password
from app.core.secrets import decrypt
from app.models.entities import User


def verify_totp(user: User, code: str | None) -> bool:
    if not user.totp_secret:
        return False
    try:
        secret = decrypt(user.totp_secret)
    except Exception:
        return False
    clean_code = (code or "").replace(" ", "").strip()
    if not clean_code:
        return False
    return pyotp.TOTP(secret).verify(clean_code, valid_window=1)


def require_current_password(user: User, password: str | None) -> None:
    if not password or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )


def require_totp_if_enabled(user: User, code: str | None) -> None:
    if user.totp_enabled and not verify_totp(user, code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication code",
        )


def require_sensitive_action_step_up(user: User, password: str | None, code: str | None = None) -> None:
    require_current_password(user, password)
    require_totp_if_enabled(user, code)
