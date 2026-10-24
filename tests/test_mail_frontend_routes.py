"""Every /mail and /dns path the pages call is a route the API has.

The Email page saved a catch-all through PUT /mail/domains/{id}/catch-all and
a domain's relay with {"relay_id": n}; neither existed, so both did nothing.
"""
import re

from mail_fixtures import REPO_ROOT

PAGES = ["Mail.jsx", "MailAutoresponders.jsx", "Dns.jsx"]
CALL_RE = re.compile(r"request\(\s*[`'](/(?:mail|dns)[^`'?]*)")


def _route_patterns():
    from app.api import dns as dns_api, mail as mail_api

    patterns = []
    for route in list(mail_api.router.routes) + list(dns_api.router.routes):
        regex = re.sub(r"\{[^}]+\}", "[^/]+", route.path)
        patterns.append((re.compile(f"^{regex}$"), sorted(route.methods)))
    return patterns


def test_pages_call_existing_routes():
    patterns = _route_patterns()
    missing = []
    for page in PAGES:
        text = (REPO_ROOT / "frontend" / "src" / "pages" / page).read_text(encoding="utf-8")
        for path in CALL_RE.findall(text):
            # ${...} pieces are ids and zone names; dnsPath() builds /dns/zones/<zone>.
            concrete = re.sub(r"\$\{[^}]*\}", "x", path).rstrip("/") or path
            if not any(regex.match(concrete) for regex, _ in patterns):
                missing.append(f"{page}: {path}")
    assert missing == []


def test_relay_picker_sends_the_field_the_api_reads():
    text = (REPO_ROOT / "frontend" / "src" / "pages" / "Mail.jsx").read_text(encoding="utf-8")
    assert "JSON.stringify({ relay: relayId || '' })" in text
    assert "relay_id" not in text
