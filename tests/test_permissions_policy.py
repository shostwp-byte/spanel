"""Regression tests for anti-conflict and configurable HTTP Permissions-Policy."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import nodejs_manager as nm
from app.api import websites as websites_api
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.models.entities import User, UserPackage, Website
from app.services import addons, nginx, shell
from app.services.shell import CommandResult


def _setup_test_db():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False
    )
    return eng, SessionLocal


def _build_client(SessionLocal, current_user_holder: dict) -> TestClient:
    app = FastAPI()
    app.include_router(websites_api.router, prefix="/api")
    app.include_router(nm.router, prefix="/api")

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _override_user():
        return current_user_holder["user"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    return TestClient(app)


def test_normalize_permissions_policy_preserves_other_directives_and_blocks_injection():
    assert nginx.normalize_permissions_policy("") == ""
    assert nginx.normalize_permissions_policy("   ") == ""
    assert nginx.normalize_permissions_policy("default") == ""

    partial = nginx.normalize_permissions_policy("microphone=(self)")
    assert partial == (
        "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), "
        "bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()"
    )

    from_dict = nginx.normalize_permissions_policy(
        directives={"microphone": "(self)", "camera": "(self)", "display-capture": "(self)"}
    )
    assert "microphone=(self)" in from_dict
    assert "camera=(self)" in from_dict
    assert "display-capture=(self)" in from_dict
    assert "geolocation=()" in from_dict
    assert "payment=()" in from_dict

    with_origin = nginx.normalize_permissions_policy(
        'microphone=(self "https://meet.example.com" https://cdn.example.com:8443)'
    )
    assert 'microphone=(self "https://meet.example.com" "https://cdn.example.com:8443")' in with_origin

    for bad in (
        "microphone=(self); add_header X-Evil 1",
        "microphone=(self)\nadd_header X-Evil 1",
        "microphone=(self)'",
        "microphone=$evil",
        "unknown-sensor=(self)",
        "microphone=(self), microphone=()",
        'microphone=("javascript:alert(1)")',
    ):
        with pytest.raises(ValueError):
            nginx.normalize_permissions_policy(bad)


def test_render_vhost_dedup_and_upstream_aware_proxy_headers(tmp_path):
    site_root = tmp_path / "digipower.cloud"
    site_root.mkdir(parents=True)

    with patch.object(nginx.site_users, "is_site_root_for_domain", return_value=True):
        # 1. Proxied app with default SPanel policy emits dynamic $spanel_pp_* header + proxy_hide_header
        proxy_default = nginx.render_vhost(
            domain="digipower.cloud",
            root_path=str(site_root),
            app_type="application",
            app_port=21000,
        )
        assert "proxy_hide_header Permissions-Policy;" in proxy_default
        assert proxy_default.count("add_header Permissions-Policy ") == 1
        for feat in nginx.DEFAULT_PERMISSIONS_POLICY_FEATURES:
            assert f"$spanel_pp_{feat}" in proxy_default

        # 2. Proxied app with explicit microphone=(self) emits single unified policy header
        proxy_mic = nginx.render_vhost(
            domain="digipower.cloud",
            root_path=str(site_root),
            app_type="application",
            app_port=21000,
            permissions_policy="microphone=(self)",
        )
        assert "proxy_hide_header Permissions-Policy;" in proxy_mic
        assert proxy_mic.count("add_header Permissions-Policy ") == 1
        assert (
            "add_header Permissions-Policy 'camera=(), microphone=(self), geolocation=(), "
            "payment=(), usb=(), bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()' always;"
            in proxy_mic
        )
        assert "$spanel_pp_microphone" not in proxy_mic

        # 3. WordPress and PHP templates include fastcgi_hide_header Permissions-Policy;
        for app_t in ("wordpress", "php"):
            rendered = nginx.render_vhost(
                domain="digipower.cloud",
                root_path=str(site_root),
                app_type=app_t,
                php_version="8.4",
                permissions_policy="camera=(self), microphone=(self)",
            )
            assert "fastcgi_hide_header Permissions-Policy;" in rendered
            assert rendered.count("add_header Permissions-Policy ") == 1
            assert "camera=(self), microphone=(self)" in rendered


def test_custom_nginx_permissions_policy_allowed_and_deduplicated(tmp_path, monkeypatch):
    # Still reject disallowed security/cookie headers in Custom Nginx
    for forbidden in (
        'add_header Strict-Transport-Security "max-age=0";',
        'add_header Set-Cookie "a=b";',
        'add_header X-Frame-Options "ALLOWALL";',
        'add_header Permissions-Policy "microphone=(self); evil" always;',
    ):
        with pytest.raises(ValueError):
            nginx.validate_custom_nginx(forbidden)

    custom_pp = (
        'add_header Permissions-Policy "camera=(), microphone=(self), geolocation=(), '
        'payment=(), usb=(), bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()" always;'
    )
    assert nginx.validate_custom_nginx(custom_pp) == custom_pp
    assert nginx.custom_sets_server_permissions_policy(custom_pp) is True
    assert nginx.custom_sets_server_permissions_policy("location /sub { " + custom_pp + " }") is False

    site_root = tmp_path / "digipower.cloud"
    site_root.mkdir(parents=True)
    with patch.object(nginx.site_users, "is_site_root_for_domain", return_value=True):
        # When custom include defines server-scope Permissions-Policy, main vhost must NOT emit a second one
        rendered = nginx.render_vhost(
            domain="digipower.cloud",
            root_path=str(site_root),
            app_type="application",
            app_port=21000,
            custom_directives=custom_pp,
        )
        assert "add_header Permissions-Policy " not in rendered
        assert "proxy_hide_header Permissions-Policy;" in rendered

    # Also test update_custom_block removes and restores server-level header cleanly
    sites_avail = tmp_path / "sites-available"
    sites_avail.mkdir()
    monkeypatch.setattr(settings, "nginx_sites_available", str(sites_avail))
    monkeypatch.setattr(settings, "command_dry_run", False)
    with patch.object(nginx.site_users, "is_site_root_for_domain", return_value=True):
        vhost_text = nginx.render_vhost(
            domain="digipower.cloud",
            root_path=str(site_root),
            app_type="application",
            app_port=21000,
        )
    vhost_file = sites_avail / "digipower.cloud.conf"
    vhost_file.write_text(vhost_text, encoding="utf-8")

    with patch.object(nginx, "_write_custom_include", return_value="/tmp/custom.conf"), \
         patch.object(nginx.shell, "privileged", return_value=CommandResult("ok", 0, "", "")):
        nginx.update_custom_block("digipower.cloud", custom_pp)
        after_custom = vhost_file.read_text(encoding="utf-8")
        assert "add_header Permissions-Policy " not in after_custom

        nginx.update_custom_block("digipower.cloud", "")
        after_clear = vhost_file.read_text(encoding="utf-8")
        assert "$spanel_pp_microphone" in after_clear


def test_nginx_upgrade_map_blocks_exist_and_evaluate_upstream_headers():
    """Verify helper, install.sh and update.sh all ship complete $spanel_pp_* maps
    and validate the PCRE behavior against upstream Permissions-Policy strings."""
    scripts = [
        REPO_ROOT / "installer" / "files" / "spanel-helper.sh",
        REPO_ROOT / "installer" / "install.sh",
        REPO_ROOT / "installer" / "update.sh",
    ]
    extracted_maps = []
    for path in scripts:
        txt = path.read_text(encoding="utf-8")
        assert "$spanel_pp_microphone" in txt, f"Missing $spanel_pp_microphone map in {path.name}"
        m = re.search(
            r"map \$http_upgrade \$connection_upgrade[\s\S]+?^NGINX$",
            txt,
            re.M,
        )
        assert m is not None, f"Could not extract 00-spanel-upgrade-map block from {path.name}"
        extracted_maps.append(m.group(0))

    assert extracted_maps[0] == extracted_maps[1] == extracted_maps[2], (
        "00-spanel-upgrade-map.conf heredoc drifted between spanel-helper.sh, install.sh and update.sh"
    )

    map_block = extracted_maps[0]
    # Extract every feature map regex and simulate Nginx map evaluation
    map_defs = re.findall(
        r'map \$upstream_http_permissions_policy (\$spanel_pp_[a-z]+)\s*\{\s*'
        r'default "([^"]+)";\s*'
        r'"~([^"]+)" \$spanel_pp_val;\s*\}',
        map_block,
    )
    assert len(map_defs) == 9

    def eval_nginx_pp(upstream_header: str) -> str:
        parts = []
        for _, default_val, raw_re in map_defs:
            # Convert Nginx regex flags: strip leading `*` (case-insensitive) and `\x22`
            py_re = raw_re.lstrip("*").replace(r"\x22", '"')
            # Strip PCRE named group syntax `(?<spanel_pp_val>...)` -> `(?P<spanel_pp_val>...)`
            py_re = py_re.replace("(?<spanel_pp_val>", "(?P<spanel_pp_val>")
            m = re.search(py_re, upstream_header, re.IGNORECASE)
            parts.append(m.group("spanel_pp_val") if m else default_val)
        return ", ".join(parts)

    assert eval_nginx_pp("") == nginx.DEFAULT_PERMISSIONS_POLICY
    assert eval_nginx_pp("microphone=(self)") == (
        "camera=(), microphone=(self), geolocation=(), payment=(), usb=(), "
        "bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()"
    )
    assert eval_nginx_pp('camera=(self), microphone=(self "https://a.example.com")') == (
        'camera=(self), microphone=(self "https://a.example.com"), geolocation=(), '
        "payment=(), usb=(), bluetooth=(), magnetometer=(), gyroscope=(), accelerometer=()"
    )
    # Disallowed characters in upstream value fall back safely to default ()
    assert eval_nginx_pp("microphone=(self); evil=1").startswith("camera=(), microphone=(),")


def test_api_permissions_policy_persistence_and_tenant_isolation(tmp_path, monkeypatch):
    eng, SessionLocal = _setup_test_db()
    monkeypatch.setattr(addons, "is_installed", lambda slug: True)
    if hasattr(settings, "nodejs_manager_enabled"):
        monkeypatch.setattr(settings, "nodejs_manager_enabled", True)

    with SessionLocal() as db:
        pkg = UserPackage(name="std", node_apps_limit=5, node_app_memory_mb=1024)
        db.add(pkg)
        db.flush()
        alice = User(
            username="alice",
            email="alice@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg.id,
        )
        bob = User(
            username="bob",
            email="bob@example.com",
            hashed_password="x",
            role="end_user",
            package_id=pkg.id,
        )
        db.add_all([alice, bob])
        db.flush()

        site_root = tmp_path / "digipower.cloud"
        (site_root / "public_html").mkdir(parents=True)
        (site_root / "public_html" / "package.json").write_text('{"name":"app","dependencies":{"next":"16.0.0"}}', encoding="utf-8")

        site = Website(
            domain="digipower.cloud",
            owner_id=alice.id,
            root_path=str(site_root),
            document_root="public_html",
            linux_user="alice",
            app_type="application",
            status="active",
        )
        db.add(site)
        db.commit()
        db.refresh(site)
        site_id = site.id
        alice_ref, bob_ref = alice, bob

    holder = {"user": bob_ref}
    client = _build_client(SessionLocal, holder)

    # 1. Cross-tenant isolation: Bob cannot modify Alice's site
    resp_forbid = client.patch(
        f"/api/websites/{site_id}/permissions-policy",
        json={"permissions_policy": "microphone=(self)"},
    )
    assert resp_forbid.status_code in (403, 404)

    holder["user"] = alice_ref
    written_vhosts = []

    def fake_rewrite(domain, root_path, **kwargs):
        written_vhosts.append((domain, kwargs))
        return f"/etc/nginx/sites-available/{domain}.conf"

    with patch.object(nginx, "rewrite_vhost", side_effect=fake_rewrite), \
         patch.object(nm, "_resolve_nodejs_paths", return_value=("alice", site_root, site_root / "public_html")), \
         patch.object(nm, "_query_nodejs_worker_status", return_value={"status": "running", "port": 21000}):
        # 2. End user configures microphone=(self) via Node.js Manager API
        r_nm = client.post(
            f"/api/nodejs-manager/instances/{site_id}/permissions-policy",
            json={"permissions_policy": "microphone=(self)"},
        )
        assert r_nm.status_code == 200, r_nm.text
        data_nm = r_nm.json()
        assert "microphone=(self)" in data_nm["permissions_policy"]
        assert "camera=()" in data_nm["permissions_policy"]
        assert written_vhosts[-1][1]["permissions_policy"] == data_nm["permissions_policy"]

        # 3. Subsequent _sync_nodejs_nginx_vhost preserves website.permissions_policy
        with SessionLocal() as db:
            db_site = db.query(Website).filter(Website.id == site_id).first()
            nm._sync_nodejs_nginx_vhost(db_site, "alice", site_root, 21000, strict=True)
        assert "microphone=(self)" in written_vhosts[-1][1]["permissions_policy"]

        # 4. Resetting back to default via PATCH /websites/{id}/permissions-policy clears override
        r_reset = client.patch(
            f"/api/websites/{site_id}/permissions-policy",
            json={"permissions_policy": ""},
        )
        assert r_reset.status_code == 200, r_reset.text
        assert r_reset.json()["permissions_policy"] == ""
        assert written_vhosts[-1][1]["permissions_policy"] == ""
