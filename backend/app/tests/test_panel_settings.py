from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import panel_settings as panel_settings_api
from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import User
from app.schemas.schemas import AdminAccountUpdate, PanelSslInstall


def _db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _admin(email="admin@example.com", password="old-password"):
    return User(
        id=1,
        username="admin",
        email=email,
        hashed_password=hash_password(password),
        role="admin",
        is_active=True,
    )


def test_update_admin_account_updates_email_and_password(monkeypatch):
    """The panel password must not reach the Linux account any more.

    It used to: this test asserted `captured["password"] == "new-password-123"`,
    which is precisely the coupling 0033 removed. sshd offers password
    authentication to that Linux account on port 22, so the panel password was
    brute-forceable from the internet, and a panel password is root through the
    sudo helper.
    """
    db = _db_session()
    user = _admin()
    original_hash = user.hashed_password
    db.add(user)
    db.commit()
    captured = {}

    monkeypatch.setattr(panel_settings_api.site_users, "set_panel_user_password", lambda username, password: captured.update(username=username, password=password))
    monkeypatch.setattr(panel_settings_api, "log_action", lambda *args, **kwargs: None)

    result = panel_settings_api.update_admin_account(
        AdminAccountUpdate(
            email="new-admin@example.com",
            password="new-password-123",
            current_password="old-password",
        ),
        request=None,
        db=db,
        current_user=user,
    )

    db.refresh(user)
    assert result == {"message": "Admin account updated", "password_changed": True}
    assert user.email == "new-admin@example.com"
    assert user.token_version == 1
    assert user.hashed_password != original_hash

    # This admin was a legacy account (sftp_password_set_at was NULL), so its
    # Linux password was the OLD panel password. Leaving it there would mean a
    # rotation the admin believes killed the old secret quietly did not - so it
    # is replaced, with something that is not the new panel password either.
    assert captured["username"] == "admin"
    assert captured["password"] != "new-password-123", (
        "the panel password must never be written to the Linux account"
    )
    assert len(captured["password"]) >= 20
    assert user.sftp_password_set_at is not None, (
        "the account is no longer sharing one secret, and the column has to say so"
    )


def test_an_admin_with_its_own_sftp_password_keeps_it_on_a_panel_change(monkeypatch):
    """Once separated, a panel password change is none of SFTP's business."""
    from datetime import datetime

    db = _db_session()
    user = _admin()
    user.sftp_password_set_at = datetime(2026, 1, 1)
    db.add(user)
    db.commit()
    calls = []

    monkeypatch.setattr(panel_settings_api.site_users, "set_panel_user_password", lambda username, password: calls.append(username))
    monkeypatch.setattr(panel_settings_api, "log_action", lambda *args, **kwargs: None)

    panel_settings_api.update_admin_account(
        AdminAccountUpdate(password="another-password-99", current_password="old-password"),
        request=None,
        db=db,
        current_user=user,
    )

    assert calls == [], "an account with its own SFTP password must be left alone"
    assert user.sftp_password_set_at == datetime(2026, 1, 1)


def test_install_panel_ssl_uses_current_admin_email(monkeypatch):
    db = _db_session()
    user = _admin(email="owner@example.com")
    db.add(user)
    db.commit()
    captured = {}

    def fake_install_panel_ssl(email, panel_hostname=None, panel_port=None, panel_url=None):
        captured["email"] = email
        captured["panel_hostname"] = panel_hostname
        captured["panel_port"] = panel_port
        captured["panel_url"] = panel_url
        return {"message": "ok"}

    monkeypatch.setattr(panel_settings_api.panel_settings, "install_panel_ssl", fake_install_panel_ssl)
    monkeypatch.setattr(panel_settings_api, "log_action", lambda *args, **kwargs: None)

    panel_settings_api.install_panel_ssl(
        PanelSslInstall(
            panel_hostname="panel.example.test",
            panel_port=2222,
            email="ignored@example.com",
        ),
        request=None,
        db=db,
        current_user=user,
    )

    assert captured == {
        "email": "owner@example.com",
        "panel_hostname": "panel.example.test",
        "panel_port": 2222,
        "panel_url": None,
    }
