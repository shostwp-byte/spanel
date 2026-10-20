"""An end user has to be able to see whether their own site loads CRS.

The WAF page's website list read the CRS state from GET /waf/crs, which is
admin-only. An end user's request was refused, the frontend swallowed the
refusal (it asks silently), and the badge fell through to its default: every
site rendered "CRS off".

On the server this was reported from, twenty of twenty-two sites had CRS on.
The panel told every customer the opposite, and told them so consistently
enough that it looked like a setting rather than a bug.

CRS is also the one WAF feature with a memory bill - roughly 50 MB of nginx per
site that loads it - so "which of my sites carry it" is a question the owner has
a real reason to ask.
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = PROJECT_ROOT / "backend" / "app" / "schemas" / "schemas.py"
WEBSITES_API = PROJECT_ROOT / "backend" / "app" / "api" / "websites.py"
WAF_API = PROJECT_ROOT / "backend" / "app" / "api" / "waf.py"
APP_JSX = PROJECT_ROOT / "frontend" / "src" / "App.jsx"


def test_the_website_payload_carries_its_own_crs_state():
    """So the badge does not depend on an endpoint the owner may not call."""
    src = SCHEMAS.read_text(encoding="utf-8")
    block = src.split("class WebsiteOut(BaseModel):", 1)[1].split("\nclass ", 1)[0]
    assert "crs_enabled:" in block, "an owner cannot see whether their site opted in"
    assert "crs_active:" in block, (
        "opted in and actually enforcing are different states; the badge "
        "distinguishes them and needs both"
    )


def test_the_list_fills_in_whether_crs_is_actually_enforcing():
    src = WEBSITES_API.read_text(encoding="utf-8")
    body = src.split("def _sync_live_ssl_flags(", 1)[1].split("\ndef ", 1)[0]
    assert "crs_active" in body
    assert "active_crs_mode()" in body, (
        "a site opted into CRS while the server-wide switch is off is not "
        "protected, and the badge must not claim it is"
    )


def test_the_admin_only_endpoint_is_still_admin_only():
    """The fix is to move the per-site fact, not to widen the server-wide one.

    /waf/crs reports the whole fleet, the server's memory estimate and the
    global mode. None of that belongs to one customer.
    """
    src = WAF_API.read_text(encoding="utf-8")
    body = src.split('@router.get("/crs")', 1)[1].split("\n@router", 1)[0]
    assert "_require_admin(current_user)" in body


def test_the_list_leaves_crs_to_the_site_page():
    """"WAF on" beside "CRS block" in the list confused customers (operator,
    2026-09-25). The list shows the WAF alone; CRS lives on each site's page.
    """
    src = APP_JSX.read_text(encoding="utf-8")
    row = src.split('className="waf-overview-row"', 1)[1].split("</div>;", 1)[0]
    assert "site.waf_enabled ? 'badge ok' : 'badge'" in row
    assert "crs" not in row.lower(), "CRS is back in the WAF list"


def test_the_site_page_shows_and_switches_crs_for_its_owner():
    """The state reads the site's own config, never /waf/crs, whose refusal an
    end user would see as "off"; and the switch is open to whoever may manage
    the site's WAF, as the WAF switch is.
    """
    src = APP_JSX.read_text(encoding="utf-8")
    page = src.split("function renderWafSite()", 1)[1].split("\n  function ", 1)[0]
    assert "toggleSiteCrs(" in page
    assert "wafSiteConfig?.crs_active" in page
    assert "crs?.websites" not in page

    api = WAF_API.read_text(encoding="utf-8")
    body = api.split('@router.put("/websites/{website_id}/crs")', 1)[1].split("\n@router", 1)[0]
    assert "_owned_website(db, website_id, current_user)" in body
    assert "_require_admin" not in body
