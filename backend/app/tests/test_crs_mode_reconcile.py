"""A lost CRS setting must not quietly take CRS off every site.

There are two records of the same fact. The panel settings hold what an admin
chose; /etc/nginx/modsec/spanel-crs-mode holds what was last deployed. Every
re-render of a site's rules asks active_crs_mode(), and a missing settings key
read as "off".

That is not hypothetical. On a live server the settings file lost the key, an
update re-rendered every site, and 20 customer sites ran without CRS for
seventeen minutes. Nothing was logged: to the renderer, "off" is a perfectly
ordinary answer.

So a missing key now defers to what is deployed, loudly. An explicit "off" is
still obeyed - that is a decision, not a gap.
"""

import logging

import pytest

from app.services import panel_settings, waf


@pytest.fixture
def mode_file(tmp_path, monkeypatch):
    path = tmp_path / "spanel-crs-mode"
    monkeypatch.setattr(waf, "CRS_MODE_FILE", path)
    return path


@pytest.fixture
def stored(monkeypatch):
    """Whatever the panel settings say the mode is."""
    box = {"value": None}
    monkeypatch.setattr(panel_settings, "stored_crs_mode", lambda: box["value"])
    return box


# --- the gap that caused the incident ---------------------------------------

@pytest.mark.parametrize("deployed", ["block", "detect"])
def test_a_missing_setting_defers_to_what_is_deployed(mode_file, stored, deployed):
    """The renderer must not answer 'off' just because the key went missing."""
    mode_file.write_text(deployed + "\n", encoding="utf-8")
    stored["value"] = None
    assert waf.active_crs_mode() == deployed


def test_and_it_says_so_loudly(mode_file, stored, caplog):
    mode_file.write_text("block\n", encoding="utf-8")
    stored["value"] = None
    with caplog.at_level(logging.ERROR, logger="spanel.waf"):
        waf.active_crs_mode()
    assert caplog.records, "a lost CRS mode has to reach the log"
    assert "missing" in caplog.text.lower()


def test_a_missing_setting_with_nothing_deployed_is_genuinely_off(mode_file, stored):
    """A machine that never had CRS is not a machine that lost it."""
    stored["value"] = None
    assert not mode_file.exists()
    assert waf.active_crs_mode() == "off"


def test_a_missing_setting_and_a_deployed_off_is_off(mode_file, stored):
    mode_file.write_text("off\n", encoding="utf-8")
    stored["value"] = None
    assert waf.active_crs_mode() == "off"


# --- an explicit choice is still a choice -----------------------------------

def test_an_explicit_off_is_obeyed_even_with_crs_deployed(mode_file, stored):
    """Turning CRS off is a decision. Do not second-guess it."""
    mode_file.write_text("block\n", encoding="utf-8")
    stored["value"] = "off"
    assert waf.active_crs_mode() == "off"


def test_a_disagreement_is_logged_but_the_setting_wins(mode_file, stored, caplog):
    mode_file.write_text("block\n", encoding="utf-8")
    stored["value"] = "detect"
    with caplog.at_level(logging.WARNING, logger="spanel.waf"):
        assert waf.active_crs_mode() == "detect"
    assert "disagree" in caplog.text.lower()


def test_agreement_is_quiet(mode_file, stored, caplog):
    mode_file.write_text("block\n", encoding="utf-8")
    stored["value"] = "block"
    with caplog.at_level(logging.WARNING, logger="spanel.waf"):
        assert waf.active_crs_mode() == "block"
    assert not caplog.records


# --- reading the deployed file ----------------------------------------------

def test_an_unreadable_mode_file_is_not_a_mode(mode_file, stored):
    stored["value"] = "block"
    assert waf.deployed_crs_mode() is None
    assert waf.active_crs_mode() == "block", "the setting still answers"


@pytest.mark.parametrize("text,expected", [
    ("block\n", "block"), ("  detect  ", "detect"), ("off", "off"),
    ("nonsense", "off"), ("", None),
])
def test_the_deployed_file_is_read_as_a_mode(mode_file, text, expected):
    mode_file.write_text(text, encoding="utf-8")
    assert waf.deployed_crs_mode() == expected


# --- what the panel is told -------------------------------------------------

def test_stored_crs_mode_tells_never_set_from_set_to_off(monkeypatch):
    raw = {}
    monkeypatch.setattr(panel_settings, "_read_raw_lenient", lambda: dict(raw))
    assert panel_settings.stored_crs_mode() is None

    raw["crs_mode"] = "off"
    assert panel_settings.stored_crs_mode() == "off"

    raw["crs_mode"] = "  BLOCK "
    assert panel_settings.stored_crs_mode() == "block"

    raw["crs_mode"] = "   "
    assert panel_settings.stored_crs_mode() is None
