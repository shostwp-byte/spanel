"""Unit and integration tests for the End-User Laravel Manager module."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

try:
    import pytest
except ImportError:
    class _RaisesContext:
        def __init__(self, expected_exc):
            self.expected_exc = expected_exc
            self.value = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                raise AssertionError(f"Expected {self.expected_exc.__name__} to be raised")
            if issubclass(exc_type, self.expected_exc):
                self.value = exc_val
                return True
            return False

    pytest = SimpleNamespace(raises=lambda exc: _RaisesContext(exc))

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import laravel_manager as lm


def _make_request(headers: dict[str, str] | None = None):
    return SimpleNamespace(headers=headers or {})


def test_require_end_user_laravel_access_blocks_admin_without_user_view_header():
    admin = SimpleNamespace(id=1, username="admin", role="admin")
    with pytest.raises(HTTPException) as exc:
        lm._require_end_user_laravel_access(admin, _make_request({}))
    assert exc.value.status_code == 403

    # Allowed when switching to User view level via X-Access-Role: user
    lm._require_end_user_laravel_access(admin, _make_request({"x-access-role": "user"}))

    # Allowed directly for end_user / customer
    customer = SimpleNamespace(id=2, username="shostc", role="end_user")
    lm._require_end_user_laravel_access(customer, _make_request({}))


def test_parse_and_update_env_preserves_comments_and_quotes_special_values():
    raw = (
        "# Application Settings\n"
        "APP_NAME=Laravel\n"
        "APP_ENV=local\n"
        "APP_KEY=\n"
        "APP_DEBUG=true\n"
    )
    parsed = lm._parse_env_details(raw)
    assert parsed["app_name"] == "Laravel"
    assert parsed["app_debug"] is True

    updated = lm._update_env_text(
        raw,
        {
            "APP_NAME": "SHost ERP Portal",
            "APP_ENV": "production",
            "APP_DEBUG": False,
            "REDIS_HOST": "127.0.0.1",
        },
    )
    reparsed = lm._parse_env_details(updated)
    assert "# Application Settings" in updated
    assert reparsed["app_name"] == "SHost ERP Portal"
    assert reparsed["app_env"] == "production"
    assert reparsed["app_debug"] is False
    assert reparsed["redis_host"] == "127.0.0.1"


def test_build_instance_summary_reads_composer_and_env_metadata(tmp_path):
    site_root = tmp_path / "domains" / "crm.shost.vn"
    app_dir = site_root / "public_html"
    (app_dir / "public").mkdir(parents=True)
    (app_dir / "storage" / "logs").mkdir(parents=True)
    (app_dir / "bootstrap" / "cache").mkdir(parents=True)
    (app_dir / "artisan").write_text("#!/usr/bin/env php\n<?php\n", encoding="utf-8")
    (app_dir / "composer.json").write_text(
        json.dumps(
            {
                "require": {
                    "php": "^8.2",
                    "laravel/framework": "^11.9",
                    "laravel/sanctum": "^4.0",
                }
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "composer.lock").write_text(
        json.dumps(
            {
                "packages": [
                    {"name": "laravel/framework", "version": "v11.27.2"},
                    {"name": "laravel/sanctum", "version": "v4.0.3"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (app_dir / ".env").write_text(
        'APP_NAME="SHost CRM"\n'
        "APP_ENV=production\n"
        "APP_KEY=base64:dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=\n"
        "APP_DEBUG=false\n"
        "DB_DATABASE=shostc_crm\n"
        "DB_USERNAME=shostc_user\n"
        "CACHE_STORE=redis\n"
        "QUEUE_CONNECTION=redis\n",
        encoding="utf-8",
    )

    site = SimpleNamespace(
        id=10,
        owner_id=2,
        domain="crm.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        created_at=None,
    )
    owner = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = owner

    with patch.object(lm.wordpress_live, "resolve_owned_wp_database", return_value=None), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]):
        summary = lm._build_instance_summary(site, db, {"instances": {}})

    assert summary is not None
    assert summary["domain"] == "crm.shost.vn"
    assert summary["laravel_version"] == "11.27.2"
    assert summary["app_name"] == "SHost CRM"
    assert summary["app_env"] == "production"
    assert summary["app_debug"] is False
    assert summary["app_key_set"] is True
    assert summary["public_docroot_active"] is True
    assert summary["db_name"] == "shostc_crm"
    assert summary["cache_driver"] == "redis"
    assert summary["queue_connection"] == "redis"
    assert summary["packages_count"] == 2


def test_install_laravel_instance_configures_public_docroot_env_and_scheduler(tmp_path):
    site_root = tmp_path / "domains" / "erp.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    site = SimpleNamespace(
        id=15,
        owner_id=2,
        domain="erp.shost.vn",
        root_path=str(site_root),
        document_root="public_html",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="none",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = lambda: site

    payload = lm.LaravelInstallPayload(
        website_id=15,
        app_name="SHost ERP",
        laravel_version="11.x",
        php_version="8.3",
        install_source="composer",
        db_mode="auto",
        cache_driver="redis",
        queue_connection="redis",
        enable_scheduler=True,
        enable_queue_worker=True,
        queue_workers=2,
    )

    state_holder = {"instances": {}}
    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch("app.api.databases.enforce_user_database_limit"), \
            patch.object(lm.site_users, "ensure_site_runtime", return_value="shostc"), \
            patch.object(
                lm.mariadb,
                "create_database",
                return_value={
                    "db_name": "lv_erpshost_a1",
                    "db_user": "u_erpshost_a1",
                    "db_password": "StrongDbPass123!",
                },
            ), \
            patch.object(lm, "_run_as_site_user", return_value=(0, "OK")), \
            patch.object(lm.wordpress_live, "resolve_owned_wp_database", return_value=None), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm.cron, "add_cron") as mock_add_cron, \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"):
        res = lm.install_laravel_instance(payload, db=db, current_user=user)

    assert site.document_root == "public_html/public"
    assert site.nginx_rewrite_mode == "laravel"
    assert (app_dir / "artisan").is_file()
    assert (app_dir / "composer.json").is_file()
    assert (app_dir / "public" / "index.php").is_file()
    assert (app_dir / ".env").is_file()
    assert (app_dir / "spanel-schedule-cron.php").is_file()
    mock_add_cron.assert_called_once()

    env_details = lm._parse_env_details((app_dir / ".env").read_text(encoding="utf-8"))
    assert env_details["app_name"] == "SHost ERP"
    assert env_details["app_env"] == "production"
    assert env_details["app_debug"] is False
    assert env_details["app_key"].startswith("base64:")
    assert env_details["db_name"] == "lv_erpshost_a1"
    assert res["instance"]["public_docroot_active"] is True
    assert res["instance"]["queue_worker"]["enabled"] is True
    assert res["instance"]["queue_worker"]["workers"] == 2


def test_artisan_command_whitelists_and_blocks_disallowed_commands(tmp_path):
    site_root = tmp_path / "domains" / "api.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)
    (app_dir / "artisan").write_text("#!/usr/bin/env php\n<?php\n", encoding="utf-8")
    (app_dir / ".env").write_text(
        "APP_NAME=API\nAPP_ENV=production\nAPP_DEBUG=false\nAPP_KEY=base64:dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=\n",
        encoding="utf-8",
    )

    site = SimpleNamespace(
        id=20,
        owner_id=2,
        domain="api.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    state_holder = {"instances": {}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm.wordpress_live, "resolve_owned_wp_database", return_value=None), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm, "_run_as_site_user", return_value=(0, "INFO  Nothing to migrate.")):
        res = lm.run_artisan_command(
            20,
            lm.LaravelArtisanPayload(command="migrate"),
            db=db,
            current_user=user,
        )
        assert "Nothing to migrate" in res["output"]

        # Disallowed command like db:wipe or tinker must raise 400
        with pytest.raises(HTTPException) as exc:
            lm.run_artisan_command(
                20,
                lm.LaravelArtisanPayload(command="db:wipe"),
                db=db,
                current_user=user,
            )
        assert exc.value.status_code == 400


def test_update_laravel_env_creates_backup_and_clears_config_cache(tmp_path):
    site_root = tmp_path / "domains" / "app.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)
    (app_dir / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (app_dir / ".env").write_text(
        "APP_NAME=OldName\nAPP_ENV=local\nAPP_DEBUG=true\nAPP_KEY=base64:dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=\n",
        encoding="utf-8",
    )

    site = SimpleNamespace(
        id=21,
        owner_id=2,
        domain="app.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    state_holder = {"instances": {}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm.wordpress_live, "resolve_owned_wp_database", return_value=None), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm, "_run_as_site_user", return_value=(0, "Configuration cache cleared!")):
        resp = lm.update_laravel_env(
            21,
            lm.LaravelEnvUpdatePayload(
                app_name="SHost Enterprise",
                app_env="production",
                app_debug=False,
                cache_driver="redis",
            ),
            db=db,
            current_user=user,
        )

    assert (app_dir / ".env.bak").is_file()
    assert resp["parsed"]["app_name"] == "SHost Enterprise"
    assert resp["parsed"]["app_env"] == "production"
    assert resp["parsed"]["app_debug"] is False
    assert resp["parsed"]["cache_driver"] == "redis"


def test_composer_and_security_hardening_endpoints(tmp_path):
    site_root = tmp_path / "domains" / "sec.shost.vn"
    app_dir = site_root / "public_html"
    (app_dir / "public").mkdir(parents=True)
    (app_dir / "storage" / "app" / "public").mkdir(parents=True)
    (app_dir / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (app_dir / "composer.json").write_text(
        json.dumps({"require": {"php": "^8.2", "laravel/framework": "^11.0"}}),
        encoding="utf-8",
    )
    (app_dir / ".env").write_text(
        "APP_NAME=App\nAPP_ENV=local\nAPP_DEBUG=true\nAPP_KEY=short\n",
        encoding="utf-8",
    )

    site = SimpleNamespace(
        id=22,
        owner_id=2,
        domain="sec.shost.vn",
        root_path=str(site_root),
        document_root="public_html",
        linux_user="shostc",
        php_version="8.3",
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="none",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    state_holder = {"instances": {}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm.wordpress_live, "resolve_owned_wp_database", return_value=None), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm, "_run_as_site_user", return_value=(0, "Installed package")):
        # Invalid package name rejected
        with pytest.raises(HTTPException) as exc_pkg:
            lm.run_composer_action(
                22,
                lm.LaravelComposerPayload(action="require", package="evil; rm -rf /"),
                db=db,
                current_user=user,
            )
        assert exc_pkg.value.status_code == 400

        # Valid package require updates composer.json
        comp_res = lm.run_composer_action(
            22,
            lm.LaravelComposerPayload(action="require", package="spatie/laravel-permission:^6.10"),
            db=db,
            current_user=user,
        )
        assert any(p["name"] == "spatie/laravel-permission" for p in comp_res["instance"]["packages"])

        # Security hardening fixes docroot, APP_DEBUG, APP_KEY, and storage .htaccess
        sec_res = lm.manage_laravel_security(
            22,
            lm.LaravelSecurityPayload(action="secure"),
            db=db,
            current_user=user,
        )

    assert site.document_root == "public_html/public"
    assert site.nginx_rewrite_mode == "laravel"
    assert (app_dir / "storage" / "app" / "public" / ".htaccess").is_file()
    env_after = lm._parse_env_details((app_dir / ".env").read_text(encoding="utf-8"))
    assert env_after["app_debug"] is False
    assert env_after["app_key"].startswith("base64:")
    assert sec_res["instance"]["security_passed"] == sec_res["instance"]["security_total"]


if __name__ == "__main__":
    test_require_end_user_laravel_access_blocks_admin_without_user_view_header()
    test_parse_and_update_env_preserves_comments_and_quotes_special_values()
    with tempfile.TemporaryDirectory() as _d1:
        test_build_instance_summary_reads_composer_and_env_metadata(Path(_d1))
    with tempfile.TemporaryDirectory() as _d2:
        test_install_laravel_instance_configures_public_docroot_env_and_scheduler(Path(_d2))
    with tempfile.TemporaryDirectory() as _d3:
        test_artisan_command_whitelists_and_blocks_disallowed_commands(Path(_d3))
    with tempfile.TemporaryDirectory() as _d4:
        test_update_laravel_env_creates_backup_and_clears_config_cache(Path(_d4))
    with tempfile.TemporaryDirectory() as _d5:
        test_composer_and_security_hardening_endpoints(Path(_d5))
    print("ALL LARAVEL MANAGER TESTS PASSED")
