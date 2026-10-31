"""Regressions found while reviewing the 1.0.7 release candidate.

Each test names the fault it guards against, so a failure says what broke.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import pytest
except ImportError:
    pytest = SimpleNamespace(
        mark=SimpleNamespace(
            parametrize=lambda *a, **kw: (lambda f: f),
            skipif=lambda *a, **kw: (lambda f: f),
        )
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

HELPER = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
UPDATE_SH = REPO_ROOT / "installer" / "update.sh"
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"


def _helper_section(start: str, end: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    return text.split(start, 1)[1].split(end, 1)[0]


# --- scheduled backups ------------------------------------------------------

def test_scheduled_backup_calls_create_user_backup_with_its_real_signature():
    """run_one() passed encryption_passphrase=, which create_user_backup does
    not take: every scheduled backup died with TypeError before starting."""
    from app.services import backup, backup_scheduler

    user = SimpleNamespace(id=7, username="alice")
    schedule = SimpleNamespace(
        id=1, user_id=7, target_id=None, retention=3, backup_mode="full",
        full_every_n=7, name_suffix="full_date",
        last_run_at=None, last_status=None, last_message=None,
    )
    db = SimpleNamespace(commit=lambda: None)

    with patch.object(backup, "create_user_backup", autospec=True, return_value="/tmp/a.tar.gz") as create, \
            patch.object(backup, "prune_user_backups", autospec=True), \
            patch.object(backup_scheduler, "_schedule_users", return_value=[user]), \
            patch.object(backup_scheduler, "_upload_if_configured", return_value="local"):
        assert backup_scheduler.run_one(db, schedule) is True, schedule.last_message

    create.assert_called_once()
    assert schedule.last_status == "ok"


# --- cron: one grammar for the backend and for root's cron-write --------------

CRON_ARG_SAMPLES = [
    ("'php8.3 cron.php'", True),
    ("'php8.3 script.php '\"'\"'hello world'\"'\"''", True),  # shlex.quote style
    ("'php8.3 script.php '\\''hello world'\\'''", True),  # '\'' style
    ("'date +\\%F'", True),
    ("'id'>>/etc/cron.d/evil''", False),
    ("'x' ; id #'", False),
    ("'a' 'b'", False),
    ("'a'\"'\"''$(id)''", False),
    ("'a'\\''", False),
    ("'date +%F'", False),  # cron would cut the line at %
    ("'a\\\\%b'", False),  # "\\" is a pair, so this % is unescaped too
]


def _python_accepts(arg: str) -> bool:
    from app.services.cron import JAILED_COMMAND_ARG_RE, _UNESCAPED_PERCENT_RE

    return bool(JAILED_COMMAND_ARG_RE.fullmatch(arg)) and not _UNESCAPED_PERCENT_RE.search(arg)


@pytest.mark.parametrize("arg,expected", CRON_ARG_SAMPLES)
def test_backend_cron_argument_grammar(arg, expected):
    assert _python_accepts(arg) is expected


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_helper_cron_write_uses_the_same_grammar_as_the_backend(tmp_path):
    """validate_cron_write_line accepted lines cron-write refused (and the
    reverse for '"'"' quoting): the two must agree on every sample."""
    section = _helper_section("  cron-write)", "  service-status)")
    defs = [
        line.strip() for line in section.splitlines()
        if line.strip().startswith(("q=", "jailed_arg_re=", "unescaped_pct_re="))
    ]
    assert len(defs) == 3, defs
    script = "\n".join(defs) + r'''
while IFS= read -r s; do
  if [[ "$s" =~ $jailed_arg_re ]] && ! [[ "$s" =~ $unescaped_pct_re ]]; then echo 1; else echo 0; fi
done
'''
    samples = [arg for arg, _ in CRON_ARG_SAMPLES]
    # Script in a file and bytes on stdin: a -c argument and text mode both
    # rewrite backslashes / line endings on their way to bash on Windows.
    script_file = tmp_path / "grammar.sh"
    script_file.write_bytes(script.encode("utf-8"))
    out = subprocess.run(
        [shutil.which("bash"), script_file.as_posix()], input=("\n".join(samples) + "\n").encode("utf-8"),
        capture_output=True, check=True,
    ).stdout.decode("utf-8").split()
    assert out == ["1" if _python_accepts(arg) else "0" for arg in samples]
    assert out == ["1" if expected else "0" for _, expected in CRON_ARG_SAMPLES]


def test_helper_cron_write_keeps_the_working_directory():
    """A second =~ inside the loop reset BASH_REMATCH, so every jailed line was
    written with an empty <cwd> and no tenant cron job could start."""
    section = _helper_section("  cron-write)", "  service-status)")
    loop = section.split("while IFS= read -r line; do", 1)[1]
    after_first_match = loop.split("; then", 1)[1]
    second_match = after_first_match.index("=~")
    assert "BASH_REMATCH" not in after_first_match[second_match:]
    assert '"$cwd_word" "$quoted_cmd" "$marker"' in section


@pytest.mark.parametrize("command", [
    "php8.3 cron.php",
    "php8.3 script.php 'hello world' > /dev/null 2>&1",
    "php8.3 report.php --date=$(date +%F) # 100%",
    'php8.3 a.php "double quoted" \\\\%',
])
def test_rendered_jailed_lines_pass_both_validators(command):
    from app.services import cron

    line = cron._render_jailed_line(
        "alice", "*/5 * * * *", "/home/alice/domains/example.com/public_html", command, "example.com",
    )
    assert cron.validate_cron_write_line("alice", line) is True
    parsed = cron._parse_cron_line(0, line)
    assert parsed["command"] == command


def test_percent_escaping_round_trips_like_cron():
    from app.services.cron import _escape_percent, _unescape_percent

    # A % after an odd run of backslashes has no crontab spelling (cron always
    # eats one backslash there), so only even runs are expected to round-trip.
    for raw in ["100%", "a\\\\%b", "%%", "no percent"]:
        assert _unescape_percent(_escape_percent(raw)) == raw
    assert _escape_percent("a\\\\%b") == "a\\\\\\%b"


# --- update.sh cron migration ------------------------------------------------

def test_update_cron_migration_never_runs_app_code_as_root():
    body = UPDATE_SH.read_text(encoding="utf-8").split("migrate_site_cron_php_binary() {", 1)[1].split("\n}\n", 1)[0]
    # Stage 1 (root) uses the system interpreter in isolated mode.
    assert '/usr/bin/python3 -I - "$backup_dir" "$spool_json"' in body
    # Stage 2 is the only place the app is imported, and it runs as spanel.
    as_root, as_spanel = body.split("sudo -u spanel env", 1)
    assert "from app." not in as_root and "PYTHONPATH" not in as_root
    assert "from app." in as_spanel
    # Stage 3 only acts on users stage 1 dumped, whatever spanel prints.
    assert 'grep -qxF -- "$migrated_user" <<<"$dumped_users" || continue' in body


# --- process monitor ----------------------------------------------------------

def test_process_kill_is_limited_to_hosting_accounts():
    section = _helper_section("  process-kill)", "\n  *)\n")
    assert '(( target_uid < 1000 || target_uid >= 65000 ))' in section
    assert section.index("target_uid") < section.index('kill -9 "$target_pid"')


def test_api_keeps_proc_invisible():
    for script in (INSTALL_SH, UPDATE_SH):
        text = script.read_text(encoding="utf-8")
        assert "ProtectProc=invisible" in text
        assert "ProtectProc=default" not in text
        assert "ProcSubset=all" not in text


# --- webhooks --------------------------------------------------------------------

def test_legacy_webhook_url_is_recognised_after_webhook_key_rotation():
    from app.core.config import settings
    from app.services import site_apps

    app = SimpleNamespace(id=3, owner_id=1, name="api")
    with patch.object(settings, "webhook_secret_key", ""):
        legacy = site_apps.legacy_webhook_token_for(app)
        current = site_apps.webhook_token_for(app)
    assert legacy != current
    with patch.object(settings, "webhook_secret_key", "rotated-webhook-key-0123456789abcdef"):
        assert site_apps.verify_webhook_token(app, current) is False
        assert site_apps.is_legacy_webhook_token(app, legacy) is True
        assert site_apps.is_legacy_webhook_token(app, "") is False


# --- nginx QUIC ------------------------------------------------------------------

def test_deleting_the_reuseport_vhost_hands_reuseport_on_per_address(tmp_path):
    """Only the plain 443 / [::]:443 listeners were handed on before; a vhost
    on a dedicated IP lost its reuseport for good."""
    from app.core.config import settings
    from app.services import nginx

    (tmp_path / "a.com.conf").write_text("    listen 443 quic;\n    listen 10.0.0.5:443 quic;\n", encoding="utf-8")
    (tmp_path / "b.com.conf").write_text(
        "    listen 443 quic;\n    listen 10.0.0.5:443 quic;\n    listen [::]:443 quic reuseport;\n", encoding="utf-8",
    )
    with patch.object(settings, "nginx_sites_available", str(tmp_path)):
        nginx._ensure_quic_reuseport_owner()
        nginx._ensure_quic_reuseport_owner()  # idempotent

    a = (tmp_path / "a.com.conf").read_text(encoding="utf-8")
    b = (tmp_path / "b.com.conf").read_text(encoding="utf-8")
    assert "listen 443 quic reuseport;" in a and "listen 10.0.0.5:443 quic reuseport;" in a
    assert "listen 443 quic;" in b and "listen 10.0.0.5:443 quic;" in b
    assert (a + b).count("reuseport") == 3


# --- archive extraction scan ---------------------------------------------------

def test_extract_archive_hands_unscanned_files_to_the_background_queue(tmp_path):
    """Files over the inline scan budget used to be skipped silently."""
    from app.services import file_manager

    site_dir = tmp_path / "site"
    (site_dir / "out").mkdir(parents=True)
    with zipfile.ZipFile(site_dir / "code.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("small.php", "<?php echo 1;")
        zf.writestr("big.php", "<?php /*" + "a" * (10 * 1024 * 1024) + "*/")
    site = SimpleNamespace(id=5, root_path=str(site_dir), domain="example.com", linux_user="")

    scanned, queued = [], []
    with patch.object(file_manager.site_users, "fix_site_path"), \
            patch.object(file_manager, "_clear_fastcgi_cache"), \
            patch.object(file_manager.malware_scan, "scan_upload_realtime",
                         side_effect=lambda path, **_: scanned.append(Path(path).name) or {"status": "clean"}), \
            patch.object(file_manager.malware_queue, "enqueue",
                         side_effect=lambda wid, path, name: queued.append((wid, name))):
        file_manager.extract_archive(site, "code.zip", "out")

    assert scanned == ["small.php"]
    assert queued == [(5, "big.php")]


import re
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import DatabaseAccount, User, Website
from app.services import mariadb, panel_settings, wordpress_live


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    wordpress_live.engine = engine
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


def test_wordpress_manager_toggles_and_endpoints_end_to_end():
    import tempfile
    from app.api import wordpress_manager
    from app.core.config import settings

    db = _make_db()
    user = User(username="shostc", email="shostc@example.com", hashed_password="x", role="user")
    db.add(user)
    db.commit()

    with tempfile.TemporaryDirectory() as tmpdir:
        site_root = Path(tmpdir) / "shost.com.vn"
        wp_dir = site_root / "public_html"
        (wp_dir / "wp-includes").mkdir(parents=True, exist_ok=True)
        (wp_dir / "wp-content" / "plugins").mkdir(parents=True, exist_ok=True)
        (wp_dir / "wp-content" / "themes").mkdir(parents=True, exist_ok=True)
        (wp_dir / "wp-includes" / "version.php").write_text(
            "<?php\n$wp_version = '6.8.1';\n", encoding="utf-8"
        )
        (wp_dir / "wp-config.php").write_text(
            "<?php\n"
            "define('DB_NAME', 'wp_shost_1');\n"
            "define('DB_USER', 'u_wp_shost_1');\n"
            "define('DB_PASSWORD', 'SecretPass123!');\n"
            "$table_prefix = 'wp_';\n"
            "/* That's all, stop editing! */\n",
            encoding="utf-8",
        )

        site = Website(
            domain="shost.com.vn",
            owner_id=user.id,
            root_path=str(site_root),
            php_version="8.4",
            ssl_enabled=True,
        )
        db.add(site)
        db.commit()

        db_acc = DatabaseAccount(
            owner_id=user.id,
            website_id=site.id,
            db_name="wp_shost_1",
            db_user="u_wp_shost_1",
            db_password="enc",
        )
        db.add(db_acc)
        db.commit()

        orig_dry_run = settings.command_dry_run
        settings.command_dry_run = True
        try:
          with patch.object(wordpress_live, "toolkit_state_path", side_effect=lambda: Path(tmpdir) / "wordpress_toolkit_state.json"):
            st = wordpress_manager._load_toolkit_state()
            st.setdefault("instances", {}).pop(str(site.id), None)
            wordpress_manager._save_toolkit_state(st)

            # 0. Enabling password_protection before any password is set must return HTTP 400 (never lock out user with random password)
            from fastapi import HTTPException

            try:
                wordpress_manager.update_wp_toggles(
                    website_id=site.id,
                    payload=wordpress_manager.WpTogglesPayload(password_protection=True),
                    db=db,
                    current_user=user,
                )
                raise AssertionError("Expected HTTPException(400) when enabling password_protection without a password")
            except HTTPException as exc:
                assert exc.status_code == 400

            # 1. Test all 6 toggles + standalone debug_log + password_protection with credentials
            r1 = wordpress_manager.update_wp_toggles(
                website_id=site.id,
                payload=wordpress_manager.WpTogglesPayload(
                    search_engine_indexing=False,
                    caching_enabled=True,
                    maintenance_mode=True,
                    debugging=True,
                    debug_log=True,
                    debug_display=False,
                    password_protection=True,
                    basic_auth_user="staging_admin",
                    basic_auth_password="StrongPassword123!",
                    disable_wp_cron=True,
                ),
                db=db,
                current_user=user,
            )
            assert "message" in r1
            t1 = r1["instance"]["toggles"]
            assert t1["search_engine_indexing"] is False
            assert t1["caching_enabled"] is True
            assert t1["maintenance_mode"] is True
            assert t1["debugging"] is True
            assert t1["debug_log"] is True
            assert t1["password_protection"] is True
            assert t1["disable_wp_cron"] is True
            assert r1["instance"]["password_protection_configured"] is True

            # Standalone debug_log toggle (e.g. from Debug Modal)
            r2 = wordpress_manager.update_wp_toggles(
                website_id=site.id,
                payload=wordpress_manager.WpTogglesPayload(
                    debug_log=False,
                ),
                db=db,
                current_user=user,
            )
            assert r2["instance"]["toggles"]["debug_log"] is False

            # Re-enable / update password protection after hash is already configured
            r3 = wordpress_manager.update_wp_toggles(
                website_id=site.id,
                payload=wordpress_manager.WpTogglesPayload(
                    password_protection=True,
                ),
                db=db,
                current_user=user,
            )
            assert r3["instance"]["basic_auth_user"] == "staging_admin"
            assert r3["instance"]["password_protection_configured"] is True

            # Disable password protection and maintenance mode
            r4 = wordpress_manager.update_wp_toggles(
                website_id=site.id,
                payload=wordpress_manager.WpTogglesPayload(
                    maintenance_mode=False,
                    password_protection=False,
                    caching_enabled=False,
                    disable_wp_cron=False,
                    search_engine_indexing=True,
                ),
                db=db,
                current_user=user,
            )
            assert r4["instance"]["toggles"]["maintenance_mode"] is False
            assert r4["instance"]["toggles"]["password_protection"] is False

            # 2. Test update_wp_general
            rg = wordpress_manager.update_wp_general(
                website_id=site.id,
                payload=wordpress_manager.WpGeneralUpdatePayload(
                    site_title="SHost Portal",
                    admin_user="wpadmin_sec",
                ),
                db=db,
                current_user=user,
            )
            assert "message" in rg
            assert rg["instance"]["site_title"] == "SHost Portal"
            assert rg["instance"]["admin_user"] == "wpadmin_sec"

            # 3. Test apply_wp_security
            rs = wordpress_manager.apply_wp_security(
                website_id=site.id,
                payload=wordpress_manager.WpSecurityActionPayload(
                    check_ids=["disallow_file_edit", "disable_xmlrpc"],
                    action="secure",
                ),
                db=db,
                current_user=user,
            )
            assert "message" in rs

            # 4. Test execute_wp_tool (update_config, clear_cache, create_snapshot)
            rc = wordpress_manager.execute_wp_tool(
                website_id=site.id,
                payload=wordpress_manager.WpToolsActionPayload(
                    action="update_config",
                    wp_memory_limit="512M",
                    wp_max_memory_limit="512M",
                    wp_post_revisions="10",
                ),
                db=db,
                current_user=user,
            )
            assert rc["instance"]["wp_memory_limit"] == "512M"
        finally:
            settings.command_dry_run = orig_dry_run


def test_round_9_snapshot_restore_clone_and_db_isolation():
    import tempfile
    from fastapi import HTTPException
    from app.api import wordpress_manager
    from app.core.config import settings
    from app.services.shell import CommandResult

    db = _make_db()
    admin = User(username="admin", email="admin@example.com", hashed_password="x", role="admin")
    alice = User(username="alice", email="alice@example.com", hashed_password="x", role="user")
    bob = User(username="bob", email="bob@example.com", hashed_password="x", role="user")
    db.add_all([admin, alice, bob])
    db.commit()

    with tempfile.TemporaryDirectory() as tmpdir:
        alice_root = Path(tmpdir) / "alice.example.com"
        alice_wp = alice_root / "public_html"
        (alice_wp / "wp-includes").mkdir(parents=True, exist_ok=True)
        (alice_root / "wp-snapshots").mkdir(parents=True, exist_ok=True)
        (alice_wp / "wp-includes" / "version.php").write_text("<?php\n$wp_version = '6.8.1';\n", encoding="utf-8")

        bob_root = Path(tmpdir) / "bob.example.com"
        bob_wp = bob_root / "public_html"
        bob_wp.mkdir(parents=True, exist_ok=True)

        alice_site = Website(domain="alice.example.com", owner_id=alice.id, root_path=str(alice_root), php_version="8.4")
        bob_site = Website(domain="bob.example.com", owner_id=bob.id, root_path=str(bob_root), php_version="8.4")
        db.add_all([alice_site, bob_site])
        db.commit()

        alice_db = DatabaseAccount(
            owner_id=alice.id,
            website_id=alice_site.id,
            db_name="wp_alice_db",
            db_user="u_alice_db",
            db_password="enc",
        )
        db.add(alice_db)
        db.commit()

        # 1. query_wp_db_live_meta without owner_id/site_id must return empty metadata without running SQL
        with patch.object(wordpress_live, "_run_mariadb_sql") as sql_mock:
            meta = wordpress_live.query_wp_db_live_meta("wp_alice_db", "wp_")
            assert meta["site_title"] == ""
            sql_mock.assert_not_called()

        # 2. restore_live_snapshot must reject snapshot_id not in inst["snapshots"] (404)
        (alice_root / "wp-snapshots" / "rogue.tar.gz").write_bytes(b"fake")
        inst_state = {"snapshots": [{"id": "legit-snap.tar.gz"}]}
        try:
            wordpress_live.restore_live_snapshot(
                "alice",
                alice_root,
                alice_wp,
                "wp_alice_db",
                "rogue.tar.gz",
                inst=inst_state,
                owner_id=alice.id,
                site_id=alice_site.id,
                db=db,
            )
            raise AssertionError("Expected 404 for untracked snapshot_id")
        except HTTPException as exc:
            assert exc.status_code == 404

        # 3. restore_live_snapshot must NEVER call mariadb.import_database when wp db import fails
        (alice_root / "wp-snapshots" / "legit-snap.tar.gz").write_bytes(b"archive")
        (alice_root / "wp-snapshots" / "legit-snap.tar.gz.sql").write_text("GRANT ALL ON *.* TO 'pwn'@'%';\n", encoding="utf-8")
        orig_dry = settings.command_dry_run
        settings.command_dry_run = False
        try:
            with patch.object(wordpress_live.shell, "privileged", return_value=CommandResult("tar", 0, "", "")), \
                 patch.object(wordpress_live, "run_wp_cli", return_value=CommandResult("wp", 1, "", "ERROR 1044: Access denied")), \
                 patch.object(wordpress_live.mariadb, "import_database") as root_import_mock:
                try:
                    wordpress_live.restore_live_snapshot(
                        "alice",
                        alice_root,
                        alice_wp,
                        "wp_alice_db",
                        "legit-snap.tar.gz",
                        inst=inst_state,
                        owner_id=alice.id,
                        site_id=alice_site.id,
                        db=db,
                    )
                    raise AssertionError("Expected HTTPException(400) when wp db import fails")
                except HTTPException as exc:
                    assert exc.status_code == 400
                root_import_mock.assert_not_called()
        finally:
            settings.command_dry_run = orig_dry

        # 4. install_wp_instance with db_mode='existing' must block binding Alice's DB to Bob's site (and block admin direct access)
        for caller in (admin, bob):
            try:
                wordpress_manager.install_wp_instance(
                    payload=wordpress_manager.WpInstallPayload(
                        website_id=bob_site.id,
                        db_mode="existing",
                        database_id=alice_db.id,
                    ),
                    db=db,
                    current_user=caller,
                )
                raise AssertionError(f"Expected HTTPException(403) for caller={caller.username}")
            except HTTPException as exc:
                assert exc.status_code == 403

        # 5. mariadb.import_database with tenant_import_credentials uses tenant --defaults-file and --binary-mode
        sql_file = Path(tmpdir) / "tenant.sql"
        sql_file.write_text("CREATE TABLE t (id INT);\n", encoding="utf-8")
        seen_cmd = []
        orig_dry2 = settings.command_dry_run
        settings.command_dry_run = False
        try:
            def fake_run(cmd, **kwargs):
                seen_cmd.extend(cmd)
                return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

            with patch.object(mariadb.subprocess, "run", side_effect=fake_run):
                with mariadb.tenant_import_credentials("u_alice_db", "TenantSecret123!"):
                    mariadb.import_database("wp_alice_db", str(sql_file))
            assert "--binary-mode" in seen_cmd
            assert any(arg.startswith("--defaults-file=") and "spanel-mysql-import-" in arg for arg in seen_cmd)
        finally:
            settings.command_dry_run = orig_dry2


def test_impersonate_and_exit_end_to_end():
    from fastapi import HTTPException, Response
    from jose import jwt
    from app.api import auth, deps
    from app.core.config import settings
    from app.core.security import ALGORITHM

    db = _make_db()
    admin = User(username="admin", email="admin@example.com", hashed_password="x", role="admin", is_active=True, token_version=1)
    customer = User(username="shostc", email="shostc@example.com", hashed_password="x", role="user", is_active=True, token_version=0)
    db.add_all([admin, customer])
    db.commit()

    def _dummy_request(payload=None, token=None):
        return SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"user-agent": "unittest", "x-forwarded-proto": "https"},
            url=SimpleNamespace(scheme="https", netloc="localhost:2222"),
            cookies={},
            state=SimpleNamespace(jwt_payload=payload or {}, jwt_token=token),
        )

    # 1. Admin impersonates customer
    orig_rl = settings.rate_limit_backend
    settings.rate_limit_backend = "memory"
    try:
        req1 = _dummy_request()
        resp1 = Response()
        imp_res = auth.impersonate_user(
            user_id=customer.id,
            request=req1,
            response=resp1,
            otp="",
            db=db,
            current_user=admin,
        )
    finally:
        settings.rate_limit_backend = orig_rl
    assert imp_res.access_token
    imp_payload = jwt.decode(imp_res.access_token, settings.secret_key, algorithms=[ALGORITHM])
    assert imp_payload["sub"] == "shostc"
    assert imp_payload["imp"] is True
    assert imp_payload["imp_by"] == admin.id
    assert imp_payload["imp_user"] == "admin"

    # 2. Exit impersonation ("Return to admin")
    req2 = _dummy_request(payload=imp_payload, token=imp_res.access_token)
    resp2 = Response()
    exit_res = auth.exit_impersonation(
        request=req2,
        response=resp2,
        db=db,
        current_user=customer,
    )
    assert exit_res.access_token
    admin_payload = jwt.decode(exit_res.access_token, settings.secret_key, algorithms=[ALGORITHM])
    assert admin_payload["sub"] == "admin"
    assert not admin_payload.get("imp")

    # 3. Revoked impersonation token can no longer be used
    try:
        deps._user_from_token(imp_res.access_token, db)
        raise AssertionError("Expected revoked impersonation token to be rejected with HTTP 401")
    except HTTPException as exc:
        assert exc.status_code == 401


def test_strict_role_isolation_between_admin_reseller_and_end_user():
    from fastapi import BackgroundTasks, HTTPException
    from app.api import app_installer, databases, websites, wordpress_manager
    from app.schemas.schemas import WebsiteCreate, WebsiteUpdate

    db = _make_db()
    admin = User(username="admin", email="admin@example.com", hashed_password="x", role="admin", is_active=True)
    reseller = User(username="reseller1", email="reseller@example.com", hashed_password="x", role="reseller", is_active=True)
    customer = User(username="shostc", email="shostc@example.com", hashed_password="x", role="user", is_active=True)
    db.add_all([admin, reseller, customer])
    db.commit()

    site = Website(domain="shost.com.vn", owner_id=customer.id, root_path="/home/shostc/shost.com.vn", php_version="8.4")
    db.add(site)
    db.commit()

    cust_db = DatabaseAccount(owner_id=customer.id, website_id=site.id, db_name="wp_shost", db_user="u_shost", db_password="enc")
    db.add(cust_db)
    db.commit()

    # 1. WordPress Manager endpoints must reject Admin and Reseller (403) unless impersonating an End User
    for privileged_user in (admin, reseller):
        for fn_call in (
            lambda u=privileged_user: wordpress_manager.list_wp_instances(db=db, current_user=u),
            lambda u=privileged_user: wordpress_manager.scan_wp_instances(db=db, current_user=u),
            lambda u=privileged_user: wordpress_manager._get_accessible_website(site.id, db, u),
        ):
            try:
                fn_call()
                raise AssertionError(f"Expected 403 for WordPress Manager access by {privileged_user.role}")
            except HTTPException as exc:
                assert exc.status_code == 403

    # End User can access their own WordPress Manager instances
    wp_list = wordpress_manager.list_wp_instances(db=db, current_user=customer)
    assert len(wp_list["websites"]) == 1 and wp_list["websites"][0]["domain"] == "shost.com.vn"

    # 2. App Installer must reject Admin and Reseller (403)
    for privileged_user in (admin, reseller):
        try:
            app_installer.install_app(
                payload=app_installer.AppInstallRequest(app_type="wordpress", domain="shost.com.vn"),
                background_tasks=BackgroundTasks(),
                db=db,
                current_user=privileged_user,
            )
            raise AssertionError(f"Expected 403 for App Installer access by {privileged_user.role}")
        except HTTPException as exc:
            assert exc.status_code == 403

    # 3. Websites creation/assignment must reject Admin/Reseller owning websites directly
    dummy_req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    with patch.object(websites, "reconcile_websites_from_disk"):
        for privileged_user in (admin, reseller):
            try:
                websites.create_website(
                    payload=WebsiteCreate(domain="illegal-admin-site.com", php_version="8.4", install_wordpress=False),
                    request=dummy_req,
                    db=db,
                    current_user=privileged_user,
                )
                raise AssertionError(f"Expected 403 when {privileged_user.role} creates website without End User owner")
            except HTTPException as exc:
                assert exc.status_code == 403

        # Assigning a domain to an Admin or Reseller account must be rejected with 403
        for non_end_user in (admin, reseller):
            try:
                websites.update_website(
                    website_id=site.id,
                    payload=WebsiteUpdate(owner_id=non_end_user.id),
                    db=db,
                    current_user=admin,
                )
                raise AssertionError(f"Expected 403 when assigning domain to {non_end_user.role}")
            except HTTPException as exc:
                assert exc.status_code == 403

    # 4. list_databases scopes strictly to current_user.id (Admin does not see End User DBs in End-User MySQL list)
    assert databases.list_databases(q="", db=db, current_user=admin) == []
    assert len(databases.list_databases(q="", db=db, current_user=customer)) == 1

    # 5. When Admin switches Access Role Switch to 'User' (X-Access-Role: user), only Admin's own websites/WP instances are visible
    user_role_req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"x-access-role": "user"})
    with patch.object(websites, "reconcile_websites_from_disk"):
        assert websites.list_websites(q="", scope="", request=user_role_req, db=db, current_user=admin) == []
    tok = wordpress_manager._WP_ACCESS_ROLE_CTX.set("user")
    try:
        admin_wp_in_user_role = wordpress_manager.list_wp_instances(db=db, current_user=admin)
        assert admin_wp_in_user_role["websites"] == []
        # Even in User Access Role, Admin cannot access customer's WordPress site without "Login as"
        try:
            wordpress_manager._get_accessible_website(site.id, db, admin)
            raise AssertionError("Expected 403 when Admin in User Access Role tries to access customer's website")
        except HTTPException as exc:
            assert exc.status_code == 403
    finally:
        wordpress_manager._WP_ACCESS_ROLE_CTX.reset(tok)


def test_p0_1_clone_rejects_symlink_and_mismatched_uid_and_cleans_orphan_db(tmp_path):
    import os
    import stat as stat_mod
    from fastapi import HTTPException
    from app.core.config import settings
    from app.services.shell import CommandResult

    db = _make_db()
    alice = User(username="alice", email="alice@example.com", hashed_password="x", role="user", is_active=True)
    db.add(alice)
    db.commit()

    src_root = tmp_path / "src.example.com"
    src_wp = src_root / "public_html"
    (src_wp / "wp-includes").mkdir(parents=True, exist_ok=True)
    (src_root / "wp-snapshots").mkdir(parents=True, exist_ok=True)

    dst_root = tmp_path / "dst.example.com"
    dst_wp = dst_root / "public_html"
    dst_wp.mkdir(parents=True, exist_ok=True)

    secret_file = tmp_path / "secret_my.cnf"
    secret_file.write_text("[client]\npassword=SUPER_SECRET_MARIADB_ROOT\n", encoding="utf-8")

    src_site = Website(domain="src.example.com", owner_id=alice.id, root_path=str(src_root), php_version="8.4", linux_user="alice")
    dst_site = Website(domain="dst.example.com", owner_id=alice.id, root_path=str(dst_root), php_version="8.4", linux_user="alice")
    db.add_all([src_site, dst_site])
    db.commit()

    orig_dry = settings.command_dry_run
    settings.command_dry_run = False
    try:
        # Case A: src_stage_abs is replaced by a symlink to secret_file outside site_root
        install_calls = []
        dropped_dbs = []

        def fake_priv_symlink(action, helper_args=None, fallback=None, **kwargs):
            if action == "site-file-install":
                install_calls.append((action, helper_args))
                return CommandResult(action, 0, "", "")
            if action == "terminal-exec" and helper_args and len(helper_args) >= 5 and helper_args[2] == "tar" and helper_args[3] == "-czf":
                stage_rel = helper_args[4]
                stage_path = src_root / stage_rel
                stage_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    stage_path.symlink_to(secret_file)
                except OSError:
                    # On Windows without symlink privilege, write file and patch Path.is_symlink below
                    stage_path.write_text("placeholder", encoding="utf-8")
                return CommandResult(action, 0, "", "")
            return CommandResult(action, 0, "", "")

        orig_is_symlink = Path.is_symlink

        def check_is_symlink(self_path):
            if self_path.name.startswith(".clone-src-"):
                return True
            return orig_is_symlink(self_path)

        with patch.object(wordpress_live.shell, "privileged", side_effect=fake_priv_symlink), \
             patch.object(wordpress_live.site_users, "ensure_site_runtime", return_value="alice"), \
             patch.object(wordpress_live.mariadb, "create_database", return_value={"db_name": "wp_dst_1", "db_user": "u_wp_dst_1", "db_password": "Pass123!"}), \
             patch.object(wordpress_live.mariadb, "drop_database", side_effect=lambda d, u: dropped_dbs.append((d, u))), \
             patch.object(Path, "is_symlink", check_is_symlink):
            try:
                wordpress_live.execute_live_clone_or_sync(
                    act="clone",
                    site=src_site,
                    target_site=dst_site,
                    db=db,
                    inst={"site_title": "Src", "subpath": ""},
                    state={"instances": {}},
                    linux_user="alice",
                    site_root=src_root,
                    wp_dir=src_wp,
                    php_ver="8.4",
                    target_linux_user="alice",
                    target_site_root=dst_root,
                    target_wp_dir=dst_wp,
                    target_subpath="",
                    sync_files=True,
                    sync_database=False,
                    src_cfg_text="",
                    src_db_name="",
                    src_prefix="wp_",
                    set_wp_config_constant_fn=lambda t, k, v: t,
                )
                raise AssertionError("Expected HTTPException(500) when clone archive is a symlink")
            except HTTPException as exc:
                assert exc.status_code == 500

        assert install_calls == [], "site-file-install must never be called when source archive is a symlink"
        assert dropped_dbs == [("wp_dst_1", "u_wp_dst_1")], "Orphaned DB created before clone failure must be dropped"
        assert db.query(DatabaseAccount).filter_by(website_id=dst_site.id).count() == 0

        # Case B: src_stage_abs is a regular file but st_uid does not match source linux_user
        install_calls_uid = []
        dropped_dbs_uid = []

        def fake_priv_uid(action, helper_args=None, fallback=None, **kwargs):
            if action == "site-file-install":
                install_calls_uid.append((action, helper_args))
                return CommandResult(action, 0, "", "")
            if action == "terminal-exec" and helper_args and len(helper_args) >= 5 and helper_args[2] == "tar" and helper_args[3] == "-czf":
                stage_rel = helper_args[4]
                stage_path = src_root / stage_rel
                stage_path.parent.mkdir(parents=True, exist_ok=True)
                stage_path.write_bytes(b"NOT_OWNED_BY_ALICE")
                return CommandResult(action, 0, "", "")
            return CommandResult(action, 0, "", "")

        fake_stat = SimpleNamespace(st_mode=stat_mod.S_IFREG | 0o600, st_uid=998, st_nlink=1)
        with patch.object(wordpress_live.shell, "privileged", side_effect=fake_priv_uid), \
             patch.object(wordpress_live.site_users, "ensure_site_runtime", return_value="alice"), \
             patch.object(wordpress_live.mariadb, "create_database", return_value={"db_name": "wp_dst_2", "db_user": "u_wp_dst_2", "db_password": "Pass123!"}), \
             patch.object(wordpress_live.mariadb, "drop_database", side_effect=lambda d, u: dropped_dbs_uid.append((d, u))), \
             patch.object(wordpress_live.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1005)), \
             patch.object(os, "fstat", return_value=fake_stat):
            try:
                wordpress_live.execute_live_clone_or_sync(
                    act="clone",
                    site=src_site,
                    target_site=dst_site,
                    db=db,
                    inst={"site_title": "Src", "subpath": ""},
                    state={"instances": {}},
                    linux_user="alice",
                    site_root=src_root,
                    wp_dir=src_wp,
                    php_ver="8.4",
                    target_linux_user="alice",
                    target_site_root=dst_root,
                    target_wp_dir=dst_wp,
                    target_subpath="",
                    sync_files=True,
                    sync_database=False,
                    src_cfg_text="",
                    src_db_name="",
                    src_prefix="wp_",
                    set_wp_config_constant_fn=lambda t, k, v: t,
                )
                raise AssertionError("Expected HTTPException(500) when source file st_uid != linux_user uid")
            except HTTPException as exc:
                assert exc.status_code == 500

        assert install_calls_uid == [], "site-file-install must never be called on UID mismatch"
        assert dropped_dbs_uid == [("wp_dst_2", "u_wp_dst_2")]
        assert db.query(DatabaseAccount).filter_by(website_id=dst_site.id).count() == 0
    finally:
        settings.command_dry_run = orig_dry


def test_p0_2_uploads_php_blocked_preserves_vhost_and_subpath_app_type(tmp_path):
    from app.api import wordpress_manager
    from app.core.config import settings
    from app.models.entities import WebsiteAlias
    from app.services import nginx
    from app.services.shell import CommandResult

    db = _make_db()
    alice = User(username="alice", email="alice@example.com", hashed_password="x", role="user", is_active=True)
    db.add(alice)
    db.commit()

    site_root = tmp_path / "alice.example.com"
    wp_dir = site_root / "public_html"
    (wp_dir / "wp-includes").mkdir(parents=True, exist_ok=True)
    (wp_dir / "wp-content" / "uploads").mkdir(parents=True, exist_ok=True)
    (wp_dir / "wp-includes" / "version.php").write_text("<?php\n$wp_version = '6.8.1';\n", encoding="utf-8")
    (wp_dir / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'wp_a');\n", encoding="utf-8")

    site = Website(
        domain="alice.example.com",
        owner_id=alice.id,
        root_path=str(site_root),
        php_version="8.4",
        linux_user="alice",
        app_type="php",
        nginx_custom="# custom directive keep me\n",
        waf_enabled=False,
        http_flood_enabled=True,
    )
    db.add(site)
    db.commit()

    alias_mirror = WebsiteAlias(website_id=site.id, domain="www.alice.example.com", mode="alias")
    alias_redir = WebsiteAlias(website_id=site.id, domain="old.alice.example.com", mode="redirect")
    db.add_all([alias_mirror, alias_redir])
    db.commit()
    db.refresh(site)

    orig_dry = settings.command_dry_run
    settings.command_dry_run = False
    try:
        captured_vhost_calls = []

        def fake_rewrite_vhost(domain, root_path, **kwargs):
            captured_vhost_calls.append((domain, root_path, kwargs))
            return "server {}"

        # 1. Root site: enabling uploads_php_blocked calls _rewrite_website_vhost preserving aliases, redirects, custom_directives, waf_enabled
        st = wordpress_manager._load_toolkit_state()
        st.setdefault("instances", {})[str(site.id)] = {"installed": True, "subpath": ""}
        wordpress_manager._save_toolkit_state(st)

        with patch.object(nginx, "rewrite_vhost", side_effect=fake_rewrite_vhost), \
             patch.object(wordpress_live.shell, "privileged", return_value=CommandResult("write", 0, "", "")):
            wordpress_manager.apply_wp_security(
                website_id=site.id,
                payload=wordpress_manager.WpSecurityActionPayload(
                    check_ids=["uploads_php_blocked"],
                    action="secure",
                ),
                db=db,
                current_user=alice,
            )

        assert len(captured_vhost_calls) == 1
        dom, _, kw = captured_vhost_calls[0]
        assert dom == "alice.example.com"
        assert kw["app_type"] == "wordpress"
        assert kw["custom_directives"] == "# custom directive keep me\n"
        assert kw["waf_enabled"] is False
        assert kw["http_flood_enabled"] is True
        assert "www.alice.example.com" in (kw.get("aliases") or [])
        assert "old.alice.example.com" in (kw.get("redirects") or [])
        assert site.app_type == "wordpress"

        # 2. Subpath WordPress on a non-wordpress site: site.app_type must NOT change,
        # and subpath Nginx location rule must be added to site.nginx_custom and applied via rewrite_vhost
        site.app_type = "php"
        db.commit()
        st = wordpress_manager._load_toolkit_state()
        st.setdefault("instances", {})[str(site.id)] = {"installed": True, "subpath": "blog"}
        wordpress_manager._save_toolkit_state(st)
        (wp_dir / "blog" / "wp-includes").mkdir(parents=True, exist_ok=True)
        (wp_dir / "blog" / "wp-includes" / "version.php").write_text("<?php\n$wp_version = '6.8.1';\n", encoding="utf-8")

        captured_vhost_calls.clear()
        with patch.object(nginx, "rewrite_vhost", side_effect=fake_rewrite_vhost), \
             patch.object(wordpress_live.shell, "privileged", return_value=CommandResult("write", 0, "", "")):
            wordpress_manager.apply_wp_security(
                website_id=site.id,
                payload=wordpress_manager.WpSecurityActionPayload(
                    check_ids=["uploads_php_blocked"],
                    action="secure",
                ),
                db=db,
                current_user=alice,
            )
            # Also verify turning off sensitive_files_blocked does not write fake readme.html (P2-4)
            wordpress_manager.apply_wp_security(
                website_id=site.id,
                payload=wordpress_manager.WpSecurityActionPayload(
                    check_ids=["sensitive_files_blocked"],
                    action="revert",
                ),
                db=db,
                current_user=alice,
            )

        assert site.app_type == "php", "Subpath WordPress must not overwrite parent site.app_type"
        assert len(captured_vhost_calls) == 1
        expected_subpath_rule = (
            "location ^~ /blog/wp-content/uploads/ "
            "{ expires 30d; location ~* \\.php$ { deny all; } }"
        )
        assert expected_subpath_rule in (site.nginx_custom or "")
        assert nginx.validate_custom_nginx(site.nginx_custom).strip() == site.nginx_custom.strip()
        assert not (wp_dir / "blog" / "readme.html").exists()

        # Verify upgrading an older v1 ^~ rule (without expires) replaces it cleanly without duplicates
        site.nginx_custom = "location ^~ /blog/wp-content/uploads/ { location ~* \\.php$ { deny all; } }\n"
        db.commit()
        with patch.object(nginx, "rewrite_vhost", side_effect=fake_rewrite_vhost), \
             patch.object(wordpress_live.shell, "privileged", return_value=CommandResult("write", 0, "", "")):
            wordpress_manager.apply_wp_security(
                website_id=site.id,
                payload=wordpress_manager.WpSecurityActionPayload(
                    check_ids=["uploads_php_blocked"],
                    action="secure",
                ),
                db=db,
                current_user=alice,
            )
        assert (site.nginx_custom or "").count("location ^~ /blog/wp-content/uploads/") == 1
        assert expected_subpath_rule in (site.nginx_custom or "")

        # P2-1: Render both real vhost types via nginx.render_vhost and verify custom_include_path is placed AFTER
        # location ~ \.php$ so custom regex locations cannot shadow FastCGI PHP and leak source code.
        with patch.object(nginx.site_users, "is_site_root_for_domain", return_value=True):
            for vhost_app_type in ("php", "wordpress"):
                rendered_vhost = nginx.render_vhost(
                    domain="alice.example.com",
                    root_path=str(site_root),
                    app_type=vhost_app_type,
                    php_version="8.4",
                    custom_directives=site.nginx_custom,
                )
                assert rendered_vhost.index("location ~ \\.php$") < rendered_vhost.index(
                    "include /etc/nginx/spanel/custom/alice.example.com.conf;"
                )

        # Verify write_wp_file_text raises HTTPException(500) when site-file-write fails (P2-14)
        from fastapi import HTTPException

        with patch.object(wordpress_live.shell, "privileged", return_value=CommandResult("write", 1, "", "permission denied")):
            try:
                wordpress_live.write_wp_file_text("alice", site_root, wp_dir, "wp-config.php", "<?php\n", "0640")
                raise AssertionError("Expected HTTPException(500) when site-file-write returns non-zero")
            except HTTPException as exc:
                assert exc.status_code == 500
                assert "permission denied" in exc.detail
    finally:
        settings.command_dry_run = orig_dry


def test_p1_2_and_p1_3_definer_strip_and_no_root_fallback(tmp_path):
    from app.core.config import settings
    from app.models.entities import RemoteDbHost
    from app.services import mariadb, remote_mysql

    orig_dry = settings.command_dry_run
    settings.command_dry_run = False
    try:
        sql_file = tmp_path / "dump_with_definer.sql"
        sql_file.write_text(
            "CREATE TABLE `wp_posts` (`id` int PRIMARY KEY, `post_content` text);\n"
            "INSERT INTO `wp_posts` VALUES (1, 'Post mentioning DEFINER=`admin`@`localhost` inside body');\n"
            "/*!50003 CREATE*/ /*!50017 DEFINER=`u_src_site`@`localhost`*/ /*!50003 TRIGGER `wp_trg` BEFORE INSERT ON `wp_posts` FOR EACH ROW BEGIN END */;\n"
            "CREATE DEFINER=`other_user`@`%` TRIGGER `wp_trg2` AFTER INSERT ON `wp_posts` FOR EACH ROW BEGIN END;\n"
            "CREATE DEFINER=`admin`@`localhost` VIEW `v_posts` AS SELECT * FROM `wp_posts`;\n",
            encoding="utf-8",
        )

        # P1-3: Calling import_database_as_tenant (or import_database) without credentials or DatabaseAccount MUST raise RuntimeError
        # and MUST NOT invoke subprocess.run with ~/.my.cnf
        subproc_calls = []

        class _Done:
            returncode = 0
            stderr = b""

        def fake_subproc(args, stdin=None, **kwargs):
            data = stdin.read() if stdin is not None else b""
            cnf_arg = next((a for a in args if a.startswith("--defaults-file=")), "")
            cnf_text = Path(cnf_arg.split("=", 1)[1]).read_text(encoding="utf-8") if cnf_arg else ""
            subproc_calls.append({"args": args, "stdin": data, "cnf": cnf_text})
            return _Done()

        with patch.object(mariadb.subprocess, "run", side_effect=fake_subproc):
            try:
                mariadb.import_database_as_tenant("wp_missing_creds_db", str(sql_file))
                raise AssertionError("Expected RuntimeError when tenant DB credentials are missing")
            except RuntimeError as exc:
                assert "missing tenant database credentials" in str(exc)
            assert subproc_calls == [], "subprocess.run must never be called with ~/.my.cnf when tenant creds are missing"

            # P1-2: With tenant credentials, DDL DEFINER clauses are stripped while INSERT data is preserved
            mariadb.import_database_as_tenant(
                "wp_target_db",
                str(sql_file),
                db_user="u_target_db",
                db_password="TargetSecret123!",
            )
            assert len(subproc_calls) == 1
            call = subproc_calls[0]
            assert call["args"][0] == "mysql"
            assert "--binary-mode" in call["args"]
            assert call["args"][-1] == "wp_target_db"
            assert 'user="u_target_db"' in call["cnf"]
            assert b"DEFINER=`u_src_site`" not in call["stdin"]
            assert b"DEFINER=`other_user`" not in call["stdin"]
            assert b"CREATE DEFINER=`admin`" not in call["stdin"]
            assert b"Post mentioning DEFINER=`admin`@`localhost` inside body" in call["stdin"]
            assert b"TRIGGER `wp_trg`" in call["stdin"]
            assert b"TRIGGER `wp_trg2`" in call["stdin"]

        # P1-3 remote backend: RemoteMySQLBackend.import_database_as_tenant uses tenant user/password and strips DDL DEFINER
        remote_calls = []

        def fake_remote_subproc(args, stdin=None, **kwargs):
            data = stdin.read() if stdin is not None else b""
            cnf_arg = next((a for a in args if a.startswith("--defaults-file=")), "")
            cnf_text = Path(cnf_arg.split("=", 1)[1]).read_text(encoding="utf-8") if cnf_arg else ""
            remote_calls.append({"args": args, "stdin": data, "cnf": cnf_text})
            return _Done()

        host = RemoteDbHost(
            id=1,
            name="rdb1",
            host="10.10.0.15",
            port=3306,
            admin_user="rdb_admin",
            admin_password="",
            use_tls=False,
            enabled=True,
        )
        r_backend = remote_mysql.RemoteMySQLBackend(host)
        with patch.object(remote_mysql.subprocess, "run", side_effect=fake_remote_subproc):
            try:
                r_backend.import_database_as_tenant("wp_missing_remote", str(sql_file))
                raise AssertionError("Expected RuntimeError when remote tenant DB credentials are missing")
            except RuntimeError as exc:
                assert "missing tenant database credentials" in str(exc)
            assert remote_calls == []

            r_backend.import_database_as_tenant(
                "wp_remote_db",
                str(sql_file),
                db_user="u_remote_tenant",
                db_password="RemoteTenantPass!",
            )
            assert len(remote_calls) == 1
            rcall = remote_calls[0]
            assert 'user="u_remote_tenant"' in rcall["cnf"]
            assert 'password="RemoteTenantPass!"' in rcall["cnf"]
            assert 'user="rdb_admin"' not in rcall["cnf"]
            assert b"DEFINER=`u_src_site`" not in rcall["stdin"]
            assert b"Post mentioning DEFINER=`admin`@`localhost` inside body" in rcall["stdin"]

        # Verify backup_verify.trial_restore_sql uses import_database_as_panel
        from app.services import backup_verify
        from app.services.shell import CommandResult

        panel_imports = []
        with patch.object(mariadb, "_run_sql", return_value=CommandResult("sql", 0, "", "")), \
             patch.object(mariadb, "import_database_as_panel", side_effect=lambda n, p: panel_imports.append((n, p))), \
             patch.object(mariadb.shell, "run", return_value=CommandResult("cnt", 0, "4\n", "")):
            res = backup_verify.trial_restore_sql(sql_file)
            assert res["ok"] is True and res["tables"] == 4
            assert len(panel_imports) == 1 and panel_imports[0][0].startswith("spanel_verify_")
    finally:
        settings.command_dry_run = orig_dry


def test_p1_4_legacy_admin_owned_resources_warning_and_owner_transfer():
    from fastapi import HTTPException
    from app.api import websites
    from app.models.entities import DnsZone, MailAccount, MailDomain, SftpAccount, SiteApp
    from app.services import sftp_accounts, teardown

    db = _make_db()
    admin = User(username="admin", email="admin@example.com", hashed_password="x", role="admin", is_active=True)
    reseller = User(username="res1", email="res1@example.com", hashed_password="x", role="reseller", is_active=True)
    end_user = User(
        username="alice",
        email="alice@example.com",
        hashed_password="x",
        role="user",
        is_active=True,
        website_limit=2,
        database_limit=2,
        sftp_accounts_limit=2,
        mail_accounts_limit=5,
        storage_limit_mb=1024,
    )
    capped_user = User(
        username="bobcap",
        email="bobcap@example.com",
        hashed_password="x",
        role="user",
        is_active=True,
        website_limit=1,
    )
    db.add_all([admin, reseller, end_user, capped_user])
    db.commit()

    bob_existing = Website(
        domain="bob-existing.example.com",
        owner_id=capped_user.id,
        root_path="/home/bobcap/bob-existing.example.com",
        php_version="8.4",
        linux_user="bobcap",
        app_type="php",
    )
    db.add(bob_existing)
    db.commit()

    app_row = SiteApp(
        owner_id=reseller.id,
        name="legacy-node-app",
        kind="node",
        port=4310,
    )
    db.add(app_row)
    db.commit()

    legacy_site = Website(
        domain="legacy-admin.example.com",
        owner_id=reseller.id,
        root_path="/home/res1/legacy-admin.example.com",
        php_version="8.4",
        linux_user="res1",
        app_type="wordpress",
        app_id=app_row.id,
    )
    db.add(legacy_site)
    db.commit()

    legacy_db = DatabaseAccount(
        owner_id=reseller.id,
        website_id=legacy_site.id,
        db_name="wp_legacy_admin",
        db_user="u_legacy_admin",
        db_password="secret",
    )
    legacy_sftp = SftpAccount(
        owner_id=reseller.id,
        website_id=legacy_site.id,
        label="deploy",
        linux_user="sftp_res1_deploy_a1b2c3",
        chroot_path="/var/lib/spanel-sftp/sftp_res1_deploy_a1b2c3",
    )
    legacy_zone = DnsZone(name="legacy-admin.example.com", owner_id=reseller.id)
    legacy_mail_domain = MailDomain(domain="legacy-admin.example.com", owner_id=reseller.id)
    legacy_mailbox = MailAccount(
        owner_id=reseller.id,
        domain="legacy-admin.example.com",
        local_part="info",
        password_hash="{SHA512-CRYPT}$6$xyz",
    )
    db.add_all([legacy_db, legacy_sftp, legacy_zone, legacy_mail_domain, legacy_mailbox])
    db.commit()

    report = websites.audit_privileged_role_owned_resources(db)
    assert len(report["websites"]) == 1 and report["websites"][0]["domain"] == "legacy-admin.example.com"
    assert len(report["databases"]) == 1 and report["databases"][0]["db_name"] == "wp_legacy_admin"
    assert any("legacy-admin.example.com" in w for w in report["warnings"])
    assert any("wp_legacy_admin" in w for w in report["warnings"])

    # 1. Cannot transfer ownership to another privileged role (reseller)
    try:
        websites.transfer_website_owner(
            website_id=legacy_site.id,
            payload=websites.WebsiteTransferOwnerPayload(target_owner_id=reseller.id),
            request=None,
            db=db,
            current_user=admin,
        )
        raise AssertionError("Expected 403 when transferring website to Reseller")
    except HTTPException as exc:
        assert exc.status_code == 403

    # 2. Cannot transfer to an End User who has reached website_limit
    try:
        websites.transfer_website_owner(
            website_id=legacy_site.id,
            payload=websites.WebsiteTransferOwnerPayload(target_owner_id=capped_user.id),
            request=None,
            db=db,
            current_user=admin,
        )
        raise AssertionError("Expected 403 when target End User has reached website_limit")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "website limit" in exc.detail.lower()

    # 3. If vhost rewrite fails after runtime move, runtime is rolled back to old path/owner
    move_calls = []
    mail_move_calls = []
    from app.services import mail as mail_service

    with patch.object(websites.site_users, "move_site_runtime", side_effect=lambda o, n, u, p=None: move_calls.append((o, n, u))), \
         patch.object(mail_service, "move_domain_maildirs", side_effect=lambda o, n, d: mail_move_calls.append((o, n, d))), \
         patch.object(websites, "_ensure_default_waf_file"), \
         patch.object(websites, "_rewrite_website_vhost", side_effect=[HTTPException(status_code=500, detail="nginx failed"), None]):
        try:
            websites.transfer_website_owner(
                website_id=legacy_site.id,
                payload=websites.WebsiteTransferOwnerPayload(target_owner_id=end_user.id),
                request=None,
                db=db,
                current_user=admin,
            )
            raise AssertionError("Expected 500 when vhost rewrite fails")
        except HTTPException as exc:
            assert exc.status_code == 500
    expected_alice_root = websites.site_users.site_root_for_panel_username("alice", "legacy-admin.example.com") if hasattr(websites.site_users, "site_root_for_panel_username") else websites.site_users.site_root_for_panel_user("alice", "legacy-admin.example.com")
    assert len(move_calls) == 2
    assert move_calls[0] == ("/home/res1/legacy-admin.example.com", expected_alice_root, "alice")
    assert move_calls[1] == (expected_alice_root, "/home/res1/legacy-admin.example.com", "res1")
    db.refresh(legacy_site)
    assert legacy_site.owner_id == reseller.id

    # 4. Transferring ownership to End User moves site + DB + SFTP + SiteApp + DNS + Mail (including physical maildir move)
    sftp_ensured = []
    mail_move_calls.clear()
    with patch.object(websites.site_users, "move_site_runtime"), \
         patch.object(mail_service, "move_domain_maildirs", side_effect=lambda o, n, d: mail_move_calls.append((o, n, d))), \
         patch.object(websites, "_ensure_default_waf_file"), \
         patch.object(websites, "_rewrite_website_vhost"), \
         patch.object(sftp_accounts, "ensure_account", side_effect=lambda owner_u, sub_u, root: (sftp_ensured.append((owner_u, sub_u, root)) or f"/var/lib/spanel-sftp/{sub_u}")):
        updated = websites.transfer_website_owner(
            website_id=legacy_site.id,
            payload=websites.WebsiteTransferOwnerPayload(target_owner_id=end_user.id),
            request=None,
            db=db,
            current_user=admin,
        )
    assert updated.owner_id == end_user.id
    assert updated.linux_user == "alice"
    assert updated.root_path == expected_alice_root
    assert sftp_ensured == [("alice", "sftp_res1_deploy_a1b2c3", expected_alice_root)]
    assert mail_move_calls == [("res1", "alice", "legacy-admin.example.com")]

    db.refresh(legacy_db)
    db.refresh(legacy_sftp)
    db.refresh(app_row)
    db.refresh(legacy_zone)
    db.refresh(legacy_mail_domain)
    db.refresh(legacy_mailbox)
    assert legacy_db.owner_id == end_user.id
    assert legacy_sftp.owner_id == end_user.id
    assert app_row.owner_id == end_user.id
    assert legacy_zone.owner_id == end_user.id
    assert legacy_mail_domain.owner_id == end_user.id
    assert legacy_mailbox.owner_id == end_user.id

    # 5. Deleting the old owner (reseller) does NOT delete transferred resources
    teardown.purge_owned_resources(db, reseller.id)
    db.delete(reseller)
    db.commit()

    assert db.query(Website).filter(Website.id == legacy_site.id).first() is not None
    assert db.query(DatabaseAccount).filter(DatabaseAccount.id == legacy_db.id).first() is not None
    assert db.query(SftpAccount).filter(SftpAccount.id == legacy_sftp.id).first() is not None
    assert db.query(SiteApp).filter(SiteApp.id == app_row.id).first() is not None
    assert db.query(MailDomain).filter(MailDomain.id == legacy_mail_domain.id).first() is not None
    assert db.query(MailAccount).filter(MailAccount.id == legacy_mailbox.id).first() is not None

    report_after = websites.audit_privileged_role_owned_resources(db)
    assert report_after["websites"] == []
    assert report_after["databases"] == []
    assert report_after["warnings"] == []


if __name__ == "__main__":
    import tempfile as _tf

    test_installer_and_updater_python_heredocs_compile_cleanly()
    test_mariadb_rejects_reserved_databases()
    test_wp_database_authorization_blocks_cross_tenant_and_unregistered_dbs()
    test_wp_slug_and_config_validators_reject_injection()
    test_helper_site_runtime_move_and_update_frontend_build_hardening()
    test_panel_settings_get_brand_returns_expected_fields()
    test_wordpress_manager_toggles_and_endpoints_end_to_end()
    test_round_9_snapshot_restore_clone_and_db_isolation()
    test_impersonate_and_exit_end_to_end()
    test_strict_role_isolation_between_admin_reseller_and_end_user()
    with _tf.TemporaryDirectory() as _td:
        test_p0_1_clone_rejects_symlink_and_mismatched_uid_and_cleans_orphan_db(Path(_td))
    with _tf.TemporaryDirectory() as _td2:
        test_p0_2_uploads_php_blocked_preserves_vhost_and_subpath_app_type(Path(_td2))
    with _tf.TemporaryDirectory() as _td3:
        test_p1_2_and_p1_3_definer_strip_and_no_root_fallback(Path(_td3))
    test_p1_4_legacy_admin_owned_resources_warning_and_owner_transfer()
    print("ALL TESTS PASSED")


