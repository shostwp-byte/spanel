"""The tools an assistant can actually call.

Separate from `mcp.py` on purpose: that file is the transport and the
permission model, this one is the surface area. They change for different
reasons and at different rates, and a reviewer reading one should not have to
scroll past the other.

Every tool here calls the API's own endpoint function rather than reaching
into a service or the database. That is the single most important rule in the
file. Ownership checks, quota, audit and the sudo helper boundary all live
inside those endpoints; a tool that went around them would be a second
implementation of the panel's permission model, and the two would drift.

Websites are addressed by domain, because that is what a person says to an
assistant and what the assistant reads in a log. Somebody else's domain is
reported as not existing - never as forbidden, which would confirm it is real.

Importing this module is what registers the tools. `app.api.mcp` imports it
for that reason and uses nothing from it.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Optional

from app.api import databases as databases_api
from app.api import firewall as firewall_api
from app.api import maintenance as maintenance_api
from app.api import services as services_api
from app.api import updates as updates_api
from app.api import users as users_api
from app.api import waf as waf_api
from app.api import websites as websites_api
from app.core.permissions import is_admin_role
from app.models.entities import User, Website
from app.api.maintenance import FileBulkDelete, FileMkdir, FileTransfer, FileWrite
from app.api.waf import WafCustomRulesUpdate
from app.schemas.schemas import (
    FirewallIpRule, ServiceAction, UserBackupCreate, WebsiteWafUpdate,
)
from app.services import file_manager
from app.services import firewall as firewall_service
from app.services import server_network
from app.services import site_users
from app.services import waf as waf_service
from app.services.mcp import (
    DOMAIN_ARG, Context, ToolError, private_or_reserved, tool,
)


# --- shared lookups ---------------------------------------------------------

def _website(ctx: Context, domain: str) -> Website:
    """The website this domain names, if the caller may see it.

    A domain belonging to another account raises the same error as one that
    was never created. Saying "not yours" would tell a customer's assistant
    which domains exist on the server.
    """
    wanted = (domain or "").strip().lower().lstrip(".")
    if not wanted:
        raise ToolError("A domain is required")
    query = ctx.db.query(Website).filter(Website.domain == wanted)
    if not is_admin_role(ctx.user.role):
        query = query.filter(Website.owner_id == ctx.user.id)
    website = query.first()
    if website is None:
        raise ToolError(
            f"No website named {wanted}. Use list_websites to see what there is."
        )
    return website


def _rows(value) -> list:
    """Endpoints return models, dicts or a wrapper; tools return plain data."""
    if isinstance(value, dict):
        for key in ("items", "rows", "results"):
            if key in value and isinstance(value[key], list):
                return value[key]
        return [value]
    if isinstance(value, list):
        return value
    return [value]


def _thin(row, *fields) -> dict:
    """Only the fields an assistant can use.

    Everything sent here is tokens the model pays for and context it has to
    read past. Password hashes and internal ids are also simply not its
    business.
    """
    out = {}
    for name in fields:
        value = getattr(row, name, None) if not isinstance(row, dict) else row.get(name)
        if value is not None:
            out[name] = value
    return out


# --- who am I ---------------------------------------------------------------

@tool("whoami", "Who this token belongs to",
      "The account this token acts as, and what it may do. Call this first if "
      "you are unsure whether you are working as an administrator or as one "
      "hosting customer - it decides which other tools exist.")
def _whoami(ctx: Context, args: dict):
    return {
        "username": ctx.user.username,
        "role": "administrator" if ctx.is_admin else "hosting customer",
        "can_make_changes": ctx.can_write,
        "token_name": ctx.token.name,
        "token_expires_at": ctx.token.expires_at,
        "scope": "every website on this server" if ctx.is_admin
                 else "only websites owned by this account",
    }


# --- websites ---------------------------------------------------------------

@tool("list_websites", "List websites",
      "Every website you can see, with its domain, PHP version and whether "
      "SSL and the firewall rules are on. Start here: the other tools take a "
      "domain, and this is where the domains come from.",
      {"search": {"type": "string", "maxLength": 255,
                  "description": "Optional. Matches domain, path or Linux user."}})
def _list_websites(ctx: Context, args: dict):
    found = websites_api.list_websites(
        q=args.get("search", ""), db=ctx.db, current_user=ctx.user)
    return {"websites": [
        _thin(row, "domain", "php_version", "app_type", "status",
              "ssl_enabled", "waf_enabled", "root_path")
        for row in _rows(found)
    ]}


@tool("read_site_log", "Read a website's log",
      "The tail of one website's access or error log. Use the error log when "
      "a site is failing and the access log when you need to see what traffic "
      "it is getting.",
      {"domain": DOMAIN_ARG,
       "kind": {"type": "string", "enum": ["access", "error"],
                "description": "Which log. Defaults to access."},
       "lines": {"type": "integer", "minimum": 1, "maximum": 5000,
                 "description": "How many lines from the end. Defaults to 200."}},
      required=("domain",))
def _read_site_log(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return websites_api.get_website_log(
        website_id=website.id, kind=args.get("kind", "access"),
        lines=args.get("lines", 200), db=ctx.db, current_user=ctx.user)


# --- databases --------------------------------------------------------------

@tool("list_databases", "List databases",
      "The MySQL databases you can see, and which website each belongs to. "
      "Passwords are never included.",
      {"search": {"type": "string", "maxLength": 255}})
def _list_databases(ctx: Context, args: dict):
    found = databases_api.list_databases(
        q=args.get("search", ""), db=ctx.db, current_user=ctx.user)
    return {"databases": [
        _thin(row, "db_name", "db_user", "website_id", "size_mb", "created_at")
        for row in _rows(found)
    ]}


def _same_network(rule_ip: str, wanted: str) -> bool:
    """Whether a firewall rule is about this address.

    Not a string comparison. The firewall normalises what it stores, so asking
    it to block 185.220.101.7 produces a rule whose ip reads
    "185.220.101.7/32" - and comparing the two as text never matches. That
    made block_ip miss what it had already blocked and add a duplicate rule
    every single call, and made unblock_ip answer "is not blocked" about an
    address that was, which is the worse of the two: it is a confident wrong
    answer rather than a mess.
    """
    try:
        return (ipaddress.ip_network(rule_ip or "", strict=False)
                == ipaddress.ip_network(wanted or "", strict=False))
    except ValueError:
        return (rule_ip or "").strip() == (wanted or "").strip()


def _denied_rules(wanted: str) -> list[dict]:
    return [rule for rule in firewall_service.rules()
            if (rule.get("action") or "").upper() == "DENY"
            and _same_network(rule.get("ip") or "", wanted)]


# --- backups ----------------------------------------------------------------

@tool("list_backups", "List an account's backups",
      "The backup archives held for an account, newest first. Administrators "
      "may name any account; everyone else gets their own.",
      {"username": {"type": "string", "maxLength": 64,
                    "description": "Administrators only. Defaults to your own account."}})
def _list_backups(ctx: Context, args: dict):
    owner = ctx.user
    wanted = (args.get("username") or "").strip()
    if wanted and wanted != ctx.user.username:
        if not ctx.is_admin:
            raise ToolError(f"No account named {wanted}")
        owner = ctx.db.query(User).filter(User.username == wanted).first()
        if owner is None:
            raise ToolError(f"No account named {wanted}")
    return maintenance_api.list_user_backups(
        user_id=owner.id, db=ctx.db, current_user=ctx.user)


@tool("list_backup_jobs", "List running and recent backup jobs",
      "Backup jobs and their state. Use this after starting a backup to see "
      "whether it finished, rather than assuming it did.")
def _list_backup_jobs(ctx: Context, args: dict):
    return maintenance_api.list_backup_jobs(current_user=ctx.user)


@tool("list_backup_schedules", "List scheduled backups",
      "Every backup schedule on the server: when it runs, where it sends the "
      "archive, how many it keeps, and how the last run went. The last "
      "message is where a failing schedule says why.",
      admin_only=True)
def _list_backup_schedules(ctx: Context, args: dict):
    found = maintenance_api.list_backup_schedules(db=ctx.db, current_user=ctx.user)
    return {"schedules": [
        _thin(row, "id", "schedule", "retention", "name_suffix", "is_active",
              "last_run_at", "last_status", "last_message")
        for row in _rows(found)
    ]}


# --- the server -------------------------------------------------------------

@tool("server_resources", "CPU, memory and disk",
      "What the machine is doing right now. Use this before blaming a site "
      "for being slow - the answer is often that the box is out of memory.")
def _server_resources(ctx: Context, args: dict):
    return services_api.get_resource_usage(current_user=ctx.user)


@tool("list_services", "List system services",
      "nginx, PHP-FPM, MariaDB, Redis and the rest, with whether each is "
      "running. Check here before restarting anything.",
      admin_only=True)
def _list_services(ctx: Context, args: dict):
    return services_api.get_services(current_user=ctx.user)


@tool("panel_update_status", "Panel and OS update status",
      "Which version the panel is on, whether a newer release exists, and how "
      "many operating system updates are waiting.",
      admin_only=True)
def _panel_update_status(ctx: Context, args: dict):
    return updates_api.get_update_status(refresh=False, current_user=ctx.user)


# --- users and audit --------------------------------------------------------

@tool("list_users", "List panel accounts",
      "Every hosting account on the server, with its limits and whether it is "
      "suspended.",
      admin_only=True)
def _list_users(ctx: Context, args: dict):
    found = users_api.list_users(db=ctx.db, current_user=ctx.user)
    return {"users": [
        _thin(row, "id", "username", "email", "role", "is_active",
              "website_limit", "storage_limit_mb")
        for row in _rows(found)
    ]}


@tool("recent_audit_log", "Recent panel activity",
      "What has been done on this panel lately, newest first - who did it, to "
      "what, and when. Anything an assistant did appears here as mcp_tool.",
      {"limit": {"type": "integer", "minimum": 1, "maximum": 200,
                 "description": "How many entries. Defaults to 50."},
       "action": {"type": "string", "maxLength": 64,
                  "description": "Optional. Only entries with this action."}},
      admin_only=True)
def _recent_audit_log(ctx: Context, args: dict):
    return users_api.list_audit(
        user_id=None, action=args.get("action"), limit=args.get("limit", 50),
        offset=0, db=ctx.db, current_user=ctx.user)


# --- firewall and WAF -------------------------------------------------------

@tool("list_firewall_rules", "List firewall rules",
      "Whether the firewall is on and every rule it holds, with the rule "
      "numbers. A blocked address is removed by its number, so read this "
      "before unblocking anything.",
      admin_only=True)
def _list_firewall_rules(ctx: Context, args: dict):
    return firewall_api.get_status(current_user=ctx.user)


@tool("list_waf_rules", "List WAF rules",
      "The web application firewall rules available on this server, with the "
      "id of each. Use it to see what a rule id in a blocked request means.",
      admin_only=True)
def _list_waf_rules(ctx: Context, args: dict):
    return waf_api.get_waf_rules(current_user=ctx.user)


@tool("read_waf_access_log", "Read what the WAF allowed and blocked",
      "Requests the web application firewall saw, newest first, with the rule "
      "that blocked each one. This is the tool for 'why is this visitor "
      "getting a 403' and for finding an address worth blocking.",
      {"domain": {**DOMAIN_ARG, "description":
                  "Optional. Only this website's requests."},
       "verdict": {"type": "string", "enum": ["all", "allow", "block", "error"],
                   "description": "Defaults to all."},
       "search": {"type": "string", "maxLength": 200,
                  "description": "Optional. Matches the address, path or user agent."},
       "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                 "description": "How many entries. Defaults to 50."}})
def _read_waf_access_log(ctx: Context, args: dict):
    website_id: Optional[int] = None
    if args.get("domain"):
        website_id = _website(ctx, args["domain"]).id
    return waf_api.get_waf_access_logs(
        website_id=website_id, verdict=args.get("verdict", "all"),
        q=args.get("search", ""), limit=args.get("limit", 50), lines=5000,
        db=ctx.db, current_user=ctx.user)


# --- files ------------------------------------------------------------------

# A model asked for "the file" will happily pull a 40 MB log into its own
# context and then be unable to do anything else. The panel's own endpoint has
# no such limit because a browser scrolling a file is a different problem.
MAX_READ_LINES = 2000


@tool("list_files", "List files in a website",
      "What is in a directory of a website, with sizes and modification "
      "times. Paths are relative to the website's root.",
      {"domain": DOMAIN_ARG,
       "path": {"type": "string", "maxLength": 1024,
                "description": "Directory relative to the website root. "
                               "Empty or omitted means the root itself."}},
      required=("domain",))
def _list_files(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return maintenance_api.list_files(
        website_id=website.id, path=args.get("path", ""),
        db=ctx.db, current_user=ctx.user)


@tool("read_file", "Read a file from a website",
      f"The contents of one file, up to {MAX_READ_LINES} lines. Longer files "
      "come back truncated and say so, so check that before concluding "
      "something is missing from the end.",
      {"domain": DOMAIN_ARG,
       "path": {"type": "string", "maxLength": 1024,
                "description": "File relative to the website root."}},
      required=("domain", "path"))
def _read_file(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    result = maintenance_api.read_file(
        website_id=website.id, path=args["path"], db=ctx.db, current_user=ctx.user)

    content = result.get("content", "") if isinstance(result, dict) else str(result)
    lines = content.splitlines()
    if len(lines) <= MAX_READ_LINES:
        return {"path": args["path"], "lines": len(lines), "truncated": False,
                "content": content}
    return {
        "path": args["path"],
        "lines": MAX_READ_LINES,
        "total_lines": len(lines),
        "truncated": True,
        "note": f"Showing the first {MAX_READ_LINES} of {len(lines)} lines.",
        "content": "\n".join(lines[:MAX_READ_LINES]),
    }


# --- the three that needed something spanel did not have --------------------

@tool("get_website", "Everything about one website",
      "One website in full: PHP version, document root, SSL state and expiry, "
      "the firewall settings and every alias pointing at it. Use this when "
      "list_websites has told you the domain and you need the detail.",
      {"domain": DOMAIN_ARG},
      required=("domain",))
def _get_website(ctx: Context, args: dict):
    """Composed rather than fetched.

    spanel has no GET /websites/{id}: the panel builds this page from the
    listing plus two more calls, and so does this. Composing here rather than
    adding an endpoint keeps MCP from being the reason a new surface exists.
    """
    website = _website(ctx, args["domain"])
    detail = _thin(
        website, "domain", "php_version", "app_type", "status", "root_path",
        "document_root", "ssl_enabled", "waf_enabled", "waf_default_rules",
        "http_flood_enabled", "nginx_rewrite_mode", "linux_user", "created_at",
    )
    try:
        aliases = websites_api.list_website_aliases(
            website_id=website.id, db=ctx.db, current_user=ctx.user)
        detail["aliases"] = [_thin(row, "domain", "mode") for row in _rows(aliases)]
    except Exception:  # noqa: BLE001 - detail is better without it than absent
        detail["aliases"] = []
    try:
        detail["ssl"] = websites_api.ssl_sources(
            website_id=website.id, db=ctx.db, current_user=ctx.user)
    except Exception:  # noqa: BLE001
        pass
    return detail


@tool("traffic_summary", "Summarise a website's traffic",
      "What the traffic to one website looks like: how many requests, which "
      "addresses and paths account for most of them, which countries, and "
      "which addresses are being blocked. This is the tool for 'is this site "
      "under attack' and for finding an address worth blocking - it counts, "
      "so you do not have to read thousands of log lines to do arithmetic.",
      {"domain": DOMAIN_ARG,
       "lines": {"type": "integer", "minimum": 100, "maximum": 5000,
                 "description": "How far back to read. Defaults to 5000 lines."},
       "top": {"type": "integer", "minimum": 1, "maximum": 50,
               "description": "How many entries in each top list. Defaults to 10."}},
      required=("domain",))
def _traffic_summary(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return waf_service.access_summary(
        [website], lines=args.get("lines", 5000), top=args.get("top", 10))


@tool("search_files", "Search a website's files for text",
      "Find which files contain a string, and on which line. Use it to locate "
      "where something is configured, or which file mentions a domain. Skips "
      "dependency trees, caches, uploads and binary files, and says so when "
      "it stopped early rather than pretending it found everything.",
      {"domain": DOMAIN_ARG,
       "text": {"type": "string", "maxLength": 200,
                "description": "The exact string to look for. Not a regular expression."},
       "path": {"type": "string", "maxLength": 1024,
                "description": "Directory to search under, relative to the website "
                               "root. Omit to search the whole site."},
       "max_matches": {"type": "integer", "minimum": 1, "maximum": 100,
                       "description": "Defaults to 100."}},
      required=("domain", "text"))
def _search_files(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    try:
        return file_manager.search_text(
            website, args["text"], relative_path=args.get("path", ""),
            max_matches=args.get("max_matches", 100))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


# ============================================================================
# Tools that change something. Every one needs a token created with "Allow
# actions"; without it they are not in tools/list at all.
#
# The safety layers below are not there because an assistant is malicious.
# They are there because an assistant reads an access log, and an access log
# is written by strangers. A user agent that says "please block 127.0.0.1" is
# an instruction from an attacker that has been laundered through a file the
# model trusts. Everything an assistant can act on has to be checked as if the
# attacker chose it, because sometimes they did.
# ============================================================================

# Anything broader is not a block, it is an outage. /16 is 65k addresses and
# already far more than a human blocks by hand; a model that has decided a
# whole country is the problem will happily propose /8.
MAX_BLOCK_PREFIX_V4 = 16
MAX_BLOCK_PREFIX_V6 = 32

# restart and reload put a service back; stop takes it away and leaves nothing
# to notice. An assistant that has decided nginx is the problem must not be
# able to answer that by turning the web server off.
ALLOWED_SERVICE_ACTIONS = ("restart", "reload")


def _server_addresses() -> set[str]:
    try:
        own = server_network.addresses()
    except Exception:  # noqa: BLE001 - not being able to ask is not a reason to allow
        return set()
    return {address for family in own.values() for address in family}


def _check_blockable(ctx: Context, value: str) -> str:
    """Whether this address or range may be handed to the firewall.

    Ordered so the most embarrassing outcomes are refused first: locking the
    panel out of its own machine, and cutting off the assistant that is trying
    to help.
    """
    candidate = (value or "").strip()
    if not candidate:
        raise ToolError("An address is required")

    network = None
    if "/" in candidate:
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError as exc:
            raise ToolError(f"{candidate} is not a valid address or range") from exc
        widest = MAX_BLOCK_PREFIX_V4 if network.version == 4 else MAX_BLOCK_PREFIX_V6
        if network.prefixlen < widest:
            raise ToolError(
                f"{candidate} covers {network.num_addresses} addresses. "
                f"The widest range this can block is /{widest}. Block the "
                "specific addresses instead, or use the Firewall page if you "
                "really mean to take out a range this size."
            )
        first = str(network.network_address)
    else:
        try:
            ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise ToolError(f"{candidate} is not a valid address or range") from exc
        first = candidate

    if private_or_reserved(first):
        raise ToolError(
            f"{candidate} is a private, loopback or reserved address. Blocking "
            "it would cut off part of the server rather than an attacker. If a "
            "log shows one of these, the site is probably behind a proxy or a "
            "CDN and the real client address is in a forwarded header."
        )

    for own in _server_addresses():
        if own == first or (network is not None and _in_network(own, network)):
            raise ToolError(
                f"{candidate} is this server's own address. Blocking it would "
                "take the machine off the network."
            )

    if ctx.client_ip:
        if ctx.client_ip == first or (network is not None and _in_network(ctx.client_ip, network)):
            raise ToolError(
                f"{candidate} is the address this request came from. Blocking "
                "it would disconnect you."
            )
    return candidate


def _in_network(address: str, network) -> bool:
    try:
        return ipaddress.ip_address(address) in network
    except ValueError:
        return False


# --- backups ----------------------------------------------------------------

@tool("create_backup", "Back up an account now",
      "Start a full backup of an account - its websites, databases and files. "
      "Returns once the job has been queued, so follow it with "
      "list_backup_jobs to see whether it finished.",
      {"username": {"type": "string", "maxLength": 64,
                    "description": "Administrators only. Defaults to your own account."}},
      writes=True)
def _create_backup(ctx: Context, args: dict):
    owner = ctx.user
    wanted = (args.get("username") or "").strip()
    if wanted and wanted != ctx.user.username:
        if not ctx.is_admin:
            raise ToolError(f"No account named {wanted}")
        owner = ctx.db.query(User).filter(User.username == wanted).first()
        if owner is None:
            raise ToolError(f"No account named {wanted}")
    return maintenance_api.create_user_backup(
        payload=UserBackupCreate(user_id=owner.id),
        request=ctx.request, db=ctx.db, current_user=ctx.user)


@tool("run_backup_schedule", "Run a backup schedule now",
      "Run one scheduled backup immediately, exactly as the timer would. Use "
      "it to prove a schedule works rather than waiting until tonight to find "
      "out it does not.",
      {"schedule_id": {"type": "integer", "minimum": 1, "maximum": 100000,
                       "description": "From list_backup_schedules."}},
      required=("schedule_id",), admin_only=True, writes=True)
def _run_backup_schedule(ctx: Context, args: dict):
    return maintenance_api.run_backup_schedule_now(
        schedule_id=args["schedule_id"], request=ctx.request,
        db=ctx.db, current_user=ctx.user)


# --- certificates and the per-site firewall ---------------------------------

@tool("issue_ssl_certificate", "Get a certificate for a website",
      "Issue or renew a Let's Encrypt certificate. The domain must already "
      "resolve to this server or the request will fail - check that first if "
      "it does.",
      {"domain": DOMAIN_ARG}, required=("domain",), writes=True)
def _issue_ssl_certificate(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return websites_api.enable_ssl(
        website_id=website.id, db=ctx.db, current_user=ctx.user)


@tool("set_website_waf", "Turn a website's WAF on or off",
      "Enable or disable the web application firewall for one website. "
      "Turning it off is how you confirm the WAF is what is blocking a "
      "legitimate request - turn it back on afterwards.",
      {"domain": DOMAIN_ARG,
       "enabled": {"type": "boolean", "description": "True to protect, false to stop."}},
      required=("domain", "enabled"), writes=True)
def _set_website_waf(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return websites_api.set_website_waf(
        website_id=website.id,
        payload=WebsiteWafUpdate(waf_enabled=bool(args["enabled"])),
        request=ctx.request, db=ctx.db, current_user=ctx.user)


# --- files ------------------------------------------------------------------

@tool("write_file", "Write a file in a website",
      "Create a file or replace one completely. There is no partial edit: "
      "read the file first, change what you mean to change, and send the "
      "whole thing back. Missing parent directories are created.",
      {"domain": DOMAIN_ARG,
       "path": {"type": "string", "maxLength": 1024,
                "description": "File relative to the website root."},
       "content": {"type": "string", "maxLength": 1_000_000,
                   "description": "The complete new contents."}},
      required=("domain", "path", "content"), writes=True)
def _write_file(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return maintenance_api.write_file(
        payload=FileWrite(website_id=website.id, path=args["path"],
                          content=args["content"]),
        db=ctx.db, current_user=ctx.user)


@tool("create_directory", "Make a directory in a website",
      "Create one directory inside a website. write_file already creates any "
      "parent directories it needs, so reach for this only when you want an "
      "empty directory - a quarantine folder to move suspicious files into, "
      "for instance.",
      {"domain": DOMAIN_ARG,
       "path": {"type": "string", "maxLength": 1024,
                "description": "Where to create it, relative to the website root."},
       "name": {"type": "string", "maxLength": 255,
                "description": "The new directory's name."}},
      required=("domain", "name"), writes=True)
def _create_directory(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return maintenance_api.make_directory(
        payload=FileMkdir(website_id=website.id,
                          path=args.get("path", site_users.PUBLIC_DIR),
                          name=args["name"]),
        db=ctx.db, current_user=ctx.user)


@tool("move_file", "Move a file or directory",
      "Move something to another directory inside the same website. Use it to "
      "set a suspicious file aside rather than deleting it - a quarantine "
      "directory is recoverable and a deletion is not.",
      {"domain": DOMAIN_ARG,
       "path": {"type": "string", "maxLength": 1024,
                "description": "What to move, relative to the website root."},
       "destination": {"type": "string", "maxLength": 1024,
                       "description": "The directory to move it into."}},
      required=("domain", "path", "destination"), writes=True)
def _move_file(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    return maintenance_api.move_entries(
        payload=FileTransfer(website_id=website.id, paths=[args["path"]],
                             destination_path=args["destination"]),
        db=ctx.db, current_user=ctx.user)


@tool("delete_file", "Delete a file or directory",
      "Permanently delete one file or directory from a website. There is no "
      "undo and no trash. Prefer move_file into a quarantine directory unless "
      "you are certain.",
      {"domain": DOMAIN_ARG,
       "path": {"type": "string", "maxLength": 1024,
                "description": "What to delete, relative to the website root."}},
      required=("domain", "path"), writes=True, destructive=True)
def _delete_file(ctx: Context, args: dict):
    website = _website(ctx, args["domain"])
    wanted = (args["path"] or "").strip().strip("/")
    # The file manager already refuses to escape the site. What it does not
    # refuse is emptying it: "" is the site root and public_html is every page
    # the site serves. Both are one plausible model mistake away from a
    # customer's website being gone.
    if not wanted or wanted in {".", site_users.PUBLIC_DIR.strip("/")}:
        raise ToolError(
            f"Refusing to delete {wanted or 'the website root'}: that is the "
            "website itself, not a file in it. Name something inside it."
        )
    return maintenance_api.delete_entries(
        payload=FileBulkDelete(website_id=website.id, paths=[wanted]),
        db=ctx.db, current_user=ctx.user)


# --- the firewall -----------------------------------------------------------

@tool("block_ip", "Block an address at the firewall",
      "Block one address, or a small range, at the server firewall. Use "
      "traffic_summary or read_waf_access_log first to be sure the address is "
      "actually the problem. Addresses behind a CDN are the CDN, not the "
      "visitor - blocking one takes the whole site off for everybody.",
      {"ip": {"type": "string", "maxLength": 64,
              "description": "An address, or a range no wider than /16."},
       "reason": {"type": "string", "maxLength": 200,
                  "description": "Why, for the audit log."}},
      required=("ip",), admin_only=True, writes=True)
def _block_ip(ctx: Context, args: dict):
    wanted = _check_blockable(ctx, args["ip"])

    # Already blocked is not an error and must not add a second rule: the list
    # fills with duplicates and every one of them has to be removed by hand.
    for rule in _denied_rules(wanted):
        return {"ip": wanted, "already_blocked": True,
                "rule_number": rule.get("number"),
                "note": "Already blocked; nothing was added."}

    result = firewall_api.block_ip(
        payload=FirewallIpRule(ip=wanted), request=ctx.request, current_user=ctx.user)
    return {"ip": wanted, "already_blocked": False, "reason": args.get("reason", ""),
            "result": result}


@tool("unblock_ip", "Unblock an address",
      "Remove a firewall block. Finds the rule by address, so you do not need "
      "the rule number.",
      {"ip": {"type": "string", "maxLength": 64,
              "description": "The address or range to unblock."}},
      required=("ip",), admin_only=True, writes=True)
def _unblock_ip(ctx: Context, args: dict):
    """spanel deletes a firewall rule by its number, not by address.

    The panel's own page works that way because a person is looking at the
    numbered list. An assistant has an address from a log and no list, so the
    lookup happens here rather than making the model fetch the rules, pick a
    number and hope it picked the right one.
    """
    wanted = (args["ip"] or "").strip()
    if not wanted:
        raise ToolError("An address is required")
    matches = _denied_rules(wanted)
    if not matches:
        raise ToolError(
            f"{wanted} is not blocked. Use list_firewall_rules to see what is."
        )
    removed = []
    # Highest number first: deleting by position renumbers everything below it.
    for rule in sorted(matches, key=lambda r: int(r.get("number") or 0), reverse=True):
        number = int(rule.get("number") or 0)
        if number <= 0:
            continue
        firewall_api.delete_rule(number=number, current_user=ctx.user)
        removed.append(number)
    return {"ip": wanted, "removed_rules": removed}


# --- WAF rules --------------------------------------------------------------

@tool("add_waf_rule", "Add a WAF rule",
      "Block requests matching one thing: an address, a path, a user agent or "
      "something in the query string. You choose what to match and the value; "
      "the rule itself is written by the panel, so you never supply "
      "ModSecurity syntax. Rules added this way are marked with who added "
      "them and can be removed on the WAF page.",
      {"match": {"type": "string", "enum": ["ip", "path", "user_agent", "query"],
                 "description": "What part of the request to look at."},
       "value": {"type": "string", "maxLength": 200,
                 "description": "The value to match. Letters, digits and "
                                ". _ : / @ = , + ~ ? & | - only."},
       "reason": {"type": "string", "maxLength": 200,
                  "description": "Why, recorded in the rule itself."}},
      required=("match", "value"), admin_only=True, writes=True)
def _add_waf_rule(ctx: Context, args: dict):
    """Read, append, write. The same race the panel's own WAF page has.

    There is no compare-and-set on this file: the panel stores global custom
    rules as one blob and saves it whole. Two people saving at the same second
    lose one of the two changes, whether both are people or one is an
    assistant. Narrowed as far as it can be - the read and the write are
    back to back with no network in between - and the provenance comment on
    every rule added here is what makes a lost or unexpected one identifiable
    afterwards. The count is returned so a caller can see the file grew by one
    and not by one minus somebody else's edit.
    """
    current = waf_service.custom_rules()
    before = current.stdout if hasattr(current, "stdout") else str(current)

    try:
        combined, rule_id = waf_service.append_mcp_rule(
            before, args["match"], args["value"],
            who=ctx.user.username,
            when=datetime.utcnow().strftime("%Y-%m-%d"),
            reason=args.get("reason", ""),
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    waf_api.save_waf_custom_rules(
        payload=WafCustomRulesUpdate(content=combined), current_user=ctx.user)
    return {
        "rule_id": rule_id,
        "match": args["match"],
        "value": args["value"],
        "rules_before": before.count("SecRule"),
        "rules_after": combined.count("SecRule"),
        "note": "Added to the global custom rules. Remove it on the WAF page "
                "if it turns out to be wrong.",
    }


# --- services ---------------------------------------------------------------

@tool("restart_service", "Restart or reload a service",
      "Restart or reload one system service - nginx, PHP-FPM, MariaDB and so "
      "on. Reload first where the service supports it: it applies new "
      "configuration without dropping connections. There is no stop, on "
      "purpose.",
      {"name": {"type": "string", "maxLength": 64,
                "description": "The service name, from list_services."},
       "action": {"type": "string", "enum": list(ALLOWED_SERVICE_ACTIONS),
                  "description": "Defaults to restart."}},
      required=("name",), admin_only=True, writes=True)
def _restart_service(ctx: Context, args: dict):
    action = args.get("action", "restart")
    if action not in ALLOWED_SERVICE_ACTIONS:
        # Unreachable through the enum, kept because the enum is a schema and
        # schemas are edited.
        raise ToolError(f"action must be one of: {', '.join(ALLOWED_SERVICE_ACTIONS)}")
    return services_api.run_service_action(
        payload=ServiceAction(name=args["name"], action=action),
        current_user=ctx.user)
