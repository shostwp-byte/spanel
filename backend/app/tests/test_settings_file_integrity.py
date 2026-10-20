"""The settings file must not be emptied by a failed read.

Almost every writer in panel_settings reads the file, adds a key and writes the
whole thing back. While a failed read returned `{}`, that sequence was an
erase: the next write replaced every setting the panel had with the one key
the caller happened to be setting.

It happened on a live server. A settings write performed as root left the file
owned by root; the panel account's next read was denied, the error was
swallowed, and the file went from six keys to one - taking the malware
schedule and the panel name with it.

Two separate faults, both fixed here:

  the read could not tell "no settings yet" from "cannot read the settings",
  and

  the write did not carry the file's owner across, so writing as root handed
  the panel account a file it could no longer read.
"""

import json
import os
import stat as stat_module

import pytest

from app.services import panel_settings

# Windows has no chown and maps chmod to something much coarser, so the
# ownership and permission tests below only mean anything on POSIX. CI is
# Linux, which is where the bug they cover actually happened.
posix_only = pytest.mark.skipif(
    not hasattr(os, "chown"), reason="file ownership and modes are POSIX"
)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(panel_settings, "SETTINGS_FILE", tmp_path / "panel-settings.json")
    return tmp_path / "panel-settings.json"


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


# --- absent is not the same as unreadable -----------------------------------

def test_no_file_yet_reads_as_no_settings(settings_file):
    assert panel_settings._read_raw() == {}


def test_a_file_that_is_not_json_refuses_to_read(settings_file):
    settings_file.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(panel_settings.SettingsUnreadable):
        panel_settings._read_raw()


def test_a_file_holding_something_other_than_an_object_refuses(settings_file):
    settings_file.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(panel_settings.SettingsUnreadable):
        panel_settings._read_raw()


def test_a_read_error_refuses_rather_than_looking_empty(settings_file, monkeypatch):
    """The exact shape of the live incident: EACCES on an existing file."""
    _write(settings_file, {"app_name": "MyPanel", "malware_scan_enabled": True})

    def _denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(settings_file), "read_text", _denied)
    with pytest.raises(panel_settings.SettingsUnreadable):
        panel_settings._read_raw()


# --- and a writer must not proceed on that ----------------------------------

def test_a_writer_leaves_the_file_alone_when_it_cannot_be_read(settings_file, monkeypatch):
    """The whole point. Refusing to write beats writing one key over six."""
    original = {"app_name": "MyPanel", "malware_scan_enabled": True,
                "malware_realtime_enabled": True, "malware_schedule": {"websites": [1, 3]}}
    _write(settings_file, original)

    def _denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(settings_file), "read_text", _denied)
    with pytest.raises(panel_settings.SettingsUnreadable):
        panel_settings.save_crs_mode("block")

    monkeypatch.undo()
    assert json.loads(settings_file.read_text(encoding="utf-8")) == original


def test_a_writer_still_works_normally(settings_file):
    _write(settings_file, {"app_name": "MyPanel", "malware_scan_enabled": True})
    panel_settings.save_crs_mode("detect")
    stored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert stored["crs_mode"] == "detect"
    assert stored["app_name"] == "MyPanel", "the other keys have to survive"
    assert stored["malware_scan_enabled"] is True


# --- lookups that must not fail a request -----------------------------------

def test_a_lookup_falls_back_to_a_default_instead_of_raising(settings_file, monkeypatch):
    _write(settings_file, {"crs_mode": "block"})

    def _denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(settings_file), "read_text", _denied)
    assert panel_settings.crs_mode() == "off", (
        "a vhost render must not fail because the settings file is unreadable"
    )


def test_the_lenient_reader_is_not_used_by_anything_that_writes():
    """One rule, checked mechanically: writers read strictly."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "services"
    offenders = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^(?:async )?def ([a-zA-Z_]+)\(", text, re.MULTILINE):
            start = match.start()
            nxt = text.find("\ndef ", start + 1)
            body = text[start:nxt if nxt != -1 else len(text)]
            if "_read_raw_lenient()" in body and "_write_raw(" in body:
                offenders.append(f"{path.name}:{match.group(1)}")
    assert not offenders, (
        "these read leniently and then write the whole file back, which is how "
        f"settings get erased: {offenders}"
    )


# --- the write keeps whose file it is ---------------------------------------

@posix_only
def test_the_write_preserves_the_existing_mode(settings_file):
    _write(settings_file, {"a": 1})
    os.chmod(settings_file, 0o600)
    panel_settings._write_raw({"a": 1, "b": 2})
    assert stat_module.S_IMODE(settings_file.stat().st_mode) == 0o600


@posix_only
@pytest.mark.skipif(hasattr(os, "getuid") and os.getuid() != 0,
                    reason="changing a file's owner needs root")
def test_the_write_preserves_the_existing_owner(settings_file):
    """Writing as root must not hand the panel account an unreadable file."""
    _write(settings_file, {"a": 1})
    os.chown(settings_file, 1000, 1000)
    panel_settings._write_raw({"a": 1, "b": 2})
    after = settings_file.stat()
    assert (after.st_uid, after.st_gid) == (1000, 1000)


@posix_only
def test_a_brand_new_file_takes_the_directorys_owner(settings_file, monkeypatch):
    """No previous file to copy from: the data dir says who the panel is."""
    chowned = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: chowned.append((uid, gid)))
    panel_settings._write_raw({"a": 1})
    assert settings_file.exists()
    if chowned:
        expected = panel_settings.SETTINGS_DIR.stat()
        assert chowned[-1] == (expected.st_uid, expected.st_gid)
