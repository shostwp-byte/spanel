"""clamd is installed for scan-on-upload and for nothing else.

maldet runs every scheduled, on-demand and real-time scan, and it calls
`clamscan` with both signature sets on the command line - it never opens
clamd's socket. So a daemon left running beside it is a second resident copy of
the same ~1 GB of signatures.

That was not theoretical. On a live 8 GB server, 16 OOM kills in 7 days:

    Sep 18 22:13  clamd     1.56 GB      Sep 21 01:40  clamd     1.14 GB
    Sep 18 22:18  clamscan  0.87 GB      Sep 21 01:40  clamscan  1.05 GB
    Sep 18 22:18  nginx     0.69 GB      Sep 21 15:50  clamd     2.17 GB
                                         Sep 21 15:50  nginx     0.71 GB

clamd in 9 of the 16, twice taking nginx down with it. clamscan held ~1 GB
throughout - the size of the signature set, and unavoidable.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


@pytest.fixture(scope="module")
def helper_source():
    return HELPER_SCRIPT.read_text(encoding="utf-8")


def _function(source, name):
    start = source.index(f"{name}() {{")
    # Functions here are separated by a line that closes at column zero.
    end = source.index("\n}\n", start)
    return source[start:end]


# --- installing the engine must not install the daemon ----------------------

def test_the_engine_install_no_longer_pulls_in_the_daemon(helper_source):
    body = _function(helper_source, "install_clamav_engine")
    assert "clamav-daemon" not in body, (
        "installing the scanner must not leave a resident daemon nothing calls"
    )
    assert "apt-get install -y clamav" in body
    assert "systemctl enable --now clamav-daemon" not in body


def test_the_engine_install_still_installs_what_maldet_needs(helper_source):
    body = _function(helper_source, "install_clamav_engine")
    assert "freshclam" in body, "the signature database still has to be fetched"


# --- the daemon is its own, separate decision -------------------------------

def test_installing_the_daemon_is_a_verb_of_its_own(helper_source):
    assert "clamav-daemon-install)" in helper_source
    assert "clamav-daemon-remove)" in helper_source
    body = _function(helper_source, "install_clamav_daemon")
    assert "systemctl enable --now clamav-daemon" in body
    assert "tune_clamd_limits" in body


def test_a_new_daemon_gets_the_memory_ceiling_immediately(helper_source):
    """It reached 2.17 GB before the guard existed. Never again unbounded."""
    body = _function(helper_source, "install_clamav_daemon")
    assert "spanel-memory-guard" in body


# --- removing it must never take the engine ---------------------------------

def test_removal_refuses_when_maldet_is_absent(helper_source):
    """Without maldet the panel falls back to clamdscan, which needs clamd."""
    body = _function(helper_source, "remove_clamav_daemon")
    assert "$MALDET_BIN" in body
    assert "deny " in body


def test_removal_asks_apt_what_it_would_do_first(helper_source):
    body = _function(helper_source, "remove_clamav_daemon")
    assert "apt-get -s remove clamav-daemon" in body, (
        "simulate before removing, or apt may take the engine with it"
    )
    assert re.search(r"Remv \(clamav\|clamav-base\|clamav-freshclam\)", body), (
        "the simulation has to be checked for the engine packages by name"
    )


def test_removal_never_autoremoves(helper_source):
    """autoremove decides for itself, and cannot know maldet calls clamscan."""
    body = _function(helper_source, "remove_clamav_daemon")
    assert "autoremove" not in body.replace("no autoremove", "")


def test_removal_is_a_no_op_when_the_daemon_is_not_there(helper_source):
    body = _function(helper_source, "remove_clamav_daemon")
    assert "nothing to remove" in body


def test_removal_says_so_if_the_engine_disappeared_anyway(helper_source):
    body = _function(helper_source, "remove_clamav_daemon")
    assert "command -v clamscan" in body


# --- the toggle drives the daemon -------------------------------------------

def test_turning_scan_on_upload_on_installs_the_daemon_and_off_removes_it():
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "panel_settings.py").read_text(encoding="utf-8")
    body = source[source.index("def set_malware_scan_on_upload"):]
    body = body[:body.index("\ndef _persist_malware_realtime")]
    assert '"clamav-daemon-install" if enabled else "clamav-daemon-remove"' in body


def test_the_setting_is_saved_even_if_the_daemon_step_fails():
    """A failed apt run must not leave the panel disagreeing with itself."""
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "panel_settings.py").read_text(encoding="utf-8")
    body = source[source.index("def set_malware_scan_on_upload"):]
    body = body[:body.index("\ndef _persist_malware_realtime")]
    write_at = body.index("_write_raw(data)")
    call_at = body.index("shell.privileged(verb")
    assert write_at < call_at, "persist the choice before acting on it"
    assert "except Exception" in body


# --- and the update sweeps up what is already out there ---------------------

def test_the_update_removes_an_idle_daemon():
    text = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "clamav-daemon-remove" in text


def test_the_update_leaves_it_alone_when_scan_on_upload_is_on():
    text = UPDATE_SCRIPT.read_text(encoding="utf-8")
    index = text.index("clamav-daemon-remove")
    window = text[index - 900:index]
    assert "malware_scan_on_upload" in window, (
        "an operator who switched the feature on must keep their daemon"
    )


def test_the_update_routes_the_removal_through_the_panel_account():
    """The helper refuses a direct root call; `|| true` would hide that."""
    text = UPDATE_SCRIPT.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "clamav-daemon-remove" in l and "sudo" in l)
    assert "sudo -u spanel" in line
