"""End users configuring the WAF on their own websites.

Getting this wrong exposes one customer's sites and logs to another, or hands a
customer arbitrary ModSecurity directives on a shared server. Each test below is
one of those two failures.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import waf as waf_api
from app.core.database import Base
from app.models.entities import User, UserPackage, Website


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    db.add(UserPackage(id=1, name="With WAF", waf_enabled=True))
    db.add(UserPackage(id=2, name="No WAF", waf_enabled=False))
    db.add(User(id=1, username="admin", email="a@b.c", hashed_password="x", role="admin"))
    db.add(User(id=2, username="alice", email="al@b.c", hashed_password="x", role="end_user", package_id=1))
    db.add(User(id=3, username="bob", email="bo@b.c", hashed_password="x", role="end_user", package_id=1))
    db.add(User(id=4, username="carol", email="ca@b.c", hashed_password="x", role="end_user", package_id=2))
    db.add(User(id=5, username="dave", email="da@b.c", hashed_password="x", role="end_user"))
    db.add(Website(id=10, domain="alice-site.test", owner_id=2, root_path="/home/alice/alice-site.test",
                   waf_custom_rules="SecRuleRemoveById 942100"))
    db.add(Website(id=11, domain="bob-site.test", owner_id=3, root_path="/home/bob/bob-site.test"))
    db.commit()
    return db


def _user(db, username):
    return db.query(User).filter(User.username == username).first()


def test_an_owner_reaches_their_own_website():
    db = _db()
    site = waf_api._owned_website(db, 10, _user(db, "alice"))

    assert site.domain == "alice-site.test"


def test_another_customers_website_is_not_found():
    """404, not 403: a different code would confirm the id exists."""
    db = _db()

    with pytest.raises(HTTPException) as caught:
        waf_api._owned_website(db, 11, _user(db, "alice"))
    assert caught.value.status_code == 404


def test_an_admin_reaches_any_website():
    db = _db()

    assert waf_api._owned_website(db, 11, _user(db, "admin")).domain == "bob-site.test"


def test_a_package_without_the_waf_is_refused():
    db = _db()
    carol = _user(db, "carol")
    db.add(Website(id=12, domain="carol-site.test", owner_id=carol.id, root_path="/home/carol/x"))
    db.commit()

    assert waf_api.may_manage_waf(carol) is False
    with pytest.raises(HTTPException) as caught:
        waf_api._owned_website(db, 12, carol)
    assert caught.value.status_code == 403


def test_an_account_with_no_package_keeps_access():
    """UserPackage.waf_enabled defaults to True, so no package must not mean no WAF."""
    db = _db()

    assert waf_api.may_manage_waf(_user(db, "dave")) is True


def test_an_admin_is_never_gated_by_a_package():
    db = _db()
    admin = _user(db, "admin")
    admin.package_id = 2  # the package with the WAF switched off
    db.commit()

    assert waf_api.may_manage_waf(admin) is True


def test_an_end_user_cannot_change_custom_rules(monkeypatch):
    """Custom rules are arbitrary ModSecurity directives - code execution on a
    shared server if a customer can write them."""
    db = _db()
    payload = waf_api.WebsiteWafRulesUpdate(
        enabled_rule_ids=["php-sensitive-files"],
        custom_rules="SecRule REQUEST_URI \"@rx .\" \"id:1,phase:1,pass,exec:/tmp/x.lua\"",
    )

    with pytest.raises(HTTPException) as caught:
        waf_api.save_website_waf(payload, 10, db=db, current_user=_user(db, "alice"))
    assert caught.value.status_code == 403
    assert "administrator" in caught.value.detail


def test_an_end_user_may_save_rules_while_leaving_custom_rules_alone(monkeypatch):
    db = _db()
    saved = {}

    def fake_save(website, ids, custom):
        saved["ids"], saved["custom"] = list(ids), custom
        from app.services.shell import CommandResult
        return CommandResult(command="waf-site-save", returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(waf_api.waf, "save_website_config", fake_save)
    monkeypatch.setattr(waf_api.waf, "site_config", lambda w: {"domain": w.domain})
    monkeypatch.setattr(waf_api.nginx, "update_waf_block", lambda *a, **k: None)

    payload = waf_api.WebsiteWafRulesUpdate(
        enabled_rule_ids=["php-sensitive-files"],
        custom_rules="SecRuleRemoveById 942100",   # unchanged
    )
    waf_api.save_website_waf(payload, 10, db=db, current_user=_user(db, "alice"))

    assert saved["ids"] == ["php-sensitive-files"]
    assert saved["custom"] == "SecRuleRemoveById 942100"


def test_the_site_config_tells_the_ui_who_may_edit_custom_rules(monkeypatch):
    db = _db()
    monkeypatch.setattr(waf_api.waf, "site_config", lambda w: {"domain": w.domain})

    as_owner = waf_api.get_website_waf(10, db=db, current_user=_user(db, "alice"))
    as_admin = waf_api.get_website_waf(10, db=db, current_user=_user(db, "admin"))

    assert as_owner["may_edit_custom_rules"] is False
    assert as_admin["may_edit_custom_rules"] is True


def test_access_logs_without_a_website_id_stay_within_the_callers_sites(monkeypatch):
    """The dangerous default: no website_id used to mean every site on the box."""
    db = _db()
    seen = {}

    def fake_access_logs(websites, **kwargs):
        seen["domains"] = [w.domain for w in websites]
        return {"items": [], "websites": []}

    monkeypatch.setattr(waf_api.waf, "access_logs", fake_access_logs)
    waf_api.get_waf_access_logs(
        website_id=None, verdict="all", q="", limit=50, lines=100,
        db=db, current_user=_user(db, "alice"),
    )

    assert seen["domains"] == ["alice-site.test"]


def test_admins_still_see_every_site_in_the_access_logs(monkeypatch):
    db = _db()
    seen = {}
    monkeypatch.setattr(waf_api.waf, "access_logs",
                        lambda websites, **k: seen.update(domains=[w.domain for w in websites]) or {"items": [], "websites": []})

    waf_api.get_waf_access_logs(
        website_id=None, verdict="all", q="", limit=50, lines=100,
        db=db, current_user=_user(db, "admin"),
    )

    assert sorted(seen["domains"]) == ["alice-site.test", "bob-site.test"]


def test_clearing_logs_cannot_reach_another_customers_site(monkeypatch):
    db = _db()
    monkeypatch.setattr(waf_api.waf, "clear_access_logs", lambda websites: len(list(websites)))

    with pytest.raises(HTTPException) as caught:
        waf_api.clear_waf_access_logs(website_id=11, db=db, current_user=_user(db, "alice"))
    assert caught.value.status_code == 404


def test_the_bot_overview_lists_only_the_callers_sites(monkeypatch):
    db = _db()
    monkeypatch.setattr(waf_api.waf, "website_blocked_bots", lambda site: [])
    monkeypatch.setattr(waf_api.waf, "effective_blocked_bots", lambda site: [])
    monkeypatch.setattr(waf_api.panel_settings, "global_blocked_bots", lambda: [])

    data = waf_api.list_blocked_bots(db=db, current_user=_user(db, "alice"))

    assert [row["domain"] for row in data["websites"]] == ["alice-site.test"]


def test_server_wide_screens_stay_admin_only():
    db = _db()
    alice = _user(db, "alice")

    for call in (
        lambda: waf_api.get_waf_status(current_user=alice),
        lambda: waf_api.get_waf_rules(current_user=alice),
    ):
        with pytest.raises(HTTPException) as caught:
            call()
        assert caught.value.status_code == 403
