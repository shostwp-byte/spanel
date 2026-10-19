from hmac import compare_digest
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import ALGORITHM
from app.models.entities import RevokedToken, User
from app.services import demo_mode


# auto_error=False because the token may instead be in an HttpOnly cookie.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_token(
    bearer: Optional[str],
    cookie_token: Optional[str],
) -> Optional[str]:
    if bearer:
        return bearer
    if cookie_token:
        return cookie_token
    return None


def _user_from_token(token: str, db: Session) -> tuple[User, dict]:
    credentials_exception = _credentials_exception()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        jti: Optional[str] = payload.get("jti")
        token_version = int(payload.get("tv", 0))
        if username is None:
            raise credentials_exception
    except (JWTError, ValueError) as exc:
        raise credentials_exception from exc

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    # Impersonated sessions (admin "Login as") may target suspended users.
    # The `imp` claim is only set by the impersonate endpoint which already
    # verified the caller is an admin, so the bypass is safe.
    if not user.is_active and not payload.get("imp"):
        raise credentials_exception
    if (user.token_version or 0) != token_version:
        raise credentials_exception
    if jti and db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first():
        raise credentials_exception
    return user, payload


def _attach_auth_state(request: Request, token: str, payload: dict) -> None:
    request.state.jwt_payload = payload
    request.state.jwt_token = token


def _enforce_cookie_csrf(request: Request, bearer_token: Optional[str], session_cookie: Optional[str]) -> None:
    # When the request was authenticated via cookie (browser flow) we ALSO
    # require a CSRF token on mutating methods. Bearer auth (CLI/SDK) is
    # exempt because it cannot be triggered cross-origin without an explicit
    # Authorization header which browsers will not send automatically.
    if session_cookie and not bearer_token:
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_cookie = request.cookies.get("spanel_csrf")
            csrf_header = request.headers.get("x-csrf-token")
            # Constant-time compare to avoid leaking the cookie value via
            # response-time differences. Both inputs must exist.
            if (
                not csrf_cookie
                or not csrf_header
                or not compare_digest(csrf_cookie, csrf_header)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF token missing or invalid",
                )


def get_current_user_optional(
    request: Request,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
    session_cookie: Optional[str] = Cookie(default=None, alias="spanel_session"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    token = _resolve_token(bearer_token, session_cookie)
    if not token:
        return None
    try:
        user, payload = _user_from_token(token, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise
    _attach_auth_state(request, token, payload)
    _enforce_cookie_csrf(request, bearer_token, session_cookie)
    # Every signed-in request passes here, so this is where a demo account is
    # held to looking (services/demo_mode.py).
    demo_mode.enforce(request, user, payload)
    return user


def get_current_user(
    request: Request,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
    session_cookie: Optional[str] = Cookie(default=None, alias="spanel_session"),
    db: Session = Depends(get_db),
) -> User:
    token = _resolve_token(bearer_token, session_cookie)
    if not token:
        raise _credentials_exception()
    user, payload = _user_from_token(token, db)
    _attach_auth_state(request, token, payload)
    _enforce_cookie_csrf(request, bearer_token, session_cookie)
    # Every signed-in request passes here, so this is where a demo account is
    # held to looking (services/demo_mode.py).
    demo_mode.enforce(request, user, payload)
    return user
