"""The connection limiter was turning away paying visitors.

Measured on a live server: two WooCommerce shops, 113 and 413 requests
refused in one day. Every one was a .js or .css, and 432 of the 526 carried a
gclid - somebody who had just clicked a paid advert. They got the shop with
its stylesheet missing.

Nothing was attacking. nginx counts each HTTP/2 stream as a connection, and a
WordPress page with two hundred plugin assets opens more concurrent streams
than any sane connection limit. One visitor, one page view, reads as a flood.

The fix uses the mechanism the challenge cookie already relies on: nginx does
not count a request whose limit key is empty. Static files now get an empty
key. Nothing is lost - nginx serves them from disk without touching PHP or the
database, which is what the limiter exists to protect.
"""

import re

from app.services import nginx


def _zones():
    return nginx.render_http_flood_zones([])


def _key_for(uri: str, challenge_cookie: str = "") -> str:
    """What nginx would resolve $spanel_http_flood_key to.

    A small interpreter for the two maps, because the thing worth testing is
    the outcome for a given request, not the text of the configuration.
    """
    config = _zones()

    static = ""
    for line in config.splitlines():
        line = line.strip()
        if line.startswith("~*[.]"):
            pattern, _, value = line.rstrip(";").partition(" ")
            if re.search(pattern[2:], uri, re.IGNORECASE):
                static = value.strip()
                break

    combined = f"{static}:{challenge_cookie}"
    for line in config.splitlines():
        line = line.strip().rstrip(";")
        if line.startswith('"~'):
            pattern, _, value = line.partition('" ')
            if re.search(pattern.strip('"')[1:], combined):
                return value.strip().strip('"')
    return "$binary_remote_addr"


# --- what stops being counted ------------------------------------------------

def test_the_assets_that_were_being_refused_are_no_longer_counted():
    """The exact requests out of the live error log."""
    for uri in (
        "/wp-content/plugins/yith-woocommerce-wishlist/assets/js/jquery.selectBox.min.js",
        "/wp-content/plugins/woocommerce/assets/js/prettyPhoto/jquery.prettyPhoto.min.js",
        "/wp-content/plugins/woocommerce-products-filter/ext/slideout/js/slideout.js",
    ):
        assert _key_for(uri) == "", uri


def test_every_kind_of_asset_a_theme_loads():
    for uri in ("/style.css", "/app.js", "/module.mjs", "/app.js.map",
                "/logo.png", "/hero.JPG", "/photo.jpeg", "/icon.svg",
                "/anim.gif", "/next.webp", "/new.avif", "/favicon.ico",
                "/font.woff", "/font.woff2", "/font.ttf", "/font.otf", "/font.eot"):
        assert _key_for(uri) == "", uri


def test_the_challenge_cookie_still_exempts_whoever_passed_it():
    """The behaviour that was already there must survive the change."""
    assert _key_for("/checkout/", challenge_cookie="1") == ""


# --- what is still counted ---------------------------------------------------

def test_the_requests_worth_limiting_are_still_limited():
    """PHP is what the limiter exists for: it costs a worker and a database
    connection, and a flood of it is what takes a shop down."""
    for uri in ("/", "/checkout/", "/wp-login.php", "/xmlrpc.php",
                "/wp-admin/admin-ajax.php", "/?s=something"):
        assert _key_for(uri) == "$binary_remote_addr", uri


def test_an_extension_in_the_middle_of_a_path_does_not_exempt_it():
    """Anchored at the end, or /wp-login.php?x=.css would walk straight through."""
    assert _key_for("/evil.css/wp-login.php") == "$binary_remote_addr"
    assert _key_for("/wp-content/js/../../wp-login.php") == "$binary_remote_addr"


def test_a_php_file_named_like_an_asset_is_still_php():
    assert _key_for("/style.css.php") == "$binary_remote_addr"


# --- the configuration itself ------------------------------------------------

def test_both_limiters_read_the_same_key():
    """limit_req and limit_conn share $spanel_http_flood_key, so the exemption
    has to reach both - and it does, because there is only one key."""
    config = _zones()
    assert "limit_conn_zone $spanel_http_flood_key" in config

    class _Site:
        domain = "shop.test"
        http_flood_enabled = True
        http_flood_config = ""

    with_site = nginx.render_http_flood_zones([_Site()])
    assert "limit_req_zone $spanel_http_flood_key" in with_site


def test_the_key_is_built_from_the_normalised_path():
    """$uri, not $request_uri: the query string is not part of the file name,
    and ?x=.css must not make a page look like a stylesheet."""
    config = _zones()
    assert "map $uri $spanel_http_flood_static" in config
    assert "$request_uri" not in config
