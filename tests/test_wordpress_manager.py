"""Comprehensive regression & behavioral tests for the WordPress Manager overhaul (v1.0.8)."""
import asyncio
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api import wordpress_manager
from app.core.config import settings
from app.core.database import Base
from app.models.entities import DatabaseAccount, User, Website
from app.services import cron, mariadb, nginx, teardown, wordpress, wordpress_live


def _make_db():
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=test_engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    return SessionLocal(), test_engine


def _seed_wp_files(wp_dir: Path, db_name: str = "shostc_wp1", db_user: str = "shostc_u1", table_prefix: str = "wp_sp_"):
    (wp_dir / "wp-includes").mkdir(parents=True, exist_ok=True)
    (wp_dir / "wp-content" / "plugins").mkdir(parents=True, exist_ok=True)
    (wp_dir / "wp-content" / "themes" / "twentytwentyfive").mkdir(parents=True, exist_ok=True)
    (wp_dir / "wp-includes" / "version.php").write_text(
        "<?php\n$wp_version = '6.7.2';\n",
        encoding="utf-8",
    )
    (wp_dir / "wp-config.php").write_text(
        "<?php\n"
        f"define( 'DB_NAME', '{db_name}' );\n"
        f"define( 'DB_USER', '{db_user}' );\n"
        "define( 'DB_PASSWORD', 'SecretPass123!' );\n"
        "define( 'DB_HOST', 'localhost' );\n"
        f"$table_prefix = '{table_prefix}';\n"
        "require_once ABSPATH . 'wp-settings.php';\n",
        encoding="utf-8",
    )
    (wp_dir / "wp-content" / "themes" / "twentytwentyfive" / "style.css").write_text(
        "/*\nTheme Name: Twenty Twenty-Five\nVersion: 1.1\n*/\n",
        encoding="utf-8",
    )


def _make_fake_privileged(fastcgi_clears: list):
    """Simulate spanel-helper filesystem commands accurately on local disk."""

    def fake_privileged(command_key: str, *, helper_args=None, fallback=None, check=True, input=None, **kwargs):
        args = list(helper_args or [])
        if command_key == "site-file-write":
            # args: [linux_user, site_root, rel_path, mode]
            site_root = Path(args[1])
            rel_path = args[2]
            target = site_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(input if input is not None else "", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command_key == "site-file-read":
            site_root = Path(args[1])
            rel_path = args[2]
            target = site_root / rel_path
            if not target.is_file():
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            return SimpleNamespace(returncode=0, stdout=target.read_text(encoding="utf-8"), stderr="")
        if command_key == "site-file-install":
            # args: [linux_user, site_root, rel_to_root, tmp_upload]
            site_root = Path(args[1])
            rel_path = args[2]
            src_tmp = Path(args[3])
            target = site_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src_tmp.read_bytes())
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command_key == "rm-site":
            # args: [linux_user, site_root, target_path]
            target = Path(args[2] if len(args) >= 3 else args[0])
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink(missing_ok=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command_key == "site-document-root-ensure":
            # args: [linux_user, site_root, rel_dir]
            site_root = Path(args[1])
            rel_dir = args[2]
            doc_root = site_root / rel_dir
            doc_root.mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command_key == "fastcgi-cache-clear":
            fastcgi_clears.append(True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command_key == "terminal-exec":
            # args: [linux_user, site_root, subcmd, ...]
            if len(args) >= 4 and args[2] == "cat":
                target = Path(args[1]) / args[3]
                if not target.is_file():
                    return SimpleNamespace(returncode=1, stdout="", stderr="not found")
                return SimpleNamespace(returncode=0, stdout=target.read_text(encoding="utf-8"), stderr="")
            if len(args) >= 4 and args[2] == "stat":
                target = Path(args[1]) / args[-1]
                if not target.exists():
                    return SimpleNamespace(returncode=1, stdout="", stderr="not found")
                return SimpleNamespace(returncode=0, stdout="640\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="640\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_privileged


def test_static_zero_panel_unlink_and_frontend_contract():
    api_src = (REPO_ROOT / "backend" / "app" / "api" / "wordpress_manager.py").read_text(encoding="utf-8")
    assert ".unlink(" not in api_src, "wordpress_manager.py must not call Path.unlink() directly on tenant files"

    fe_src = (REPO_ROOT / "frontend" / "src" / "pages" / "WordPressManager.jsx").read_text(encoding="utf-8")
    for required_token in (
        "useMemo",
        "openRemoveModal",
        "handleConfirmRemoveInstances",
        "removeDropDb",
        "openMaintenanceSetupModal",
        "handleUploadMaintenanceBackground",
        "/maintenance-background",
        "MAINTENANCE_PRESET_OPTIONS",
        "checksumResultModal",
        "applySetModal",
        "searchReplaceOutput",
    ):
        assert required_token in fe_src, f"Missing expected frontend symbol/feature in WordPressManager.jsx: {required_token}"


def test_detach_vs_remove_and_scan_no_resurrection_live_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        state_file = tmp / "wordpress_toolkit.json"
        home_base = tmp / "home"
        site_root = home_base / "shostc" / "shost.com.vn"
        wp_dir = site_root / "public_html"
        snap_dir = site_root / "wp-snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "snap-old.tar.gz").write_bytes(b"dummy-snapshot")
        _seed_wp_files(wp_dir, db_name="shostc_wp_live", db_user="shostc_u_live")

        db, test_engine = _make_db()
        owner = User(
            username="shostc",
            email="shostc@shost.com.vn",
            hashed_password="x",
            role="user",
            is_active=True,
        )
        db.add(owner)
        db.commit()

        site = Website(
            domain="shost.com.vn",
            root_path=str(site_root),
            document_root="public_html",
            linux_user="shostc",
            php_version="8.4",
            app_type="wordpress",
            nginx_rewrite_mode="front_controller",
            ssl_enabled=True,
            owner_id=owner.id,
        )
        db.add(site)
        db.commit()

        db_acc = DatabaseAccount(
            db_name="shostc_wp_live",
            db_user="shostc_u_live",
            db_password="x",
            owner_id=owner.id,
            website_id=None,  # Even when scanned / not linked via website_id!
        )
        db.add(db_acc)
        db.commit()

        fastcgi_clears = []
        fake_priv = _make_fake_privileged(fastcgi_clears)
        dropped_dbs = []
        vhost_writes = []

        orig_dry = settings.command_dry_run
        settings.command_dry_run = False
        try:
            with (
                patch.object(wordpress_live, "engine", test_engine),
                patch.object(wordpress_live, "toolkit_state_path", return_value=state_file),
                patch.object(wordpress_manager.site_users, "HOME_ROOT", home_base),
                patch.object(wordpress_live.shell, "privileged", side_effect=fake_priv),
                patch.object(wordpress_manager.shell, "privileged", side_effect=fake_priv),
                patch.object(
                    wordpress_manager,
                    "_run_wp_cli",
                    return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                ),
                patch.object(
                    wordpress_live,
                    "query_wp_db_live_meta",
                    return_value={"site_title": "SHOST Live", "admin_user": "wpadmin", "admin_email": "admin@shost.com.vn"},
                ),
                patch.object(mariadb, "drop_database", side_effect=lambda d, u: dropped_dbs.append((d, u))),
                patch("app.api.websites._rewrite_website_vhost", side_effect=lambda *a, **kw: vhost_writes.append((a, kw))),
            ):
                # 1. Initial Scan discovers the WordPress site on disk
                scan1 = wordpress_manager.scan_wp_instances(db=db, current_user=owner)
                assert scan1["discovered"] == 1
                listed1 = wordpress_manager.list_wp_instances(db=db, current_user=owner)
                assert len(listed1["instances"]) == 1

                # 2. Detach ONLY hides from WordPress Manager; files & DB stay intact, and Scan re-discovers it
                wordpress_manager.detach_wp_instance(website_id=site.id, db=db, current_user=owner)
                assert len(wordpress_manager.list_wp_instances(db=db, current_user=owner)["instances"]) == 0
                assert (wp_dir / "wp-config.php").is_file(), "Detach must NOT delete wp-config.php"
                assert db.query(DatabaseAccount).filter_by(db_name="shostc_wp_live").first() is not None

                scan2 = wordpress_manager.scan_wp_instances(db=db, current_user=owner)
                assert scan2["discovered"] == 1
                assert len(wordpress_manager.list_wp_instances(db=db, current_user=owner)["instances"]) == 1

                # 3. Remove (default drop_database=True) wipes public_html + wp-snapshots AND drops MySQL Database!
                rem_res = wordpress_manager.remove_wp_instance(
                    website_id=site.id,
                    drop_database=True,
                    db=db,
                    current_user=owner,
                )
                assert rem_res["dropped_database"] == "shostc_wp_live"
                assert dropped_dbs == [("shostc_wp_live", "shostc_u_live")]
                assert db.query(DatabaseAccount).filter_by(db_name="shostc_wp_live").first() is None
                assert not (wp_dir / "wp-config.php").exists(), "Remove must delete WordPress source files in public_html"
                assert not (wp_dir / "wp-includes").exists(), "Remove must delete wp-includes"
                assert wp_dir.is_dir(), "Empty public_html directory must be recreated cleanly for the domain"
                assert not snap_dir.exists(), "wp-snapshots directory must be purged on root WP removal"
                db.refresh(site)
                assert site.app_type == "php"
                assert site.nginx_rewrite_mode == "none"
                assert len(vhost_writes) >= 1
                assert len(fastcgi_clears) >= 1

                # 4. Subsequent Scan MUST NOT resurrect the deleted instance!
                scan3 = wordpress_manager.scan_wp_instances(db=db, current_user=owner)
                assert scan3["discovered"] == 0, f"Deleted instance resurrected on Scan: {scan3}"
                assert wordpress_manager.list_wp_instances(db=db, current_user=owner)["instances"] == []

                # 5. Subpath removal (/blog) with drop_database=False deletes ONLY public_html/blog, keeping root files & DB
                (wp_dir / "landing.html").write_text("<h1>Root Landing</h1>", encoding="utf-8")
                blog_dir = wp_dir / "blog"
                _seed_wp_files(blog_dir, db_name="shostc_wp_blog", db_user="shostc_u_blog")
                blog_db = DatabaseAccount(
                    db_name="shostc_wp_blog",
                    db_user="shostc_u_blog",
                    db_password="x",
                    owner_id=owner.id,
                )
                db.add(blog_db)
                db.commit()
                st = wordpress_live.load_toolkit_state_safe()
                st.setdefault("instances", {})[str(site.id)] = {"installed": True, "subpath": "/blog"}
                wordpress_live.save_toolkit_state_safe(st)

                rem_sub = wordpress_manager.remove_wp_instance(
                    website_id=site.id,
                    drop_database=False,
                    db=db,
                    current_user=owner,
                )
                assert rem_sub["dropped_database"] is None
                assert not blog_dir.exists(), "Subpath /blog directory must be completely removed"
                assert (wp_dir / "landing.html").is_file(), "Root landing.html outside /blog must remain intact"
                assert db.query(DatabaseAccount).filter_by(db_name="shostc_wp_blog").first() is not None
        finally:
            settings.command_dry_run = orig_dry
            db.close()
            test_engine.dispose()


def test_maintenance_mode_custom_message_background_upload_and_sso_bypass():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        state_file = tmp / "wordpress_toolkit.json"
        home_base = tmp / "home"
        site_root = home_base / "shostc" / "shost.com.vn"
        wp_dir = site_root / "public_html"
        _seed_wp_files(wp_dir)

        db, test_engine = _make_db()
        owner = User(
            username="shostc",
            email="shostc@shost.com.vn",
            hashed_password="x",
            role="user",
            is_active=True,
        )
        db.add(owner)
        db.commit()

        site = Website(
            domain="shost.com.vn",
            root_path=str(site_root),
            document_root="public_html",
            linux_user="shostc",
            php_version="8.4",
            app_type="wordpress",
            ssl_enabled=True,
            owner_id=owner.id,
        )
        db.add(site)
        db.commit()

        fastcgi_clears = []
        fake_priv = _make_fake_privileged(fastcgi_clears)

        orig_dry = settings.command_dry_run
        settings.command_dry_run = False
        try:
            with (
                patch.object(wordpress_live, "engine", test_engine),
                patch.object(wordpress_live, "toolkit_state_path", return_value=state_file),
                patch.object(wordpress_manager.site_users, "HOME_ROOT", home_base),
                patch.object(wordpress_live.shell, "privileged", side_effect=fake_priv),
                patch.object(wordpress_manager.shell, "privileged", side_effect=fake_priv),
                patch.object(
                    wordpress_manager,
                    "_run_wp_cli",
                    return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                ),
                patch.object(
                    wordpress_live,
                    "query_wp_db_live_meta",
                    return_value={"site_title": "SHOST Portal", "admin_user": "wpadmin", "admin_email": "admin@shost.com.vn"},
                ),
            ):
                # 1. Enable Maintenance Mode with custom title, multi-line message + XSS attempt, and preset
                custom_title = "Nâng cấp hạ tầng Cloud NVMe"
                custom_msg = "Hệ thống đang nâng cấp máy chủ định kỳ.\nQuay lại lúc 22:00! <script>alert(1)</script>"
                res_on = wordpress_manager.update_wp_toggles(
                    website_id=site.id,
                    payload=wordpress_manager.WpTogglesPayload(
                        maintenance_mode=True,
                        maintenance_title=custom_title,
                        maintenance_message=custom_msg,
                        maintenance_bg_preset="midnight_nebula",
                        maintenance_bg_overlay=65,
                    ),
                    db=db,
                    current_user=owner,
                )
                assert res_on["instance"]["toggles"]["maintenance_mode"] is True
                assert res_on["instance"]["maintenance_title"] == custom_title
                assert res_on["instance"]["maintenance_message"] == custom_msg
                assert res_on["instance"]["maintenance_bg_preset"] == "midnight_nebula"
                assert res_on["instance"]["maintenance_bg_overlay"] == 65

                dropin_php = (wp_dir / "wp-content" / "maintenance.php").read_text(encoding="utf-8")
                assert "503 Service Unavailable" in dropin_php
                assert "Retry-After: 3600" in dropin_php
                assert "Nâng cấp hạ tầng Cloud NVMe" in dropin_php
                assert "Hệ thống đang nâng cấp máy chủ định kỳ.<br>" in dropin_php
                assert "&lt;script&gt;alert(1)&lt;/script&gt;" in dropin_php
                assert "<script>alert(1)</script>" not in dropin_php
                assert "#1e1b4b" in dropin_php and "#3b0764" in dropin_php  # midnight_nebula gradient

                dot_maint = (wp_dir / ".maintenance").read_text(encoding="utf-8")
                assert "$upgrading = $__spanel_bypass ? 0 : time();" in dot_maint
                assert "spanel_maint_bypass" in dot_maint
                assert (wp_dir / "wp-content" / "mu-plugins" / "spanel-maintenance-bypass.php").is_file()
                assert len(fastcgi_clears) >= 1

                # 2. Disguised PHP upload MUST be rejected with HTTP 400
                bad_upload = UploadFile(filename="evil.png", file=io.BytesIO(b"<?php system($_GET['x']); ?>"))
                try:
                    asyncio.run(
                        wordpress_manager.upload_wp_maintenance_background(
                            website_id=site.id,
                            file=bad_upload,
                            db=db,
                            current_user=owner,
                        )
                    )
                    raise AssertionError("Expected HTTPException(400) for disguised PHP upload")
                except HTTPException as exc:
                    assert exc.status_code == 400

                # 3. Valid PNG magic-byte upload succeeds, installs into wp-content/maintenance-assets/, and updates maintenance.php
                png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
                good_upload = UploadFile(filename="banner.png", file=io.BytesIO(png_bytes))
                up_res = asyncio.run(
                    wordpress_manager.upload_wp_maintenance_background(
                        website_id=site.id,
                        file=good_upload,
                        db=db,
                        current_user=owner,
                    )
                )
                bg_url = up_res["bg_url"]
                assert bg_url.startswith("/wp-content/maintenance-assets/maintenance-bg-") and bg_url.endswith(".png")
                installed_asset = wp_dir / bg_url.lstrip("/")
                assert installed_asset.is_file()
                assert installed_asset.read_bytes() == png_bytes

                dropin_with_bg = (wp_dir / "wp-content" / "maintenance.php").read_text(encoding="utf-8")
                assert bg_url in dropin_with_bg
                assert "rgba(15, 23, 42, 0.65)" in dropin_with_bg

                # 4. 1-Click SSO login embeds matching spanel_maint_bypass HMAC cookie so Admin bypasses maintenance!
                sso_res = wordpress_manager.create_wp_sso_login(website_id=site.id, db=db, current_user=owner)
                assert "spanel_sso=" in sso_res["sso_url"]
                sso_php = (wp_dir / "wp-content" / "mu-plugins" / "spanel-sso-login.php").read_text(encoding="utf-8")
                st_now = wordpress_live.load_toolkit_state_safe()
                secret_hex = st_now["instances"][str(site.id)]["maintenance_bypass_secret"]
                expected_sig = wordpress_live.maintenance_bypass_token_for_secret(secret_hex)
                assert expected_sig in sso_php
                assert "spanel_maint_bypass" in sso_php

                # 5. Delete background image & turn OFF maintenance mode -> cleans .maintenance & bypass mu-plugin, preserves custom text
                wordpress_manager.delete_wp_maintenance_background(website_id=site.id, db=db, current_user=owner)
                assert not installed_asset.exists()

                res_off = wordpress_manager.update_wp_toggles(
                    website_id=site.id,
                    payload=wordpress_manager.WpTogglesPayload(maintenance_mode=False),
                    db=db,
                    current_user=owner,
                )
                assert res_off["instance"]["toggles"]["maintenance_mode"] is False
                assert not (wp_dir / ".maintenance").exists()
                assert not (wp_dir / "wp-content" / "mu-plugins" / "spanel-maintenance-bypass.php").exists()
                assert res_off["instance"]["maintenance_message"] == custom_msg
        finally:
            settings.command_dry_run = orig_dry
            db.close()
            test_engine.dispose()


def test_admin_password_reset_stdin_and_subpath_install():
    captured = []

    def fake_priv(command_key: str, *, helper_args=None, fallback=None, check=True, input=None, **kwargs):
        captured.append((command_key, list(helper_args or []), input))
        return SimpleNamespace(returncode=0, stdout="Success\n", stderr="")

    orig_dry = settings.command_dry_run
    settings.command_dry_run = False
    try:
        with patch.object(wordpress.shell, "privileged", side_effect=fake_priv):
            wordpress.reset_admin_password(
                "/home/shostc/shost.com.vn/public_html",
                "wpadmin",
                "NewSuperStrong#2026",
                linux_user="shostc",
                php_version="8.4",
            )
            assert len(captured) == 1
            cmd_key, cmd_args, stdin_text = captured[0]
            assert cmd_key == "wp-site"
            assert "--prompt=user_pass" in cmd_args
            assert not any("user_pass=/dev/stdin" in a for a in cmd_args), "Must NEVER pass literal /dev/stdin as password"
            assert not any("NewSuperStrong#2026" in a for a in cmd_args), "Password must NEVER leak in process argv"
            assert stdin_text == "NewSuperStrong#2026\n"

            # Reject newline injection or short password
            for bad_pw in ("short123", "ValidLength12\ninjected"):
                try:
                    wordpress.reset_admin_password(
                        "/home/shostc/shost.com.vn/public_html",
                        "wpadmin",
                        bad_pw,
                        linux_user="shostc",
                        php_version="8.4",
                    )
                    raise AssertionError(f"Expected ValueError for bad password {bad_pw!r}")
                except ValueError:
                    pass

            # Verify subpath install targets public_html/blog with https://domain/blog URL
            captured.clear()
            wordpress.install_wordpress(
                domain="shost.com.vn",
                db={
                    "db_name": "shostc_wp_blog",
                    "db_user": "shostc_u_blog",
                    "db_password": "StrongDbPass#2026",
                },
                title="SHOST Blog",
                admin_user="wpadmin",
                admin_password="StrongAdmin#2026",
                admin_email="admin@shost.com.vn",
                php_version="8.4",
                linux_user="shostc",
                root_path="/home/shostc/shost.com.vn",
                subpath="/blog",
                ssl_enabled=True,
            )
            core_install_calls = [c for c in captured if c[0] == "wp-site" and "install" in c[1] and "core" in c[1]]
            assert len(core_install_calls) == 1
            _, install_argv, install_stdin = core_install_calls[0]
            assert "--url=https://shost.com.vn/blog" in install_argv
            assert any("public_html/blog" in a.replace("\\", "/") for a in install_argv)
            assert "--prompt=admin_password" in install_argv
            assert not any("StrongAdmin#2026" in a for a in install_argv)
            assert install_stdin == "StrongAdmin#2026\n"
    finally:
        settings.command_dry_run = orig_dry


def test_wp_cron_sync_checksums_security_revert_and_clone_self_guard():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        state_file = tmp / "wordpress_toolkit.json"
        home_base = tmp / "home"
        site_root = home_base / "shostc" / "shost.com.vn"
        wp_dir = site_root / "public_html"
        _seed_wp_files(wp_dir)

        db, test_engine = _make_db()
        owner = User(
            username="shostc",
            email="shostc@shost.com.vn",
            hashed_password="x",
            role="user",
            is_active=True,
        )
        db.add(owner)
        db.commit()

        site = Website(
            domain="shost.com.vn",
            root_path=str(site_root),
            document_root="public_html",
            linux_user="shostc",
            php_version="8.4",
            app_type="wordpress",
            ssl_enabled=True,
            owner_id=owner.id,
        )
        db.add(site)
        db.commit()

        fastcgi_clears = []
        fake_priv = _make_fake_privileged(fastcgi_clears)
        cron_store: list[dict] = []

        def fake_list_cron(domain, cron_user="www-data"):
            return [{"index": idx, **e} for idx, e in enumerate(cron_store) if e["domain"] == domain]

        def fake_add_cron(website, schedule, command):
            entry = {"domain": website.domain, "schedule": schedule, "command": command}
            cron_store.append(entry)
            return f"{schedule} {command}"

        def fake_delete_cron(domain, index, cron_user="www-data"):
            domain_items = [i for i, e in enumerate(cron_store) if e["domain"] == domain]
            if 0 <= index < len(domain_items):
                cron_store.pop(domain_items[index])

        orig_dry = settings.command_dry_run
        settings.command_dry_run = False
        try:
            with (
                patch.object(wordpress_live, "engine", test_engine),
                patch.object(wordpress_live, "toolkit_state_path", return_value=state_file),
                patch.object(wordpress_manager.site_users, "HOME_ROOT", home_base),
                patch.object(wordpress_live.shell, "privileged", side_effect=fake_priv),
                patch.object(wordpress_manager.shell, "privileged", side_effect=fake_priv),
                patch.object(cron, "list_cron_entries", side_effect=fake_list_cron),
                patch.object(cron, "add_cron", side_effect=fake_add_cron),
                patch.object(cron, "delete_cron", side_effect=fake_delete_cron),
                patch.object(
                    wordpress_live,
                    "query_wp_db_live_meta",
                    return_value={"site_title": "SHOST", "admin_user": "quangadmin", "admin_email": "q@shost.com.vn"},
                ),
            ):
                # 1. Toggling disable_wp_cron=True registers a real 15m system cron job; False removes it
                wordpress_manager.update_wp_toggles(
                    website_id=site.id,
                    payload=wordpress_manager.WpTogglesPayload(disable_wp_cron=True),
                    db=db,
                    current_user=owner,
                )
                assert len(cron_store) == 1
                assert cron_store[0]["schedule"] == "*/15 * * * *"
                assert "cron event run --due-now" in cron_store[0]["command"]

                wordpress_manager.update_wp_toggles(
                    website_id=site.id,
                    payload=wordpress_manager.WpTogglesPayload(disable_wp_cron=False),
                    db=db,
                    current_user=owner,
                )
                assert cron_store == []

                # 2. verify_checksums reports verified=False & ok=False when WP-CLI returns non-zero
                with patch.object(
                    wordpress_manager,
                    "_run_wp_cli",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr="Warning: File doesn't verify against checksum: wp-login.php",
                    ),
                ):
                    chk_res = wordpress_manager.apply_wp_security(
                        website_id=site.id,
                        payload=wordpress_manager.WpSecurityActionPayload(action="verify_checksums"),
                        db=db,
                        current_user=owner,
                    )
                    assert chk_res["ok"] is False
                    assert chk_res["verified"] is False
                    assert "wp-login.php" in chk_res["output"]

                # 3. Reverting no_default_admin_user MUST NOT clobber custom username 'quangadmin' into 'admin'
                rename_calls = []
                with (
                    patch.object(
                        wordpress_manager,
                        "_run_wp_cli",
                        return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                    ),
                    patch.object(
                        wordpress_live,
                        "rename_wp_admin_user_direct",
                        side_effect=lambda *a, **kw: rename_calls.append((a, kw)) or True,
                    ),
                ):
                    wordpress_manager.apply_wp_security(
                        website_id=site.id,
                        payload=wordpress_manager.WpSecurityActionPayload(
                            action="revert",
                            check_ids=["no_default_admin_user"],
                        ),
                        db=db,
                        current_user=owner,
                    )
                    assert rename_calls == [], "Must NOT rename custom admin 'quangadmin' back to weak 'admin'!"

                # 4. Clone/Sync onto identical website + subpath MUST be blocked with HTTP 400
                try:
                    wordpress_manager.execute_wp_tool(
                        website_id=site.id,
                        payload=wordpress_manager.WpToolsActionPayload(
                            action="clone",
                            target_website_id=site.id,
                            target_subpath="",
                            sync_files=True,
                            sync_database=True,
                        ),
                        db=db,
                        current_user=owner,
                    )
                    raise AssertionError("Expected HTTPException(400) for self-cloning onto identical path")
                except HTTPException as exc:
                    assert exc.status_code == 400
        finally:
            settings.command_dry_run = orig_dry
            db.close()
            test_engine.dispose()


class WordPressManagerOverhaulTests(unittest.TestCase):
    def test_1_static_zero_panel_unlink_and_frontend_contract(self):
        test_static_zero_panel_unlink_and_frontend_contract()

    def test_2_detach_vs_remove_and_scan_no_resurrection_live_mode(self):
        test_detach_vs_remove_and_scan_no_resurrection_live_mode()

    def test_3_maintenance_mode_custom_message_background_upload_and_sso_bypass(self):
        test_maintenance_mode_custom_message_background_upload_and_sso_bypass()

    def test_4_admin_password_reset_stdin_and_subpath_install(self):
        test_admin_password_reset_stdin_and_subpath_install()

    def test_5_wp_cron_sync_checksums_security_revert_and_clone_self_guard(self):
        test_wp_cron_sync_checksums_security_revert_and_clone_self_guard()


if __name__ == "__main__":
    unittest.main(verbosity=2)
