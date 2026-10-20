"""Deleting a website (or its owner) must take the certificate with it.

A lineage left behind keeps certbot.timer renewing a name the server no longer
answers for, which eventually turns into a permanently failed unit. The risk in
removing it is the opposite one: certificates routinely cover more than the site
they were issued for, so these tests pin down when the cleanup must decline.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import User, Website, WebsiteAlias
from app.services import ssl
from app.services.shell import CommandResult

HELPER_SCRIPT = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"


def _db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    owner = User(id=1, username="admin", email="admin@example.test", hashed_password="x", role="admin")
    doomed = Website(id=1, domain="doomed.test", owner_id=1, root_path="/home/admin/doomed.test")
    keeper = Website(id=2, domain="keeper.test", owner_id=1, root_path="/home/admin/keeper.test")
    db.add_all([owner, doomed, keeper])
    db.add(WebsiteAlias(id=1, website_id=1, domain="www2.doomed.test", mode="alias"))
    db.add(WebsiteAlias(id=2, website_id=2, domain="shop.keeper.test", mode="alias"))
    db.commit()
    return db


def _capture(monkeypatch, *, returncode=0, stdout="Deleted Let's Encrypt certificate doomed.test"):
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, list(helper_args or [])))
        return CommandResult(command=helper_command, returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(ssl.shell, "privileged", fake_privileged)
    return calls


def _no_panel_url(monkeypatch):
    from app.services import panel_settings

    monkeypatch.setattr(panel_settings, "configured_panel_url", lambda: "")


def test_deletes_the_certificate_when_nothing_else_uses_it(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    # The cert covers only the site being removed, plus its own www name.
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test", "www.doomed.test"]})
    calls = _capture(monkeypatch)

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert calls == [("certbot-delete", ["doomed.test"])]
    assert "Deleted" in note


def test_keeps_a_certificate_that_still_covers_another_website(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    # One certificate was expanded to cover a second site hosted here; deleting
    # the lineage would drop that live site to a TLS error.
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test", "keeper.test"]})
    calls = _capture(monkeypatch)

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert calls == []
    assert "keeper.test" in note
    assert "kept" in note


def test_keeps_a_certificate_that_covers_another_websites_alias(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test", "shop.keeper.test"]})
    calls = _capture(monkeypatch)

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert calls == []
    assert "shop.keeper.test" in note


def test_the_sites_own_alias_does_not_protect_it(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    # www2.doomed.test belongs to the site being deleted, so it is no reason to
    # keep the certificate alive.
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test", "www2.doomed.test"]})
    calls = _capture(monkeypatch)

    ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert calls == [("certbot-delete", ["doomed.test"])]


def test_keeps_the_certificate_the_panel_is_served_on(monkeypatch):
    from app.services import panel_settings

    db = _seed(_db_session())
    # An admin pointed the panel at a hosted domain, so :2222 is reading this
    # exact certificate. Removing it would meet every admin with a TLS warning.
    monkeypatch.setattr(panel_settings, "configured_panel_url", lambda: "https://panel.doomed.test:2222")
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test", "panel.doomed.test"]})
    calls = _capture(monkeypatch)

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert calls == []
    assert "panel.doomed.test" in note


def test_a_wildcard_certificate_covering_a_live_site_is_kept(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["keeper.test", "*.keeper.test"]})
    calls = _capture(monkeypatch)

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert calls == []
    assert "keeper.test" in note


def test_a_failing_helper_never_blocks_the_deletion(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test"]})
    _capture(monkeypatch, returncode=1, stdout="certbot is unhappy")

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert "could not remove" in note


def test_an_exploding_helper_never_blocks_the_deletion(monkeypatch):
    db = _seed(_db_session())
    _no_panel_url(monkeypatch)
    monkeypatch.setattr(ssl, "cert_info", lambda name: {"sans": ["doomed.test"]})

    def boom(*args, **kwargs):
        raise OSError("sudo went missing")

    monkeypatch.setattr(ssl.shell, "privileged", boom)

    note = ssl.release_site_certificates(db, "doomed.test", exclude_website_id=1)

    assert "could not remove" in note


def test_delete_ssl_refuses_to_escape_the_certificate_directory(monkeypatch):
    calls = _capture(monkeypatch)
    for bad in ("../../etc/letsencrypt/live/other", "a/b", ""):
        try:
            ssl.delete_ssl(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")
    assert calls == []


def test_helper_validates_the_domain_before_deleting_anything():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    body = helper.split("delete_ssl_cert()")[1].split("\n}\n")[0]
    # _safe_domain only blocks path traversal; the strict name check that keeps
    # a crafted argument out of `certbot delete` lives in the helper.
    assert "require_domain" in body


def test_helper_exposes_the_verb_and_guards_the_panel_certificate():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "certbot-delete)" in helper
    assert "delete_ssl_cert()" in helper
    # The panel's own certificate must be refused even when asked directly.
    assert 'deny "refusing to delete ${name}: the panel is served on this certificate"' in helper
    # Removing the lineage must also drop the copy the panel serves on :2222.
    body = helper.split("delete_ssl_cert()")[1].split("\n}\n")[0]
    assert "sync_panel_sni_certificates" in body
