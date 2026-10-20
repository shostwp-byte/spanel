"""Contract tests for the shared billing module.

`modules/servers/spanel/` is one WHMCS module used against both spanel and
OPanel, so the module is the fixed side: these tests pin the exact response
keys it reads. Renaming or dropping one of them breaks provisioning for every
customer, and the module cannot be adjusted to compensate.
"""

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import provisioning as provisioning_api
from app.core.database import Base
from app.models.entities import ProvisioningAccount, User, UserPackage, Website
from app.services import provisioning as provisioning_service
from app.services.provisioning import create_api_token


def _db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/provisioning/v1/",
            "raw_path": b"/api/provisioning/v1/",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"authorization", f"Bearer {token}".encode()), (b"user-agent", b"WHMCS")],
            "client": ("203.0.113.10", 40000),
            "server": ("panel.example.com", 443),
        }
    )


def _seed(db):
    raw, _token = create_api_token(db, "whmcs", "provisioning:read,provisioning:write", "")
    package = UserPackage(id=1, name="Starter", slug="starter", website_limit=5, storage_limit_mb=1024)
    user = User(
        id=1,
        username="bp7_abc123",
        email="bp7_abc123@users.spanel.dev",
        hashed_password="x",
        role="end_user",
        package_id=1,
        website_limit=5,
        storage_limit_mb=1024,
    )
    website = Website(
        id=1,
        domain="customer.test",
        owner_id=1,
        root_path="/home/bp7_abc123/customer.test",
        linux_user="bp7_abc123",
    )
    account = ProvisioningAccount(
        id=1,
        external_id="whmcs:7",
        user_id=1,
        package_id=1,
        primary_website_id=1,
        status="active",
    )
    db.add_all([package, user, website, account])
    db.commit()
    return raw


# Keys spanel_save_service_record() and spanel_ClientArea() read.
ACCOUNT_KEYS = {"external_id", "username", "email", "domain", "status", "package_id", "package_name", "service_label"}
# Keys spanel_save_service_note() reads from UsageUpdate.
USAGE_KEYS = {"storage_used_bytes", "storage_limit_bytes", "storage_percent"}


def test_login_returns_login_url_key():
    """spanel_sso_url() reads data['login_url'] and nothing else.

    Returning only 'url' made every SSO click fall back to the plain panel
    URL, so customers landed on a login form instead of their account.
    """
    db = _db_session()
    raw = _seed(db)

    result = provisioning_api.create_login_url("whmcs:7", _request(raw), db=db)

    assert "login_url" in result
    assert result["login_url"]
    assert "/api/auth/sso/" in result["login_url"]


def test_account_response_has_every_key_the_module_reads():
    db = _db_session()
    raw = _seed(db)

    data = provisioning_api.get_account("whmcs:7", _request(raw), db=db)

    assert ACCOUNT_KEYS.issubset(data.keys())
    assert data["service_label"] == "Starter #7"
    assert data["domain"] == "customer.test"


def test_usage_response_has_every_key_the_module_reads(monkeypatch):
    db = _db_session()
    raw = _seed(db)
    monkeypatch.setattr(
        provisioning_api.storage_quota,
        "storage_usage_summary",
        lambda _db, _user: {"storage_used_bytes": 1024, "storage_limit_bytes": 2048, "storage_percent": 50.0},
    )

    data = provisioning_api.get_usage("whmcs:7", _request(raw), db=db)

    assert USAGE_KEYS.issubset(data.keys())


def test_plans_response_has_id_and_name():
    """spanel_package_options() indexes packages by 'id' and labels by 'name'."""
    db = _db_session()
    raw = _seed(db)

    plans = provisioning_api.list_plans(_request(raw), db=db)

    assert plans and {"id", "name"}.issubset(plans[0].keys())


def test_responses_never_collide_with_the_envelope_unwrap():
    """The module unwraps any body carrying both 'success' and 'data'.

    OPanel replies with that envelope; spanel replies with bare objects. A
    spanel body that happened to have both keys would be silently unwrapped
    into the wrong value.
    """
    db = _db_session()
    raw = _seed(db)
    request = _request(raw)

    bodies = [
        provisioning_api.get_account("whmcs:7", request, db=db),
        provisioning_api.create_login_url("whmcs:7", request, db=db),
        provisioning_api.change_package(
            "whmcs:7",
            provisioning_api.ProvisioningPackageChange(package_id=1),
            request,
            db=db,
        ),
    ]

    for body in bodies:
        keys = set(body.keys())
        assert not {"success", "data"}.issubset(keys), f"envelope collision in {keys}"


def test_terminate_does_not_back_up_unless_asked():
    """The shared module sends a plain DELETE with no query string.

    The backup runs inline, so defaulting it on would push the request past
    the module's HTTP timeout on any sizeable account.
    """
    import inspect

    declared = inspect.signature(provisioning_api.terminate).parameters["backup"].default
    # FastAPI resolves the Query(...) marker to its default when the caller
    # omits the parameter, which is exactly what the module does.
    assert declared.default is False

    service_default = inspect.signature(provisioning_service.terminate_account).parameters["backup"].default
    assert service_default is False
