from pathlib import Path
from types import SimpleNamespace
import re
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import DatabaseAccount, User, Website
from app.services import mariadb, panel_settings, wordpress_live


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _assert_raises_reserved(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as exc:
        assert "reserved" in str(exc).lower()
        return
    raise AssertionError(f"Expected ValueError('reserved') from {fn}")


def test_installer_and_updater_python_heredocs_compile_cleanly():
    pattern = re.compile(r"<<'(PY_[A-Z0-9_]+|PYEOF|PY)'(?:\s|$|\"|\))")
    for rel_path in ("installer/update.sh", "installer/files/spanel-helper.sh"):
        script_path = REPO_ROOT / rel_path
        lines = script_path.read_text(encoding="utf-8").splitlines()
        i = 0
        blocks_checked = 0
        while i < len(lines):
            line = lines[i]
            m = pattern.search(line)
            if m and "python" in line:
                delim = m.group(1)
                start_line = i + 1
                body_lines = []
                i += 1
                found_end = False
                while i < len(lines):
                    if lines[i] == delim:
                        found_end = True
                        break
                    body_lines.append(lines[i])
                    i += 1
                assert found_end, f"Unterminated heredoc {delim} starting at {rel_path}:{start_line}"
                source = "\n".join(body_lines) + "\n"
                compile(source, f"{rel_path}:{start_line}", "exec")
                blocks_checked += 1
            i += 1
        assert blocks_checked > 0


def test_mariadb_rejects_reserved_databases():
    for reserved in ("mysql", "information_schema", "performance_schema", "sys", "spanel", "phpmyadmin"):
        _assert_raises_reserved(mariadb._reject_reserved_database, reserved)
        _assert_raises_reserved(mariadb.create_database_credentials, reserved, "u_valid_user", "StrongPass123!")
        _assert_raises_reserved(mariadb.drop_database, reserved, "u_valid_user")
        _assert_raises_reserved(mariadb.export_database, reserved, "/tmp/out.sql")
        _assert_raises_reserved(mariadb.set_database_write_lock, reserved, "u_valid_user", True)


def test_wp_database_authorization_blocks_cross_tenant_and_unregistered_dbs():
    db = _make_db()
    alice = User(username="alice", email="alice@example.com", hashed_password="x", role="user")
    bob = User(username="bob", email="bob@example.com", hashed_password="x", role="user")
    db.add_all([alice, bob])
    db.commit()

    alice_site = Website(domain="alice.example.com", owner_id=alice.id, root_path="/home/alice/alice.example.com")
    bob_site = Website(domain="bob.example.com", owner_id=bob.id, root_path="/home/bob/bob.example.com")
    db.add_all([alice_site, bob_site])
    db.commit()

    bob_db = DatabaseAccount(
        owner_id=bob.id,
        website_id=bob_site.id,
        db_name="wp_bob_secret",
        db_user="u_bob_secret",
        db_password="enc",
    )
    alice_db = DatabaseAccount(
        owner_id=alice.id,
        website_id=None,
        db_name="wp_alice_own",
        db_user="u_alice_own",
        db_password="enc",
    )
    db.add_all([bob_db, alice_db])
    db.commit()

    # Alice's site pointing wp-config.php at Bob's database must be rejected
    assert wordpress_live.is_authorized_wp_database(
        "wp_bob_secret", owner_id=alice.id, site_id=alice_site.id, db=db
    ) is False
    assert wordpress_live.resolve_owned_wp_database(
        db, alice_site, {"db_name": "wp_bob_secret", "db_user": "u_bob_secret"}
    ) is None

    # Reserved database names must always be rejected
    assert wordpress_live.is_authorized_wp_database(
        "mysql", owner_id=alice.id, site_id=alice_site.id, db=db
    ) is False

    # Alice's own database is authorized and linked to alice_site
    assert wordpress_live.is_authorized_wp_database(
        "wp_alice_own", owner_id=alice.id, site_id=alice_site.id, db=db
    ) is True
    resolved = wordpress_live.resolve_owned_wp_database(
        db, alice_site, {"db_name": "wp_alice_own", "db_user": "u_alice_own"}
    )
    assert resolved is not None
    assert resolved.id == alice_db.id
    assert resolved.owner_id == alice.id


def test_wp_slug_and_config_validators_reject_injection():
    assert wordpress_live.WP_SLUG_RE.fullmatch("classic-editor")
    assert wordpress_live.WP_SLUG_RE.fullmatch("--allow-root") is None
    assert wordpress_live.WP_SLUG_RE.fullmatch("-exec") is None

    assert wordpress_live.WP_MEMORY_LIMIT_RE.fullmatch("256M")
    assert wordpress_live.WP_MEMORY_LIMIT_RE.fullmatch("256M'); system('id');//") is None

    assert wordpress_live.WP_POST_REVISIONS_RE.fullmatch("true")
    assert wordpress_live.WP_POST_REVISIONS_RE.fullmatch("10")
    assert wordpress_live.WP_POST_REVISIONS_RE.fullmatch("true); system('id');//") is None


def test_helper_site_runtime_move_and_update_frontend_build_hardening():
    helper_text = (REPO_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    assert 'cp -a "$old_target"/. "$new_target"/ 2>/dev/null || true' not in helper_text
    assert 'deny "target path already exists: $new_target"' in helper_text

    update_text = (REPO_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    assert "sudo -u spanel npm install" in update_text
    assert "sudo -u spanel VITE_API_URL=/api npm run build" in update_text


def test_panel_settings_get_brand_returns_expected_fields():
    brand = panel_settings.get_brand()
    assert hasattr(brand, "app_name")
    assert hasattr(brand, "panel_url")
    assert hasattr(brand, "panel_domain")


def test_phpmyadmin_sso_loopback_and_signon_script():
    from app.api.databases import _is_loopback_peer

    for host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1", "127.0.0.42"):
        req = SimpleNamespace(client=SimpleNamespace(host=host))
        assert _is_loopback_peer(req) is True, f"Expected loopback True for {host}"

    for host in ("8.8.8.8", "::ffff:8.8.8.8", "192.168.1.10", ""):
        req = SimpleNamespace(client=SimpleNamespace(host=host))
        assert _is_loopback_peer(req) is False, f"Expected loopback False for {host}"

    signon_php = (REPO_ROOT / "installer" / "files" / "phpmyadmin-spanel-signon.php").read_text(encoding="utf-8")
    assert "SPANEL_LAST_SSO_TOKEN" in signon_php
    assert "__SPANEL_API_BASE__" in signon_php


class Release107RegressionTests(unittest.TestCase):
    def test_1_heredocs(self):
        test_installer_and_updater_python_heredocs_compile_cleanly()

    def test_2_reserved_dbs(self):
        test_mariadb_rejects_reserved_databases()

    def test_3_wp_db_auth(self):
        test_wp_database_authorization_blocks_cross_tenant_and_unregistered_dbs()

    def test_4_validators(self):
        test_wp_slug_and_config_validators_reject_injection()

    def test_5_helper_and_update(self):
        test_helper_site_runtime_move_and_update_frontend_build_hardening()

    def test_6_panel_settings_brand(self):
        test_panel_settings_get_brand_returns_expected_fields()

    def test_7_phpmyadmin_sso(self):
        test_phpmyadmin_sso_loopback_and_signon_script()


