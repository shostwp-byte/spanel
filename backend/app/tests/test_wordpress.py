from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import websites
from app.core.database import Base
from app.models.entities import DatabaseAccount, User, Website
from app.schemas.schemas import WebsiteWordPressInstall
from app.services import wordpress


def _db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_delete_wordpress_binds_rm_site_to_owner_and_site_root(monkeypatch):
    calls = []
    root = "/home/siteuser/example.test"

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(wordpress.shell, "privileged", fake_privileged)

    wordpress.delete_wordpress(root)

    assert calls[0][0] == "rm-site"
    assert calls[0][1][0] == "siteuser"
    assert calls[0][1][1].replace("\\", "/").endswith(root)
    assert calls[0][1][2].replace("\\", "/").endswith(root)
    assert calls[0][2]["fallback"][0:2] == ["rm", "-rf"]
    assert calls[0][2]["fallback"][2].replace("\\", "/").endswith(root)


def test_install_wordpress_on_existing_site_updates_site(monkeypatch):
    db = _db_session()
    user = User(id=1, username="client", email="client@example.test", hashed_password="x", role="end_user")
    site = Website(
        id=1,
        domain="example.test",
        owner_id=1,
        root_path="/home/client/example.test",
        linux_user="client",
        php_version="8.4",
        app_type="php",
        nginx_rewrite_mode="none",
    )
    db.add_all([user, site])
    db.commit()
    calls = {}

    monkeypatch.setattr(
        websites.storage_quota,
        "enforce_user_storage_quota",
        lambda *args, **kwargs: calls.setdefault("quota", kwargs["incoming_bytes"]),
    )
    def fake_create_database(domain, **kwargs):
        calls["create_database"] = {"domain": domain, **kwargs}
        return {"db_name": "wp_example", "db_user": "u_wp_example", "db_password": "db-secret"}

    monkeypatch.setattr(websites.mariadb, "create_database", fake_create_database)
    monkeypatch.setattr(websites.mariadb, "drop_database", lambda *args: calls.setdefault("drop", args))

    def fake_install(domain, db_info, title, admin_user, admin_password, admin_email, php_version, linux_user, root_path=None):
        calls["install"] = {
            "domain": domain,
            "db": db_info,
            "title": title,
            "admin_user": admin_user,
            "admin_password": admin_password,
            "admin_email": admin_email,
            "php_version": php_version,
            "linux_user": linux_user,
            "root_path": root_path,
        }
        return root_path

    def fake_rewrite(website, **overrides):
        calls["rewrite"] = overrides
        return "ok"

    monkeypatch.setattr(websites.wordpress, "install_wordpress", fake_install)
    monkeypatch.setattr(websites, "_ensure_default_waf_file", lambda domain: calls.setdefault("waf", domain))
    monkeypatch.setattr(websites, "_rewrite_website_vhost", fake_rewrite)
    monkeypatch.setattr(websites, "encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr(websites, "log_action", lambda *args, **kwargs: None)

    payload = WebsiteWordPressInstall(
        title="Example Site",
        admin_user="admin",
        admin_email="admin@example.com",
        admin_password="StrongPass123!",
    )

    result = websites.install_wordpress_on_website(1, payload, request=None, db=db, current_user=user)

    assert result.app_type == "wordpress"
    assert result.nginx_rewrite_mode == "front_controller"
    assert calls["create_database"]["if_not_exists"] is False
    assert calls["install"]["domain"] == "example.test"
    assert calls["install"]["root_path"].replace("\\", "/").endswith("/home/client/example.test")
    assert calls["rewrite"]["app_type"] == "wordpress"
    assert calls["rewrite"]["rewrite_mode"] == "front_controller"
    account = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == 1).one()
    assert account.db_name == "wp_example"
