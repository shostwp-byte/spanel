"""Terminal API endpoints."""

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.security import ALGORITHM
from app.models.entities import RevokedToken, User, Website
from app.services import demo_mode, terminal

logger = logging.getLogger("spanel.terminal")

router = APIRouter(prefix="/terminal", tags=["terminal"])


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if not origin:
        return True
    origin_host = (urlparse(origin).netloc or "").lower()
    request_host = (websocket.headers.get("host") or "").lower()
    if origin_host and request_host and origin_host == request_host:
        return True
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    if settings.panel_url:
        allowed.add(settings.panel_url.rstrip("/"))
    return not allowed or origin in allowed


def _current_user_from_session_cookie(websocket: WebSocket, db: Session) -> User | None:
    token = websocket.cookies.get("spanel_session")
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        token_version = int(payload.get("tv", 0))
        jti = payload.get("jti")
        if not username:
            return None
    except (JWTError, ValueError):
        return None
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        return None
    if (user.token_version or 0) != token_version:
        return None
    if jti and db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first():
        return None
    return user


def may_use_terminal(current_user: User) -> bool:
    """Whether this account is allowed a shell at all.

    Separate from website ownership: owning a site says which site you may act
    on, not whether the terminal is part of what you are paying for. Admins
    always may - they administer the server.
    """
    if is_admin_role(current_user.role):
        return True
    return bool(getattr(current_user, "terminal_enabled", False))


async def get_user_website(
    website_id: int,
    db: Session,
    current_user: User,
) -> Website:
    """Get website, verify ownership, and verify terminal is permitted."""
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    # Check ownership or admin role
    if website.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    # Owning the site is not the same as being entitled to a shell on it. The
    # frontend hides the terminal for accounts without it, but hiding a button
    # is not access control - this endpoint answers curl just as happily.
    if not may_use_terminal(current_user):
        raise HTTPException(status_code=403, detail="Terminal is not enabled for your account.")
    return website


class TerminalExecRequest(BaseModel):
    """Request model for terminal command execution."""

    command: str


class TerminalExecResponse(BaseModel):
    """Response model for terminal command execution."""

    exit_code: int
    stdout: str
    stderr: str


@router.post("/exec/{website_id}", response_model=TerminalExecResponse)
async def exec_command(
    website_id: int,
    request: TerminalExecRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute a single command as the website user.

    This endpoint is suitable for non-interactive commands like:
    - php artisan migrate
    - composer install
    - npm run build
    """
    website = await get_user_website(website_id, db, current_user)

    # terminal.exec_command is subprocess.run under the hood, with a timeout of
    # up to 915s for composer/npm/git/curl and friends. This handler is async,
    # so calling it inline would block the one uvicorn event loop that serves
    # every other tenant, the admin UI and /auth/login for that whole time -
    # `curl <host-that-never-answers>` is enough. Hand it to the threadpool.
    result = await run_in_threadpool(
        terminal.exec_command,
        website.linux_user,
        request.command,
        cwd=terminal.default_cwd(website.root_path),
        php_version=getattr(website, "php_version", None),
    )

    return TerminalExecResponse(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


@router.get("/allowed-commands")
async def list_allowed_commands(
    current_user: User = Depends(get_current_user),
):
    """List all allowed commands for terminal access."""
    return {"commands": sorted(terminal.ALLOWED_COMMANDS)}


@router.websocket("/ws/{website_id}")
async def terminal_websocket(
    websocket: WebSocket,
    website_id: int,
    db: Session = Depends(get_db),
):
    """WebSocket endpoint for interactive terminal sessions.

    Auth is handled via cookies (spanel_session and spanel_csrf) passed
    automatically by the browser.

    Messages:
    - Client -> Server: {"type": "input", "data": "command\n"}
    - Client -> Server: {"type": "resize", "cols": 80, "rows": 24}
    - Client -> Server: {"type": "ping"}
    - Server -> Client: {"type": "output", "data": "output text"}
    - Server -> Client: {"type": "exit", "code": 0}
    - Server -> Client: {"type": "pong"}
    """
    if not _origin_allowed(websocket):
        await websocket.close(code=4003, reason="Origin not allowed")
        return

    # Get token and validate step by step for better error messages
    token = websocket.cookies.get("spanel_session")
    if not token:
        await websocket.close(code=4001, reason="No session cookie")
        return

    # Decode and validate JWT
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        token_version = int(payload.get("tv", 0))
        jti = payload.get("jti")
        if not username:
            await websocket.close(code=4001, reason="Invalid token: missing username")
            return
    except jwt.ExpiredSignatureError:
        await websocket.close(code=4001, reason="Session expired")
        return
    except jwt.JWTClaimsError:
        await websocket.close(code=4001, reason="Invalid token: claims error")
        return
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Check user exists and is active
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        await websocket.close(code=4001, reason="User not found")
        return
    if not user.is_active:
        await websocket.close(code=4001, reason="User account is disabled")
        return

    # Check token version
    if (user.token_version or 0) != token_version:
        await websocket.close(code=4001, reason="Session invalidated")
        return

    # Check if token is revoked
    if jti and db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first():
        await websocket.close(code=4001, reason="Session revoked")
        return

    current_user = user

    # The REST side refuses a demo visitor in get_current_user; this door does
    # not pass through it, so it asks the same question itself.
    if demo_mode.is_demo_session(current_user, payload):
        await websocket.close(code=4003, reason=demo_mode.REFUSED_READ)
        return

    # Get website
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        await websocket.close(code=4004, reason="Website not found")
        return

    # Check ownership
    if website.owner_id != current_user.id and not is_admin_role(current_user.role):
        await websocket.close(code=4003, reason="Access denied")
        return

    # ...and the same entitlement check the REST endpoint makes. Both doors
    # have to be locked: the websocket is reachable without ever touching
    # /terminal/exec.
    if not may_use_terminal(current_user):
        await websocket.close(code=4003, reason="Terminal is not enabled for your account")
        return

    if not website.linux_user:
        await websocket.close(code=4004, reason="Website runtime user is missing")
        return

    root_path = Path(website.root_path)
    if not root_path.exists() or not root_path.is_dir():
        await websocket.close(code=4005, reason="Website root path does not exist or is not accessible")
        return

    await websocket.accept()
    cwd = terminal.default_cwd(website.root_path)
    await websocket.send_json({"type": "cwd", "data": cwd})

    try:
        while True:
            message = await websocket.receive_text()

            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "data": "Invalid JSON message"
                })
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "input":
                command = (msg.get("data", "") or "").strip()
                if not command:
                    await websocket.send_json({"type": "exit", "code": 0})
                    continue
                if command in {"clear", "cls"}:
                    await websocket.send_json({"type": "clear"})
                    await websocket.send_json({"type": "exit", "code": 0})
                    continue

                try:
                    argv = terminal.split_command(command)
                except ValueError as exc:
                    await websocket.send_json({"type": "output", "data": f"{exc}\r\n"})
                    await websocket.send_json({"type": "exit", "code": 2})
                    continue

                if argv[0] == "cd":
                    if len(argv) > 2:
                        await websocket.send_json({"type": "output", "data": "usage: cd [path]\r\n"})
                        await websocket.send_json({"type": "exit", "code": 2})
                        continue
                    try:
                        cwd = terminal.resolve_cwd(website.root_path, cwd, argv[1] if len(argv) == 2 else "")
                    except ValueError as exc:
                        await websocket.send_json({"type": "output", "data": f"{exc}\r\n"})
                        await websocket.send_json({"type": "exit", "code": 1})
                        continue
                    await websocket.send_json({"type": "cwd", "data": cwd})
                    await websocket.send_json({"type": "exit", "code": 0})
                    continue

                # Same reason as the REST door above: blocking here stalls the
                # shared event loop, not just this socket.
                result = await run_in_threadpool(
                    terminal.exec_command,
                    website.linux_user,
                    command,
                    cwd=cwd,
                    php_version=getattr(website, "php_version", None),
                )
                await websocket.send_json({
                    "type": "output",
                    "data": result.stdout + result.stderr,
                })
                await websocket.send_json({
                    "type": "exit",
                    "code": result.exit_code,
                })
            elif msg_type == "resize":
                # Terminal resize - can be used for PTY support in future
                pass
            else:
                await websocket.send_json({
                    "type": "error",
                    "data": f"Unknown message type: {msg_type}"
                })

    except WebSocketDisconnect:
        pass  # Client disconnected
    except Exception:
        # The exception text stays server-side. It carries paths, module names
        # and occasionally command output, and the client asking for it is an
        # end user with a shell on one site, not an operator.
        logger.exception("Terminal websocket failed for website %s", website_id)
        try:
            await websocket.send_json({
                "type": "error",
                "data": "Server error. Check the panel logs for details."
            })
        except Exception:
            pass  # WebSocket already closed
