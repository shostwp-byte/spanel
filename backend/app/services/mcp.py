"""Model Context Protocol: letting an AI assistant operate the panel.

An assistant holding a personal token reaches the panel over MCP and gets
exactly the permissions of the person who made the token. An administrator's
token sees the whole server; a customer's sees their own websites, databases,
backups and files and nothing else.

Three ideas carry the whole design.

**Hide rather than refuse.** A tool the token may not use does not appear in
`tools/list`, and calling it by name answers "Unknown tool" - the same answer a
name that does not exist gets. A read-only token cannot discover that the
writing tools are there, and a customer's token cannot discover the
administrative ones. Refusing by name would turn the tool list into an
enumeration oracle.

**Call the endpoints, do not reimplement them.** Every tool that does anything
calls the API's own endpoint function, passing `db=ctx.db` and
`current_user=ctx.user`. Ownership checks, quota, audit and the helper
boundary then live in one place and cannot drift between the panel and MCP. A
website is found by domain, and somebody else's website is reported as not
existing rather than as forbidden - "forbidden" would confirm it is real.

**No new dependency and no new listener.** The JSON-RPC here is a few hundred
lines rather than the `mcp` package, and it answers on the panel's existing
port behind the panel's existing certificate.

Transport is Streamable HTTP in its plain-JSON form: no SSE, no sessions. GET
and DELETE on the endpoint are 405.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

from sqlalchemy.orm import Session

from app.core.permissions import Role, normalize_role
from app.models.entities import McpToken, User
from app.services.audit import log_action


logger = logging.getLogger("spanel.mcp")

# --- protocol ---------------------------------------------------------------

# Newest first. A client asking for something we do not know is answered with
# the newest we have, which is what the specification calls for: it then
# decides whether it can live with that.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_INFO = {"name": "spanel", "title": "spanel", "version": "1.0.0"}

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# --- tokens -----------------------------------------------------------------

TOKEN_PREFIX = "bpmcp_"
TOKEN_BYTES = 32
PREFIX_KEPT = 12
MAX_TOKENS_PER_USER = 10
MIN_TOKEN_DAYS = 1
MAX_TOKEN_DAYS = 365

# One database write per minute per token at most. Without this every tool
# call would be a write, and the column is only ever read by a person looking
# at their token list.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def generate_token() -> tuple[str, str, str]:
    """A new token, plus what the database keeps: (token, hash, prefix).

    The token is returned to the caller once and never stored. Anyone who
    loses it makes another one.
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token), token[:PREFIX_KEPT]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(db: Session, token: str) -> Optional[McpToken]:
    """The token row this token names, if it may still be used.

    Every reason to refuse returns None rather than saying which one it was:
    an expired token and a revoked one are the same answer to whoever is
    holding it.
    """
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    row = db.query(McpToken).filter(McpToken.token_hash == hash_token(token)).first()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at is None or row.expires_at <= datetime.utcnow():
        return None
    # The owner matters as much as the token. Suspending an account has to
    # take its assistants with it, or a suspended customer keeps a way in.
    owner = db.query(User).filter(User.id == row.user_id).first()
    if owner is None or not owner.is_active:
        return None
    return row


def touch(db: Session, row: McpToken) -> None:
    now = datetime.utcnow()
    if row.last_used_at is None or now - row.last_used_at >= LAST_USED_RESOLUTION:
        row.last_used_at = now
        db.commit()


# --- errors -----------------------------------------------------------------

class ToolError(Exception):
    """Something the assistant should be told about, in words it can act on.

    Raised by a tool and returned as `isError: true` with the message, never
    as a transport-level failure. An assistant that gets a 500 has nothing to
    work with; one that is told "that website does not exist" can try again.
    """


# --- the registry -----------------------------------------------------------

@dataclass
class Context:
    """Everything a tool is allowed to know about who is calling."""

    db: Session
    user: User
    token: McpToken
    client_ip: str = ""
    # The real HTTP request, handed to every endpoint a tool calls.
    #
    # Passing None here was a bug that reached a live panel: firewall.block_ip
    # reads `request.client.host` to refuse blocking a range that contains the
    # caller's own address, and the assistant got
    # `'NoneType' object has no attribute 'client'` - then guessed out loud
    # that iptables was down. Two things wrong with None, and the crash was
    # the smaller one: it also disabled the panel's own guard against locking
    # yourself out. It carries the audit trail as well, so a write made
    # through MCP records the address and client it came from.
    request: Optional[Any] = None

    @property
    def is_admin(self) -> bool:
        return normalize_role(self.user.role) == Role.admin

    @property
    def can_write(self) -> bool:
        return bool(self.token.can_write)


@dataclass
class Tool:
    name: str
    title: str
    description: str
    properties: dict
    required: tuple[str, ...]
    handler: Callable[[Context, dict], Any]
    admin_only: bool = False
    writes: bool = False
    destructive: bool = False

    def visible_to(self, ctx: Context) -> bool:
        if self.admin_only and not ctx.is_admin:
            return False
        if self.writes and not ctx.can_write:
            return False
        return True

    def schema(self) -> dict:
        body = {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }
        # The hints are advice to the client, not enforcement. destructiveHint
        # is what makes an assistant stop and ask before it deletes something.
        body["annotations"] = {
            "readOnlyHint": not self.writes,
            "destructiveHint": self.destructive,
        }
        return body


REGISTRY: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, properties: Optional[dict] = None, *,
         required: Iterable[str] = (), admin_only: bool = False,
         writes: bool = False, destructive: bool = False):
    """Register one tool.

    `description` is read by the assistant and is the only thing telling it
    when to reach for this rather than something else, so it is written for
    that reader: what it does, and when it is the right choice.
    """

    def decorate(handler: Callable[[Context, dict], Any]) -> Callable[[Context, dict], Any]:
        if name in REGISTRY:
            raise RuntimeError(f"duplicate MCP tool: {name}")
        REGISTRY[name] = Tool(
            name=name, title=title, description=description,
            properties=dict(properties or {}), required=tuple(required),
            handler=handler, admin_only=admin_only,
            writes=writes, destructive=destructive,
        )
        return handler

    return decorate


def visible_tools(ctx: Context) -> list[Tool]:
    return [REGISTRY[name] for name in sorted(REGISTRY) if REGISTRY[name].visible_to(ctx)]


# --- argument checking ------------------------------------------------------

def validate_arguments(spec: Tool, arguments: dict) -> dict:
    """Check what the assistant sent before a tool sees any of it.

    Models produce arguments that are close rather than right - a string where
    an integer belongs, a field name that reads sensibly but is not the one in
    the schema. Catching that here means every tool can trust its input, and
    the assistant gets a message naming the field rather than a traceback.
    """
    if not isinstance(arguments, dict):
        raise ToolError("Arguments must be an object")

    unknown = sorted(set(arguments) - set(spec.properties))
    if unknown:
        raise ToolError(
            f"Unknown argument(s): {', '.join(unknown)}. "
            f"This tool takes: {', '.join(sorted(spec.properties)) or 'nothing'}"
        )

    missing = [name for name in spec.required if arguments.get(name) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}")

    checked: dict = {}
    for key, value in arguments.items():
        if value is None:
            continue
        checked[key] = _check_one(key, value, spec.properties[key])
    return checked


def _check_one(name: str, value: Any, rule: dict) -> Any:
    expected = rule.get("type")

    if expected == "integer":
        # A model that means 50 sometimes sends "50". Accept that, reject
        # anything that is not a whole number.
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ToolError(f"{name} must be a whole number")
        try:
            value = int(str(value).strip())
        except ValueError:
            raise ToolError(f"{name} must be a whole number") from None
        low, high = rule.get("minimum"), rule.get("maximum")
        if low is not None and value < low:
            raise ToolError(f"{name} must be at least {low}")
        if high is not None and value > high:
            raise ToolError(f"{name} must be at most {high}")
        return value

    if expected == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ToolError(f"{name} must be true or false")

    if expected == "string":
        if not isinstance(value, str):
            raise ToolError(f"{name} must be text")
        allowed = rule.get("enum")
        if allowed and value not in allowed:
            raise ToolError(f"{name} must be one of: {', '.join(allowed)}")
        longest = rule.get("maxLength")
        if longest is not None and len(value) > longest:
            raise ToolError(f"{name} must be at most {longest} characters")
        return value

    return value


# --- running a tool ---------------------------------------------------------

def call_tool(ctx: Context, name: str, arguments: Optional[dict]) -> dict:
    """Run one tool and shape the result the way `tools/call` wants it.

    A tool this token may not use is reported as unknown, exactly as a
    misspelled name would be - see the module docstring.
    """
    spec = REGISTRY.get(name)
    if spec is None or not spec.visible_to(ctx):
        return _tool_error(f"Unknown tool: {name}")

    try:
        arguments = validate_arguments(spec, arguments or {})
        result = spec.handler(ctx, arguments)
    except ToolError as exc:
        return _tool_error(str(exc))
    except Exception as exc:  # noqa: BLE001 - an assistant cannot use a 500
        logger.exception("MCP tool %s failed", name)
        return _tool_error(f"{name} failed: {exc}")

    if spec.writes:
        _audit(ctx, name, arguments)

    return {
        "content": [{"type": "text", "text": _as_text(result)}],
        "isError": False,
    }


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _as_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


# Arguments worth recording, and the ones that must never be. File contents go
# in as a length: the audit log is read by administrators and is not the place
# to keep a copy of everything an assistant ever wrote.
_AUDIT_ELIDED = {"content", "body", "text"}


def _audit(ctx: Context, name: str, arguments: dict) -> None:
    parts = []
    for key in sorted(arguments):
        value = arguments[key]
        if key in _AUDIT_ELIDED:
            parts.append(f"{key}={len(str(value))} chars")
        else:
            parts.append(f"{key}={str(value)[:120]}")
    detail = " ".join(parts)[:900]
    try:
        log_action(ctx.db, ctx.user.id, "mcp_tool", name, detail)
    except Exception:  # pragma: no cover - a failed audit must not fail the tool
        logger.warning("could not write audit row for MCP tool %s", name)


# --- JSON-RPC ---------------------------------------------------------------

def _result(request_id: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(ctx: Context, message: Any) -> Optional[dict]:
    """One JSON-RPC message in, at most one response out.

    None means the message was a notification: the specification says to send
    nothing back at all, which the endpoint turns into 202 with no body.
    """
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message")

    method = message.get("method")
    if not isinstance(method, str):
        return _error(message.get("id"), INVALID_REQUEST, "Missing method")

    # A notification has no id. Ours are all lifecycle noise we can ignore,
    # but answering one at all would be a protocol violation.
    is_notification = "id" not in message
    request_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "params must be an object")

    if is_notification:
        return None

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": [t.schema() for t in visible_tools(ctx)]})

    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(request_id, INVALID_PARAMS, "params.name is required")
        return _result(request_id, call_tool(ctx, name, params.get("arguments")))

    return _error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def handle_payload(ctx: Context, payload: Any) -> Optional[Any]:
    """A whole request body: one message or a batch.

    A batch of nothing but notifications produces no response, same as a
    single notification.
    """
    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "Empty batch")
        answers = [a for a in (handle_message(ctx, m) for m in payload) if a is not None]
        return answers or None
    return handle_message(ctx, payload)


# --- shared argument shapes -------------------------------------------------

DOMAIN_ARG = {
    "type": "string",
    "maxLength": 255,
    "description": "The website's domain, exactly as it appears in the panel.",
}

# --- helpers the tools share ------------------------------------------------

def private_or_reserved(address: str) -> bool:
    """Whether an address is one that must never be handed to the firewall."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(
        parsed.is_private or parsed.is_loopback or parsed.is_link_local
        or parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified
    )
