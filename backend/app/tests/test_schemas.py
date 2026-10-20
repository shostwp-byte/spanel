import pytest
from pydantic import ValidationError

from app.schemas.schemas import PhpConfigUpdate, ProvisioningAccountCreate, UserPackageCreate, WebsiteCreate, WebsiteAliasCreate


def test_website_alias_create_accepts_redirect_mode():
    payload = WebsiteAliasCreate(domain="Alias.Example.Test", mode="redirect")

    assert payload.domain == "alias.example.test"
    assert payload.mode == "redirect"


def test_website_alias_create_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        WebsiteAliasCreate(domain="alias.example.test", mode="mirror")


def test_default_php_version_and_memory_limit_are_84_and_1024m():
    assert WebsiteCreate(domain="example.test").php_version == "8.4"
    config = PhpConfigUpdate()
    assert config.php_version == "8.4"
    assert config.memory_limit == "1024M"


def test_user_package_create_normalizes_name():
    payload = UserPackageCreate(name="  Starter   Plus  ", website_limit=10, storage_limit_mb=2048)

    assert payload.name == "Starter Plus"
    assert payload.website_limit == 10
    assert payload.storage_limit_mb == 2048

def test_provisioning_account_allows_missing_email_and_domain():
    payload = ProvisioningAccountCreate(
        external_id="whmcs:123",
        username="bp_123",
        password="strong-password-123",
        package_id=1,
    )

    assert payload.email is None
    assert payload.domain is None

def test_provisioning_account_rejects_wordpress_without_domain():
    with pytest.raises(ValidationError):
        ProvisioningAccountCreate(
            external_id="whmcs:123",
            username="bp_123",
            password="strong-password-123",
            package_id=1,
            install_wordpress=True,
        )
