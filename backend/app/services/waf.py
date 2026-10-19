import csv
import gzip
import hashlib
import ipaddress
import json
import logging
import re
import socket
import threading
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from app.core.config import settings
from app.models.entities import Website
from app.services import nginx
from app.services.shell import CommandResult, shell

try:
    import maxminddb
except ImportError:  # pragma: no cover - optional GeoIP support
    maxminddb = None

logger = logging.getLogger("spanel.waf")


DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
MAX_CUSTOM_BYTES = 64 * 1024
MAX_SITE_RULE_BYTES = 160 * 1024
# nginx escapes " and control chars in its string vars, so a quoted field never
# contains a bare " - matching with [^"]* keeps this linear. The old
# (?:[^"\\]|\\.)* form backtracked catastrophically on binary/garbage requests
# (TLS handshakes hitting :80, fuzzers), turning a few such lines into seconds.
ACCESS_LOG_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<request>[^"]*)" (?P<status>\d{3}) (?P<body_bytes>\S+)'
    r'(?: "(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)")?'
    r'(?: (?P<request_time>[0-9.]+))?'
)
ACCESS_REASON_RULES = [
    (re.compile(r"(?:^|/)\.env(?:\.|$|[?])", re.I), "Block environment file probe"),
    (re.compile(r"(?:^|/)\.git/", re.I), "Block git metadata probe"),
    (re.compile(r"/composer\.(?:json|lock)(?:$|[?])", re.I), "Block Composer metadata probe"),
    (re.compile(r"/wp-config\.php(?:\.|$|[?])", re.I), "Block WordPress config probe"),
    (re.compile(r"/wp-content/(?:uploads|cache|upgrade)/[^?]*\.php(?:$|[?])", re.I), "Block WordPress upload PHP probe"),
    (re.compile(r"/wp-admin/(?:install|setup-config)\.php(?:$|[?])", re.I), "Block WordPress installer probe"),
    (re.compile(r"[?&]author=[0-9]+(?:&|$)", re.I), "Block WordPress author scan"),
    (re.compile(r"/_ignition/execute-solution(?:$|[?])", re.I), "Block Laravel Ignition RCE probe"),
    (re.compile(r"/(?:artisan|server\.php)(?:$|[?])", re.I), "Block Laravel runtime probe"),
    (re.compile(r"/storage/logs/[^?]*\.log(?:$|[?])", re.I), "Block Laravel log probe"),
    (re.compile(r"(?:\.\./|\.\.\\|%2e%2e%2f|%252e%252e%252f)", re.I), "Block path traversal"),
    (re.compile(r"/(?:c99|r57|shell|cmd|wso)\.php(?:$|[?])", re.I), "Block PHP runtime probe"),
]
GEOIP_COUNTRY_DB_CANDIDATES = (
    "/usr/share/GeoIP/GeoLite2-Country.mmdb",
    "/usr/local/share/GeoIP/GeoLite2-Country.mmdb",
    "/var/lib/GeoIP/GeoLite2-Country.mmdb",
)
DBIP_CACHE_BASENAME = "dbip-country-lite-{year}-{month}.csv.gz"
DBIP_DOWNLOAD_TIMEOUT = 20
COUNTRY_NAMES = {
    "AU": "Australia",
    "BR": "Brazil",
    "CA": "Canada",
    "CN": "China",
    "DE": "Germany",
    "ES": "Spain",
    "FR": "France",
    "GB": "United Kingdom",
    "HK": "Hong Kong",
    "ID": "Indonesia",
    "IN": "India",
    "JP": "Japan",
    "KR": "South Korea",
    "MY": "Malaysia",
    "PH": "Philippines",
    "RU": "Russia",
    "SG": "Singapore",
    "TH": "Thailand",
    "US": "United States",
    "VN": "Vietnam",
    "ZZ": "Unknown",
}

DEFAULT_RULES = [
    {
        "id": "php-sensitive-files",
        "category": "PHP",
        "title": "PHP sensitive files",
        "description": "Blocks direct probes for PHP app secrets, Composer metadata, git data, and phpinfo files.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/\\.env(?:\\.|$)|/\\.user\\.ini(?:\\.|$)|/\\.git/|/composer\\.(?:json|lock)(?:$|[?])|/(?:phpinfo|info)\\.php(?:$|[?])|/(?:config|database|db)\\.php\\.(?:bak|old|save|txt)(?:$|[?]))" "id:1001301,phase:1,deny,status:403,log,msg:'spanel blocked PHP sensitive file probe'""",
    },
    {
        "id": "php-path-traversal",
        "category": "PHP",
        "title": "Path traversal",
        "description": "Blocks ../ and encoded traversal probes in URLs and query arguments.",
        "rules": """SecRule REQUEST_URI|ARGS "@rx (?i)(?:\\.\\./|\\.\\.\\\\|%2e%2e%2f|%252e%252e%252f)" "id:1001302,phase:1,deny,status:403,log,msg:'spanel blocked PHP path traversal'""",
    },
    {
        "id": "php-runtime-probes",
        "category": "PHP",
        "title": "PHP runtime probes",
        "description": "Blocks direct probes for common PHP webshell names and old PHPUnit RCE paths.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/(?:c99|r57|shell|cmd|wso)\\.php(?:$|[?])|/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin\\.php(?:$|[?]))" "id:1001303,phase:1,deny,status:403,log,msg:'spanel blocked PHP runtime probe'""",
    },
    {
        "id": "laravel-sensitive-files",
        "category": "Laravel",
        "title": "Laravel sensitive files",
        "description": "Blocks probes for Laravel environment files, logs, artisan, and cached PHP config.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/\\.env(?:\\.|$)|/artisan(?:$|[?])|/server\\.php(?:$|[?])|/storage/logs/[^?]*\\.log(?:$|[?])|/bootstrap/cache/[^?]*\\.php(?:$|[?]))" "id:1001201,phase:1,deny,status:403,log,msg:'spanel blocked Laravel sensitive path'""",
    },
    {
        "id": "laravel-ignition-rce",
        "category": "Laravel",
        "title": "Laravel Ignition RCE probes",
        "description": "Blocks direct probes for the old Laravel Ignition execute-solution endpoint.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/_ignition/execute-solution(?:$|[?]))" "id:1001202,phase:1,deny,status:403,log,msg:'spanel blocked Laravel Ignition RCE probe'""",
    },
    {
        "id": "wordpress-sensitive-files",
        "category": "WordPress",
        "title": "WordPress sensitive files",
        "description": "Blocks wp-config probes, uploads PHP execution probes, and internal WordPress PHP paths.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/wp-config\\.php(?:\\.|$|[?])|/wp-content/(?:uploads|cache|upgrade)/[^?]*\\.php(?:$|[?])|/wp-admin/includes/[^?]*\\.php(?:$|[?])|/wp-includes/[^?]*\\.php(?:$|[?]))" "id:1001101,phase:1,deny,status:403,log,msg:'spanel blocked WordPress sensitive path'""",
    },
    {
        "id": "wordpress-xmlrpc-author-scan",
        "category": "WordPress",
        "title": "WordPress author scans",
        "description": "Blocks ?author= enumeration scans while leaving XML-RPC compatibility to site policy.",
        "rules": """SecRule ARGS:author "@rx ^[0-9]+$" "id:1001103,phase:1,deny,status:403,log,msg:'spanel blocked WordPress author enumeration'""",
    },
    {
        "id": "wordpress-install-upgrade",
        "category": "WordPress",
        "title": "WordPress installer probes",
        "description": "Blocks direct access to WordPress installation scripts after deployment.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/wp-admin/install\\.php(?:$|[?])|/wp-admin/setup-config\\.php(?:$|[?]))" "id:1001104,phase:1,deny,status:403,log,msg:'spanel blocked WordPress installer probe'""",
    },
]

LEGACY_RULE_ID_MAP = {
    "general-sensitive-files": "php-sensitive-files",
    "general-path-traversal": "php-path-traversal",
    "general-command-injection": "php-runtime-probes",
    "general-sqli": None,
    "general-xss": None,
}


def _rule_ids() -> set[str]:
    return {rule["id"] for rule in DEFAULT_RULES}


def _validate_domain(domain: str) -> str:
    value = (domain or "").strip().lower()
    if not DOMAIN_RE.fullmatch(value):
        raise ValueError("Invalid domain")
    return value


def _validate_custom_rules(content: str) -> str:
    value = content or ""
    if "\x00" in value:
        raise ValueError("WAF rules cannot contain NUL bytes")
    if len(value.encode("utf-8")) > MAX_CUSTOM_BYTES:
        raise ValueError("WAF custom rules must be 64 KB or smaller")
    return value.replace("\r\n", "\n").strip()


def _parse_enabled_rule_ids(value: str | None) -> set[str]:
    valid = _rule_ids()
    if not value:
        return set(valid)
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return set(valid)
    if not isinstance(raw, list):
        return set(valid)
    selected = {
        LEGACY_RULE_ID_MAP.get(rule_id, rule_id)
        for item in raw
        for rule_id in [str(item)]
        if LEGACY_RULE_ID_MAP.get(rule_id, rule_id) in valid
    }
    return selected


def validate_enabled_rule_ids(rule_ids: Iterable[str]) -> list[str]:
    valid = _rule_ids()
    selected = []
    for rule_id in rule_ids:
        value = LEGACY_RULE_ID_MAP.get(str(rule_id), str(rule_id))
        if value is None:
            continue
        if value not in valid:
            raise ValueError(f"Unknown WAF rule: {value}")
        if value not in selected:
            selected.append(value)
    return selected


def default_rule_definitions() -> list[dict]:
    return [
        {
            "id": rule["id"],
            "category": rule["category"],
            "title": rule["title"],
            "description": rule["description"],
            "enabled_default": True,
        }
        for rule in DEFAULT_RULES
    ]


def site_rules_file(domain: str) -> str:
    safe_domain = _validate_domain(domain)
    return f"/etc/nginx/modsec/sites/{safe_domain}.conf"


CRS_CONF_PATH = "/etc/nginx/modsec/spanel-crs.conf"
# The server-wide custom rules: the WAF page's global box and what add_waf_rule
# writes. Loaded by every site's rule file - since the per-site files arrived
# (2026-06-03) this file had been loaded by nothing, and every rule in it,
# typed or added over MCP, did nothing at all.
GLOBAL_CUSTOM_PATH = "/etc/nginx/modsec/spanel-custom.conf"
CRS_MODES = ("off", "detect", "block")


def render_site_rules(
    domain: str,
    enabled_rule_ids: Iterable[str],
    custom_rules: str = "",
    crs_mode: str = "off",
) -> str:
    safe_domain = _validate_domain(domain)
    enabled = set(validate_enabled_rule_ids(enabled_rule_ids))
    custom = _validate_custom_rules(custom_rules)
    mode = normalize_crs_mode(crs_mode)
    chunks = [
        f"# spanel WAF rules for {safe_domain}",
        "Include /etc/nginx/modsec/spanel-base.conf",
        "",
        "# spanel selected default rules",
    ]
    for rule in DEFAULT_RULES:
        if rule["id"] not in enabled:
            continue
        chunks.append(f"# {rule['category']} - {rule['title']} ({rule['id']})")
        chunks.append(rule["rules"].strip())
        if rule.get("exceptions"):
            chunks.append(rule["exceptions"].strip())
    if mode != "off":
        # After spanel's own rules, which deny outright on a single match and
        # are cheaper: no point scoring a request that is already refused.
        chunks.extend(["", f"# OWASP CRS ({mode})", f"Include {CRS_CONF_PATH}"])
    # The server's custom rules, then the site's own. Both after everything
    # else: SecRuleRemoveById only affects rules that are already loaded, so a
    # site can still excuse itself from a CRS rule or from a global one.
    chunks.extend(["", "# spanel global custom rules", f"Include {GLOBAL_CUSTOM_PATH}"])
    chunks.extend(["", "# spanel custom rules"])
    if custom:
        chunks.append(custom)
    content = "\n".join(chunks).strip() + "\n"
    if len(content.encode("utf-8")) > MAX_SITE_RULE_BYTES:
        raise ValueError("WAF site rules are too large")
    return content


def normalize_crs_mode(value) -> str:
    mode = str(value or "off").strip().lower()
    return mode if mode in CRS_MODES else "off"


CRS_MODE_FILE = Path("/etc/nginx/modsec/spanel-crs-mode")


def deployed_crs_mode() -> str | None:
    """The mode the machine is actually running, as the helper last wrote it.

    Written by the `waf-crs-mode` verb and world-readable, so this needs no
    privilege. It is the record of what was deployed, as against what the
    panel thinks it chose.
    """
    try:
        text = CRS_MODE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return normalize_crs_mode(text) if text else None


def active_crs_mode() -> str:
    """The server-wide OWASP CRS mode: off, detect, or block.

    Two records of the same fact, and they can disagree. The panel settings
    hold what an admin chose; /etc/nginx/modsec/spanel-crs-mode holds what was
    last deployed. Every re-render of a site's rules asks this function, so
    when the settings lose the key - and they can, a settings file is one
    unreadable moment away from being rewritten without it - the next render
    quietly drops CRS from every site.

    That happened: 20 customer sites lost CRS for seventeen minutes during an
    update, with nothing logged, because the key was gone and a missing key
    reads as "off".

    So a missing key defers to what is deployed rather than silently
    downgrading, and says so loudly. An explicit "off" is still obeyed: that is
    somebody's decision, not a gap.
    """
    from app.services import panel_settings

    chosen = panel_settings.stored_crs_mode()
    deployed = deployed_crs_mode()

    if chosen is None:
        if deployed and deployed != "off":
            logger.error(
                "CRS mode is missing from the panel settings while %s has %r. "
                "Using the deployed mode: re-rendering site rules as 'off' here "
                "would take CRS off every site. Set the mode in the panel to "
                "record it again.",
                CRS_MODE_FILE, deployed,
            )
            return deployed
        return "off"

    normalized = normalize_crs_mode(chosen)
    if deployed and deployed != normalized:
        logger.warning(
            "CRS mode disagrees: panel settings say %r, %s says %r. The panel "
            "setting wins; the next render will bring the machine into line.",
            normalized, CRS_MODE_FILE, deployed,
        )
    return normalized


def crs_status() -> dict:
    """What the machine reports about CRS, plus the mode the panel recorded."""
    result = shell.privileged(
        "waf-crs-status",
        check=False,
        fallback=["bash", "-lc", "echo mode=off; echo installed=no; echo conf=no; echo rule_files=0; echo sites_including=0"],
    )
    info = {
        "mode": "off", "installed": False, "conf": False, "rule_files": 0,
        "sites_including": 0, "nginx_pss_mb": 0, "ram_available_mb": 0, "ram_total_mb": 0,
    }
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in {"installed", "conf"}:
            info[key] = value == "yes"
        elif key in {"rule_files", "sites_including", "nginx_pss_mb", "ram_available_mb", "ram_total_mb"}:
            info[key] = int(value) if value.isdigit() else 0
        elif key == "mode":
            info["mode"] = normalize_crs_mode(value)
    info["panel_mode"] = active_crs_mode()
    # What the panel recorded, separately from what it is falling back to, so
    # the page can say "the settings lost this" rather than showing agreement
    # that is only there because active_crs_mode() papered over the gap.
    from app.services import panel_settings

    info["recorded_mode"] = panel_settings.stored_crs_mode()
    info["mode_unrecorded"] = info["recorded_mode"] is None and info["panel_mode"] != "off"
    return info


def set_crs_mode(mode: str, websites: Iterable[Website]) -> dict:
    """Switch CRS on or off server-wide and rewrite every site's rule file.

    Persisting the mode without rewriting the site files would change nothing:
    the include lives in each site's own rules, which is what lets one site
    carry exceptions the next one does not.
    """
    from app.services import panel_settings

    target = normalize_crs_mode(mode)
    if str(mode or "").strip().lower() not in CRS_MODES:
        raise ValueError(f"Unknown CRS mode: {mode}")

    sites = list(websites)

    def rewrite(mode_for_sites: str) -> list[dict]:
        problems = []
        for website in sites:
            try:
                outcome = sync_site_rules(
                    website.domain,
                    website_enabled_rule_ids(website),
                    website_custom_rules(website),
                    crs_mode=mode_for_sites if site_uses_crs(website) else "off",
                )
                if outcome.returncode != 0:
                    problems.append({"domain": website.domain, "error": (outcome.stderr or outcome.stdout or "").strip()[:200]})
            except (RuntimeError, ValueError) as exc:
                problems.append({"domain": website.domain, "error": str(exc)[:200]})
        return problems

    if target == "off":
        # Order matters and cost an outage the first time round. Deleting
        # spanel-crs.conf first leaves every site file still including a path
        # that no longer exists, so the very next `nginx -t` - triggered by
        # rewriting the first site - fails, and the rest of the rewrite never
        # happens. Clear the references first, then remove the file.
        panel_settings.save_crs_mode(target)
        failures = rewrite("off")
        result = shell.privileged(
            "waf-crs-mode", helper_args=["off"], check=False,
            fallback=["bash", "-lc", "echo 'OWASP CRS disabled'"],
        )
    else:
        result = shell.privileged(
            "waf-crs-mode", helper_args=[target], check=False,
            fallback=["bash", "-lc", f"echo 'OWASP CRS mode: {target}'"],
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Could not change the CRS mode").strip())
        panel_settings.save_crs_mode(target)
        failures = rewrite(target)

    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not change the CRS mode").strip())
    return {
        "mode": target,
        "message": (result.stdout or "").strip(),
        "failures": failures,
        "sites_using_crs": sum(1 for w in sites if site_uses_crs(w)) if target != "off" else 0,
    }


def site_uses_crs(website: Website) -> bool:
    """CRS applies to a site only when both toggles agree.

    waf_enabled is the site's WAF switch; crs_enabled is the separate opt-in
    that exists because CRS is the one WAF feature with a memory bill.
    """
    return bool(getattr(website, "waf_enabled", False) and getattr(website, "crs_enabled", False))


# Every nginx server block builds its own copy of the rule set, so this grows
# with the number of sites opted in rather than being paid once.
#
# Measured in PSS, which is the only figure worth quoting here: nginx parses the
# rules in the master and the workers fork, so those pages are shared, and
# summing RSS across processes counts them once per worker. On a live server
# with 19 sites the two readings were 3976 MB of RSS against 961 MB of PSS -
# roughly four times' difference, and the RSS number is the one that makes CRS
# look unaffordable when it is not.
#
# 50 MB per site is that 961 MB spread across 19, rounded up. The panel reports
# the measured PSS next to this estimate, and that measurement is what an admin
# should act on.
CRS_PSS_MB_PER_SITE = 50
CRS_RSS_MB_PER_SITE = CRS_PSS_MB_PER_SITE  # kept: the API field name is public


def crs_memory_estimate(site_count: int) -> int:
    return max(0, int(site_count)) * CRS_RSS_MB_PER_SITE


def website_enabled_rule_ids(website: Website) -> set[str]:
    return _parse_enabled_rule_ids(getattr(website, "waf_default_rules", ""))


def website_custom_rules(website: Website) -> str:
    return _validate_custom_rules(getattr(website, "waf_custom_rules", "") or "")


def sync_site_rules(
    domain: str,
    enabled_rule_ids: Iterable[str],
    custom_rules: str = "",
    crs_mode: str = "off",
) -> CommandResult:
    """Write one site's rule file.

    crs_mode defaults to off rather than to the server-wide setting. Whether a
    site loads CRS is a per-site opt-in with a memory bill attached, so a caller
    that has not thought about it must not turn it on by omission - which is
    exactly what happened: creating a website on a server in block mode gave the
    new site CRS while its own crs_enabled flag said off.

    Callers holding a Website should use sync_website_rules instead.
    """
    safe_domain = _validate_domain(domain)
    mode = normalize_crs_mode(crs_mode)
    content = render_site_rules(safe_domain, enabled_rule_ids, custom_rules, crs_mode=mode)
    return shell.privileged(
        "waf-site-save",
        helper_args=[safe_domain],
        check=False,
        input=content,
        fallback=["bash", "-lc", "cat >/tmp/spanel-waf-site.conf && echo WAF site rules saved"],
    )


def sync_website_rules(website: Website) -> CommandResult:
    # A site with the WAF switched off, or without its own CRS opt-in, gets no
    # CRS whatever the server-wide mode says.
    mode = active_crs_mode() if site_uses_crs(website) else "off"
    return sync_site_rules(
        website.domain,
        website_enabled_rule_ids(website),
        website_custom_rules(website),
        crs_mode=mode,
    )


def remove_site_rules(domain: str) -> str:
    """Drop a deleted site's rule file.

    Call this only after the vhost is gone: the helper refuses while anything
    still points at the file, because a missing modsecurity_rules_file fails
    `nginx -t` and the next reload would take every site down.

    Never raises - a website must stay deletable when this does not work.
    """
    try:
        safe_domain = _validate_domain(domain)
    except ValueError:
        return ""
    try:
        result = shell.privileged(
            "waf-site-delete",
            helper_args=[safe_domain],
            check=False,
            fallback=["bash", "-lc", f"rm -f /etc/nginx/modsec/sites/{safe_domain}.conf"],
        )
    except Exception as exc:  # pragma: no cover - helper failure
        return f"could not remove the WAF rules for {safe_domain}: {exc}"
    if result.returncode != 0:
        return f"could not remove the WAF rules for {safe_domain}: {(result.stderr or result.stdout or '').strip()}"
    return (result.stdout or "").strip()


def site_config(website: Website) -> dict:
    from app.services import nginx, panel_settings

    enabled = website_enabled_rule_ids(website)
    return {
        "website_id": website.id,
        "domain": website.domain,
        "waf_enabled": bool(website.waf_enabled),
        "crs_enabled": bool(getattr(website, "crs_enabled", False)),
        "crs_mode": active_crs_mode(),
        "crs_active": site_uses_crs(website) and active_crs_mode() != "off",
        "http_flood_enabled": bool(getattr(website, "http_flood_enabled", False)),
        "http_flood_config": nginx.http_flood_config_for_website(website),
        "rules_file": site_rules_file(website.domain),
        "default_rules": [
            {
                **rule,
                "enabled": rule["id"] in enabled,
                "enabled_default": True,
            }
            for rule in default_rule_definitions()
        ],
        "enabled_rule_ids": [rule["id"] for rule in DEFAULT_RULES if rule["id"] in enabled],
        "custom_rules": website_custom_rules(website),
        "blocked_bots": nginx.normalize_blocked_bots(getattr(website, "blocked_bots", "") or ""),
        "global_blocked_bots": panel_settings.global_blocked_bots(),
        "effective_blocked_bots": effective_blocked_bots(website),
    }


def website_blocked_bots(website: Website) -> list[str]:
    from app.services import nginx

    return nginx.normalize_blocked_bots(getattr(website, "blocked_bots", "") or "")


def _merge_bots(first, second) -> list[str]:
    """Union, order-preserving, case-insensitive, first spelling wins."""
    from app.services import nginx

    seen: set[str] = set()
    merged: list[str] = []
    for name in list(first) + list(second):
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(name)
    return nginx.normalize_blocked_bots(merged)


def effective_blocked_bots(website: Website) -> list[str]:
    """What actually gets written into this site's vhost.

    The server-wide list plus anything set on the site itself. Keeping the two
    apart in storage is what lets a bot added globally protect every site at
    once, without flattening it into 23 copies that then drift.
    """
    from app.services import panel_settings

    return _merge_bots(panel_settings.global_blocked_bots(), website_blocked_bots(website))


def save_website_blocked_bots(website: Website, raw, mode: str = "replace") -> list[str]:
    """Store one website's own list and re-render its vhost.

    Returns the site's own cleaned list - not the effective one - because that
    is what the caller just edited. Raises ValueError for a list that is too
    long or malformed, which the API turns into a 400.
    """
    from app.services import nginx

    incoming = nginx.normalize_blocked_bots(raw)
    bots = _merge_bots(website_blocked_bots(website), incoming) if mode == "add" else incoming

    website.blocked_bots = "\n".join(bots)
    nginx.update_bot_block(website.domain, effective_blocked_bots(website))
    return bots


def resync_bot_blocks(websites) -> tuple[list[str], list[dict]]:
    """Re-render every site's bot block, e.g. after the global list changed.

    Each site is written independently and a failure on one does not abandon
    the rest: changing the global list touches every vhost on the server, and
    the operator needs to know exactly which ones took it.
    """
    from app.services import nginx

    done, failed = [], []
    for site in websites:
        try:
            nginx.update_bot_block(site.domain, effective_blocked_bots(site))
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            failed.append({"domain": site.domain, "error": str(exc)})
            continue
        done.append(site.domain)
    return done, failed


def save_website_config(website: Website, enabled_rule_ids: Iterable[str], custom_rules: str) -> CommandResult:
    selected = validate_enabled_rule_ids(enabled_rule_ids)
    custom = _validate_custom_rules(custom_rules)
    website.waf_default_rules = json.dumps(selected, ensure_ascii=True)
    website.waf_custom_rules = custom
    # Editing a site's rule selection must not change whether it loads CRS.
    return sync_site_rules(
        website.domain,
        selected,
        custom,
        crs_mode=active_crs_mode() if site_uses_crs(website) else "off",
    )


def status():
    return shell.privileged(
        "waf-status",
        check=False,
        fallback=["bash", "-lc", "test -f /etc/nginx/modsec/spanel-base.conf && echo installed || echo not-installed"],
    )


def install_engine():
    return shell.privileged(
        "waf-install",
        check=False,
        fallback=["bash", "-lc", "apt-get update && apt-get install -y libnginx-mod-http-modsecurity modsecurity-crs"],
    )


def update_rules():
    return shell.privileged(
        "waf-update",
        check=False,
        fallback=["bash", "-lc", "echo no WAF updater found"],
    )


def default_rules():
    return shell.privileged(
        "waf-default-rules",
        check=False,
        fallback=["bash", "-lc", "cat /etc/nginx/modsec/spanel-default.conf 2>/dev/null || true"],
    )


def custom_rules():
    return shell.privileged(
        "waf-custom-rules",
        check=False,
        fallback=["bash", "-lc", "cat /etc/nginx/modsec/spanel-custom.conf 2>/dev/null || true"],
    )


def save_custom_rules(content: str):
    return shell.privileged(
        "waf-custom-save",
        check=False,
        input=_validate_custom_rules(content),
        fallback=["bash", "-lc", "cat >/tmp/spanel-waf-custom.conf && echo WAF custom rules saved"],
    )


def _parse_nginx_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z")
    except (TypeError, ValueError):
        return None


def _split_request(value: str) -> tuple[str, str, str]:
    parts = (value or "").split()
    if len(parts) >= 3:
        return parts[0].upper(), parts[1], parts[2]
    if len(parts) == 2:
        return parts[0].upper(), parts[1], ""
    return "", value or "", ""


def _access_verdict(status_code: int) -> str:
    if status_code in {401, 403, 429, 444}:
        return "block"
    if status_code >= 500:
        return "error"
    return "allow"


def _access_reason(path: str, status_code: int) -> str:
    verdict = _access_verdict(status_code)
    if verdict == "allow":
        return "Allowed"
    if status_code == 429:
        return "HTTP flood rate limit"
    for pattern, reason in ACCESS_REASON_RULES:
        if pattern.search(path or ""):
            return reason
    if status_code == 403:
        return "Blocked by WAF or Nginx"
    if verdict == "error":
        return "Upstream/server error"
    return "Blocked"


def _duration_ms(value: str | None) -> int:
    try:
        return max(0, round(float(value or 0) * 1000))
    except (TypeError, ValueError):
        return 0


def _geoip_country_db_paths() -> list[Path]:
    configured = (settings.geoip_country_db or "").strip()
    if configured:
        return [Path(configured)]
    return [Path(path) for path in GEOIP_COUNTRY_DB_CANDIDATES]


@lru_cache(maxsize=1)
def _geoip_country_reader():
    if maxminddb is None:
        return None
    for path in _geoip_country_db_paths():
        if not path.is_file():
            continue
        try:
            return maxminddb.open_database(str(path))
        except Exception:
            continue
    return None


def _dbip_country_url(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    template = (settings.geoip_dbip_country_url or "").strip()
    if not template:
        return ""
    return template.format(year=f"{current.year:04d}", month=f"{current.month:02d}")


def _dbip_country_cache_path(now: datetime | None = None) -> Path:
    current = now or datetime.now(timezone.utc)
    cache_dir = Path((settings.geoip_dbip_cache_dir or "/var/lib/spanel/geoip").strip())
    return cache_dir / DBIP_CACHE_BASENAME.format(year=f"{current.year:04d}", month=f"{current.month:02d}")


def _ensure_dbip_country_cache() -> Path | None:
    path = _dbip_country_cache_path()
    if path.is_file():
        return path
    url = _dbip_country_url()
    if not url:
        return None
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"https", "http"}:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "spanel GeoIP updater"})
        with urllib.request.urlopen(request, timeout=DBIP_DOWNLOAD_TIMEOUT) as response:
            if getattr(response, "status", 200) >= 400:
                return None
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with tmp_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            tmp_path.replace(path)
    except Exception:
        return None
    return path if path.is_file() else None


def _ip_str_to_int(value: str) -> tuple[int, int] | None:
    """(version, integer) for a well-formed IP string, or None.

    Uses socket.inet_pton (C) instead of the ipaddress module: the dbip CSV has
    ~700k rows and ipaddress.ip_address on each start/end pair took ~20-60s to
    build the table on the first access-log view.
    """
    try:
        if ":" in value:
            return 6, int.from_bytes(socket.inet_pton(socket.AF_INET6, value), "big")
        return 4, int.from_bytes(socket.inet_pton(socket.AF_INET, value), "big")
    except OSError:
        return None


_DBIP_WARM_LOCK = threading.Lock()
_DBIP_WARM_STATE = {"started": False, "ready": False}


def _dbip_ranges_ready() -> bool:
    """True once the dbip country table is built.

    Building it parses a ~700k-row CSV (~7-20s cold), so the first call kicks
    the build onto a daemon thread and returns False - an access-log view then
    renders immediately with empty country columns, and later refreshes (the
    response has a short TTL cache) show the geo data once the table is warm.
    """
    if _DBIP_WARM_STATE["ready"]:
        return True
    with _DBIP_WARM_LOCK:
        if _DBIP_WARM_STATE["ready"]:
            return True
        if not _DBIP_WARM_STATE["started"]:
            _DBIP_WARM_STATE["started"] = True

            def _build() -> None:
                try:
                    _dbip_country_ranges()
                finally:
                    _DBIP_WARM_STATE["ready"] = True

            threading.Thread(target=_build, name="spanel-dbip-warm", daemon=True).start()
    return False


def _warm_dbip_ranges_blocking() -> None:
    """Build the table synchronously (test helper / one-off warmers)."""
    _dbip_country_ranges()
    _DBIP_WARM_STATE["started"] = True
    _DBIP_WARM_STATE["ready"] = True


@lru_cache(maxsize=1)
def _dbip_country_ranges() -> tuple[tuple[tuple[int, int, str], ...], tuple[tuple[int, int, str], ...]]:
    path = _ensure_dbip_country_cache()
    if path is None:
        return (), ()
    ipv4_ranges: list[tuple[int, int, str]] = []
    ipv6_ranges: list[tuple[int, int, str]] = []
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) < 3:
                    continue
                start = _ip_str_to_int(row[0].strip())
                end = _ip_str_to_int(row[1].strip())
                if not start or not end or start[0] != end[0]:
                    continue
                country_code = row[2].strip().upper()
                (ipv4_ranges if start[0] == 4 else ipv6_ranges).append((start[1], end[1], country_code))
    except Exception:
        return (), ()
    ipv4_ranges.sort(key=lambda item: item[0])
    ipv6_ranges.sort(key=lambda item: item[0])
    return tuple(ipv4_ranges), tuple(ipv6_ranges)


def _lookup_dbip_country(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> dict[str, str]:
    if not _dbip_ranges_ready():
        return {"country": "", "country_code": ""}
    ipv4_ranges, ipv6_ranges = _dbip_country_ranges()
    ranges = ipv4_ranges if address.version == 4 else ipv6_ranges
    if not ranges:
        return {"country": "", "country_code": ""}
    value = int(address)
    low = 0
    high = len(ranges) - 1
    while low <= high:
        mid = (low + high) // 2
        start, end, country_code = ranges[mid]
        if value < start:
            high = mid - 1
        elif value > end:
            low = mid + 1
        else:
            if country_code == "ZZ":
                return {"country": "", "country_code": ""}
            return {"country": COUNTRY_NAMES.get(country_code, country_code), "country_code": country_code}
    return {"country": "", "country_code": ""}


def _lookup_ip_country(ip: str) -> dict[str, str]:
    value = (ip or "").strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return {"country": "", "country_code": ""}
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return {"country": "", "country_code": ""}
    dbip_country = _lookup_dbip_country(address)
    if dbip_country["country_code"]:
        return dbip_country
    reader = _geoip_country_reader()
    if reader is None:
        return {"country": "", "country_code": ""}
    try:
        record = reader.get(value) or {}
    except Exception:
        return {"country": "", "country_code": ""}
    country = record.get("country") or record.get("registered_country") or {}
    names = country.get("names") or {}
    country_code = country.get("iso_code") or ""
    return {
        "country": names.get("en") or country_code,
        "country_code": country_code,
    }


def _parse_access_log_line(domain: str, line: str, sequence: int) -> tuple[datetime, dict] | None:
    match = ACCESS_LOG_RE.match(line or "")
    if not match:
        return None
    status_code = int(match.group("status"))
    method, path, protocol = _split_request(match.group("request"))
    timestamp = _parse_nginx_time(match.group("time"))
    sort_time = timestamp or datetime.min.replace(tzinfo=timezone.utc)
    digest = hashlib.sha256(f"{domain}\0{sequence}\0{line}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    ip = match.group("ip") or ""
    country = _lookup_ip_country(ip)
    item = {
        "id": digest,
        "domain": domain,
        "verdict": _access_verdict(status_code),
        "timestamp": timestamp.isoformat() if timestamp else "",
        "duration_ms": _duration_ms(match.group("request_time")),
        "ip": ip,
        "country": country["country"],
        "country_code": country["country_code"],
        "method": method,
        "path": path,
        "protocol": protocol,
        "status": status_code,
        "reason": _access_reason(path, status_code),
        "user_agent": match.group("user_agent") or "",
        "referer": match.group("referer") or "",
        "raw": line,
    }
    return sort_time, item


def _matches_access_filter(item: dict, verdict: str, query: str) -> bool:
    if verdict and verdict != "all" and item.get("verdict") != verdict:
        return False
    needle = (query or "").strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        str(item.get(key, ""))
        for key in ("domain", "verdict", "method", "path", "ip", "country", "country_code", "reason", "status", "user_agent", "referer", "protocol", "raw")
    ).lower()
    return needle in haystack


def _read_site_logs(domains: list[str], lines: int) -> dict[str, str | None]:
    """{domain: content} for every site's access log in one helper spawn.

    ``None`` means the file does not exist. Falls back to per-site reads only
    when the batch helper is unavailable (dev).
    """
    if not domains:
        return {}
    result = shell.privileged(
        "site-logs-read-many",
        helper_args=["access", str(lines), *domains],
        check=False,
        fallback=None,
    ) if _batch_helper_available() else None

    if result is None or result.returncode != 0:
        out: dict[str, str | None] = {}
        for domain in domains:
            data = nginx.read_site_log(domain, "access", lines)
            out[domain] = data.get("content") or "" if data.get("exists") else None
        return out

    blocks: dict[str, str | None] = {domain: None for domain in domains}
    for chunk in (result.stdout or "").split("\x1f"):
        if not chunk:
            continue
        head, _, body = chunk.partition("\n")
        domain = head.strip()
        if domain not in blocks:
            continue
        blocks[domain] = None if body.strip() == "spanel_LOG_MISSING" else body
    return blocks


def _batch_helper_available() -> bool:
    from app.services.shell import _use_helper

    return _use_helper()


# Reading two dozen nginx logs off a cold disk takes seconds; the page polls
# every few seconds, so the same answer is served from here in between.
_ACCESS_LOG_CACHE: dict[tuple, tuple[float, dict]] = {}
_ACCESS_LOG_TTL = 4.0


def access_logs(
    websites: Iterable[Website],
    *,
    verdict: str = "all",
    query: str = "",
    limit: int = 50,
    lines: int = 5000,
) -> dict:
    import time

    websites = list(websites)
    safe_limit = max(1, min(int(limit or 50), 500))
    safe_lines = max(1, min(int(lines or 5000), 5000))
    safe_verdict = verdict if verdict in {"all", "allow", "block", "error"} else "all"
    filtering = safe_verdict != "all" or bool((query or "").strip())
    # Without a filter the newest `limit` lines per site are all we can show, so
    # there is no reason to tail (and parse) thousands. With a filter we need
    # the deep history to find enough matches.
    scan_lines = safe_lines if filtering else min(safe_lines, max(safe_limit * 4, 400))

    cache_key = (tuple(sorted(w.domain for w in websites)), safe_verdict, (query or "").strip(), safe_limit, scan_lines)
    hit = _ACCESS_LOG_CACHE.get(cache_key)
    if hit and (time.monotonic() - hit[0]) < _ACCESS_LOG_TTL:
        return {**hit[1], "cached": True}

    # Start (once) the background build of the geo table so it overlaps the log
    # read + parse below instead of blocking the first request that needs it.
    _dbip_ranges_ready()

    domains = [_validate_domain(w.domain) for w in websites]
    blocks = _read_site_logs(domains, scan_lines)

    sortable: list[tuple[datetime, int, dict]] = []
    parsed_count = 0
    sequence = 0
    missing: list[str] = []
    for domain in domains:
        content = blocks.get(domain)
        if content is None:
            missing.append(domain)
            continue
        for line in content.splitlines():
            sequence += 1
            parsed = _parse_access_log_line(domain, line, sequence)
            if not parsed:
                continue
            parsed_count += 1
            sort_time, item = parsed
            if _matches_access_filter(item, safe_verdict, query):
                sortable.append((sort_time, parsed_count, item))
    sortable.sort(key=lambda row: (row[0], row[1]), reverse=True)
    items = [item for _, _, item in sortable[:safe_limit]]
    payload = {
        "items": items,
        "total": len(sortable),
        "scanned": parsed_count,
        "limit": safe_limit,
        "lines": scan_lines,
        "verdict": safe_verdict,
        "query": (query or "").strip(),
        "missing": missing,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    if len(_ACCESS_LOG_CACHE) > 32:
        _ACCESS_LOG_CACHE.clear()
    _ACCESS_LOG_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


# --- rules an assistant is allowed to add -----------------------------------

# An assistant never writes ModSecurity. It picks what to match on and supplies
# one value; the rule itself is built here from a fixed template. The whole
# point is that no string an AI produces is ever interpreted as rule syntax:
# a model that has been talked into emitting `" "id:1,phase:1,exec:/bin/sh` by
# something it read in an access log gets that rejected as a value, not
# executed as a directive.
MCP_RULE_TARGETS = {
    "ip": ("REMOTE_ADDR", "@ipMatch", "requests from {value}"),
    "path": ("REQUEST_URI", "@contains", "requests for {value}"),
    "user_agent": ("REQUEST_HEADERS:User-Agent", "@contains", "requests claiming to be {value}"),
    "query": ("QUERY_STRING", "@contains", "requests whose query contains {value}"),
}

# Deliberately narrow, and narrower than "what a URL can hold". Every character
# that could end the quoted operand, start a macro, or continue a directive is
# absent: no quote of either kind, no backslash, no percent, no brace, no
# newline. What is left cannot leave the string it is placed in.
MCP_RULE_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@=,+~?&|-]{1,200}$")

# Reserved for rules added this way, and outside the range the panel's own
# rules use, so a human reading the file can tell at a glance where a rule
# came from - and deleting the whole range never takes a panel rule with it.
MCP_RULE_ID_FIRST = 1090000
MCP_RULE_ID_LAST = 1099999

_MCP_RULE_ID_RE = re.compile(r"\bid:(\d{6,7})\b")


def mcp_rule_value(value: str) -> str:
    """The one string an assistant supplies, or a refusal naming the problem."""
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("A value to match on is required")
    if not MCP_RULE_VALUE_RE.fullmatch(candidate):
        raise ValueError(
            "That value contains characters a rule may not carry. Letters, "
            "digits and . _ : / @ = , + ~ ? & | - are allowed, up to 200 "
            "characters."
        )
    return candidate


# What real visitors send. A user-agent rule matches by substring, so a value
# found in any of these would block people, or the crawlers a website lives on.
# Not hypothetical: an assistant added "10_15_7" - every Mac browser's version
# string - to block one scanner.
ORDINARY_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) coc_coc_browser/140.0.0 Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Zalo iOS/25.9 ZaloTheme/light ZaloLanguage/vn",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
)

# Paths every WordPress or PHP site serves to its own visitors and editors. A
# path rule matches by substring too: "/wp-json/batch/v1" is the block editor's
# own endpoint, and "wp-login" would lock every administrator out.
ORDINARY_PATHS = (
    "/", "/index.php", "/wp-login.php", "/wp-admin/", "/wp-admin/admin-ajax.php",
    "/wp-admin/post.php?post=1&action=edit", "/wp-json/wp/v2/posts", "/wp-json/batch/v1",
    "/wp-content/themes/theme/style.css", "/wp-includes/js/jquery/jquery.min.js",
    "/wp-content/uploads/2026/09/photo.jpg", "/feed/", "/sitemap.xml", "/robots.txt",
    "/favicon.ico", "/cart/", "/checkout/", "/my-account/",
)


def ordinary_traffic_conflict(match: str, value: str) -> str:
    """Why a rule would block ordinary traffic, or "" when it would not."""
    import ipaddress

    needle = value.lower()
    if match == "user_agent":
        if len(needle) < 4:
            return "a user-agent value that short matches browsers by accident"
        for agent in ORDINARY_USER_AGENTS:
            if needle in agent.lower():
                return f"it appears in the user agent of ordinary visitors or search crawlers: {agent[:90]}"
    elif match == "path":
        if len(needle) < 4:
            return "a path that short matches ordinary pages"
        for path in ORDINARY_PATHS:
            if needle in path.lower():
                return f"it matches pages every site serves, such as {path}"
    elif match == "query":
        if len(needle) < 3:
            return "a query value that short matches ordinary links"
    elif match == "ip":
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            return ""
        if network.prefixlen < (16 if network.version == 4 else 32):
            return f"{network} is too wide a range: it covers {network.num_addresses:,} addresses"
        # The server reaches its own sites over loopback (health and
        # certificate checks); a LAN range would be its neighbours.
        internal = [ipaddress.ip_network(n) for n in (
            "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10")]
        if any(network.version == n.version and (network.subnet_of(n) or n.subnet_of(network)) for n in internal):
            return f"{network} is the server itself or its private network"
    return ""


def next_mcp_rule_id(existing: str) -> int:
    """The lowest id in the reserved range that the file is not already using.

    Reusing an id ModSecurity has already seen makes it refuse to load the
    whole configuration, which takes every site on the box down at the next
    nginx reload. Cheaper to scan the file.
    """
    used = {int(found) for found in _MCP_RULE_ID_RE.findall(existing or "")}
    for candidate in range(MCP_RULE_ID_FIRST, MCP_RULE_ID_LAST + 1):
        if candidate not in used:
            return candidate
    raise ValueError(
        "All 10000 rule ids reserved for assistant-added rules are in use. "
        "Remove some on the WAF page first."
    )


def render_mcp_rule(match: str, value: str, *, rule_id: int, who: str, when: str,
                    reason: str = "") -> str:
    """One ModSecurity rule, built from a template around a checked value.

    phase:1 always. These are blocks - an address, a path, a user agent - and
    deciding them before the request body is read is both cheaper and the only
    phase where the decision is certain to be reached.
    """
    if match not in MCP_RULE_TARGETS:
        raise ValueError(
            "match must be one of: " + ", ".join(sorted(MCP_RULE_TARGETS))
        )
    safe_value = mcp_rule_value(value)
    conflict = ordinary_traffic_conflict(match, safe_value)
    if conflict:
        raise ValueError(f"Refused: {conflict}. Block something narrower.")
    if not MCP_RULE_ID_FIRST <= int(rule_id) <= MCP_RULE_ID_LAST:
        raise ValueError("Rule id is outside the range reserved for this")

    variable, operator, description = MCP_RULE_TARGETS[match]
    message = description.format(value=safe_value)

    # The provenance line answers the question a rule nobody recognises always
    # raises six months later: who added this, when, and what for.
    note = mcp_rule_value(who) if who else "unknown"
    stamp = mcp_rule_value(when) if when else ""
    why = re.sub(r"[^A-Za-z0-9 ._:/-]", "", (reason or "").strip())[:120]

    header = "# spanel-mcp: added by " + note
    if stamp:
        header += " on " + stamp
    if why:
        header += " - " + why

    directive = (
        'SecRule ' + variable + ' "' + operator + ' ' + safe_value + '" '
        + '"id:' + str(int(rule_id)) + ',phase:1,deny,status:403,log,'
        + "msg:'spanel MCP: blocked " + message + "'\""
    )
    return header + "\n" + directive


def append_mcp_rule(existing: str, match: str, value: str, *, who: str, when: str,
                    reason: str = "") -> tuple[str, int]:
    """The new file contents, and the id that was allocated.

    Read-modify-write against one file, which is what the panel's own WAF page
    does too. Two people saving at the same moment is a lost update here as it
    is there; the provenance comment is what makes an assistant's rule
    identifiable afterwards either way.
    """
    current = _validate_custom_rules(existing or "")
    rule_id = next_mcp_rule_id(current)
    rule = render_mcp_rule(match, value, rule_id=rule_id, who=who, when=when,
                           reason=reason)
    combined = (current + "\n\n" + rule) if current else rule
    return _validate_custom_rules(combined), rule_id


def access_summary(
    websites: Iterable[Website],
    *,
    lines: int = 5000,
    top: int = 10,
) -> dict:
    """Counts rather than entries: what the traffic looks like, in one answer.

    `access_logs` exists already and returns the entries themselves, but it
    caps what it returns at 500 and an assistant asked "who is hammering this
    site" does not want 500 rows - it wants the shape. Handing a model the
    rows and letting it count would spend the operator's tokens on arithmetic
    and get it wrong on ties.

    Reuses the same reader and the same line parser as `access_logs`, so a fix
    to either reaches both and the two can never disagree about what a line
    means.
    """
    websites = list(websites)
    safe_lines = max(1, min(int(lines or 5000), 5000))
    safe_top = max(1, min(int(top or 10), 50))

    domains = [_validate_domain(w.domain) for w in websites]
    blocks = _read_site_logs(domains, safe_lines)

    total = 0
    verdicts: dict[str, int] = {}
    statuses: dict[int, int] = {}
    by_ip: dict[str, int] = {}
    by_path: dict[str, int] = {}
    by_country: dict[str, int] = {}
    blocked_ips: dict[str, int] = {}
    first_seen = ""
    last_seen = ""
    missing: list[str] = []
    sequence = 0

    for domain in domains:
        content = blocks.get(domain)
        if content is None:
            missing.append(domain)
            continue
        for line in content.splitlines():
            sequence += 1
            parsed = _parse_access_log_line(domain, line, sequence)
            if not parsed:
                continue
            _, item = parsed
            total += 1
            verdicts[item["verdict"]] = verdicts.get(item["verdict"], 0) + 1
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            if item["ip"]:
                by_ip[item["ip"]] = by_ip.get(item["ip"], 0) + 1
                if item["verdict"] == "block":
                    blocked_ips[item["ip"]] = blocked_ips.get(item["ip"], 0) + 1
            if item["path"]:
                by_path[item["path"]] = by_path.get(item["path"], 0) + 1
            if item["country"]:
                by_country[item["country"]] = by_country.get(item["country"], 0) + 1
            stamp = item["timestamp"]
            if stamp:
                if not first_seen or stamp < first_seen:
                    first_seen = stamp
                if stamp > last_seen:
                    last_seen = stamp

    def _top(counts: dict, key_name: str) -> list[dict]:
        # Sorted by count, then by key, so the same log always produces the
        # same answer - a summary that reshuffles on ties reads as a change.
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
        return [{key_name: key, "requests": count} for key, count in ordered[:safe_top]]

    return {
        "domains": domains,
        "requests": total,
        "lines_read": safe_lines,
        "from": first_seen,
        "to": last_seen,
        "verdicts": dict(sorted(verdicts.items())),
        "top_status": _top(statuses, "status"),
        "top_ips": _top(by_ip, "ip"),
        "top_paths": _top(by_path, "path"),
        "top_countries": _top(by_country, "country"),
        "most_blocked_ips": _top(blocked_ips, "ip"),
        "no_log_yet": missing,
    }


def clear_access_logs(websites: Iterable[Website]) -> int:
    _ACCESS_LOG_CACHE.clear()
    cleared = 0
    for website in websites:
        nginx.clear_site_log(_validate_domain(website.domain), "access")
        cleared += 1
    return cleared
