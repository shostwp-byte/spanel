"""Listing the accounts must not wait on measuring them.

Disk usage for one account means walking its home directory. The list endpoint
asked for it per row, so a cold cache made the whole page wait on the slowest
account. Measured on a live panel with fifteen users: 3.79 s cold, 0.00 s warm,
and the cache is in-process with a five-minute TTL - so every panel restart and
every quiet five minutes paid it again.

The list now reports what the cache already knows and -1 for the rest. The page
draws the rows, then asks /users/storage-usage for the missing figures.
"""

import time

import pytest

from app.services import storage_quota


@pytest.fixture(autouse=True)
def empty_cache():
    storage_quota._user_usage_cache.clear()
    yield
    storage_quota._user_usage_cache.clear()


# --- the cache-only reader --------------------------------------------------

def test_an_unmeasured_account_reads_as_unknown_not_zero():
    """Zero is an answer. "I have not looked" is not the same answer."""
    assert storage_quota.cached_storage_used_bytes(4242) is None


def test_a_cached_figure_is_returned():
    storage_quota._user_usage_cache[7] = (time.time(), 1024)
    assert storage_quota.cached_storage_used_bytes(7) == 1024


def test_a_stale_figure_is_not_returned():
    old = time.time() - storage_quota.USER_USAGE_TTL_SECONDS - 1
    storage_quota._user_usage_cache[7] = (old, 1024)
    assert storage_quota.cached_storage_used_bytes(7) is None


def test_the_reader_never_walks_the_disk(monkeypatch):
    """The whole point: no filesystem call on this path."""
    def _explode(*args, **kwargs):  # pragma: no cover - reaching it is the failure
        raise AssertionError("the list endpoint measured the disk")

    monkeypatch.setattr(storage_quota, "user_storage_used_bytes", _explode)
    assert storage_quota.cached_storage_used_bytes(1) is None
    storage_quota._user_usage_cache[1] = (time.time(), 99)
    assert storage_quota.cached_storage_used_bytes(1) == 99


def test_an_id_that_arrives_as_a_string_still_matches():
    storage_quota._user_usage_cache[3] = (time.time(), 5)
    assert storage_quota.cached_storage_used_bytes("3") == 5


# --- and the endpoints are wired to it --------------------------------------

def test_the_list_endpoint_uses_the_cache_only_reader():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "api" / "users.py").read_text(encoding="utf-8")
    body = source[source.index("def list_users"):source.index("def storage_usage")]
    assert "cached_storage_used_bytes" in body
    assert "storage_usage_summary" not in body, (
        "that one measures, and measuring is what the list stopped doing"
    )
    assert 'data["storage_used_bytes"] = -1' in body


def test_the_figures_have_somewhere_to_come_from():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "api" / "users.py").read_text(encoding="utf-8")
    assert '@router.get("/storage-usage")' in source
    body = source[source.index("def storage_usage"):]
    assert "storage_usage_summary" in body, "this is the one allowed to walk"


def test_the_page_asks_for_them_only_when_some_are_missing():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "loadUserStorageUsage" in page
    guarded = [
        line for line in page.splitlines()
        if "loadUserStorageUsage()" in line and "storage_used_bytes" in line and "< 0" in line
    ]
    assert guarded, "a warm cache should cost no second request"


def test_a_row_being_measured_says_so_rather_than_showing_zero():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    body = page[page.index("function storageUsageText"):]
    body = body[:body.index("\n  }")]
    assert "< 0) return" in body, "-1 must not fall through to the byte formatter"
