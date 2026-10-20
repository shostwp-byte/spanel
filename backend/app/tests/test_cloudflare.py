"""Cloudflare token handling for wildcard SSL."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import CloudflareCredential
from app.services import cloudflare


def _db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _fake_get(zones):
    seen = {z.lower() for z in zones}

    def _get(path, token):
        name = path.split("name=", 1)[1].split("&", 1)[0]
        return {"success": True, "result": [{"name": name}] if name in seen else []}

    return _get


def test_zone_for_domain_picks_the_most_specific_real_zone(monkeypatch):
    monkeypatch.setattr(cloudflare, "_get", _fake_get(["example.com", "shop.example.com"]))
    assert cloudflare.zone_for_domain("t", "blog.shop.example.com") == "shop.example.com"
    assert cloudflare.zone_for_domain("t", "example.com") == "example.com"
    assert cloudflare.zone_for_domain("t", "sub.example.com") == "example.com"


def test_zone_for_domain_raises_when_no_zone_covers_it(monkeypatch):
    monkeypatch.setattr(cloudflare, "_get", _fake_get(["other.net"]))
    with pytest.raises(cloudflare.CloudflareError):
        cloudflare.zone_for_domain("t", "example.com")


def test_verify_token_rejects_a_non_active_token(monkeypatch):
    monkeypatch.setattr(
        cloudflare, "_get", lambda path, token: {"success": True, "result": {"status": "disabled"}}
    )
    with pytest.raises(cloudflare.CloudflareError):
        cloudflare.verify_token("some-token")


def test_the_token_is_stored_encrypted_and_round_trips():
    db = _db()
    cloudflare.save_credential(db, "Example.com", "cf-secret-token-value")

    row = db.query(CloudflareCredential).filter_by(zone="example.com").one()
    assert "cf-secret-token-value" not in row.api_token  # ciphertext, not plaintext
    assert row.api_token.startswith("fernet:")
    assert cloudflare.get_token(db, "example.com") == "cf-secret-token-value"
    assert cloudflare.has_token(db, "example.com") is True

    cloudflare.save_credential(db, "example.com", "rotated-token")
    assert cloudflare.get_token(db, "example.com") == "rotated-token"
    assert db.query(CloudflareCredential).count() == 1
