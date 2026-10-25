"""DKIM rotation with two selectors, and DKIM keys kept in backups.

Rotating used to overwrite the one "spanel" key in place: every message
signed from then until DNS caught up failed DKIM. A restore made a new key,
so the record already in DNS stopped matching.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from mail_fixtures import db, helper_function, load_tool, mail_env, make_domain, make_user, \
    panel_settings_file  # noqa: F401 - fixtures

tool = load_tool()
KEY_A, KEY_B = "A" * 300, "B" * 300
PEM = "-----BEGIN PRIVATE KEY-----\n" + ("MIIE" + "a" * 60 + "\n") * 20 + "-----END PRIVATE KEY-----\n"


# --- the helper's side ------------------------------------------------------------------

def test_key_files_never_mix_up_domains_and_selectors():
    assert tool.dkim_key_name("example.com", "spanel") == "example.com"
    assert tool.dkim_key_name("example.com", "s20261024") == "s20261024/example.com"
    # A domain whose last label looks like a selector is still its own file.
    assert tool.dkim_key_name("example.com.s1", "spanel") == "example.com.s1"
    for bad in ("", "-x", "a/b", "a..b", "x" * 64, "UPPER"):
        with pytest.raises(tool.ToolError):
            tool.check_selector(bad)
    with pytest.raises(tool.ToolError):
        tool.dkim_key_name("../etc", "spanel")


def test_exim_maps_switch_key_and_selector_together():
    domains, selectors = tool.dkim_maps({"b.example": "spanel", "a.example": "s20261024"})
    assert domains == ["a.example: s20261024/a.example", "b.example: b.example"]
    assert selectors == ["a.example: s20261024", "b.example: spanel"]


def test_exim_signs_with_the_mapped_selector():
    conf = helper_function("mail_write_exim_conf")
    assert "dkim_selector = ${lookup{$acl_m_dkim}lsearch{@@M@@/dkim_selectors}{$value}{spanel}}" in conf
    assert "dkim_domain = ${lookup{$acl_m_dkim}lsearch{@@M@@/dkim_domains}{$acl_m_dkim}{}}" in conf
    sync = helper_function("mail_sync")
    assert "spanel_mailtool.dkim_maps" in sync and 'E("dkim_selectors")' in sync
    # A server still on the old configuration gets the new one before the maps change.
    assert "grep -q 'dkim_selectors'" in sync
    drop = helper_function("mail_dkim_drop")
    assert "is the key ${domain} is signed with" in drop


# --- the state machine ------------------------------------------------------------------------

NOW = datetime(2026, 10, 24, 12, 0, 0)


def _row(**values):
    base = dict(dkim_state="", dkim_state_at=None, dkim_rotated_at=None, created_at=NOW - timedelta(days=400),
                dkim_selector="spanel", dkim_next_selector="", dkim_old_selector="")
    base.update(values)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("values, kwargs, step", [
    # Nothing under way, no automatic rotation.
    ({}, {}, "none"),
    # Automatic rotation is due a year after the domain was made...
    ({}, {"rotate_days": 365}, "start"),
    # ...but not a month after the last rotation.
    ({"dkim_rotated_at": NOW - timedelta(days=30)}, {"rotate_days": 365}, "none"),
    # Publishing: DNS shows the new record, but only just.
    ({"dkim_state": "publishing", "dkim_state_at": NOW - timedelta(minutes=5)},
     {"current_status": "ok", "next_status": "ok"}, "wait"),
    # ...an hour later it switches; a person clicking does not wait the hour.
    ({"dkim_state": "publishing", "dkim_state_at": NOW - timedelta(hours=2)},
     {"current_status": "ok", "next_status": "ok"}, "switch"),
    ({"dkim_state": "publishing", "dkim_state_at": NOW - timedelta(minutes=5)},
     {"current_status": "ok", "next_status": "ok", "manual": True}, "switch"),
    # The new record is not in public DNS while the current one is: switching would break signatures.
    ({"dkim_state": "publishing", "dkim_state_at": NOW - timedelta(days=3)},
     {"current_status": "ok", "next_status": "missing", "manual": True}, "wait"),
    # Neither is in public DNS (DNS kept elsewhere, not set up): nothing to break.
    ({"dkim_state": "publishing", "dkim_state_at": NOW - timedelta(days=3)},
     {"current_status": "missing", "next_status": "missing"}, "switch"),
    # DNS could not be asked: never switch blind.
    ({"dkim_state": "publishing", "dkim_state_at": NOW - timedelta(days=3)},
     {"current_status": "missing", "next_status": "unknown"}, "wait"),
    # Retiring: the old key stays published for the retire period.
    ({"dkim_state": "retiring", "dkim_state_at": NOW - timedelta(days=3)}, {"retire_days": 7}, "wait"),
    ({"dkim_state": "retiring", "dkim_state_at": NOW - timedelta(days=8)}, {"retire_days": 7}, "retire"),
    ({"dkim_state": "retiring", "dkim_state_at": NOW - timedelta(days=1)}, {"manual": True}, "retire"),
])
def test_rotation_steps(values, kwargs, step):
    from app.services import mail

    assert mail.dkim_next_step(_row(**values), NOW, **kwargs) == step


def test_new_selectors_never_reuse_one_in_use():
    from app.services import mail

    assert mail.new_dkim_selector(_row(), NOW) == "s20261024"
    assert mail.new_dkim_selector(_row(dkim_selector="s20261024"), NOW) == "s20261024-2"
    assert mail.new_dkim_selector(_row(dkim_selector="s20261024", dkim_old_selector="s20261024-2"), NOW) == "s20261024-3"


# --- the whole rotation, with the helper and DNS faked ------------------------------------------------

def test_rotation_publishes_switches_then_retires(db, mail_env, monkeypatch):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner, dkim_public=KEY_A)
    made, dropped = [], []

    def fake_public(domain, selector="spanel"):
        made.append((domain, selector))
        return KEY_B

    def fake_privileged(command, helper_args=None, **kwargs):
        if command == "mail-dkim-drop":
            dropped.append(tuple(helper_args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mail, "_dkim_public", fake_public)
    monkeypatch.setattr(mail.shell, "privileged", fake_privileged)
    statuses = {"dkim": "ok", "dkim-next": "missing"}
    monkeypatch.setattr(mail, "_dkim_statuses", lambda session, r: (statuses["dkim"], statuses["dkim-next"]))

    mail.start_dkim_rotation(db, row, now=NOW)
    assert made == [("example.com", "s20261024")]
    assert (row.dkim_state, row.dkim_next_selector, row.dkim_next_public) == ("publishing", "s20261024", KEY_B)
    keys = {record["key"]: record for record in mail.dns_records(row)}
    assert keys["dkim"]["name"] == "spanel._domainkey.example.com"
    assert keys["dkim-next"]["name"] == "s20261024._domainkey.example.com"
    assert KEY_B in keys["dkim-next"]["value"]
    with pytest.raises(mail.MailError, match="already under way"):
        mail.start_dkim_rotation(db, row, now=NOW)

    # Not in public DNS yet: still signed with the old key.
    assert mail.advance_dkim(db, row, now=NOW + timedelta(hours=3)) == "wait"
    assert row.dkim_selector == "spanel"

    statuses["dkim-next"] = "ok"
    assert mail.advance_dkim(db, row, now=NOW + timedelta(hours=3)) == "switch"
    assert (row.dkim_selector, row.dkim_public) == ("s20261024", KEY_B)
    assert (row.dkim_old_selector, row.dkim_old_public, row.dkim_state) == ("spanel", KEY_A, "retiring")
    # Exim got the new selector in the same mail-sync.
    assert mail_env.states[-1]["domains"][0]["dkim_selector"] == "s20261024"
    keys = {record["key"]: record for record in mail.dns_records(row)}
    assert keys["dkim"]["name"] == "s20261024._domainkey.example.com"
    assert keys["dkim-old"]["name"] == "spanel._domainkey.example.com"

    assert mail.advance_dkim(db, row, now=NOW + timedelta(days=2)) == "wait"
    assert mail.advance_dkim(db, row, now=NOW + timedelta(days=9)) == "retire"
    assert dropped == [("example.com", "spanel")]
    assert (row.dkim_state, row.dkim_old_selector, row.dkim_old_public) == ("", "", "")
    assert "dkim-old" not in {record["key"] for record in mail.dns_records(row)}


def test_cancel_drops_the_unused_key(db, mail_env, monkeypatch):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner, dkim_public=KEY_A)
    dropped = []
    monkeypatch.setattr(mail, "_dkim_public", lambda domain, selector="spanel": KEY_B)
    monkeypatch.setattr(mail.shell, "privileged", lambda command, helper_args=None, **kw:
                        dropped.append((command, tuple(helper_args or ()))) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    mail.start_dkim_rotation(db, row, now=NOW)
    mail.cancel_dkim_rotation(db, row)
    assert dropped == [("mail-dkim-drop", ("example.com", "s20261024"))]
    assert (row.dkim_state, row.dkim_selector, row.dkim_public) == ("", "spanel", KEY_A)


def test_automatic_rotation_only_when_switched_on(db, mail_env, monkeypatch):
    from app.services import mail

    owner = make_user(db)
    row = make_domain(db, owner, dkim_public=KEY_A)
    row.created_at = NOW - timedelta(days=100)
    db.commit()
    monkeypatch.setattr(mail, "_dkim_public", lambda domain, selector="spanel": KEY_B)
    assert mail.maintenance_tick(db, now=NOW) == {}
    mail.save_settings({"dkim_rotate_days": 90})
    assert mail.maintenance_tick(db, now=NOW) == {"example.com": "start"}
    assert row.dkim_state == "publishing"
    with pytest.raises(mail.MailError):
        mail.save_settings({"dkim_rotate_days": 5})


# --- backups ------------------------------------------------------------------------------------

def test_backup_keeps_the_keys_and_restore_puts_them_back(db, mail_env, monkeypatch):
    from app.models.entities import MailDomain
    from app.services import mail

    owner = make_user(db)
    make_domain(db, owner, dkim_public=KEY_A, dkim_selector="s20260101")
    calls = []

    def fake_privileged(command, helper_args=None, input=None, **kwargs):
        calls.append((command, tuple(helper_args or ()), input))
        if command == "mail-dkim-export":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"s20260101": PEM, "stale": PEM}), stderr="")
        if command == "mail-dkim-import":
            return SimpleNamespace(returncode=0, stdout=KEY_A + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mail.shell, "privileged", fake_privileged)
    manifest = mail.backup_manifest(db, owner)
    entry = manifest["domains"][0]
    assert entry["dkim_selector"] == "s20260101"
    assert entry["dkim_keys"] == {"s20260101": PEM}

    # On another server: the domain is new there.
    db.query(MailDomain).delete()
    db.commit()
    mail.restore_manifest(db, owner, json.loads(json.dumps(manifest)), lambda domain, local: None)
    restored = db.query(MailDomain).filter(MailDomain.domain == "example.com").one()
    assert (restored.dkim_selector, restored.dkim_public) == ("s20260101", KEY_A)
    assert ("mail-dkim-import", ("example.com", "s20260101"), PEM) in calls


def test_restore_refuses_what_is_not_a_key(db, mail_env, monkeypatch):
    from app.models.entities import MailDomain
    from app.services import mail

    owner = make_user(db)
    calls = []
    monkeypatch.setattr(mail.shell, "privileged", lambda command, helper_args=None, **kw:
                        calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    section = {"domains": [{"domain": "example.com", "dkim_selector": "../x", "dkim_keys": {"../x": PEM}}]}
    mail.restore_manifest(db, owner, section, lambda domain, local: None)
    section = {"domains": [{"domain": "example.com", "dkim_selector": "spanel", "dkim_keys": {"spanel": "not a key"}}]}
    mail.restore_manifest(db, owner, section, lambda domain, local: None)
    assert "mail-dkim-import" not in calls
    assert db.query(MailDomain).one().dkim_selector == "spanel"
