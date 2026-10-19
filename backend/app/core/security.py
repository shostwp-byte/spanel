import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    import crypt as unix_crypt
except ImportError:  # pragma: no cover - crypt is Unix-only and removed in Python 3.13
    unix_crypt = None

from passlib.exc import UnknownHashError
from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings


# Plain bcrypt. To prevent the 72-byte silent truncation problem (see
# security audit #8) we:
#   1. Cap password length at 72 bytes in the Pydantic schemas (bcrypt 72-byte
#      limit), so users can never set a password whose meaningful prefix is
#      truncated.
#   2. Set ``truncate_error=True`` so passlib raises rather than silently
#      truncating if anything bypasses the schema.
#
# We tried bcrypt_sha256 (which pre-hashes with SHA-256 to bypass the limit)
# but the passlib<->bcrypt 4.x compatibility issue makes that path unreliable
# in production. Plain bcrypt with the input length cap is the simplest fix.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__truncate_error=True,
)
ALGORITHM = "HS256"
BCRYPT_HASH_PREFIXES = ("$2a$", "$2b$", "$2y$")
SHADOW_HASH_PREFIXES = ("$y$", "$gy$", "$7$", "$6$", "$5$", *BCRYPT_HASH_PREFIXES)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def is_shadow_password_hash(hashed_password: str) -> bool:
    return bool(hashed_password) and hashed_password.startswith(SHADOW_HASH_PREFIXES)


def verify_shadow_password(password: str, hashed_password: str) -> bool:
    if unix_crypt is None or not is_shadow_password_hash(hashed_password):
        return False
    try:
        candidate = unix_crypt.crypt(password, hashed_password)
    except (OSError, ValueError, TypeError):
        return False
    return bool(candidate) and secrets.compare_digest(candidate, hashed_password)


def verify_password(password: str, hashed_password: str) -> bool:
    if is_shadow_password_hash(hashed_password) and not hashed_password.startswith(BCRYPT_HASH_PREFIXES):
        return verify_shadow_password(password, hashed_password)
    try:
        return pwd_context.verify(password, hashed_password)
    except (UnknownHashError, ValueError):
        # truncate_error=True raises ValueError when the password is too long.
        if hashed_password.startswith(BCRYPT_HASH_PREFIXES):
            return verify_shadow_password(password, hashed_password)
        return False


def needs_rehash(hashed_password: str) -> bool:
    if is_shadow_password_hash(hashed_password):
        return False
    try:
        return pwd_context.needs_update(hashed_password)
    except UnknownHashError:
        return False


def create_access_token(
    subject: str,
    extra: Optional[Dict[str, Any]] = None,
    expires_minutes: Optional[int] = None,
) -> str:
    now = datetime.now(timezone.utc)
    lifetime = (
        expires_minutes
        if expires_minutes is not None
        else settings.access_token_expire_minutes
    )
    expire = now + timedelta(minutes=lifetime)
    payload: Dict[str, Any] = {"sub": subject, "exp": expire, "iat": now, "jti": secrets.token_urlsafe(32)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
