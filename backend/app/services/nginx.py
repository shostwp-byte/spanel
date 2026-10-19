import hashlib
import json
import math
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from app.core.config import settings
from app.services import panel_ipv6
from app.services import site_users
from app.services.shell import shell

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "nginx"
CUSTOM_INCLUDE_DIR = Path("/etc/nginx/spanel/custom")

ALLOWED_PHP_VERSIONS = {"5.6", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"}
ALLOWED_APP_TYPES = {"wordpress", "php", "static", "application"}
# Served by proxying to an installed application's port instead of PHP-FPM.
PROXIED_APP_TYPES = {"application"}
PROXY_TIMEOUT_SECONDS = 300
ALLOWED_REWRITE_MODES = {"none", "front_controller", "laravel", "codeigniter", "seohburl"}
ALLOWED_LOG_KINDS = {"access", "error"}
DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
MAX_FULL_CONFIG_BYTES = 128 * 1024
HTTP_FLOOD_DEFAULTS = {
    "access_limit_requests": 100,
    "access_limit_window": 10,
    "access_limit_burst": 100,
    "connection_limit": 60,
}
HTTP_FLOOD_ZONES_FALLBACK = ["bash", "-lc", "cat >/tmp/spanel-http-flood-zones.conf && echo HTTP flood zones saved"]
# Bot blocking. The lists people paste in come from tools like CPGuard and run
# to a few hundred names, so these are generous - they exist to keep one paste
# from producing a config nginx will not load, not to ration the feature.
MAX_BLOCKED_BOTS = 500
MAX_BOT_NAME_LENGTH = 120
WORDPRESS_CSP = (
    "default-src 'self' https: data: blob:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
    "style-src 'self' 'unsafe-inline' https:; "
    "img-src 'self' data: https: blob:; "
    "font-src 'self' data: https:; "
    "connect-src 'self' https:; "
    "frame-src 'self' https: blob:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self' https:; "
    "frame-ancestors 'self'; "
    "upgrade-insecure-requests"
)
WORDPRESS_CSP_HEADER = f'    add_header Content-Security-Policy "{WORDPRESS_CSP}" always;'


def waf_rules_file(domain: str) -> str:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    return f"/etc/nginx/modsec/sites/{safe_domain}.conf"


def _http_flood_value(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def validate_http_flood_config(raw=None) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "access_limit_requests": _http_flood_value(raw.get("access_limit_requests"), HTTP_FLOOD_DEFAULTS["access_limit_requests"], 1, 100000),
        "access_limit_window": _http_flood_value(raw.get("access_limit_window"), HTTP_FLOOD_DEFAULTS["access_limit_window"], 1, 3600),
        "access_limit_burst": _http_flood_value(raw.get("access_limit_burst"), HTTP_FLOOD_DEFAULTS["access_limit_burst"], 0, 100000),
        "connection_limit": _http_flood_value(raw.get("connection_limit"), HTTP_FLOOD_DEFAULTS["connection_limit"], 1, 10000),
    }


def http_flood_config_for_website(website) -> dict:
    return validate_http_flood_config(getattr(website, "http_flood_config", "") or "")


def http_flood_zone_name(domain: str) -> str:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    digest = hashlib.sha1(safe_domain.encode("utf-8")).hexdigest()[:12]
    return f"spanel_hf_{digest}"


def _http_flood_rate(config: dict) -> str:
    requests = max(1, int(config["access_limit_requests"]))
    window = max(1, int(config["access_limit_window"]))
    if requests >= window:
        return f"{max(1, math.ceil(requests / window))}r/s"
    return f"{max(1, math.ceil((requests * 60) / window))}r/m"


def _http_flood_zone_line(domain: str, config: dict) -> str:
    return f"limit_req_zone $spanel_http_flood_key zone={http_flood_zone_name(domain)}:10m rate={_http_flood_rate(config)};"


def _http_flood_challenge_block() -> str:
    challenge_html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Checking browser</title>'
        '<style>body{font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;display:grid;place-items:center;min-height:100vh;margin:0}'
        'main{max-width:420px;padding:24px;text-align:center}'
        'strong{display:block;font-size:20px;margin-bottom:8px}</style>'
        '<script>setTimeout(function(){document.cookie="spanel_http_flood_ok=1; Max-Age=3600; Path=/; SameSite=Lax";'
        'window.location.replace(window.location.href)},3000)</script>'
        '</head><body><main><strong>Checking browser</strong><p>Please wait a moment and refresh automatically.</p></main></body></html>'
    )
    return f"""    error_page 429 = @spanel_http_flood_challenge;
    location @spanel_http_flood_challenge {{
        default_type text/html;
        add_header Cache-Control "no-store" always;
        return 200 '{challenge_html}';
    }}"""


def _http_flood_block(domain: str, config: dict | None = None) -> str:
    safe_config = validate_http_flood_config(config)
    zone = http_flood_zone_name(domain)
    burst = safe_config["access_limit_burst"]
    connections = safe_config["connection_limit"]
    # nodelay carries this whole feature. Without it nginx *queues* everything
    # above the rate instead of rejecting it, and the limit sits at server
    # level so it covers every image, stylesheet and font as well as the page:
    # one ordinary page with 30 assets measured 0.39s unlimited and 2.97s
    # limited, a 7.6x tax on every visitor. Worse, the 429 challenge below -
    # the thing that is supposed to tell a browser from a bot - never fired at
    # all, because nothing was ever rejected. With nodelay a browser's burst of
    # assets is served at once and only sustained excess reaches the challenge.
    limit_req = f"limit_req zone={zone} nodelay;"
    if burst > 0:
        limit_req = f"limit_req zone={zone} burst={burst} nodelay;"
    return f"""    # SPANEL HTTP FLOOD BEGIN
    {limit_req}
    limit_conn spanel_conn_flood {connections};
    limit_conn spanel_vhost_cap 150;
    limit_req zone=spanel_db_scrape_guard burst=20 nodelay;
    if ($spanel_scrape_challenge = 1) {{ return 429; }}
    limit_req_status 429;
    limit_conn_status 429;
{_http_flood_challenge_block()}
    # SPANEL HTTP FLOOD END"""


def render_http_flood_zones(websites) -> str:
    # An empty key means "do not count this request" - nginx says so for both
    # limit_req and limit_conn, and the challenge cookie already relies on it.
    # Two things get that treatment.
    #
    # Static files, because counting them was measured to do nothing but harm.
    # On two live WooCommerce sites the limiter turned away 113 and 413
    # requests in a day; every single one was a .js or .css, and 432 of the 526
    # came from someone who had just clicked a paid advert. A page with two
    # hundred plugin assets opens more concurrent HTTP/2 streams than any sane
    # connection limit, and nginx counts a stream as a connection - so one
    # visitor, one page view, looks like a flood. What they got was a shop with
    # its stylesheet missing.
    #
    # Nothing is lost by exempting them: a static file is served by nginx from
    # disk without touching PHP or the database, which is what this protection
    # exists to shield. A flood of asset requests is a bandwidth question, and
    # the firewall answers that one.
    lines = [
        "# Managed by spanel. Shared zones for per-website HTTP flood protection.",
        "map $uri $spanel_http_flood_static {",
        "    default \"\";",
        "    ~*[.](?:css|js|mjs|map|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|eot)$ static;",
        "}",
        "map \"$spanel_http_flood_static:$cookie_spanel_http_flood_ok\" $spanel_http_flood_key {",
        "    default $binary_remote_addr;",
        # A static file, whoever asked for it.
        "    \"~^static:\" \"\";",
        # Someone who has already passed the challenge.
        "    \"~:1$\" \"\";",
        "}",
        "limit_conn_zone $spanel_http_flood_key zone=spanel_conn_flood:10m;",
        "limit_conn_zone $host zone=spanel_vhost_cap:10m;",
        "map $spanel_http_flood_static $spanel_dynamic_limit_key {",
        "    default $binary_remote_addr;",
        "    static \"\";",
        "}",
        "limit_req_zone $spanel_dynamic_limit_key zone=spanel_db_scrape_guard:10m rate=5r/s;",
        "map \"$request_uri:$cookie_spanel_http_flood_ok\" $spanel_scrape_challenge {",
        "    default 0;",
        "    \"~*(?:/wp-json/|/page/[2-9]|/page/[1-9][0-9]+|[?&](?:s|p|paged|page_id|cat|tag|author|min_price|max_price|orderby|rating_filter|filter_[a-z0-9_]+)=).*:1$\" 0;",
        "    \"~*(?:/wp-json/|/page/[2-9]|/page/[1-9][0-9]+|[?&](?:s|p|paged|page_id|cat|tag|author|min_price|max_price|orderby|rating_filter|filter_[a-z0-9_]+)=)\" 1;",
        "}",
    ]
    seen = set()
    for website in websites:
        if not bool(getattr(website, "http_flood_enabled", False)):
            continue
        domain = _safe_domain(getattr(website, "domain", ""))
        zone = http_flood_zone_name(domain)
        if zone in seen:
            continue
        seen.add(zone)
        lines.append(_http_flood_zone_line(domain, http_flood_config_for_website(website)))
    return "\n".join(lines).strip() + "\n"


def sync_http_flood_zones(websites):
    content = render_http_flood_zones(websites)
    return shell.privileged(
        "http-flood-zones-save",
        check=False,
        input=content,
        fallback=HTTP_FLOOD_ZONES_FALLBACK,
    )


def normalize_blocked_bots(raw) -> list[str]:
    """Turn whatever the user pasted into a clean list of user-agent tokens.

    People paste these in bulk - a CPGuard list, a blog post, a column out of a
    spreadsheet - so accept newlines, commas and semicolons as separators, drop
    blanks, and de-duplicate case-insensitively while keeping the first spelling
    seen. Bot names are matched as substrings of User-Agent, not as patterns:
    "AhrefsBot" is meant to catch "Mozilla/5.0 (compatible; AhrefsBot/7.0...)".
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        candidates = re.split(r"[\n,;]+", raw)
    else:
        candidates = list(raw)

    bots: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        name = str(candidate).strip().strip('"').strip("'").strip()
        if not name:
            continue
        if len(name) > MAX_BOT_NAME_LENGTH:
            raise ValueError(f"Bot name is too long (max {MAX_BOT_NAME_LENGTH} characters): {name[:40]}...")
        # The rendered form is a double-quoted nginx string. re.escape() makes
        # the name literal to PCRE, but it does not touch `"` - that is not a
        # regex metacharacter - so a name containing one would close the string
        # early and let the rest be parsed as configuration. Backslashes and
        # control characters are refused for the same reason: nothing that can
        # steer the nginx lexer belongs in a User-Agent substring.
        if any(ch in name for ch in '"\\\r\n'):
            raise ValueError(f'Bot name cannot contain quotes, backslashes or line breaks: {name[:40]}')
        if any(ord(ch) < 32 for ch in name):
            raise ValueError(f"Bot name cannot contain control characters: {name[:40]}")
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        bots.append(name)

    if len(bots) > MAX_BLOCKED_BOTS:
        raise ValueError(f"Too many bots (max {MAX_BLOCKED_BOTS}); got {len(bots)}")
    return bots


def _bot_block(bots: list[str]) -> str:
    """One `if` with one combined regex, not one `if` per bot.

    A few hundred separate `if` blocks would be both unreadable and slow; nginx
    evaluates them in order on every request. A single alternation is matched
    once. `re.escape` is what makes the names literal - plenty of real bot
    strings carry regex metacharacters ("Mozilla/5.0", "bingbot/2.0",
    "Sogou web spider/4.0"), and unescaped they would match far more than the
    operator asked for, or fail to compile at all.
    """
    alternation = "|".join(re.escape(bot) for bot in bots)
    return f"""    # SPANEL BOT BLOCK BEGIN
    if ($http_user_agent ~* "({alternation})") {{ return 403; }}
    # SPANEL BOT BLOCK END"""


def _waf_block(domain: str) -> str:
    return f"""    # SPANEL WAF BEGIN
    modsecurity on;
    modsecurity_rules_file {waf_rules_file(domain)};
    # SPANEL WAF END"""


FASTCGI_CACHE_SERVER_BLOCK = """    # SPANEL FASTCGI CACHE SERVER BEGIN
    set $spanel_skip_cache 0;
    if ($request_method = POST) { set $spanel_skip_cache 1; }
    if ($query_string != "") { set $spanel_skip_cache 1; }
    if ($http_cache_control ~* "no-cache|no-store|max-age=0") { set $spanel_skip_cache 1; }
    if ($http_pragma = "no-cache") { set $spanel_skip_cache 1; }
    if ($request_uri ~* "/wp-admin/|/wp-login.php|/xmlrpc.php|wp-.*.php|/feed/|sitemap(_index)?\\.xml") { set $spanel_skip_cache 1; }
    if ($http_cookie ~* "comment_author|wordpress_[a-f0-9]+|wordpress_logged_in|wp-postpass|woocommerce_items_in_cart|woocommerce_cart_hash|wp_woocommerce_session|edd_items_in_cart") { set $spanel_skip_cache 1; }
    add_header X-FastCGI-Cache $upstream_cache_status always;
    # SPANEL FASTCGI CACHE SERVER END"""
# Kept byte-identical to the block in templates/nginx/wordpress.conf.j2: this
# copy is injected into vhosts that already exist, that one renders new ones,
# and a site should not behave differently for having been created earlier.
#
# The old settings were 15s with min_uses 2, which together meant a page had to
# be fetched twice inside the same 15 seconds to be cached at all - so on any
# site below a few requests per second, nothing ever was. Measured on a live
# site, the same page took 1.33s uncached and 0.016s cached.
#
# Serving anonymous visitors a page up to 10 minutes old is safe here because
# the skip rules above take every author out of the cache entirely: logged-in
# cookies, WooCommerce cart cookies, POST, any query string and all of wp-admin
# bypass it, so whoever edits the page sees the change immediately.
FASTCGI_CACHE_LOCATION_BLOCK = """        # SPANEL FASTCGI CACHE LOCATION BEGIN
        fastcgi_cache SPANEL_FASTCGI;
        fastcgi_cache_methods GET HEAD;
        fastcgi_cache_valid 200 301 302 10m;
        fastcgi_cache_valid 404 1m;
        fastcgi_cache_min_uses 1;
        fastcgi_cache_use_stale error timeout invalid_header updating http_500 http_503;
        fastcgi_cache_background_update on;
        fastcgi_cache_revalidate on;
        fastcgi_cache_bypass $spanel_skip_cache;
        fastcgi_no_cache $spanel_skip_cache;
        fastcgi_no_cache $upstream_http_set_cookie;
        fastcgi_cache_lock on;
        # SPANEL FASTCGI CACHE LOCATION END"""

# Block-opening directives that nest scopes; matched against the original
# text so the trailing ``{`` is preserved.
DANGEROUS_BLOCKS = re.compile(
    r"(?mi)(?:^|[;{}\s])\s*("
    r"server\s*\{|"
    r"http\s*\{|"
    r"events\s*\{|"
    r"stream\s*\{|"
    r"upstream\s+[@A-Za-z0-9_][A-Za-z0-9_]*\s*\{"
    r")"
)

# Single-line directives. Matched against text where ``{``, ``}``, and ``;``
# are turned into newlines so directives written on the same physical line
# as their enclosing block are still seen by the line-start anchor.
DANGEROUS_DIRECTIVES = re.compile(
    r"(?mi)^\s*("
    r"include\s+|"            # arbitrary file inclusion
    r"load_module\b|"         # load shared object
    r"user\s+|"               # change worker UID
    r"daemon\s+|"
    r"pid\s+|"
    r"working_directory\b|"
    r"lua_|"                  # ngx_lua
    r"perl_|"                 # ngx_http_perl
    r"js_|"                   # njs scripting
    r"pcre_jit\b|"
    # ---- routing / upstream subversion ----
    r"proxy_pass\b|"
    r"fastcgi_pass\b|"
    r"uwsgi_pass\b|"
    r"scgi_pass\b|"
    r"grpc_pass\b|"
    # ---- arbitrary file read / serve ----
    r"alias\s+|"
    r"root\s+|"
    r"auth_basic_user_file\b|"
    # ---- arbitrary file write via logging ----
    r"access_log\s+|"
    r"error_log\s+|"
    # ---- HTTP response control / phishing primitives ----
    r"return\s+|"             # forced redirects, body injection
    r"error_page\s+|"         # remap error responses to attacker URI
    r"more_set_headers\b|"
    r"more_clear_headers\b|"
    r"auth_request\b|"        # delegate auth to attacker endpoint
    r"sub_filter\b|"          # rewrite response body
    r"sub_filter_once\b|"
    r"addition_before_body\b|"
    r"addition_after_body\b|"
    # ---- cert path override ----
    r"ssl_certificate\b|"
    r"ssl_certificate_key\b|"
    r"ssl_trusted_certificate\b"
    r")"
)

CACHE_HEADER_NAMES = frozenset(
    {
        "cache-control",
        "cdn-cache-control",
        "expires",
        "pragma",
        "surrogate-control",
        "vary",
        "x-cache",
        "x-cache-status",
        "x-fastcgi-cache",
    }
)
ADD_HEADER_NAME_RE = re.compile(
    r"(?mi)(?:^|[;{}])\s*add_header\s+"
    r"([!#$%&'*+\-.^_`|~0-9A-Za-z]+)(?=\s)"
)
TRY_FILES_RE = re.compile(r"(?mi)^\s*try_files\s+([^;{}]+);")


def _check_php_version(php_version: str | None) -> str | None:
    if php_version is None:
        return None
    if php_version not in ALLOWED_PHP_VERSIONS:
        raise ValueError(f"Unsupported PHP version: {php_version}")
    return php_version


def _check_app_type(app_type: str) -> str:
    if app_type not in ALLOWED_APP_TYPES:
        raise ValueError(f"Unsupported app type: {app_type}")
    return app_type


def ensure_proxy_upgrade_map() -> None:
    """Make sure the shared WebSocket map exists before a proxied vhost lands.

    The installer writes it, but a panel updated from an older release has not
    run that step yet, and a proxy vhost referencing $connection_upgrade without
    it fails `nginx -t` and gets rolled back with a confusing error.
    """
    try:
        shell.privileged(
            "nginx-upgrade-map-ensure",
            check=False,
            fallback=["bash", "-lc", "true"],
        )
    except RuntimeError:
        # Development without the helper installed; the vhost test will speak up.
        pass


def _check_app_port(app_type: str, app_port: int | None) -> int | None:
    """A proxied vhost is useless without somewhere to send the request.

    Refusing here beats writing a vhost that fails `nginx -t` on an empty
    proxy_pass, or worse, silently proxies to the wrong port.
    """
    if app_type not in PROXIED_APP_TYPES:
        return None
    try:
        value = int(app_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Pick an installed application for this website first") from exc
    if not 1 <= value <= 65535:
        raise ValueError("Application port is out of range")
    return value


def _check_rewrite_mode(mode: str | None) -> str:
    value = (mode or "none").strip().lower()
    if value not in ALLOWED_REWRITE_MODES:
        raise ValueError(f"Unsupported nginx rewrite mode: {mode}")
    return value


def _effective_document_root(document_root: str, rewrite_mode: str) -> str:
    safe_root = site_users.validate_document_root(document_root)
    if rewrite_mode in {"laravel", "codeigniter"} and safe_root.rstrip("/") == "public_html":
        return "public_html/public"
    return safe_root


def _vhost_path(domain: str) -> Path:
    safe_domain = (domain or "").lower()
    if not DOMAIN_RE.fullmatch(safe_domain):
        raise ValueError("Invalid domain")
    return Path(settings.nginx_sites_available) / f"{safe_domain}.conf"


def vhost_exists(domain: str) -> bool:
    """Whether a managed Nginx site config already sits on disk for this domain.

    The websites table is the usual source of truth, but a leftover config from
    a half-removed site or an out-of-band import would otherwise be silently
    overwritten by a fresh create.
    """
    try:
        return _vhost_path(domain).is_file()
    except (ValueError, OSError):
        return False


def _safe_domain(domain: str) -> str:
    safe_domain = (domain or "").strip().lower()
    if not DOMAIN_RE.fullmatch(safe_domain):
        raise ValueError("Invalid domain")
    return safe_domain


def _safe_alias_domains(aliases: list[str] | tuple[str, ...] | None) -> list[str]:
    safe_aliases: list[str] = []
    seen: set[str] = set()
    for alias in aliases or []:
        safe_alias = _safe_domain(str(alias))
        if safe_alias in seen:
            continue
        safe_aliases.append(safe_alias)
        seen.add(safe_alias)
    return safe_aliases


def _server_names(domain: str, aliases: list[str] | tuple[str, ...] | None = None) -> list[str]:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    names = [safe_domain, f"www.{safe_domain}"]
    seen = set(names)
    for alias in _safe_alias_domains(aliases):
        if alias == safe_domain or alias in seen:
            continue
        names.append(alias)
        seen.add(alias)
    return names


def _redirect_vhost_blocks(
    domain: str,
    redirects: list[str] | tuple[str, ...] | None = None,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
) -> str:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(domain)
    listen80 = f'listen {ip_address}:80;' if ip_address != '*' else 'listen 80;'
    listen443 = f'listen {ip_address}:443 ssl http2;' if ip_address != '*' else 'listen 443 ssl http2;'
    ip_address = get_website_ip(safe_domain)
    reserved = {safe_domain, f"www.{safe_domain}"}
    cert = key = ca = None
    if ssl_cert_path or ssl_key_path:
        cert, key, ca = _manual_ssl_paths(ssl_cert_path, ssl_key_path, ssl_ca_path)
        if ca:
            cert = cert.rsplit("/", 1)[0] + "/fullchain.crt"
    blocks: list[str] = []
    for redirect_domain in _safe_alias_domains(redirects):
        if redirect_domain in reserved:
            continue
        block_lines = [
            "server {",
            f"    {listen80}",
            f"    server_name {redirect_domain};",
            "",
            "    # SPANEL ACME CHALLENGE",
            "    location ^~ /.well-known/acme-challenge/ {",
            "        root /var/www/spanel-acme;",
            "        default_type text/plain;",
            "        try_files $uri =404;",
            "        access_log off;",
            "        auth_basic off;",
            "    }",
            "",
            f"    return 301 https://{safe_domain}$request_uri;",
            "}",
        ]
        if cert and key:
            block_lines.extend([
                "",
                "server {",
                f"    {listen443}",
                f"    server_name {redirect_domain};",
                f"    ssl_certificate {cert};",
                f"    ssl_certificate_key {key};",
                "    ssl_protocols TLSv1.2 TLSv1.3;",
                "    ssl_prefer_server_ciphers off;",
            ])
            if ca:
                block_lines.append(f"    ssl_trusted_certificate {ca};")
            block_lines.extend([
                f"    return 301 https://{safe_domain}$request_uri;",
                "}",
            ])
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


def _certbot_ssl_lines(content: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if (
            "ssl_certificate" in line
            or "include /etc/letsencrypt/options-ssl-nginx.conf" in line
            or "ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem" in line
        ) and stripped not in seen:
            lines.append(line)
            seen.add(stripped)
    return lines


def _append_certbot_redirect_vhosts(content: str, domain: str, redirects: list[str] | tuple[str, ...] | None = None) -> str:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(domain)
    listen80 = f'listen {ip_address}:80;' if ip_address != '*' else 'listen 80;'
    listen443 = f'listen {ip_address}:443 ssl http2;' if ip_address != '*' else 'listen 443 ssl http2;'
    ip_address = get_website_ip(safe_domain)
    reserved = {safe_domain, f"www.{safe_domain}"}
    ssl_lines = _certbot_ssl_lines(content)
    blocks: list[str] = []
    for redirect_domain in _safe_alias_domains(redirects):
        if redirect_domain in reserved:
            continue
        block_lines = [
            "server {",
            f"    {listen80}",
            f"    server_name {redirect_domain};",
            "",
            "    # SPANEL ACME CHALLENGE",
            "    location ^~ /.well-known/acme-challenge/ {",
            "        root /var/www/spanel-acme;",
            "        default_type text/plain;",
            "        try_files $uri =404;",
            "        access_log off;",
            "        auth_basic off;",
            "    }",
            "",
            f"    return 301 https://{safe_domain}$request_uri;",
            "}",
        ]
        if ssl_lines:
            block_lines.extend([
                "",
                "server {",
                f"    {listen443}",
                f"    server_name {redirect_domain};",
                *ssl_lines,
                f"    return 301 https://{safe_domain}$request_uri;",
                "}",
            ])
        blocks.append("\n".join(block_lines))
    if not blocks:
        return content
    return content.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"


def _append_redirect_vhosts(
    content: str,
    domain: str,
    redirects: list[str] | tuple[str, ...] | None = None,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
) -> str:
    redirect_blocks = _redirect_vhost_blocks(domain, redirects, ssl_cert_path, ssl_key_path, ssl_ca_path)
    if not redirect_blocks:
        return content
    return content.rstrip() + "\n\n" + redirect_blocks + "\n"


def _custom_include_path(domain: str) -> Path:
    return CUSTOM_INCLUDE_DIR / f"{_safe_domain(domain)}.conf"


def custom_include_path(domain: str) -> str:
    return f"/etc/nginx/spanel/custom/{_safe_domain(domain)}.conf"


def _custom_include_block(domain: str) -> str:
    return f"    # SPANEL CUSTOM INCLUDE\n    include {custom_include_path(domain)};"


def _ensure_custom_include_position(content: str, domain: str) -> str:
    block = _custom_include_block(domain)
    include_path = re.escape(custom_include_path(domain))
    existing_block = re.compile(
        rf"\n?[ \t]*# SPANEL CUSTOM INCLUDE[ \t]*\n"
        rf"[ \t]*include[ \t]+{include_path};[ \t]*\n?",
        re.MULTILINE,
    )
    cleaned = existing_block.sub("\n", content)
    main_location_pattern = re.compile(
        r"\n    location / \{\n"
        r"(?:        [^\n]*\n)*"
        r"    \}\n"
    )
    main_location_match = main_location_pattern.search(cleaned)
    main_location = ""
    if main_location_match:
        main_location = main_location_match.group(0).strip("\n")
        cleaned = (
            cleaned[:main_location_match.start()].rstrip()
            + "\n"
            + cleaned[main_location_match.end():].lstrip("\n")
        )
    static_location = "\n    location ~* \\.(jpg|jpeg|gif|png|css|js|ico|webp|svg|woff|woff2|ttf|eot)$ {"
    insert_at = cleaned.find(static_location)
    if insert_at == -1:
        insert_at = cleaned.rfind("\n}")
    if insert_at != -1:
        before = cleaned[:insert_at].rstrip()
        after = cleaned[insert_at + 1:].lstrip("\n")
        main_block = f"\n\n{main_location}" if main_location else ""
        return f"{before}\n\n{block}{main_block}\n\n{after}"
    return cleaned.rstrip() + f"\n\n{block}\n"


def read_custom_include(domain: str) -> str:
    try:
        return _custom_include_path(domain).read_text(encoding="utf-8")
    except OSError:
        return ""


def _custom_include_snapshot(domain: str) -> tuple[bool, str]:
    target = _custom_include_path(domain)
    try:
        return True, target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, ""


def _write_custom_include(domain: str, custom_directives: str) -> str:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    safe_custom = validate_custom_nginx(custom_directives)
    target = _custom_include_path(safe_domain)
    if settings.command_dry_run:
        return str(target)
    shell.privileged(
        "nginx-custom-write",
        helper_args=[safe_domain],
        input=safe_custom,
        fallback=[
            "bash",
            "-lc",
            (
                "install -d -m 0775 /etc/nginx/spanel/custom && "
                f"cat > /etc/nginx/spanel/custom/{safe_domain}.conf && "
                f"chmod 0664 /etc/nginx/spanel/custom/{safe_domain}.conf"
            ),
        ],
    )
    return str(target)


def _delete_custom_include(domain: str) -> None:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    if settings.command_dry_run:
        return
    shell.privileged(
        "nginx-custom-delete",
        helper_args=[safe_domain],
        fallback=["rm", "-f", str(_custom_include_path(safe_domain))],
    )


def _restore_custom_include(domain: str, snapshot: tuple[bool, str]) -> None:
    existed, content = snapshot
    if existed:
        _write_custom_include(domain, content)
    else:
        _delete_custom_include(domain)


def _check_log_kind(kind: str) -> str:
    if kind not in ALLOWED_LOG_KINDS:
        raise ValueError("Log kind must be access or error")
    return kind


def _check_tail_lines(lines: int) -> int:
    try:
        value = int(lines)
    except (TypeError, ValueError) as exc:
        raise ValueError("Log lines must be a number") from exc
    if value < 1 or value > 5000:
        raise ValueError("Log lines must be between 1 and 5000")
    return value


def _log_path(domain: str, kind: str) -> Path:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    safe_kind = _check_log_kind(kind)
    return Path("/var/log/nginx") / f"{safe_domain}.{safe_kind}.log"


def validate_custom_nginx(content: Optional[str]) -> str:
    """Sanitize and validate a custom nginx block before rendering it inside a server { } scope."""
    if not content:
        return ""
    text = content.replace("\r\n", "\n").strip()
    if len(text) > 16 * 1024:
        raise ValueError("Custom nginx block is too large")
    # Balanced braces only (allow nested blocks inside server scope).
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced braces in custom nginx block")
    if depth != 0:
        raise ValueError("Unbalanced braces in custom nginx block")
    if DANGEROUS_BLOCKS.search(text):
        raise ValueError(
            "Custom block must not nest server/http/events/stream/upstream blocks"
        )
    # Normalize so the line-anchored deny-list also catches directives
    # written on the same line as their enclosing block, e.g.
    # ``location /api { proxy_pass http://attacker; }`` would otherwise
    # slip past a plain ``^\s*proxy_pass`` match.
    normalized = re.sub(r"[{};]", "\n", text)
    if DANGEROUS_DIRECTIVES.search(normalized):
        raise ValueError(
            "Custom block must not contain disallowed directives (proxy_pass, alias, "
            "return, ssl_certificate, ...)"
        )
    add_header_count = len(re.findall(r"(?mi)(?:^|[;{}])\s*add_header\b", text))
    add_header_names = ADD_HEADER_NAME_RE.findall(text)
    if add_header_count != len(add_header_names):
        raise ValueError("Malformed or unsupported add_header directive")
    unsupported_headers = sorted(
        {
            header
            for header in add_header_names
            if header.lower() not in CACHE_HEADER_NAMES
        },
        key=str.lower,
    )
    if unsupported_headers:
        raise ValueError(
            "Custom add_header may only set cache-related headers; disallowed: "
            + ", ".join(unsupported_headers)
        )
    try_files_matches = list(TRY_FILES_RE.finditer(text))
    for match in try_files_matches:
        arguments = match.group(1).split()
        if not arguments or any(
            token.startswith("file:")
            or token.startswith("http:")
            or token.startswith("https:")
            or ".." in token.split("/")
            for token in arguments
        ):
            raise ValueError("Custom try_files directives must not use traversal or external URLs")
    try_files_count = len(re.findall(r"(?mi)(?:^|[;{}])\s*try_files\b", text))
    if try_files_count != len(try_files_matches):
        raise ValueError("Malformed or unsupported try_files directive")
    for match in re.finditer(r"(?mi)^\s*rewrite\s+\S+\s+(\S+)(?:\s+(\S+))?\s*;", text):
        replacement, flag = match.groups()
        if (
            not replacement.startswith("/")
            or replacement.startswith("//")
            or "://" in replacement
            or ".." in replacement.split("/")
            or (flag and flag not in {"last", "break"})
        ):
            raise ValueError("Custom rewrite directives must target a local URI and use last or break")
    rewrite_count = len(re.findall(r"(?mi)(?:^|[;{}])\s*rewrite\b", text))
    if rewrite_count != len(re.findall(r"(?mi)^\s*rewrite\s+\S+\s+\S+(?:\s+\S+)?\s*;", text)):
        raise ValueError("Malformed or unsupported rewrite directive")
    if "\x00" in text:
        raise ValueError("Custom nginx block contains a NUL byte")
    return text


def validate_full_nginx_config(content: Optional[str]) -> str:
    if content is None:
        raise ValueError("Nginx config is required")
    text = content.replace("\r\n", "\n").strip() + "\n"
    if len(text.encode("utf-8")) > MAX_FULL_CONFIG_BYTES:
        raise ValueError("Nginx config is too large")
    if "\x00" in text:
        raise ValueError("Nginx config contains a NUL byte")
    if "server" not in text or "{" not in text:
        raise ValueError("Nginx config must contain a server block")
    return text


def _has_ssl_config(content: str) -> bool:
    return "ssl_certificate" in content or "listen 443" in content


# Uploaded certs live here; borrowed certs (wildcard-via-Cloudflare, or a
# "use existing" reference to another site's Let's Encrypt lineage) point at the
# certbot live directory. Both roots are spanel-controlled and every path is
# built from a validated domain, never from raw request input.
_SSL_PATH_ROOTS = ("/etc/nginx/spanel/ssl/sites/", "/etc/letsencrypt/live/")


def _manual_ssl_paths(cert_path: str | None, key_path: str | None, ca_path: str | None = None) -> tuple[str, str, str | None]:
    if not cert_path or not key_path:
        raise ValueError("Manual SSL certificate and key paths are required")
    cert = str(cert_path).replace("\\", "/")
    key = str(key_path).replace("\\", "/")
    ca = str(ca_path).replace("\\", "/") if ca_path else None
    for value in (cert, key, ca):
        if not value:
            continue
        if "\x00" in value or "\n" in value or "\r" in value or ".." in value:
            raise ValueError("Manual SSL path contains unsafe characters")
        if not value.startswith(_SSL_PATH_ROOTS):
            raise ValueError("SSL paths must be under /etc/nginx/spanel/ssl/sites or /etc/letsencrypt/live")
    return cert, key, ca


def _write_backup(target: Path, content: str) -> Path:
    backup = target.with_suffix(target.suffix + ".bak")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.bak-",
            delete=False,
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.chmod(0o640)
        temp_path.replace(backup)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return backup


def _merge_certbot_ssl_config(new_content: str, existing_content: str) -> str:
    server_name = "_"
    if "server_name " in new_content:
        server_name = new_content.split("server_name ", 1)[1].split(";", 1)[0].strip()
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ssl_lines = []
    seen_ssl_lines = set()
    for line in existing_content.splitlines():
        if (
            "ssl_certificate" in line
            or "include /etc/letsencrypt/options-ssl-nginx.conf" in line
            or "ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem" in line
        ) and line.strip() not in seen_ssl_lines:
            ssl_lines.append(line)
            seen_ssl_lines.add(line.strip())
    https_lines = []
    for line in new_content.splitlines():
        if f"    {listen80}" in line:
            https_lines.append(line.replace(f"    {listen80}", f"    {listen443}"))
        else:
            https_lines.append(line)
        if "server_name" in line and ssl_lines:
            https_lines.extend(ssl_lines)
    redirect_block = "\n".join([
        "server {",
        f"    {listen80}",
        f"    server_name {server_name};",
        "    return 301 https://$host$request_uri;",
        "}",
        "",
    ])
    return redirect_block + "\n".join(https_lines) + "\n"


def apply_manual_ssl_config(new_content: str, cert_path: str, key_path: str, ca_path: str | None = None) -> str:
    cert, key, ca = _manual_ssl_paths(cert_path, key_path, ca_path)
    fullchain = cert.rsplit("/", 1)[0] + "/fullchain.crt"
    server_name = "_"
    if "server_name " in new_content:
        server_name = new_content.split("server_name ", 1)[1].split(";", 1)[0].strip()
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ip_address = get_website_ip(_domain_from_vhost(new_content))
    listen80 = f"listen {ip_address}:80;" if ip_address and ip_address != "*" else "listen 80;"
    listen443 = f"listen {ip_address}:443 ssl http2;" if ip_address and ip_address != "*" else "listen 443 ssl http2;"
    ssl_lines = [
        f"    ssl_certificate {fullchain if ca else cert};",
        f"    ssl_certificate_key {key};",
        "    ssl_protocols TLSv1.2 TLSv1.3;",
        "    ssl_prefer_server_ciphers off;",
    ]
    if ca:
        ssl_lines.append(f"    ssl_trusted_certificate {ca};")
    https_lines = []
    inserted = False
    for line in new_content.splitlines():
        if f"    {listen80}" in line:
            https_lines.append(line.replace(f"    {listen80}", f"    {listen443}"))
        else:
            https_lines.append(line)
        if not inserted and "server_name" in line:
            https_lines.extend(ssl_lines)
            inserted = True
    redirect_block = "\n".join([
        "server {",
        f"    {listen80}",
        f"    server_name {server_name};",
        "    return 301 https://$host$request_uri;",
        "}",
        "",
    ])
    return redirect_block + "\n".join(https_lines) + "\n"


def _ensure_hsts_header(content: str) -> str:
    security_headers = [
        ('Strict-Transport-Security', '    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'),
        ('Permissions-Policy', '    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=(), bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()" always;'),
        ('Content-Security-Policy', WORDPRESS_CSP_HEADER),
    ]
    headers_to_add = [header for name, header in security_headers if name not in content]
    if not headers_to_add:
        return content
    marker = '    add_header X-XSS-Protection "1; mode=block" always;'
    if marker in content:
        return content.replace(marker, f"{marker}\n" + "\n".join(headers_to_add), 1)
    server_marker = "    server_tokens off;"
    if server_marker in content:
        return content.replace(server_marker, f"{server_marker}\n" + "\n".join(headers_to_add), 1)
    return content


def _php_fpm_socket(php_version: str | None = None) -> str:
    version = _check_php_version(php_version) or settings.default_php_version
    if version not in ALLOWED_PHP_VERSIONS:
        raise ValueError(f"Unsupported PHP version: {version}")
    return f"/run/php/php{version}-fpm.sock"


def _replace_php_fpm_socket(content: str, php_version: str) -> str:
    _check_php_version(php_version)
    return re.sub(
        r"fastcgi_pass\s+unix:/run/php/php[0-9.]+-fpm\.sock;",
        f"fastcgi_pass unix:{_php_fpm_socket(php_version)};",
        content,
    )


def _replace_fastcgi_socket(content: str, socket_path: str) -> str:
    if not re.fullmatch(r"/run/php/[A-Za-z0-9_.-]+\.sock", socket_path or ""):
        raise ValueError("Invalid PHP-FPM socket path")
    return re.sub(
        r"fastcgi_pass\s+unix:[^;]+;",
        f"fastcgi_pass unix:{socket_path};",
        content,
    )


def _domain_from_vhost(content: str) -> str:
    match = re.search(r"(?m)^\s*server_name\s+([^;]+);", content or "")
    if not match:
        return "example.com"
    first = match.group(1).split()[0]
    if first.startswith("www."):
        first = first[4:]
    try:
        return _safe_domain(first)
    except ValueError:
        return "example.com"


def _replace_waf_block(content: str, enabled: bool, domain: str | None = None) -> str:
    pattern = re.compile(
        r"\n?    # SPANEL WAF BEGIN\n.*?\n    # SPANEL WAF END",
        re.DOTALL,
    )
    cleaned = pattern.sub("", content)
    if not enabled:
        return cleaned.rstrip() + "\n"
    block = _waf_block(domain or _domain_from_vhost(cleaned))
    if "    server_tokens off;" in cleaned:
        return cleaned.replace("    server_tokens off;", f"    server_tokens off;\n{block}", 1)
    if "    server_name " in cleaned:
        return re.sub(r"(    server_name [^;]+;)", f"\\1\n{block}", cleaned, count=1)
    match = re.search(r"server\s*\{", cleaned)
    if match:
        insert_at = match.end()
        return cleaned[:insert_at] + "\n" + block + cleaned[insert_at:]
    raise ValueError("Cannot find server block for WAF directives")


def _replace_bot_block(content: str, bots) -> str:
    """Insert ahead of the flood and WAF blocks.

    A blocked bot should cost as little as possible: matching one regex and
    returning 403 is cheaper than running it through ModSecurity's rule set
    and the rate-limiting zones first.
    """
    pattern = re.compile(
        r"\n?    # SPANEL BOT BLOCK BEGIN\n.*?\n    # SPANEL BOT BLOCK END",
        re.DOTALL,
    )
    cleaned = pattern.sub("", content)
    safe_bots = normalize_blocked_bots(bots)
    if not safe_bots:
        return cleaned.rstrip() + "\n"
    block = _bot_block(safe_bots)
    for anchor in ("    # SPANEL HTTP FLOOD BEGIN", "    # SPANEL WAF BEGIN"):
        if anchor in cleaned:
            return cleaned.replace(anchor, f"{block}\n\n{anchor}", 1)
    if "    server_tokens off;" in cleaned:
        return cleaned.replace("    server_tokens off;", f"    server_tokens off;\n{block}", 1)
    if "    server_name " in cleaned:
        return re.sub(r"(    server_name [^;]+;)", f"\\1\n{block}", cleaned, count=1)
    match = re.search(r"server\s*\{", cleaned)
    if match:
        insert_at = match.end()
        return cleaned[:insert_at] + "\n" + block + cleaned[insert_at:]
    raise ValueError("Cannot find server block for bot blocking directives")


def blocked_bots_in_vhost(content: str) -> list[str]:
    """Recover the bot names from an already-rendered block.

    Used to carry the block across a full vhost rewrite. The names went through
    re.escape on the way in, so they come back through a matching unescape;
    anything that does not parse yields an empty list, which the caller treats
    as "nothing to preserve" rather than guessing.
    """
    match = re.search(
        r'# SPANEL BOT BLOCK BEGIN\n\s*if \(\$http_user_agent ~\* "\((.*?)\)"\) \{ return 403; \}',
        content,
        re.DOTALL,
    )
    if not match:
        return []
    names = []
    for piece in match.group(1).split("|"):
        if not piece:
            continue
        names.append(re.sub(r"\\(.)", r"\1", piece))
    return names


def update_bot_block(domain: str, bots) -> str:
    """Rewrite just the bot block in a live vhost, leaving everything else.

    Same shape as update_waf_block: edit the file in place rather than
    re-rendering from the template, so a site whose vhost has been customised
    keeps its customisations. _test_and_reload puts the previous content back
    if nginx refuses the result, which matters here because the block is built
    from operator-supplied text.
    """
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    target = _vhost_path(safe_domain)
    if settings.command_dry_run:
        return _replace_bot_block("server {\n    server_name example.com;\n}\n", bots)
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    new_content = _replace_bot_block(existing, bots)
    _write_backup(target, existing)
    target.write_text(new_content, encoding="utf-8")
    _test_and_reload(target, existing)
    return str(target)


def _replace_http_flood_block(content: str, enabled: bool, domain: str | None = None, config: dict | str | None = None) -> str:
    pattern = re.compile(
        r"\n?    # SPANEL HTTP FLOOD BEGIN\n.*?\n    # SPANEL HTTP FLOOD END",
        re.DOTALL,
    )
    cleaned = pattern.sub("", content)
    if not enabled:
        return cleaned.rstrip() + "\n"
    block = _http_flood_block(domain or _domain_from_vhost(cleaned), validate_http_flood_config(config))
    if "    # SPANEL WAF BEGIN" in cleaned:
        return cleaned.replace("    # SPANEL WAF BEGIN", f"{block}\n\n    # SPANEL WAF BEGIN", 1)
    if "    server_tokens off;" in cleaned:
        return cleaned.replace("    server_tokens off;", f"    server_tokens off;\n{block}", 1)
    if "    server_name " in cleaned:
        return re.sub(r"(    server_name [^;]+;)", f"\\1\n{block}", cleaned, count=1)
    match = re.search(r"server\s*\{", cleaned)
    if match:
        insert_at = match.end()
        return cleaned[:insert_at] + "\n" + block + cleaned[insert_at:]
    raise ValueError("Cannot find server block for HTTP flood directives")


def _replace_fastcgi_cache_blocks(content: str, enabled: bool = True) -> str:
    server_pattern = re.compile(
        r"\n?    # SPANEL FASTCGI CACHE SERVER BEGIN\n.*?\n    # SPANEL FASTCGI CACHE SERVER END",
        re.DOTALL,
    )
    location_pattern = re.compile(
        r"\n?        # SPANEL FASTCGI CACHE LOCATION BEGIN\n.*?\n        # SPANEL FASTCGI CACHE LOCATION END",
        re.DOTALL,
    )
    cleaned = server_pattern.sub("", content)
    cleaned = location_pattern.sub("", cleaned)
    cleaned = re.sub(r"\n?\s*add_header\s+X-FastCGI-Cache\s+[^;]+;", "", cleaned)
    if not enabled:
        return cleaned.rstrip() + "\n"
    if "fastcgi_pass" not in cleaned:
        raise ValueError("Cannot find a PHP FastCGI location")

    if "    client_max_body_size " in cleaned:
        cleaned = re.sub(
            r"(    client_max_body_size [^;]+;)",
            lambda match: f"{match.group(1)}\n\n{FASTCGI_CACHE_SERVER_BLOCK}",
            cleaned,
            count=1,
        )
    elif "    server_tokens off;" in cleaned:
        cleaned = cleaned.replace("    server_tokens off;", f"    server_tokens off;\n\n{FASTCGI_CACHE_SERVER_BLOCK}", 1)
    else:
        cleaned = re.sub(
            r"(    server_name [^;]+;)",
            lambda match: f"{match.group(1)}\n\n{FASTCGI_CACHE_SERVER_BLOCK}",
            cleaned,
            count=1,
        )

    if re.search(r"fastcgi_read_timeout\s+[^;]+;", cleaned):
        return re.sub(
            r"(        fastcgi_read_timeout\s+[^;]+;)",
            lambda match: f"{match.group(1)}\n{FASTCGI_CACHE_LOCATION_BLOCK}",
            cleaned,
            count=1,
        )
    return re.sub(
        r"(        fastcgi_pass\s+[^;]+;)",
        lambda match: f"{match.group(1)}\n{FASTCGI_CACHE_LOCATION_BLOCK}",
        cleaned,
        count=1,
    )


def _undo_write(
    target: Path,
    old_content: Optional[str],
    custom_domain: Optional[str],
    custom_snapshot: Optional[tuple[bool, str]],
) -> None:
    if old_content is not None:
        target.write_text(old_content, encoding="utf-8")
    else:
        target.unlink(missing_ok=True)
    if custom_domain is not None and custom_snapshot is not None:
        _restore_custom_include(custom_domain, custom_snapshot)


# Set by deferred_reload(). None means every write tests and reloads on its own.
_DEFERRED_WRITES: Optional[list] = None


@contextmanager
def deferred_reload():
    """Write many vhosts, then test and reload once at the end.

    One test and reload per vhost is right for a single edit from the panel.
    For a bulk pass it is not: nginx re-parses the entire configuration each
    time, ModSecurity rules included, so the updater rewriting every site on a
    box with CRS enabled and 22 sites meant 22 full parses of 18,503 rules.
    That is what ran a swapless 8 GB server out of memory partway through an
    update and took nginx down with it.

    Safety is unchanged in shape: nothing is left half-applied. If the single
    test at the end fails - or the body raises - every file written inside the
    block is put back as it was before nginx is asked to reload.
    """
    global _DEFERRED_WRITES
    if _DEFERRED_WRITES is not None:
        yield  # already inside one; the outermost block owns the reload
        return
    _DEFERRED_WRITES = []
    try:
        yield
    except BaseException:
        pending, _DEFERRED_WRITES = _DEFERRED_WRITES, None
        for write in reversed(pending):
            _undo_write(*write)
        raise
    pending, _DEFERRED_WRITES = _DEFERRED_WRITES, None
    if not pending:
        return
    test = shell.privileged("nginx-test", check=False, fallback=["nginx", "-t"])
    if test.returncode != 0:
        for write in reversed(pending):
            _undo_write(*write)
        raise RuntimeError((test.stderr or test.stdout or "nginx -t failed").strip())
    shell.privileged("nginx-reload", fallback=["bash", "-lc", "nginx -t && systemctl reload nginx"])


def _test_and_reload(
    target: Path,
    old_content: Optional[str],
    custom_domain: Optional[str] = None,
    custom_snapshot: Optional[tuple[bool, str]] = None,
) -> None:
    if _DEFERRED_WRITES is not None:
        _DEFERRED_WRITES.append((target, old_content, custom_domain, custom_snapshot))
        return
    test = shell.privileged("nginx-test", check=False, fallback=["nginx", "-t"])
    if test.returncode != 0:
        _undo_write(target, old_content, custom_domain, custom_snapshot)
        raise RuntimeError((test.stderr or test.stdout or "nginx -t failed").strip())
    shell.privileged("nginx-reload", fallback=["bash", "-lc", "nginx -t && systemctl reload nginx"])


def render_vhost(
    domain: str,
    root_path: str,
    app_type: str = "wordpress",
    php_version: Optional[str] = None,
    custom_directives: str = "",
    php_fpm_socket_override: Optional[str] = None,
    waf_enabled: bool = True,
    http_flood_enabled: bool = False,
    http_flood_config: dict | str | None = None,
    document_root: str = "public_html",
    rewrite_mode: str | None = None,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
    aliases: list[str] | tuple[str, ...] | None = None,
    redirects: list[str] | tuple[str, ...] | None = None,
    app_port: int | None = None,
    blocked_bots=None,
    bind_address: Optional[str] = None,
) -> str:
    server_names = _server_names(domain, aliases)
    safe_domain = server_names[0]
    _check_app_type(app_type)
    safe_app_port = _check_app_port(app_type, app_port)
    safe_rewrite_mode = _check_rewrite_mode(rewrite_mode)
    _check_php_version(php_version)
    resolved_root = Path(root_path).resolve()
    if not site_users.is_site_root_for_domain(resolved_root, safe_domain):
        raise ValueError("root_path must be the managed root for this domain")
    validate_custom_nginx(custom_directives)
    effective_document_root = _effective_document_root(document_root, safe_rewrite_mode)
    resolved_document_root = site_users.document_root(resolved_root, effective_document_root)
    include_path = custom_include_path(safe_domain)

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=False)
    template_name = {
        "wordpress": "wordpress.conf.j2",
        "php": "php.conf.j2",
        "static": "static.conf.j2",
        "application": "proxy.conf.j2",
    }[app_type]
    template = env.get_template(template_name)
    php_fpm_socket = php_fpm_socket_override or _php_fpm_socket(php_version)
    safe_http_flood_config = validate_http_flood_config(http_flood_config)

    resolved_bind_address = bind_address or get_website_ip(safe_domain)
    rendered = template.render(
        bind_address=resolved_bind_address,
        ipv6=panel_ipv6.is_enabled(),
        domain=safe_domain,
        server_names=server_names,
        root_path=str(resolved_root),
        document_root_path=str(resolved_document_root),
        php_fpm_socket=php_fpm_socket,
        custom_include_path=include_path,
        waf_enabled=bool(waf_enabled),
        waf_rules_file=waf_rules_file(safe_domain),
        http_flood_enabled=bool(http_flood_enabled),
        http_flood_zone=http_flood_zone_name(safe_domain),
        http_flood_burst=safe_http_flood_config["access_limit_burst"],
        http_flood_connections=safe_http_flood_config["connection_limit"],
        http_flood_challenge_block=_http_flood_challenge_block(),
        rewrite_mode=safe_rewrite_mode,
        app_port=safe_app_port,
        proxy_timeout=PROXY_TIMEOUT_SECONDS,
    )
    # Applied to the rendered output rather than through the templates: it is
    # one block, identical for all four app types, and this keeps it in step
    # with update_bot_block() - a full rewrite and a targeted edit then produce
    # exactly the same thing. Done before the redirect vhosts are appended so
    # it lands in the primary server block, which is the one that serves.
    rendered = _replace_bot_block(rendered, blocked_bots)
    if ssl_cert_path or ssl_key_path:
        rendered = apply_manual_ssl_config(rendered, ssl_cert_path or "", ssl_key_path or "", ssl_ca_path)
    return _append_redirect_vhosts(rendered, domain, redirects, ssl_cert_path, ssl_key_path, ssl_ca_path)


# Back-compat shim for older imports.
def render_wordpress_vhost(domain: str, root_path: str, php_version: Optional[str] = None) -> str:
    return render_vhost(domain, root_path, app_type="wordpress", php_version=php_version)



def get_website_ip(domain: str) -> str:
    """Find if a website's owner has a dedicated IP assigned."""
    try:
        from app.core.database import SessionLocal
        from app.models.entities import Website, User, ServerIP
        with SessionLocal() as db:
            website = db.query(Website).filter(Website.domain == domain).first()
            if website and website.owner_id:
                server_ip = db.query(ServerIP).filter(ServerIP.assigned_user_id == website.owner_id).first()
                if server_ip and not server_ip.is_shared:
                    return server_ip.ip_address
    except Exception:
        pass
    return "*"

def write_vhost(
    domain: str,
    root_path: str,
    app_type: str = "wordpress",
    php_version: Optional[str] = None,
    custom_directives: str = "",
    php_fpm_socket_override: Optional[str] = None,
    waf_enabled: bool = True,
    http_flood_enabled: bool = False,
    http_flood_config: dict | str | None = None,
    document_root: str = "public_html",
    rewrite_mode: str | None = None,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
    preserve_existing_ssl: bool = True,
    aliases: list[str] | tuple[str, ...] | None = None,
    redirects: list[str] | tuple[str, ...] | None = None,
    app_port: int | None = None,
    blocked_bots=None,
    bind_address: Optional[str] = None,
) -> str:
    resolved_ip = bind_address or get_website_ip(_safe_domain(domain))
    return rewrite_vhost(
        domain,
        root_path,
        app_type=app_type,
        bind_address=resolved_ip,
        php_version=php_version,
        custom_directives=custom_directives,
        php_fpm_socket_override=php_fpm_socket_override,
        waf_enabled=waf_enabled,
        http_flood_enabled=http_flood_enabled,
        http_flood_config=http_flood_config,
        document_root=document_root,
        rewrite_mode=rewrite_mode,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        ssl_ca_path=ssl_ca_path,
        preserve_existing_ssl=preserve_existing_ssl,
        aliases=aliases,
        redirects=redirects,
        app_port=app_port,
        blocked_bots=blocked_bots,
    )


def write_wordpress_vhost(domain: str, root_path: str, php_version: Optional[str] = None) -> str:
    return write_vhost(domain, root_path, app_type="wordpress", php_version=php_version)


def rewrite_vhost(
    domain: str,
    root_path: str,
    app_type: str,
    php_version: Optional[str],
    custom_directives: str = "",
    php_fpm_socket_override: Optional[str] = None,
    waf_enabled: bool = True,
    http_flood_enabled: bool = False,
    http_flood_config: dict | str | None = None,
    document_root: str = "public_html",
    rewrite_mode: str | None = None,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
    preserve_existing_ssl: bool = True,
    aliases: list[str] | tuple[str, ...] | None = None,
    redirects: list[str] | tuple[str, ...] | None = None,
    app_port: int | None = None,
    blocked_bots=None,
    bind_address: Optional[str] = None,
) -> str:
    target = _vhost_path(domain)
    resolved_ip = bind_address or get_website_ip(_safe_domain(domain))
    if app_type in PROXIED_APP_TYPES:
        ensure_proxy_upgrade_map()
    if blocked_bots is None and not settings.command_dry_run and target.exists():
        try:
            blocked_bots = blocked_bots_in_vhost(target.read_text(encoding="utf-8"))
        except OSError:
            blocked_bots = None
    content = render_vhost(
        domain,
        root_path,
        document_root=document_root,
        app_type=app_type,
        bind_address=resolved_ip,
        php_version=php_version,
        custom_directives=custom_directives,
        php_fpm_socket_override=php_fpm_socket_override,
        waf_enabled=waf_enabled,
        http_flood_enabled=http_flood_enabled,
        http_flood_config=http_flood_config,
        rewrite_mode=rewrite_mode,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        ssl_ca_path=ssl_ca_path,
        aliases=aliases,
        redirects=None,
        app_port=app_port,
        blocked_bots=blocked_bots,
    )
    if settings.command_dry_run:
        return _append_redirect_vhosts(content, domain, redirects, ssl_cert_path, ssl_key_path, ssl_ca_path)
    custom_snapshot = _custom_include_snapshot(domain)
    _write_custom_include(domain, custom_directives)
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        _write_backup(target, existing)
        if preserve_existing_ssl and _has_ssl_config(existing) and not (ssl_cert_path and ssl_key_path):
            content = _merge_certbot_ssl_config(content, existing)
    if preserve_existing_ssl and not (ssl_cert_path and ssl_key_path):
        content = _append_certbot_redirect_vhosts(content, domain, redirects)
    else:
        content = _append_redirect_vhosts(content, domain, redirects, ssl_cert_path, ssl_key_path, ssl_ca_path)
    old_content = target.read_text(encoding="utf-8") if target.exists() else None
    target.write_text(content, encoding="utf-8")
    _test_and_reload(target, old_content, domain, custom_snapshot)
    return str(target)


def rewrite_wordpress_vhost(domain: str, root_path: str, php_version: str) -> str:
    return rewrite_vhost(domain, root_path, app_type="wordpress", php_version=php_version)


def update_custom_block(domain: str, custom_directives: str) -> str:
    target = _vhost_path(domain)
    safe_custom = validate_custom_nginx(custom_directives)
    if settings.command_dry_run:
        return safe_custom
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    positioned = _ensure_custom_include_position(existing, domain)
    custom_snapshot = _custom_include_snapshot(domain)
    _write_custom_include(domain, safe_custom)
    if positioned != existing:
        target.write_text(positioned, encoding="utf-8")
    _test_and_reload(target, existing, domain, custom_snapshot)
    return custom_include_path(domain)


def set_php_version(
    domain: str,
    php_version: str,
    php_fpm_socket_override: Optional[str] = None,
) -> str:
    target = _vhost_path(domain)
    socket_path = php_fpm_socket_override or _php_fpm_socket(php_version)
    _check_php_version(php_version)
    if settings.command_dry_run:
        return socket_path
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    new_content = _replace_fastcgi_socket(existing, socket_path)
    if new_content == existing:
        if f"fastcgi_pass unix:{socket_path};" in existing:
            return str(target)
        raise ValueError("Cannot find a PHP FastCGI socket in the Nginx config")
    _write_backup(target, existing)
    target.write_text(new_content, encoding="utf-8")
    _test_and_reload(target, existing)
    return str(target)


def set_wordpress_php_version(domain: str, php_version: str) -> str:
    return set_php_version(domain, php_version)


def harden_existing_wordpress_vhost(
    domain: str,
    root_path: str,
    php_version: str | None = None,
    custom_directives: str = "",
    php_fpm_socket_override: Optional[str] = None,
    waf_enabled: bool = True,
    http_flood_enabled: bool = False,
    http_flood_config: dict | str | None = None,
    document_root: str = "public_html",
    rewrite_mode: str | None = None,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
    aliases: list[str] | tuple[str, ...] | None = None,
    redirects: list[str] | tuple[str, ...] | None = None,
) -> str:
    return rewrite_vhost(
        domain,
        root_path,
        app_type="wordpress",
        php_version=php_version,
        custom_directives=custom_directives,
        php_fpm_socket_override=php_fpm_socket_override,
        waf_enabled=waf_enabled,
        http_flood_enabled=http_flood_enabled,
        http_flood_config=http_flood_config,
        document_root=document_root,
        rewrite_mode=rewrite_mode,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        ssl_ca_path=ssl_ca_path,
        aliases=aliases,
        redirects=redirects,
    )


def delete_wordpress_vhost(domain: str):
    target = _vhost_path(domain)
    if settings.command_dry_run:
        return str(target)
    target.unlink(missing_ok=True)
    _delete_custom_include(domain)
    shell.privileged("nginx-reload", fallback=["bash", "-lc", "nginx -t && systemctl reload nginx"])
    return str(target)


def read_vhost_config(domain: str) -> str:
    target = _vhost_path(domain)
    if not target.exists():
        raise FileNotFoundError(str(target))
    return target.read_text(encoding="utf-8")


def read_site_log(domain: str, kind: str = "access", lines: int = 200) -> dict:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    safe_kind = _check_log_kind(kind)
    safe_lines = _check_tail_lines(lines)
    path = _log_path(safe_domain, safe_kind)
    result = shell.privileged(
        "site-log-read",
        helper_args=[safe_domain, safe_kind, str(safe_lines)],
        check=False,
        fallback=["tail", "-n", str(safe_lines), str(path)],
    )
    missing = ("SPANEL_LOG_MISSING=1" in (result.stderr or "")) or ("spanel_LOG_MISSING=1" in (result.stderr or ""))
    if result.returncode != 0 and not missing:
        raise RuntimeError((result.stderr or result.stdout or "Cannot read log file").strip())
    return {
        "domain": safe_domain,
        "kind": safe_kind,
        "path": str(path),
        "lines": safe_lines,
        "content": result.stdout or "",
        "exists": not missing,
    }


def clear_site_log(domain: str, kind: str = "access") -> dict:
    safe_domain = _safe_domain(domain)
    ip_address = get_website_ip(safe_domain)
    safe_kind = _check_log_kind(kind)
    path = _log_path(safe_domain, safe_kind)
    result = shell.privileged(
        "site-log-clear",
        helper_args=[safe_domain, safe_kind],
        check=False,
        fallback=["bash", "-lc", 'test -f "$1" && : >"$1" || true', "spanel-clear-log", str(path)],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Cannot clear log file").strip())
    return {
        "domain": safe_domain,
        "kind": safe_kind,
        "path": str(path),
        "cleared": True,
    }


def update_full_config(domain: str, content: str) -> str:
    target = _vhost_path(domain)
    safe_content = validate_full_nginx_config(content)
    if settings.command_dry_run:
        return safe_content
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    _write_backup(target, existing)
    target.write_text(safe_content, encoding="utf-8")
    _test_and_reload(target, existing)
    return str(target)


def update_waf_block(domain: str, enabled: bool) -> str:
    target = _vhost_path(domain)
    if settings.command_dry_run:
        return _replace_waf_block("server {\n    server_name example.com;\n}\n", enabled, domain="example.com")
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    new_content = _replace_waf_block(existing, enabled, domain)
    _write_backup(target, existing)
    target.write_text(new_content, encoding="utf-8")
    _test_and_reload(target, existing)
    return str(target)


def update_http_flood_block(domain: str, enabled: bool, config: dict | str | None = None) -> str:
    target = _vhost_path(domain)
    safe_config = validate_http_flood_config(config)
    if settings.command_dry_run:
        return _replace_http_flood_block("server {\n    server_name example.com;\n}\n", enabled, domain="example.com", config=safe_config)
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    new_content = _replace_http_flood_block(existing, enabled, domain, safe_config)
    _write_backup(target, existing)
    target.write_text(new_content, encoding="utf-8")
    _test_and_reload(target, existing)
    return str(target)


def ensure_wordpress_fastcgi_cache(domain: str) -> str:
    target = _vhost_path(domain)
    if settings.command_dry_run:
        return _replace_fastcgi_cache_blocks(
            "server {\n    server_name example.com;\n    client_max_body_size 1100M;\n    location ~ \\.php$ {\n        fastcgi_pass unix:/run/php/php8.4-fpm.sock;\n        fastcgi_read_timeout 300;\n    }\n}\n",
            True,
        )
    if not target.exists():
        raise FileNotFoundError(str(target))
    existing = target.read_text(encoding="utf-8")
    new_content = _replace_fastcgi_cache_blocks(existing, True)
    if new_content == existing:
        return str(target)
    _write_backup(target, existing)
    target.write_text(new_content, encoding="utf-8")
    _test_and_reload(target, existing)
    return str(target)
