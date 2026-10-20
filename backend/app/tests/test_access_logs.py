from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import waf

HELPER = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


@pytest.fixture(autouse=True)
def _clear_access_log_cache():
    def _reset():
        waf._ACCESS_LOG_CACHE.clear()
        waf._dbip_country_ranges.cache_clear()
        waf._DBIP_WARM_STATE.update(started=False, ready=False)

    _reset()
    yield
    _reset()


def test_the_batch_log_reader_splits_per_domain(monkeypatch):
    monkeypatch.setattr(waf, "_batch_helper_available", lambda: True)
    blob = "\x1fa.com\nline a1\nline a2\n\x1fb.com\nspanel_LOG_MISSING\n\x1fc.com\nline c1\n"
    monkeypatch.setattr(
        waf.shell, "privileged",
        lambda *a, **k: SimpleNamespace(stdout=blob, stderr="", returncode=0),
    )
    out = waf._read_site_logs(["a.com", "b.com", "c.com"], 400)
    assert out["a.com"].splitlines() == ["line a1", "line a2"]
    assert out["b.com"] is None
    assert out["c.com"] == "line c1\n"


def test_an_unfiltered_view_only_tails_a_shallow_window(monkeypatch):
    monkeypatch.setattr(waf, "_batch_helper_available", lambda: True)
    seen = {}

    def fake(*a, **k):
        seen["lines"] = int(k["helper_args"][1])
        return SimpleNamespace(stdout="\x1fx.com\n", stderr="", returncode=0)

    monkeypatch.setattr(waf.shell, "privileged", fake)
    waf.access_logs([SimpleNamespace(domain="x.com")], verdict="all", query="", limit=50, lines=5000)
    assert seen["lines"] <= 400
    waf.access_logs([SimpleNamespace(domain="x.com")], verdict="block", query="", limit=50, lines=5000)
    assert seen["lines"] == 5000


def test_a_garbage_request_line_parses_fast(monkeypatch):
    import time

    # A TLS ClientHello sent to :80, plus a fuzzer line with many backslashes -
    # the shapes that used to make ACCESS_LOG_RE backtrack for seconds.
    garbage = "\n".join([
        r'1.2.3.4 - - [27/Jul/2026:12:00:00 +0700] "\x16\x03\x01\x00\xa5\x01\x00\x00\xa1" 400 0 "-" "-" 0.001',
        r'2.3.4.5 - - [27/Jul/2026:12:00:01 +0700] "GET /' + "a\\" * 400 + r' HTTP/1.1" 404 0 "-" "-" 0.001',
    ] * 50)
    monkeypatch.setattr(waf, "_batch_helper_available", lambda: True)
    monkeypatch.setattr(
        waf.shell, "privileged",
        lambda *a, **k: SimpleNamespace(stdout="\x1fx.com\n" + garbage, stderr="", returncode=0),
    )
    t = time.time()
    waf.access_logs([SimpleNamespace(domain="x.com")], verdict="block", query="", limit=50, lines=5000)
    assert time.time() - t < 2.0


def test_helper_has_the_batch_log_verb():
    helper = HELPER.read_text(encoding="utf-8")
    assert "site-logs-read-many)" in helper
    assert "read_site_logs_many()" in helper
    assert "/var/log/nginx/" in helper


def test_access_logs_parse_and_filter(monkeypatch):
    site = SimpleNamespace(domain="example.com")
    sample = "\n".join([
        '1.2.3.4 - - [27/Jul/2026:12:00:00 +0700] "GET /wp-config.php HTTP/1.1" 403 153 "-" "curl/8.0" 0.012',
        '5.6.7.8 - - [27/Jul/2026:12:00:01 +0700] "POST /index.php HTTP/1.1" 200 321 "-" "Mozilla/5.0" 0.200',
        '9.8.7.6 - - [27/Jul/2026:12:00:02 +0700] "GET /admin HTTP/1.1" 500 10 "-" "Mozilla/5.0" 0.050',
    ])

    monkeypatch.setattr(waf.nginx, "read_site_log", lambda domain, kind, lines: {"exists": True, "content": sample})

    result = waf.access_logs([site], verdict="block", query="wp-config", limit=50, lines=5000)

    assert result["total"] == 1
    assert result["scanned"] == 3
    assert result["items"][0]["domain"] == "example.com"
    assert result["items"][0]["verdict"] == "block"
    assert result["items"][0]["reason"] == "Block WordPress config probe"


def test_access_logs_include_country_and_filter_by_country(monkeypatch):
    site = SimpleNamespace(domain="example.com")
    sample = '8.8.8.8 - - [27/Jul/2026:12:00:00 +0700] "GET / HTTP/1.1" 200 153 "-" "curl/8.0" 0.012'

    monkeypatch.setattr(waf.nginx, "read_site_log", lambda domain, kind, lines: {"exists": True, "content": sample})
    monkeypatch.setattr(waf, "_lookup_ip_country", lambda ip: {"country": "United States", "country_code": "US"})

    result = waf.access_logs([site], verdict="all", query="United States", limit=50, lines=5000)

    assert result["total"] == 1
    assert result["items"][0]["country"] == "United States"
    assert result["items"][0]["country_code"] == "US"


def test_dbip_country_lookup_uses_cached_csv(monkeypatch, tmp_path):
    csv_path = tmp_path / "dbip-country-lite-2026-07.csv"
    csv_path.write_text("8.8.8.0,8.8.8.255,US\n1.1.1.0,1.1.1.255,AU\n", encoding="utf-8")

    monkeypatch.setattr(waf, "_ensure_dbip_country_cache", lambda: csv_path)
    waf._dbip_country_ranges.cache_clear()
    waf._warm_dbip_ranges_blocking()

    try:
        result = waf._lookup_ip_country("8.8.8.8")
    finally:
        waf._dbip_country_ranges.cache_clear()

    assert result == {"country": "United States", "country_code": "US"}


def test_clear_access_logs_calls_each_site(monkeypatch):
    cleared = []
    monkeypatch.setattr(waf.nginx, "clear_site_log", lambda domain, kind: cleared.append((domain, kind)))

    count = waf.clear_access_logs([
        SimpleNamespace(domain="example.com"),
        SimpleNamespace(domain="example.net"),
    ])

    assert count == 2
    assert cleared == [("example.com", "access"), ("example.net", "access")]
