"""Shared set-up for the Email and DNS tests (tests/test_mail_*.py).

Not a conftest: the test files import what they use, so nothing here reaches
another workstream's tests.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
TOOL_PATH = REPO_ROOT / "installer" / "files" / "spanel_mailtool.py"


def load_tool():
    """installer/files/spanel_mailtool.py, as the helper runs it."""
    spec = importlib.util.spec_from_file_location("spanel_mailtool", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def helper_function(name: str) -> str:
    """The body of one shell function of spanel-helper.sh."""
    import re

    text = HELPER.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{\n")
    # Up to the next function: a heredoc's own "}" lines are not the end.
    following = re.compile(r"^(?:[a-z_][a-z0-9_]*\(\) \{|case \"\$cmd\" in|  [a-z0-9-]+\))$", re.M)
    found = following.search(text, start + len(name) + 6)
    return text[start:found.start() if found else len(text)]


@pytest.fixture
def panel_settings_file(tmp_path, monkeypatch):
    from app.services import panel_settings

    monkeypatch.setattr(panel_settings, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(panel_settings, "SETTINGS_FILE", tmp_path / "panel-settings.json")
    return tmp_path / "panel-settings.json"


@pytest.fixture
def db():
    from app.core.database import Base, SessionLocal, engine
    from app.models import entities

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    tables = (entities.MailAutoresponder, entities.MailForwarder, entities.MailAccount, entities.MailDomain,
              entities.DnsZone)
    try:
        yield session
    finally:
        session.rollback()
        for table in tables:
            session.query(table).delete()
        session.query(entities.User).filter(entities.User.username.like("mailt%")).delete(synchronize_session=False)
        session.commit()
        session.close()


@pytest.fixture
def mail_env(db, panel_settings_file, monkeypatch):
    """The Email addon installed, DNS Manager not, and every mail-sync
    recorded instead of sent: env.states holds what the helper would get."""
    from app.services import dns, mail

    class Env:
        states: list = []
        helper_calls: list = []

    env = Env()
    env.states = []
    env.helper_calls = []
    monkeypatch.setattr(mail, "active", lambda: True)
    monkeypatch.setattr(dns, "active", lambda: False)

    def fake_sync(session):
        state = mail.build_state(session)
        env.states.append(state)
        return {"domains": [d["domain"] for d in state["domains"]], "dkim": {}}

    monkeypatch.setattr(mail, "sync", fake_sync)
    return env


def make_user(db, name: str = "mailtuser", role: str = "end_user"):
    from app.models.entities import User

    user = User(username=name, email=f"{name}@example.net", hashed_password="x", role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_domain(db, owner, name: str = "example.com", **values):
    from app.models.entities import MailDomain

    fields = {"catch_all": "", "dkim_public": "A" * 300, "webmail_host": False, "relay": ""}
    fields.update(values)
    row = MailDomain(domain=name, owner_id=owner.id, **fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_mailbox(db, owner, domain: str, local: str = "info"):
    from app.models.entities import MailAccount
    from app.services import mail

    account = MailAccount(owner_id=owner.id, domain=domain, local_part=local,
                          password_hash=mail.hash_password("secret123"), quota_mb=1024, enabled=True)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account
