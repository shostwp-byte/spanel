"""Per-site HTTP flood protection.

The limit sits at server level, so it governs every image and stylesheet as
well as the page itself. That makes the difference between rejecting excess
and queueing it the difference between a working feature and a tax on every
visitor, which is what these tests exist to hold down.
"""

import re

import pytest

from app.services import nginx


class FakeSite:
    def __init__(self, domain, enabled=True, config=""):
        self.domain = domain
        self.http_flood_enabled = enabled
        self.http_flood_config = config


def test_excess_is_rejected_rather_than_queued():
    """Measured on a live site: 30 assets took 0.39s unlimited and 2.97s
    limited, because nginx queued the burst instead of serving it. The 429
    challenge never fired either - nothing was ever rejected for it to catch.
    """
    block = nginx._http_flood_block("example.test", nginx.validate_http_flood_config(None))

    assert "nodelay" in block
    # Both spellings must carry it; burst=0 drops the burst parameter.
    no_burst = nginx._http_flood_block("example.test", {"access_limit_burst": 0})
    assert "nodelay" in no_burst
    assert "burst=" not in no_burst


@pytest.mark.parametrize("app_type", ["wordpress", "php", "static"])
def test_every_rendered_vhost_carries_nodelay_too(app_type):
    """There are two copies of this directive, not one.

    _http_flood_block builds the block injected into a vhost that already
    exists; the Jinja templates build it for a vhost being rendered fresh.
    Fixing only the first left every rewritten vhost queueing again, which is
    exactly what happened on a live server: the test above passed while the
    site went back to serving assets at the throttled rate.
    """
    content = nginx.render_vhost(
        "example.test",
        "/home/client/example.test",
        app_type=app_type,
        php_version="8.3",
        http_flood_enabled=True,
    )

    match = re.search(r"limit_req zone=[^;]+;", content)
    assert match, f"{app_type} renders no limit_req"
    assert "nodelay" in match.group(0), f"{app_type} queues instead of rejecting"


def test_a_browser_can_earn_its_way_out_but_a_bot_cannot():
    """429 serves a page whose script sets the cookie the map exempts. A client
    that does not run JavaScript never gets the cookie and stays limited.
    """
    block = nginx._http_flood_block("example.test")

    assert "error_page 429" in block
    assert "spanel_http_flood_ok" in block
    zones = nginx.render_http_flood_zones([FakeSite("example.test")])
    assert "$cookie_spanel_http_flood_ok" in zones
    # Cookie present -> empty key -> no limit applies. The key is now built
    # from two things - whether the path is a static file, and whether the
    # cookie is set - so the cookie's branch matches the end of the pair
    # rather than standing alone.
    assert '"~:1$" "";' in zones


def test_zones_exist_only_for_sites_that_asked_for_them():
    zones = nginx.render_http_flood_zones(
        [FakeSite("on.test", enabled=True), FakeSite("off.test", enabled=False)]
    )

    assert nginx.http_flood_zone_name("on.test") in zones
    assert nginx.http_flood_zone_name("off.test") not in zones


def test_the_defaults_leave_an_ordinary_page_load_alone():
    """A page pulls its assets in one burst. The default burst has to be big
    enough to swallow that, or every first visit hits the challenge.
    """
    config = nginx.validate_http_flood_config(None)

    assert config["access_limit_burst"] >= 100
    # And the sustained rate must still be well above a human's browsing.
    rate = nginx._http_flood_rate(config)
    assert rate.endswith("r/s")
    assert int(rate.removesuffix("r/s")) >= 10


def test_a_pasted_config_cannot_disable_the_protection():
    config = nginx.validate_http_flood_config(
        {"access_limit_requests": 0, "access_limit_window": 0, "connection_limit": 0}
    )

    assert config["access_limit_requests"] >= 1
    assert config["access_limit_window"] >= 1
    assert config["connection_limit"] >= 1
