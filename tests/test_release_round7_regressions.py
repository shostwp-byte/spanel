"""Regressions for Round 7 (website ownership, disk reconcile, site-runtime-move, and heredocs)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))

import _isolation_bash  # noqa: E402
from _isolation_bash import HELPER, run_bash  # noqa: E402
from app.api import websites as websites_api  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.models.entities import AuditLog, DnsZone, User, Website  # noqa: E402
from app.schemas.schemas import WebsiteCreate  # noqa: E402

UPDATE_SH = REPO_ROOT / "installer" / "update.sh"


def _find_working_bash() -> str | None:
    for name in ("bash", "sh"):
        cand = shutil.which(name)
        if not cand or cand.lower().endswith("\\windows\\system32\\bash.exe"):
            continue
        try:
            proc = subprocess.run([cand, "-c", "[[ 1 -eq 1 ]]"], capture_output=True, timeout=5)
            if proc.returncode == 0:
                return cand
        except OSError:
            continue
    return None


WORKING_BASH = _find_working_bash()


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_user(
    db,
    username: str,
    role: str = "user",
    email: str | None = None,
    website_limit: int = 10,
) -> User:
    user = User(
        username=username,
        email=email or f"{username}@example.net",
        hashed_password="x",
        role=role,
        is_active=True,
        website_limit=website_limit,
        database_limit=10,
        sftp_accounts_limit=10,
        mail_accounts_limit=10,
        storage_limit_mb=1024,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _managed_vhost_text(
    domain: str,
    user: str,
    *,
    ssl: bool = False,
    wordpress: bool = False,
    php_version: str = "8.4",
) -> str:
    root_dir = f"/home/{user}/{domain}"
    docroot = f"{root_dir}/public_html"
    ssl_line = "    listen 443 ssl;\n" if ssl else ""
    wp_marker = "    # SPANEL FASTCGI CACHE SERVER BEGIN\n" if wordpress else ""
    return (
        "server {\n"
        "    listen 80;\n"
        f"{ssl_line}"
        f"    server_name {domain};\n"
        f"    root {docroot};\n"
        f"    disable_symlinks if_not_owner from={root_dir};\n"
        f"{wp_marker}"
        f"    fastcgi_pass unix:/run/php/php{php_version}-fpm-{user}.sock;\n"
        "    # SPANEL ACME CHALLENGE\n"
        "    # SPANEL CUSTOM INCLUDE\n"
        "}\n"
    )


def test_create_website_never_claims_an_existing_website(db_session):
    """An End User creating a domain already owned by admin (or another user)
    must receive 409 Conflict and never take over ownership or files."""
    admin = _make_user(db_session, "admin", role="admin", email="admin@panel.local")
    attacker = _make_user(
        db_session,
        "brandco",
        role="user",
        email="owner@brandco.example.com",
    )
    admin_site = Website(
        domain="brandco.example.com",
        owner_id=admin.id,
        root_path="/home/admin/brandco.example.com",
        document_root="public_html",
        linux_user="admin",
        php_version="8.4",
        app_type="wordpress",
        status="active",
    )
    db_session.add(admin_site)
    db_session.commit()
    db_session.refresh(admin_site)

    with patch.object(websites_api.site_users, "move_site_runtime") as move_mock:
        with pytest.raises(HTTPException) as exc_info:
            websites_api.create_website(
                payload=WebsiteCreate(domain="brandco.example.com", install_wordpress=False),
                request=None,
                db=db_session,
                current_user=attacker,
            )

    assert exc_info.value.status_code == 409
    assert move_mock.call_count == 0
    db_session.refresh(admin_site)
    assert admin_site.owner_id == admin.id
    assert admin_site.root_path == "/home/admin/brandco.example.com"


def test_create_website_rejects_domain_when_vhost_exists_on_disk(db_session):
    """Even if the Website table has no row for a domain, an existing Nginx
    vhost on disk must block creation with 409 Conflict."""
    _make_user(db_session, "admin", role="admin")
    customer = _make_user(db_session, "alice", role="user")

    with patch.object(websites_api.nginx, "vhost_exists", return_value=True), \
         patch.object(websites_api.nginx, "write_vhost") as write_mock:
        with pytest.raises(HTTPException) as exc_info:
            websites_api.create_website(
                payload=WebsiteCreate(domain="untracked.example.com", install_wordpress=False),
                request=None,
                db=db_session,
                current_user=customer,
            )

    assert exc_info.value.status_code == 409
    assert write_mock.call_count == 0
    assert db_session.query(Website).filter(Website.domain == "untracked.example.com").first() is None


def test_listing_websites_does_not_reconcile_from_disk(db_session, tmp_path):
    """GET /api/websites must only query the database and never scan or adopt
    vhosts from disk during a request."""
    _make_user(db_session, "admin", role="admin")
    alice = _make_user(db_session, "alice", role="user")

    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    (conf_dir / "diskonly.example.com.conf").write_text(
        _managed_vhost_text("diskonly.example.com", "alice"),
        encoding="utf-8",
    )

    real_path = websites_api.Path

    def fake_path(arg):
        if str(arg) == "/etc/nginx/conf.d":
            return conf_dir
        return real_path(arg)

    with patch.object(websites_api, "Path", side_effect=fake_path), \
         patch.object(settings, "command_dry_run", False), \
         patch.object(websites_api, "reconcile_websites_from_disk", wraps=websites_api.reconcile_websites_from_disk) as rec_spy:
        rows = websites_api.list_websites(q="", scope="", request=None, db=db_session, current_user=alice)

    assert rec_spy.call_count == 0
    assert rows == []
    assert db_session.query(Website).count() == 0


def test_reconcile_adds_lost_rows_but_never_changes_an_owner(db_session, tmp_path):
    """reconcile_websites_from_disk restores missing Website rows from managed
    vhosts on disk without moving files, and never reassigns existing websites
    even when an End User matches by email @domain, username prefix, or DNS zone."""
    admin = _make_user(db_session, "admin", role="admin", email="root@panel.local")
    alice = _make_user(db_session, "alice", role="user", email="alice@example.net")
    sneaky = _make_user(
        db_session,
        "adminportal",
        role="user",
        email="ceo@adminportal.example.com",
    )

    admin_site = Website(
        domain="adminportal.example.com",
        owner_id=admin.id,
        root_path="/home/admin/adminportal.example.com",
        document_root="public_html",
        linux_user="admin",
        php_version="8.4",
        app_type="wordpress",
        status="active",
    )
    db_session.add(admin_site)
    db_session.add(DnsZone(name="adminportal.example.com", owner_id=sneaky.id))
    db_session.commit()
    db_session.refresh(admin_site)

    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    (conf_dir / "lost-site.example.com.conf").write_text(
        _managed_vhost_text("lost-site.example.com", "alice", wordpress=True, php_version="8.3"),
        encoding="utf-8",
    )
    # Orphan vhost for a user that does not exist in the DB must not be handed to sneaky or admin.
    (conf_dir / "ghost.example.com.conf").write_text(
        _managed_vhost_text("ghost.example.com", "nonexistentuser"),
        encoding="utf-8",
    )

    real_path = websites_api.Path

    def fake_path(arg):
        if str(arg) == "/etc/nginx/conf.d":
            return conf_dir
        if str(arg) == "/etc/letsencrypt/live":
            return tmp_path / "letsencrypt-live"
        return real_path(arg)

    with patch.object(websites_api, "Path", side_effect=fake_path), \
         patch.object(settings, "command_dry_run", False), \
         patch.object(websites_api, "_ensure_default_waf_file"), \
         patch.object(websites_api, "_rewrite_website_vhost") as rewrite_mock, \
         patch.object(websites_api.site_users, "move_site_runtime") as move_mock:
        websites_api.reconcile_websites_from_disk(db_session)

    assert move_mock.call_count == 0
    assert rewrite_mock.call_count == 0

    recovered = db_session.query(Website).filter(Website.domain == "lost-site.example.com").first()
    assert recovered is not None
    assert recovered.owner_id == alice.id
    assert recovered.root_path == "/home/alice/lost-site.example.com"
    assert recovered.linux_user == "alice"
    assert recovered.php_version == "8.3"
    assert recovered.app_type == "wordpress"

    assert db_session.query(Website).filter(Website.domain == "ghost.example.com").first() is None

    db_session.refresh(admin_site)
    assert admin_site.owner_id == admin.id
    assert admin_site.root_path == "/home/admin/adminportal.example.com"
    assert admin_site.linux_user == "admin"


def test_reconcile_skips_deleted_user_and_terminated_websites(db_session, tmp_path):
    """P2-1: Leftover vhosts from delete_website, delete_user (even if a new
    user with the same username is later created), or provisioning_terminate
    must be cleaned up and never adopted by the new user."""
    _make_user(db_session, "admin", role="admin")
    # New customer created with the same username "reuseduser" after an earlier delete_user
    reused_user = _make_user(db_session, "reuseduser", role="user")
    other_user = _make_user(db_session, "otheruser", role="user")

    db_session.add_all([
        AuditLog(user_id=1, action="delete_website", target="deleted-site.example.com", detail=""),
        AuditLog(user_id=1, action="delete_user", target="reuseduser", detail=""),
        AuditLog(
            user_id=None,
            action="provisioning_terminate",
            target="whmcs-svc-99",
            detail="terminated-site.example.com",
        ),
    ])
    db_session.commit()

    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    for domain, uname in (
        ("deleted-site.example.com", "otheruser"),
        ("old-user-site.example.com", "reuseduser"),
        ("terminated-site.example.com", "otheruser"),
    ):
        (conf_dir / f"{domain}.conf").write_text(
            _managed_vhost_text(domain, uname),
            encoding="utf-8",
        )

    real_path = websites_api.Path

    def fake_path(arg):
        if str(arg) == "/etc/nginx/conf.d":
            return conf_dir
        return real_path(arg)

    deleted_vhosts: list[str] = []
    with patch.object(websites_api, "Path", side_effect=fake_path), \
         patch.object(settings, "command_dry_run", False), \
         patch.object(websites_api.nginx, "delete_wordpress_vhost", side_effect=deleted_vhosts.append):
        websites_api.reconcile_websites_from_disk(db_session)

    assert db_session.query(Website).filter(Website.owner_id == reused_user.id).count() == 0
    assert db_session.query(Website).filter(Website.owner_id == other_user.id).count() == 0
    assert set(deleted_vhosts) == {
        "deleted-site.example.com",
        "old-user-site.example.com",
        "terminated-site.example.com",
    }


def test_reconcile_sets_ssl_mode_letsencrypt_only_when_le_cert_exists(db_session, tmp_path):
    """P2-4: Vhosts with `listen 443 ssl` but no Let's Encrypt certificate files
    in /etc/letsencrypt/live/<domain> must be recovered with ssl_mode='manual',
    while domains with Let's Encrypt files get ssl_mode='letsencrypt'."""
    _make_user(db_session, "admin", role="admin")
    alice = _make_user(db_session, "alice", role="user")

    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    (conf_dir / "manual-ssl.example.com.conf").write_text(
        _managed_vhost_text("manual-ssl.example.com", "alice", ssl=True),
        encoding="utf-8",
    )
    (conf_dir / "le-ssl.example.com.conf").write_text(
        _managed_vhost_text("le-ssl.example.com", "alice", ssl=True),
        encoding="utf-8",
    )

    le_live = tmp_path / "letsencrypt-live"
    le_domain_dir = le_live / "le-ssl.example.com"
    le_domain_dir.mkdir(parents=True)
    (le_domain_dir / "fullchain.pem").write_text("CERT", encoding="utf-8")
    (le_domain_dir / "privkey.pem").write_text("KEY", encoding="utf-8")

    real_path = websites_api.Path

    def fake_path(arg):
        if str(arg) == "/etc/nginx/conf.d":
            return conf_dir
        if str(arg) == "/etc/letsencrypt/live":
            return le_live
        return real_path(arg)

    with patch.object(websites_api, "Path", side_effect=fake_path), \
         patch.object(settings, "command_dry_run", False), \
         patch.object(websites_api, "_ensure_default_waf_file"):
        websites_api.reconcile_websites_from_disk(db_session)

    manual_site = db_session.query(Website).filter(Website.domain == "manual-ssl.example.com").first()
    le_site = db_session.query(Website).filter(Website.domain == "le-ssl.example.com").first()
    assert manual_site is not None and manual_site.owner_id == alice.id
    assert manual_site.ssl_enabled is True
    assert manual_site.ssl_mode == "manual"
    assert le_site is not None and le_site.owner_id == alice.id
    assert le_site.ssl_enabled is True
    assert le_site.ssl_mode == "letsencrypt"


def _site_runtime_move_case_block() -> str:
    text = HELPER.read_text(encoding="utf-8")
    match = re.search(r"^\s*site-runtime-move\)\n.*?^\s*;;\n", text, re.M | re.S)
    assert match, "site-runtime-move case block not found in spanel-helper.sh"
    return match.group(0)


@pytest.mark.skipif(not WORKING_BASH, reason="needs bash")
def test_site_runtime_move_refuses_an_existing_target(tmp_path):
    """P2-2 & P2-3: Execute the real site-runtime-move block in bash.
    It must refuse when the destination already exists (directory, file, or
    symlink) without touching source or destination, move cleanly when the
    destination does not exist, and create destination/public_html when the
    source directory was already removed."""
    home = tmp_path / "home"
    (home / "alice" / "site.example" / "public_html").mkdir(parents=True)
    source_dir = home / "alice" / "site.example"
    marker = source_dir / "public_html" / "index.php"
    marker.write_text("<?php echo 'alice';", encoding="utf-8")

    calls = tmp_path / "calls.log"
    case_block = _site_runtime_move_case_block()
    harness = (
        "set -euo pipefail\n"
        'HOME_ROOT="$TEST_HOME"\n'
        'deny() { echo "spanel-helper: $*" >&2; exit 1; }\n'
        "require_linux_user() { :; }\n"
        'require_managed_path() { echo "$1"; }\n'
        'ensure_panel_user_home() { mkdir -p "$HOME_ROOT/$1"; }\n'
        "delete_site_php_pools() { :; }\n"
        'delete_laravel_worker_units() { echo "laravel $1 $2" >> "$TEST_CALLS"; }\n'
        'delete_nodejs_worker_units() { echo "nodejs $1 $2" >> "$TEST_CALLS"; }\n'
        "harden_site_dir_path() { :; }\n"
        "own_site_tree() { :; }\n"
        "ensure_php_pool() { :; }\n"
        "run_move() {\n"
        "  case site-runtime-move in\n"
        f"{case_block}"
        "  esac\n"
        "}\n"
    )

    with patch.object(_isolation_bash, "BASH", WORKING_BASH):
        # 1. Existing directory at target -> refused, both source and target preserved
        bob_dir = home / "bob" / "site.example"
        bob_dir.mkdir(parents=True)
        bob_keep = bob_dir / "keep.txt"
        bob_keep.write_text("bob-original", encoding="utf-8")

        proc_dir = run_bash(
            tmp_path,
            harness + f'run_move bob "{source_dir.as_posix()}" "{bob_dir.as_posix()}" 8.4\n',
            check=False,
            env={"TEST_HOME": home.as_posix(), "TEST_CALLS": calls.as_posix()},
        )
        assert proc_dir.returncode != 0
        assert "target path already exists" in proc_dir.stderr
        assert marker.read_text(encoding="utf-8") == "<?php echo 'alice';"
        assert bob_keep.read_text(encoding="utf-8") == "bob-original"
        # A refused move must not tear down the source site's workers.
        assert not calls.exists()

        # 2. Existing regular file at target -> refused
        file_target = home / "bob" / "file-target.example"
        file_target.write_text("do-not-overwrite", encoding="utf-8")
        proc_file = run_bash(
            tmp_path,
            harness + f'run_move bob "{source_dir.as_posix()}" "{file_target.as_posix()}" 8.4\n',
            check=False,
            env={"TEST_HOME": home.as_posix(), "TEST_CALLS": calls.as_posix()},
        )
        assert proc_file.returncode != 0
        assert "target path already exists" in proc_file.stderr
        assert file_target.read_text(encoding="utf-8") == "do-not-overwrite"
        assert marker.exists()

        # 3. Existing symlink (including dangling symlink) at target -> refused
        link_target = home / "bob" / "link-target.example"
        try:
            link_target.symlink_to(tmp_path / "nonexistent-dangling-target")
            has_symlink = True
        except OSError:
            has_symlink = False

        if has_symlink:
            proc_link = run_bash(
                tmp_path,
                harness + f'run_move bob "{source_dir.as_posix()}" "{link_target.as_posix()}" 8.4\n',
                check=False,
                env={"TEST_HOME": home.as_posix(), "TEST_CALLS": calls.as_posix()},
            )
            assert proc_link.returncode != 0
            assert "target path already exists" in proc_link.stderr
            assert link_target.is_symlink()
            assert marker.exists()

        # 4. Clean move when target does not exist -> succeeds and preserves site files
        clean_target = home / "bob" / "moved.example"
        proc_ok = run_bash(
            tmp_path,
            harness + f'run_move bob "{source_dir.as_posix()}" "{clean_target.as_posix()}" 8.4\n',
            check=True,
            env={"TEST_HOME": home.as_posix(), "TEST_CALLS": calls.as_posix()},
        )
        assert proc_ok.returncode == 0
        assert (clean_target / "public_html" / "index.php").read_text(encoding="utf-8") == "<?php echo 'alice';"
        assert not source_dir.exists()
        # The units pointing at the old path are removed before the move;
        # websites.py recreates them at the new path afterwards.
        assert calls.read_text(encoding="utf-8").splitlines() == [
            f"laravel alice {source_dir.as_posix()}",
            f"nodejs alice {source_dir.as_posix()}",
        ]

        # 5. P2-2: Source does not exist and target does not exist -> creates target/public_html
        missing_source = home / "alice" / "already-gone.example"
        fresh_target = home / "bob" / "recreated.example"
        proc_missing_src = run_bash(
            tmp_path,
            harness + f'run_move bob "{missing_source.as_posix()}" "{fresh_target.as_posix()}" 8.4\n',
            check=True,
            env={"TEST_HOME": home.as_posix(), "TEST_CALLS": calls.as_posix()},
        )
        assert proc_missing_src.returncode == 0
        assert (fresh_target / "public_html").is_dir()


def test_all_python_heredocs_compile():
    """Every quoted Python heredoc (<<'PY...') in installer/update.sh and
    installer/files/spanel-helper.sh must be valid Python syntax without
    stray shell lines."""
    heredoc_re = re.compile(r"<<'(PY[A-Z0-9_]*)'[^\n]*\n(.*?)\n[ \t]*\1(?=\n|$)", re.S)
    counts: dict[str, int] = {}
    for script_path in (UPDATE_SH, HELPER):
        text = script_path.read_text(encoding="utf-8")
        matches = list(heredoc_re.finditer(text))
        counts[script_path.name] = len(matches)
        for idx, match in enumerate(matches, start=1):
            tag = match.group(1)
            source = match.group(2)
            compile(source, f"{script_path.name}:{tag}#{idx}", "exec")

    assert counts["update.sh"] >= 6
    assert counts["spanel-helper.sh"] >= 29
