"""The MCP endpoint, and the tokens that reach it.

Two different things live here and they authenticate differently. `/api/mcp`
is spoken to by an AI assistant holding a Bearer token and never by a browser.
Everything under `/api/mcp/tokens` is the panel's own UI, on the panel's own
session, managing those tokens.

The order of the checks on `/api/mcp` is deliberate: addon, then origin, then
token. An assistant pointed at a panel where the addon is off should be told
the endpoint does not exist rather than be asked for credentials, and a
browser that has been tricked into posting here should be stopped before its
token is ever examined.
"""

from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import McpToken, User
from app.schemas.schemas import McpTokenCreate, McpTokenCreated, McpTokenOut
from app.services import addons, mcp
# Imported for the side effect: importing it is what registers the tools.
from app.services import mcp_tools  # noqa: F401
from app.services.audit import log_action

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _require_addon() -> None:
    """An addon that is off has no endpoint, not a closed one."""
    if not addons.is_installed(addons.MCP):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _unauthorised() -> HTTPException:
    # The header is what makes a well-behaved client ask for a token rather
    # than report a broken server.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid MCP token is required",
        headers={"WWW-Authenticate": 'Bearer realm="spanel-mcp"'},
    )


def _origin_is_safe(request: Request) -> bool:
    """Refuse a request a browser was talked into making.

    An MCP client sends no Origin at all. A page on some other site can make
    the browser POST here with the user's cookies, and while this endpoint
    does not read cookies, a panel reachable on a private address is exactly
    the DNS-rebinding target this check exists for. Same host as the panel is
    allowed, anything else is not.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = (urlparse(origin).hostname or "").lower()
    if not host:
        return False
    own = (request.url.hostname or "").lower()
    return host == own


@router.post("")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    _require_addon()

    if not _origin_is_safe(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Origin not allowed")

    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise _unauthorised()

    row = mcp.authenticate(db, value.strip())
    if row is None:
        raise _unauthorised()
    owner = db.query(User).filter(User.id == row.user_id).first()
    if owner is None:
        raise _unauthorised()
    mcp.touch(db, row)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - any unparseable body is the same answer
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": mcp.PARSE_ERROR, "message": "Invalid JSON"}},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    ctx = mcp.Context(
        db=db, user=owner, token=row,
        client_ip=request.client.host if request.client else "",
        # Handed on to every endpoint a tool calls: some of them read it, and
        # the audit trail wants the address and client behind a change.
        request=request,
    )
    answer = mcp.handle_payload(ctx, payload)
    if answer is None:
        # Nothing but notifications. The specification says send no body.
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(answer)


@router.get("")
def mcp_endpoint_get() -> Response:
    """No SSE stream and no sessions, so there is nothing to GET."""
    _require_addon()
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    headers={"Allow": "POST"})


@router.delete("")
def mcp_endpoint_delete() -> Response:
    _require_addon()
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                    headers={"Allow": "POST"})


# --- managing tokens --------------------------------------------------------

def _out(row: McpToken, username: str = "") -> McpTokenOut:
    return McpTokenOut(
        id=row.id, user_id=row.user_id, username=username, name=row.name,
        prefix=row.prefix, can_write=bool(row.can_write),
        expires_at=row.expires_at, last_used_at=row.last_used_at,
        revoked_at=row.revoked_at, created_at=row.created_at,
        expired=bool(row.expires_at and row.expires_at <= datetime.utcnow()),
    )


@router.get("/tokens", response_model=list[McpTokenOut])
def list_tokens(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Your own tokens. An administrator sees everyone's.

    Showing an administrator the whole list is the only way to answer "who has
    a key to this server", which is a question they have to be able to answer.
    """
    ensure_role(current_user.role, Role.end_user)
    query = db.query(McpToken).order_by(McpToken.id.desc())
    if not is_admin_role(current_user.role):
        query = query.filter(McpToken.user_id == current_user.id)
    rows = query.all()
    names = {user.id: user.username for user in db.query(User).all()}
    return [_out(row, names.get(row.user_id, "")) for row in rows]


@router.post("/tokens", response_model=McpTokenCreated)
def create_token(payload: McpTokenCreate, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Make a token for yourself. It is shown once and never again."""
    ensure_role(current_user.role, Role.end_user)
    _require_addon()

    live = db.query(McpToken).filter(
        McpToken.user_id == current_user.id,
        McpToken.revoked_at.is_(None),
        McpToken.expires_at > datetime.utcnow(),
    ).count()
    if live >= mcp.MAX_TOKENS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You already have {mcp.MAX_TOKENS_PER_USER} active tokens. "
                   "Revoke one before making another.",
        )

    token, token_hash, prefix = mcp.generate_token()
    row = McpToken(
        user_id=current_user.id,
        name=payload.name.strip(),
        token_hash=token_hash,
        prefix=prefix,
        can_write=bool(payload.can_write),
        expires_at=datetime.utcnow() + timedelta(days=payload.expires_in_days),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_action(db, current_user.id, "create_mcp_token", row.name,
               f"write={row.can_write} days={payload.expires_in_days}", request=request)
    return McpTokenCreated(token=token, **_out(row, current_user.username).model_dump())


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, request: Request,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Revoke a token. Yours always; anyone's if you administer the server."""
    ensure_role(current_user.role, Role.end_user)
    row = db.query(McpToken).filter(McpToken.id == token_id).first()
    # Somebody else's token is reported as missing rather than forbidden, the
    # same way a website belonging to another account is.
    if row is None or (row.user_id != current_user.id and not is_admin_role(current_user.role)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        db.commit()
    log_action(db, current_user.id, "revoke_mcp_token", row.name, row.prefix, request=request)
    return {"ok": True, "id": token_id}


def revoke_all(db: Session, reason: str = "") -> int:
    """Revoke every token on the server. Called when the addon is removed.

    Stopping an addon and removing it mean different things here, and this is
    the difference: stop closes the endpoint and leaves the keys alone, remove
    takes the keys back. Anyone who reinstalls makes new ones.
    """
    now = datetime.utcnow()
    rows = db.query(McpToken).filter(McpToken.revoked_at.is_(None)).all()
    for row in rows:
        row.revoked_at = now
    if rows:
        db.commit()
    return len(rows)


def revoke_for_user(db: Session, user_id: int) -> int:
    """Take an account's tokens with the account."""
    now = datetime.utcnow()
    rows = db.query(McpToken).filter(
        McpToken.user_id == user_id, McpToken.revoked_at.is_(None)
    ).all()
    for row in rows:
        row.revoked_at = now
    if rows:
        db.commit()
    return len(rows)
