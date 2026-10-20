"""Scanning an upload is opt-in, and off is the default.

The scan costs whatever the scanner costs. With a resident clamd that is a
socket round trip. With the one-shot clamscan the panel installs by default it
is a full signature load per file: 28.2 s and 1.05 GB for a 20 MB upload,
measured on a live server. That is not a price every upload should pay by
default, and the scheduled maldet scan covers the files either way.
"""

import io
from pathlib import Path

import pytest

from app.services import file_manager, malware_queue

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND = PROJECT_ROOT / "frontend" / "src" / "App.jsx"


@pytest.fixture(autouse=True)
def _malware_addon(monkeypatch, tmp_path):
    """The Malware Scanner addon is installed unless a test says otherwise."""
    from app.services import addons

    monkeypatch.setattr(addons, "ADDONS_DIR", tmp_path)
    monkeypatch.setattr(addons, "ADDONS_FILE", tmp_path / "addons.json")
    addons.install(addons.MALWARE)


def test_off_while_the_addon_is_not_installed(monkeypatch):
    from app.services import addons

    addons.uninstall(addons.MALWARE)
    _panel_settings(monkeypatch, lambda: {malware_queue.SETTING_KEY: True})
    assert malware_queue.scan_on_upload_enabled() is False


def _panel_settings(monkeypatch, raw):
    """Stand a settings module in for the one the queue imports at call time.

    Both readers are provided. The queue reads leniently - an unreadable
    settings file must not decide that scanning is on - and a stand-in missing
    that name would send every case down the queue's own except branch, where
    they would all answer False and the True cases would be the only ones that
    noticed.
    """
    import sys
    import types

    import app.services as services

    module = types.ModuleType("app.services.panel_settings")
    module._read_raw = raw
    module._read_raw_lenient = raw
    monkeypatch.setitem(sys.modules, "app.services.panel_settings", module)
    monkeypatch.setattr(services, "panel_settings", module, raising=False)


# --- the default ------------------------------------------------------------

def test_off_when_the_setting_has_never_been_written(monkeypatch):
    """A panel that has never seen the switch must not scan on upload."""
    _panel_settings(monkeypatch, lambda: {})
    assert malware_queue.scan_on_upload_enabled() is False


def test_off_when_the_settings_file_cannot_be_read(monkeypatch):
    def _explode():
        raise OSError("settings file is gone")

    _panel_settings(monkeypatch, _explode)
    assert malware_queue.scan_on_upload_enabled() is False


@pytest.mark.parametrize("stored,expected", [(True, True), (False, False), (None, False), ("yes", True)])
def test_the_stored_value_decides(monkeypatch, stored, expected):
    raw = {} if stored is None else {malware_queue.SETTING_KEY: stored}
    _panel_settings(monkeypatch, lambda: dict(raw))
    assert malware_queue.scan_on_upload_enabled() is expected


# --- what upload_file does with it -----------------------------------------

def _website(tmp_path):
    root = tmp_path / "site"
    (root / "public_html").mkdir(parents=True)

    class _W:
        id = 1
        domain = "example.com"
        root_path = str(root)
        linux_user = None

    return _W()


def test_an_upload_is_not_handed_to_the_scanner_by_default(monkeypatch, tmp_path):
    website = _website(tmp_path)
    monkeypatch.setattr(malware_queue, "scan_on_upload_enabled", lambda: False)
    queued = []
    monkeypatch.setattr(malware_queue, "enqueue", lambda *a, **k: queued.append(a))

    file_manager.upload_file(website, "public_html", "a.txt", io.BytesIO(b"hello"))

    assert queued == [], "the default must not make every upload pay for a scan"
    assert (tmp_path / "site" / "public_html" / "a.txt").read_bytes() == b"hello"


def test_turning_it_on_hands_the_installed_file_over(monkeypatch, tmp_path):
    website = _website(tmp_path)
    monkeypatch.setattr(malware_queue, "scan_on_upload_enabled", lambda: True)
    queued = []
    monkeypatch.setattr(malware_queue, "enqueue", lambda *a, **k: queued.append(a))

    stored = file_manager.upload_file(website, "public_html", "b.txt", io.BytesIO(b"hello"))

    assert len(queued) == 1
    assert queued[0][0] == website.id
    assert queued[0][1] == stored, "the scanner gets the file where it actually landed"


# --- the inline scanner is gone --------------------------------------------

def test_the_old_blocking_scanner_is_not_still_in_the_upload_path():
    """It read the whole file into memory and blocked the request.

    Nothing called it after the queue arrived; leaving it there is an
    invitation to wire it back in.
    """
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "file_manager.py").read_text(encoding="utf-8")
    assert "_scan_before_install" not in source
    assert "scan_stream" not in source


# --- the page has to say what it costs --------------------------------------

def test_the_panel_warns_before_switching_it_on_without_a_resident_clamd():
    page = FRONTEND.read_text(encoding="utf-8")
    assert "toggleMalwareScanOnUpload" in page
    assert "scan_on_upload_is_cheap" in page, (
        "the page has to distinguish a socket round trip from a full signature load"
    )
    assert "28" in page, "say the measured cost rather than 'may be slow'"
