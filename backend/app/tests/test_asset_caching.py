"""Browser caching and the page cache: the two settings that decided site speed.

Both were shipped in a state that did the opposite of their purpose - assets
marked no-cache, and a page cache that almost never held a page - so these pin
the values and, more importantly, the reasoning that makes them safe.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import nginx

TEMPLATES = Path(nginx.__file__).resolve().parents[1] / "templates" / "nginx"
SITE_TEMPLATES = ("wordpress.conf.j2", "php.conf.j2", "static.conf.j2")


def _static_location(name: str) -> str:
    """The directives of the static-asset location, comments stripped.

    The comments explain the `expires -1` this replaced, so leaving them in
    would make the test fail on its own explanation.
    """
    body = (TEMPLATES / name).read_text(encoding="utf-8")
    match = re.search(
        r"location ~\* \\\.\(jpg\|jpeg.*?\{(.*?)\n    \}", body, re.S
    )
    assert match, f"{name} has no static-asset location"
    return "\n".join(
        line for line in match.group(1).splitlines() if not line.strip().startswith("#")
    )


@pytest.mark.parametrize("name", SITE_TEMPLATES)
def test_static_assets_are_cacheable_by_the_browser(name):
    """`expires -1` is not "no expiry set" - nginx renders it as an Expires
    date in the past plus Cache-Control: no-cache, so every image, stylesheet
    and font was refetched on every page view. Measured on a live site, a
    10-asset page spent 0.55s on round trips that a real lifetime skips, and
    that was over loopback; over a phone connection each one costs far more.
    """
    block = _static_location(name)

    assert "expires -1" not in block, "this marks every asset uncacheable"
    match = re.search(r"expires\s+(\d+)([smhdMy]);", block)
    assert match, f"{name} sets no asset lifetime"
    assert int(match.group(1)) > 0


def test_lifetimes_reflect_how_likely_a_file_is_edited_in_place():
    """WordPress fingerprints CSS/JS with ?ver= and renames uploads, so a long
    lifetime cannot serve a stale file. A hand-written static site is the case
    where someone edits style.css under the same name, so it gets the shortest.
    """
    def days(name):
        m = re.search(r"expires\s+(\d+)([smhdMy]);", _static_location(name))
        return int(m.group(1)) * {"d": 1, "h": 1 / 24, "M": 30, "y": 365}[m.group(2)]

    assert days("wordpress.conf.j2") >= days("php.conf.j2") >= days("static.conf.j2")


def test_the_page_cache_holds_a_page_long_enough_to_be_used():
    """15s with min_uses 2 meant a page had to be fetched twice inside the same
    15 seconds to be cached at all, so below a few requests per second nothing
    ever was. The same page measured 1.33s uncached and 0.016s cached.
    """
    block = nginx.FASTCGI_CACHE_LOCATION_BLOCK

    assert "fastcgi_cache_min_uses 1;" in block, "a page must cache on first use"
    match = re.search(r"fastcgi_cache_valid 200[^;]*?(\d+)([sm]);", block)
    assert match, "no 200 lifetime configured"
    seconds = int(match.group(1)) * (60 if match.group(2) == "m" else 1)
    assert seconds >= 60, "a lifetime in seconds expires before the next visitor"


def test_a_long_page_cache_is_safe_because_authors_never_see_it():
    """Raising the lifetime is only defensible while everyone who can change a
    page is excluded from the cache: they must see their edit immediately.
    """
    block = nginx.FASTCGI_CACHE_SERVER_BLOCK

    for cookie in ("wordpress_logged_in", "woocommerce_cart_hash", "comment_author"):
        assert cookie in block
    assert "$request_method = POST" in block
    assert '$query_string != ""' in block
    assert "/wp-admin/" in block
    # And a response that sets a cookie is never stored, whatever the path.
    assert "fastcgi_no_cache $upstream_http_set_cookie;" in nginx.FASTCGI_CACHE_LOCATION_BLOCK


def test_a_slow_backend_serves_the_last_good_page_instead_of_an_error():
    block = nginx.FASTCGI_CACHE_LOCATION_BLOCK

    assert "fastcgi_cache_use_stale" in block
    assert "updating" in block, "one refresh must not stall every other visitor"
    assert "fastcgi_cache_background_update on;" in block


def test_use_stale_only_names_conditions_nginx_accepts():
    """`http_502` and `http_504` look like they belong here - they are valid
    for proxy_next_upstream - but fastcgi_cache_use_stale rejects them, and
    nginx refuses to load the whole config over it. Shipping that would have
    broken every vhost rewrite on every server; the string assertions above all
    passed while it was wrong, so this checks the values themselves.
    """
    allowed = {
        "error", "timeout", "invalid_header", "updating",
        "http_500", "http_503", "http_403", "http_404", "http_429", "off",
    }
    match = re.search(r"fastcgi_cache_use_stale ([^;]+);", nginx.FASTCGI_CACHE_LOCATION_BLOCK)
    assert match, "no use_stale directive"

    bad = set(match.group(1).split()) - allowed
    assert not bad, f"nginx will refuse to start on: {sorted(bad)}"


def test_the_template_and_the_injected_block_cannot_drift():
    """One copy renders new vhosts, the other is injected into vhosts that
    already exist. A site must not behave differently for having been created
    before the change.
    """
    template = (TEMPLATES / "wordpress.conf.j2").read_text(encoding="utf-8")

    for marker, constant in (
        ("FASTCGI CACHE LOCATION", nginx.FASTCGI_CACHE_LOCATION_BLOCK),
        ("FASTCGI CACHE SERVER", nginx.FASTCGI_CACHE_SERVER_BLOCK),
    ):
        in_template = re.search(
            rf"( *# spanel {marker} BEGIN\n.*?# spanel {marker} END)", template, re.S
        )
        assert in_template, f"{marker} block missing from the template"
        assert in_template.group(1) == constant, f"{marker} drifted from nginx.py"


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx not installed")
def test_nginx_itself_accepts_the_cache_directives(tmp_path):
    """The one check the string assertions cannot make.

    `fastcgi_cache_use_stale http_502` passed every test above and still made
    nginx refuse to load the config, which on a real server means no vhost can
    be rewritten at all. Ask nginx instead of guessing: build the smallest
    config that contains these blocks and run `nginx -t` over it.
    """
    (tmp_path / "logs").mkdir()
    conf = tmp_path / "nginx.conf"
    static = _static_location("wordpress.conf.j2")
    conf.write_text(
        "pid logs/nginx.pid;\n"
        "error_log logs/error.log;\n"
        "events {}\n"
        "http {\n"
        "    access_log off;\n"
        f"    fastcgi_cache_path {tmp_path / 'cache'} levels=1:2 "
        "keys_zone=spanel_FASTCGI:1m inactive=2h max_size=16m use_temp_path=off;\n"
        # Defined alongside the zone by the installer; without it nginx warns
        # and the test output stops being a clean signal.
        '    fastcgi_cache_key "$scheme$request_method$host$request_uri";\n'
        "    server {\n"
        "        listen 8080;\n"
        "        server_name example.test;\n"
        f"{nginx.FASTCGI_CACHE_SERVER_BLOCK}\n"
        "        location ~ \.php$ {\n"
        "            fastcgi_pass unix:/run/php/nothing.sock;\n"
        f"{nginx.FASTCGI_CACHE_LOCATION_BLOCK}\n"
        "        }\n"
        "        location ~* \.(jpg|jpeg|gif|png|css|js|ico|webp|svg|woff|woff2|ttf|eot)$ {\n"
        f"{static}\n"
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["nginx", "-t", "-p", str(tmp_path), "-c", str(conf)],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, (result.stderr or result.stdout).strip()
