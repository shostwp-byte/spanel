"""Unit and behavioral regression tests for the End-User Laravel Manager module."""
import ast
import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine

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

HELPER_PATH = REPO_ROOT / "installer" / "files" / "spanel-helper.sh"
LARAVEL_API_PATH = REPO_ROOT / "backend" / "app" / "api" / "laravel_manager.py"


def _make_request(headers: Optional[Dict[str, str]] = None):
    return SimpleNamespace(headers=headers or {})


def _find_bash() -> Optional[str]:
    candidates: List[str] = []
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            gh_root = Path(local_app) / "GitHubDesktop"
            if gh_root.is_dir():
                for pat in (
                    "app-*/resources/app/git/usr/bin/bash.exe",
                    "app-*/resources/app/git/usr/bin/sh.exe",
                ):
                    for cand in sorted(gh_root.glob(pat), reverse=True):
                        if cand.is_file():
                            candidates.append(str(cand))
        for fixed in (
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\sh.exe"),
        ):
            if fixed.is_file():
                candidates.append(str(fixed))

    which_bash = shutil.which("bash")
    if which_bash:
        low = which_bash.lower()
        if os.name != "nt" or ("system32" not in low and "windowsapps" not in low):
            candidates.append(which_bash)

    for cand in candidates:
        try:
            cand_env = {
                **os.environ,
                "PATH": str(Path(cand).parent) + os.pathsep + os.environ.get("PATH", ""),
            }
            probe = subprocess.run(
                [
                    cand,
                    "-c",
                    '[[ -n "${BASH_VERSION:-}" ]] && command -v sha256sum >/dev/null 2>&1 && command -v awk >/dev/null 2>&1 && command -v mv >/dev/null 2>&1',
                ],
                capture_output=True,
                timeout=5,
                env=cand_env,
            )
            if probe.returncode == 0:
                return cand
        except OSError:
            continue
    return None


def _extract_helper_section(start_marker: str, end_marker: str) -> str:
    text = HELPER_PATH.read_text(encoding="utf-8")
    return text.split(start_marker, 1)[1].split(end_marker, 1)[0]


def _run_site_file_delete_python(
    py_code: str,
    args: List[str],
    *,
    fake_symlinks: Optional[set] = None,
    uid_overrides: Optional[Dict[Path, int]] = None,
    default_uid: int = 1001,
) -> tuple[int, str]:
    """Execute the exact embedded Python script from `site-file-delete` in `spanel-helper.sh`.

    Uses a faithful `dir_fd` + `O_NOFOLLOW` POSIX shim when running on Windows (`nt`) or
    when simulating unprivileged symlinks/UIDs, and also verifies real `O_NOFOLLOW` flag bits.
    """
    fake_symlinks_resolved = {Path(os.path.normpath(str(p))) for p in (fake_symlinks or set())}
    uid_map = {Path(os.path.normpath(str(k))): v for k, v in (uid_overrides or {}).items()}

    O_RDONLY = getattr(os, "O_RDONLY", 0)
    O_DIRECTORY = getattr(os, "O_DIRECTORY", 0x10000) or 0x10000
    O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0x20000) or 0x20000

    fd_table: Dict[int, Path] = {}
    next_fd = [100]

    def _is_sym(p: Path) -> bool:
        norm_p = Path(os.path.normpath(str(p)))
        if norm_p in fake_symlinks_resolved:
            return True
        try:
            return p.is_symlink()
        except OSError:
            return False

    def _sim_open(path_arg, flags, dir_fd=None):
        if dir_fd is not None:
            if dir_fd not in fd_table:
                raise OSError(errno.EBADF, "Bad file descriptor")
            full = Path(os.path.normpath(str(fd_table[dir_fd] / str(path_arg))))
        else:
            full = Path(os.path.normpath(str(path_arg)))

        if (flags & O_NOFOLLOW) and _is_sym(full):
            raise OSError(errno.ELOOP, f"Too many levels of symbolic links: {full}")
        if not full.exists():
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(full))
        if (flags & O_DIRECTORY) and not full.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", str(full))

        fd = next_fd[0]
        next_fd[0] += 1
        fd_table[fd] = full
        return fd

    def _sim_close(fd):
        if fd not in fd_table:
            raise OSError(errno.EBADF, "Bad file descriptor")
        del fd_table[fd]

    def _sim_lstat(path_arg, *, dir_fd=None):
        if dir_fd is not None:
            if dir_fd not in fd_table:
                raise OSError(errno.EBADF, "Bad file descriptor")
            full = Path(os.path.normpath(str(fd_table[dir_fd] / str(path_arg))))
        else:
            full = Path(os.path.normpath(str(path_arg)))

        uid = uid_map.get(full, default_uid)
        if _is_sym(full):
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=uid)
        real_st = os.lstat(full)
        return SimpleNamespace(st_mode=real_st.st_mode, st_uid=uid)

    def _sim_unlink(path_arg, *, dir_fd=None):
        if dir_fd is not None:
            if dir_fd not in fd_table:
                raise OSError(errno.EBADF, "Bad file descriptor")
            full = Path(os.path.normpath(str(fd_table[dir_fd] / str(path_arg))))
        else:
            full = Path(os.path.normpath(str(path_arg)))

        if full in fake_symlinks_resolved:
            fake_symlinks_resolved.remove(full)
            return
        os.remove(full)

    sim_os = SimpleNamespace(
        path=os.path,
        sep=os.sep,
        O_RDONLY=O_RDONLY,
        O_DIRECTORY=O_DIRECTORY,
        O_NOFOLLOW=O_NOFOLLOW,
        open=_sim_open,
        close=_sim_close,
        lstat=_sim_lstat,
        unlink=_sim_unlink,
    )
    sim_sys = SimpleNamespace(argv=["site-file-delete.py", *args])
    ns: Dict[str, Any] = {"__builtins__": __builtins__}

    with patch.dict(sys.modules, {"os": sim_os, "sys": sim_sys}):
        try:
            exec(compile(py_code, "<spanel-helper:site-file-delete>", "exec"), ns)
            assert not fd_table, f"File descriptors leaked: {fd_table}"
            return 0, ""
        except SystemExit as exc:
            assert not fd_table, f"File descriptors leaked on SystemExit: {fd_table}"
            if exc.code in (None, 0):
                return 0, ""
            return 1, str(exc.code)
        except OSError as exc:
            assert not fd_table, f"File descriptors leaked on OSError: {fd_table}"
            return 1, str(exc)


# ── Legacy & Core Functional Suite ───────────────────────────────────────────

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
    assert parsed["session_secure_cookie"] is False

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
    valid_key = lm._generate_laravel_app_key()
    (app_dir / ".env").write_text(
        'APP_NAME="SHost CRM"\n'
        "APP_ENV=production\n"
        f"APP_KEY={valid_key}\n"
        "APP_DEBUG=false\n"
        "DB_DATABASE=shostc_crm\n"
        "DB_USERNAME=shostc_user\n"
        "CACHE_STORE=redis\n"
        "QUEUE_CONNECTION=redis\n"
        "SESSION_SECURE_COOKIE=true\n"
        "SESSION_HTTP_ONLY=true\n",
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
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom=lm._update_laravel_nginx_security("", dotenv_git=True, storage_php=True),
        created_at=None,
    )
    owner = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [owner, None]

    with patch.object(lm.cron, "list_cron_entries", return_value=[]):
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
    assert summary["security_passed"] == 7


def test_install_laravel_instance_configures_public_docroot_env_and_scheduler(tmp_path):
    site_root = tmp_path / "domains" / "erp.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)
    # Benign default placeholder is safely purged before Composer create-project runs
    (app_dir / "index.html").write_text("<h1>Welcome</h1>", encoding="utf-8")

    site = SimpleNamespace(
        id=15,
        owner_id=2,
        domain="erp.shost.vn",
        root_path=str(site_root),
        document_root="public_html",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="none",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(
        id=2,
        username="shostc",
        role="end_user",
        package=None,
        website_limit=5,
        subdomain_limit=5,
        database_limit=5,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = lambda: None
    db.query.return_value.filter.return_value.all.return_value = [site]
    db.query.return_value.filter.return_value.count.return_value = 0

    def _fake_composer_and_artisan(linux_user, target_dir, cmd_args, **kwargs):
        if "create-project" in cmd_args:
            (target_dir / "public").mkdir(parents=True, exist_ok=True)
            (target_dir / "storage" / "logs").mkdir(parents=True, exist_ok=True)
            (target_dir / "bootstrap" / "cache").mkdir(parents=True, exist_ok=True)
            (target_dir / "artisan").write_text("#!/usr/bin/env php\n<?php\n", encoding="utf-8")
            (target_dir / "composer.json").write_text(
                json.dumps({"require": {"php": "^8.2", "laravel/framework": "^11.0"}}),
                encoding="utf-8",
            )
            (target_dir / "public" / "index.php").write_text("<?php\n", encoding="utf-8")
        return 0, "OK"

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
            patch("app.api.websites._rewrite_website_vhost") as mock_rewrite_vhost, \
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
            patch.object(lm, "_run_as_site_user", side_effect=_fake_composer_and_artisan), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm.cron, "add_cron") as mock_add_cron, \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"):
        res = lm.install_laravel_instance(payload, db=db, current_user=user)

    assert not (app_dir / "index.html").exists()
    assert site.document_root == "public_html/public"
    assert site.nginx_rewrite_mode == "laravel"
    assert lm.LARAVEL_NGINX_SEC_BEGIN in site.nginx_custom
    assert lm.LARAVEL_NGINX_DOTENV_MARKER in site.nginx_custom
    assert lm.LARAVEL_NGINX_STORAGE_MARKER in site.nginx_custom
    mock_rewrite_vhost.assert_called_once()
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
    assert lm._is_valid_laravel_app_key(env_details["app_key"]) is True
    assert env_details["db_name"] == "lv_erpshost_a1"
    assert res["instance"]["public_docroot_active"] is True
    assert res["instance"]["queue_worker"]["enabled"] is True
    assert res["instance"]["queue_worker"]["workers"] == 2
    assert res["instance"]["security_passed"] == 7


def test_artisan_command_whitelists_and_blocks_disallowed_commands(tmp_path):
    site_root = tmp_path / "domains" / "api.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)
    (app_dir / "artisan").write_text("#!/usr/bin/env php\n<?php\n", encoding="utf-8")
    (app_dir / ".env").write_text(
        f"APP_NAME=API\nAPP_ENV=production\nAPP_DEBUG=false\nAPP_KEY={lm._generate_laravel_app_key()}\n",
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
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_holder = {"instances": {}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
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
        f"APP_NAME=OldName\nAPP_ENV=local\nAPP_DEBUG=true\nAPP_KEY={lm._generate_laravel_app_key()}\n",
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
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_holder = {"instances": {}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
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
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="none",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_holder = {"instances": {}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch("app.api.websites._rewrite_website_vhost") as mock_rewrite_vhost, \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
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

        # Valid package require updates composer.json in dry-run mode
        comp_res = lm.run_composer_action(
            22,
            lm.LaravelComposerPayload(action="require", package="spatie/laravel-permission:^6.10"),
            db=db,
            current_user=user,
        )
        assert any(
            p["name"] == "spatie/laravel-permission" for p in comp_res["instance"]["packages"]
        )

        # Security hardening writes real Nginx directives (never Apache .htaccess) and fixes .env
        sec_res = lm.manage_laravel_security(
            22,
            lm.LaravelSecurityPayload(action="secure"),
            db=db,
            current_user=user,
        )

    assert site.document_root == "public_html/public"
    assert site.nginx_rewrite_mode == "laravel"
    assert not (app_dir / "storage" / "app" / "public" / ".htaccess").exists()
    assert lm.LARAVEL_NGINX_STORAGE_MARKER in site.nginx_custom
    assert "location ^~ /storage/" in site.nginx_custom
    assert lm.LARAVEL_NGINX_DOTENV_MARKER in site.nginx_custom
    mock_rewrite_vhost.assert_called_once()
    env_after = lm._parse_env_details((app_dir / ".env").read_text(encoding="utf-8"))
    assert env_after["app_debug"] is False
    assert lm._is_valid_laravel_app_key(env_after["app_key"]) is True
    assert env_after["session_secure_cookie"] is True
    assert sec_res["instance"]["security_passed"] == 7
    assert sec_res["instance"]["security_total"] == 7


# ── Audit Round 13 & Round 14 Behavioral Security & Lifecycle Suite ──────────

def test_p0_1_no_panel_unlink_and_helper_site_file_delete_blocks_symlink_and_wrong_owner(tmp_path):
    """P0-1: Panel user (`spanel`) never calls `.unlink()` on tenant paths; `site-file-delete`
    traverses from `$HOME_ROOT/<user>` with `O_NOFOLLOW` and checks `st_uid == expected_uid`."""
    source = LARAVEL_API_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    unlink_calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "unlink"
    ]
    assert unlink_calls == [], f"Forbidden .unlink() calls found at lines: {unlink_calls}"
    for banned in (
        "run-as-user",
        "_scaffold_laravel_skeleton",
        "security_overrides",
        "storage/app/public/.htaccess",
        "drop_database_and_user",
    ):
        assert banned not in source, f"Banned token still present in laravel_manager.py: {banned}"

    home_root = tmp_path / "home"
    user_home = home_root / "shostc"
    site_root = user_home / "erp.shost.vn"
    app_dir = site_root / "public_html"
    (app_dir / "storage" / "framework").mkdir(parents=True)
    down_file = app_dir / "storage" / "framework" / "down"
    down_file.write_text('{"time": 1}', encoding="utf-8")

    # Behavioral check (`command_dry_run=False`): _delete_app_file delegates to `site-file-delete`
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as mock_priv:
        ok = lm._delete_app_file("shostc", site_root, app_dir, "storage/framework/down", strict=True)
    assert ok is True
    mock_priv.assert_called_once_with(
        "site-file-delete",
        helper_args=["shostc", str(site_root), "public_html/storage/framework/down"],
        check=False,
    )
    # Panel Python process did NOT unlink the file itself in live mode
    assert down_file.exists()

    # Extract and execute the exact Python `O_NOFOLLOW` implementation from `spanel-helper.sh`
    section = _extract_helper_section("  site-file-delete)\n", "\n  site-file-install)\n")
    py_code = section.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    assert "os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW" in py_code
    assert "os.unlink(leaf, dir_fd=parent_fd)" in py_code

    # (a) Valid owned regular file is unlinked cleanly
    idx_file = app_dir / "index.html"
    idx_file.write_text("placeholder", encoding="utf-8")
    rc_ok, err_ok = _run_site_file_delete_python(
        py_code,
        [str(home_root), "shostc", "1001", str(site_root), "public_html/index.html"],
        default_uid=1001,
    )
    assert rc_ok == 0 and err_ok == ""
    assert not idx_file.exists()

    # (b) Already-missing file is idempotent (rc == 0)
    rc_miss, _ = _run_site_file_delete_python(
        py_code,
        [str(home_root), "shostc", "1001", str(site_root), "public_html/index.html"],
        default_uid=1001,
    )
    assert rc_miss == 0

    # (c) File owned by another UID (e.g., `spanel` uid 999 or `root` uid 0) is refused!
    foreign_file = app_dir / "panel_secret.sqlite"
    foreign_file.write_text("do-not-delete", encoding="utf-8")
    rc_uid, err_uid = _run_site_file_delete_python(
        py_code,
        [str(home_root), "shostc", "1001", str(site_root), "public_html/panel_secret.sqlite"],
        uid_overrides={foreign_file: 999},
        default_uid=1001,
    )
    assert rc_uid != 0 and "not owned by shostc" in err_uid
    assert foreign_file.exists()

    # (d) Directory target is refused (`S_ISDIR`)
    rc_dir, err_dir = _run_site_file_delete_python(
        py_code,
        [str(home_root), "shostc", "1001", str(site_root), "public_html/storage"],
        default_uid=1001,
    )
    assert rc_dir != 0 and "refusing to delete directory" in err_dir
    assert (app_dir / "storage").is_dir()

    # (e) Path escaping site_root (`../../spanel.db`) is refused
    rc_esc, err_esc = _run_site_file_delete_python(
        py_code,
        [str(home_root), "shostc", "1001", str(site_root), "../../spanel.db"],
        default_uid=1001,
    )
    assert rc_esc != 0 and "outside site root" in err_esc

    # (f) Symlinked intermediate directory (`public_html/storage` -> `/var/lib/spanel`) hits ELOOP!
    panel_target = tmp_path / "var_lib_spanel" / "spanel.db"
    panel_target.parent.mkdir(parents=True)
    panel_target.write_text("CRITICAL_PANEL_DB", encoding="utf-8")
    rc_sym, err_sym = _run_site_file_delete_python(
        py_code,
        [str(home_root), "shostc", "1001", str(site_root), "public_html/storage/framework/down"],
        fake_symlinks={app_dir / "storage"},
        default_uid=1001,
    )
    assert rc_sym != 0 and "symbolic links" in err_sym.lower()
    assert panel_target.read_text(encoding="utf-8") == "CRITICAL_PANEL_DB"


def test_p1_b7_env_bak_written_0640_and_protected_in_helper_secret_files(tmp_path):
    """P1-B7: `.env.bak` is written with mode `0640` (readable by PHP-FPM/SFTP user, blocked from
    other tenants) and preserved in `SITE_SECRET_FILES` across permission resets."""
    helper_head = HELPER_PATH.read_text(encoding="utf-8").splitlines()[:80]
    secret_lines = [ln for ln in helper_head if ln.startswith("SITE_SECRET_FILES=")]
    assert len(secret_lines) == 1
    assert ".env.bak" in secret_lines[0] and ".env" in secret_lines[0]

    site_root = tmp_path / "home" / "shostc" / "app.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)
    site = SimpleNamespace(
        id=31,
        owner_id=2,
        domain="app.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_holder = {"instances": {"31": {"installed": True}}}

    write_calls: List[tuple] = []

    def _capture_write(lu, sr, ad, rel, content, mode="0644"):
        write_calls.append((rel, mode))

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm, "_build_instance_summary", return_value={"id": 31}), \
            patch.object(lm.wordpress_live, "read_wp_file_text", return_value="APP_NAME=Old\n"), \
            patch.object(lm.wordpress_live, "write_wp_file_text", side_effect=_capture_write), \
            patch.object(lm.wordpress_live, "wp_path_exists", return_value=True), \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as mock_priv:
        lm.update_laravel_env(
            31,
            lm.LaravelEnvUpdatePayload(app_name="NewApp", app_debug=False),
            db=db,
            current_user=user,
        )

    assert write_calls == [(".env.bak", "0640"), (".env", "0640")]
    chmod_calls = [
        c.kwargs["helper_args"]
        for c in mock_priv.call_args_list
        if c.args and c.args[0] == "site-chmod"
    ]
    assert ["shostc", str(site_root), str(app_dir / ".env"), "0640"] in chmod_calls
    assert ["shostc", str(site_root), str(app_dir / ".env.bak"), "0640"] in chmod_calls


def test_p1_b1_live_terminal_exec_failure_propagates_and_rolls_back_auto_db(tmp_path):
    """P1-B1: Live commands execute via `terminal-exec` (never `run-as-user` / `su`); non-zero exit
    raises HTTP 502/500, drops the auto-created DB, restores Website fields, and never fakes files."""
    site_root = tmp_path / "home" / "shostc" / "fail.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    # 1. Direct `_run_as_site_user` behavioral check (`command_dry_run=False`)
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(returncode=0, stdout="Laravel Framework 12.1.0", stderr=""),
            ) as mock_exec:
        rc, out = lm._run_as_site_user(
            "shostc", app_dir, ["php8.4", "artisan", "--version"], timeout=45
        )
    assert rc == 0 and "12.1.0" in out
    mock_exec.assert_called_once_with(
        "terminal-exec",
        helper_args=["shostc", str(app_dir), "--timeout=45", "--php-version=8.4", "artisan", "--version"],
        check=False,
        timeout=60,
    )

    # 2. Failed `composer create-project` rolls back auto-created DatabaseAccount & Website config
    site = SimpleNamespace(
        id=41,
        owner_id=2,
        domain="fail.shost.vn",
        root_path=str(site_root),
        document_root="public_html",
        linux_user="shostc",
        php_version="8.2",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="none",
        nginx_custom="# user comment\n",
        created_at=None,
    )
    user = SimpleNamespace(
        id=2,
        username="shostc",
        role="end_user",
        package=None,
        website_limit=5,
        subdomain_limit=5,
        database_limit=5,
    )
    def _query_for_model(model):
        q = MagicMock()
        if model is lm.User:
            q.filter.return_value.first.return_value = user
        elif model is lm.Website:
            q.filter.return_value.all.return_value = [site]
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.count.return_value = 0
        return q

    db = MagicMock()
    db.query.side_effect = _query_for_model

    def _priv_fail_composer(verb, helper_args=None, **kwargs):
        if verb == "terminal-exec" and helper_args:
            if "stat" in helper_args:
                exists = (site_root / helper_args[-1]).exists()
                return SimpleNamespace(returncode=0 if exists else 1, stdout="", stderr="")
            if "create-project" in helper_args:
                assert "^12.0" in helper_args  # Verifies Laravel 12.x constraint support
                return SimpleNamespace(
                    returncode=2,
                    stdout="",
                    stderr="curl error 6: Could not resolve host: repo.packagist.org",
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm.php, "list_installed_php", return_value=["8.2", "8.3", "8.4"]), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch("app.api.databases.enforce_user_database_limit"), \
            patch.object(lm.site_users, "ensure_site_runtime", return_value="shostc"), \
            patch.object(
                lm.mariadb,
                "create_database",
                return_value={
                    "db_name": "lv_fail_db",
                    "db_user": "u_fail_db",
                    "db_password": "Secret!",
                },
            ), \
            patch.object(lm.teardown, "drop_database_record") as mock_drop_db, \
            patch.object(lm.shell, "privileged", side_effect=_priv_fail_composer):
        with pytest.raises(HTTPException) as exc_inst:
            lm.install_laravel_instance(
                lm.LaravelInstallPayload(
                    website_id=41,
                    laravel_version="12.x",
                    php_version="8.3",
                    install_source="composer",
                ),
                db=db,
                current_user=user,
            )

    assert exc_inst.value.status_code == 502
    assert "repo.packagist.org" in exc_inst.value.detail
    mock_drop_db.assert_called_once()
    dropped_acc = mock_drop_db.call_args.args[1]
    assert dropped_acc.db_name == "lv_fail_db"
    assert site.document_root == "public_html"
    assert site.php_version == "8.2"
    assert site.nginx_rewrite_mode == "none"
    assert site.nginx_custom == "# user comment\n"
    assert not (app_dir / "artisan").exists()

    # 3. Failed `composer require` in live mode raises HTTP 500 and never mutates composer.json
    orig_comp = '{"require": {"laravel/framework": "^11.0"}}\n'
    (app_dir / "composer.json").write_text(orig_comp, encoding="utf-8")
    state_holder = {"instances": {"41": {"installed": True}}}
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr="Your requirements could not be resolved"
                ),
            ):
        with pytest.raises(HTTPException) as exc_comp:
            lm.run_composer_action(
                41,
                lm.LaravelComposerPayload(action="require", package="laravel/horizon:^5.29"),
                db=db,
                current_user=user,
            )
    assert exc_comp.value.status_code == 500
    assert (app_dir / "composer.json").read_text(encoding="utf-8") == orig_comp


def test_p1_b4_maintenance_down_secret_and_up_recovery_when_autoloader_broken(tmp_path):
    """P1-B4: `artisan down` delegates to real Artisan with `--secret=...`; `artisan up` recovers
    from a broken `vendor/autoload.php` via `site-file-delete` on `down` & `maintenance.php`."""
    site_root = tmp_path / "home" / "shostc" / "maint.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    site = SimpleNamespace(
        id=51,
        owner_id=2,
        domain="maint.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_holder = {"instances": {"51": {"installed": True, "maintenance_mode": False}}}

    # 1. `down` passes `--secret=...` to `terminal-exec`
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm, "_build_instance_summary", return_value={"id": 51}), \
            patch.object(lm, "_app_path_exists", return_value=False), \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(
                    returncode=0, stdout="INFO  Application is now in maintenance mode.", stderr=""
                ),
            ) as mock_down:
        lm.run_artisan_command(
            51,
            lm.LaravelArtisanPayload(command="down", maintenance_secret="vip-access-2026"),
            db=db,
            current_user=user,
        )

    down_args = mock_down.call_args.kwargs["helper_args"]
    assert down_args[-3:] == ["artisan", "down", "--no-interaction"] or "--secret=vip-access-2026" in down_args
    assert "--secret=vip-access-2026" in down_args
    assert state_holder["instances"]["51"]["maintenance_mode"] is True

    # 2. `up` recovers cleanly via `site-file-delete` even when `artisan up` exits 255!
    down_exists = {"storage/framework/down": True, "storage/framework/maintenance.php": True}

    def _priv_broken_autoloader(verb, helper_args=None, **kwargs):
        if verb == "terminal-exec":
            return SimpleNamespace(
                returncode=255,
                stdout="",
                stderr="PHP Fatal error: Failed opening required 'vendor/autoload.php'",
            )
        if verb == "site-file-delete":
            rel = helper_args[2]
            for k in list(down_exists):
                if rel.endswith(k):
                    down_exists[k] = False
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm, "_build_instance_summary", return_value={"id": 51}), \
            patch.object(
                lm,
                "_app_path_exists",
                side_effect=lambda ad, rel, *a: down_exists.get(rel, False),
            ), \
            patch.object(lm.shell, "privileged", side_effect=_priv_broken_autoloader):
        up_res = lm.run_artisan_command(
            51,
            lm.LaravelArtisanPayload(command="up"),
            db=db,
            current_user=user,
        )

    assert "thành công" in up_res["message"]
    assert state_holder["instances"]["51"]["maintenance_mode"] is False

    # 3. `up` raises HTTP 500 if `site-file-delete` fails and `down` still exists on disk
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm, "_app_path_exists", return_value=True), \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(returncode=1, stdout="", stderr="refused"),
            ):
        with pytest.raises(HTTPException) as exc_up:
            lm.run_artisan_command(
                51,
                lm.LaravelArtisanPayload(command="up"),
                db=db,
                current_user=user,
            )
    assert exc_up.value.status_code == 500


def test_p1_b2_systemd_queue_worker_unit_generation_and_live_status(tmp_path):
    """P1-B2: Queue workers are managed via real systemd template units under `user-<uid>.slice`,
    queried live, and cleaned up on site/account deletion."""
    site_root = tmp_path / "home" / "shostc" / "queue.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    site = SimpleNamespace(
        id=61,
        owner_id=2,
        domain="queue.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_holder = {"instances": {"61": {"installed": True}}}

    def _priv_queue_sim(verb, helper_args=None, **kwargs):
        if verb == "laravel-worker-sync":
            return SimpleNamespace(
                returncode=0,
                stdout="status=running\nunit=spanel-laravel-shostc-abcdef123456@.service\nactive_workers=3\n",
                stderr="",
            )
        if verb == "laravel-worker-status":
            return SimpleNamespace(
                returncode=0,
                stdout="unit_present=1\nenabled=1\nconfigured_workers=3\nactive_workers=3\nfailed_workers=0\nstate=active\n",
                stderr="",
            )
        if verb == "terminal-exec" and helper_args and "queue:failed" in helper_args:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "id": "99",
                            "connection": "redis",
                            "queue": "high",
                            "failed_at": "2026-10-31 12:00:00",
                            "exception": "App\\Jobs\\SendInvoiceJob: Timeout exceeded",
                        }
                    ]
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm.wordpress_live, "wp_path_exists", return_value=True), \
            patch.object(lm.wordpress_live, "read_wp_file_text", return_value="QUEUE_CONNECTION=redis\n"), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm.shell, "privileged", side_effect=_priv_queue_sim) as mock_q_priv:
        q_res = lm.manage_queue_worker(
            61,
            lm.LaravelQueuePayload(
                action="start", workers=3, queue="high,default", tries=5, timeout=120, memory=256
            ),
            db=db,
            current_user=user,
        )

    sync_calls = [c for c in mock_q_priv.call_args_list if c.args and c.args[0] == "laravel-worker-sync"]
    assert len(sync_calls) == 1
    assert sync_calls[0].kwargs["helper_args"] == [
        "shostc",
        str(site_root),
        str(app_dir),
        "8.3",
        "3",
        "high,default",
        "5",
        "120",
        "256",
        "1",
    ]
    assert q_res["instance"]["queue_worker"]["enabled"] is True
    assert q_res["instance"]["queue_worker"]["active_workers"] == 3
    assert q_res["instance"]["queue_worker"]["failed_count"] == 1
    assert q_res["instance"]["failed_jobs"][0]["queue"] == "high"

    # Verify teardown hooks in spanel-helper.sh
    helper_text = HELPER_PATH.read_text(encoding="utf-8")
    assert 'delete_laravel_worker_units_for_user "$user"' in helper_text
    assert 'delete_laravel_worker_units "$user" "$root_target"' in helper_text

    # Execute the actual bash worker functions from spanel-helper.sh in an isolated sandbox
    bash_bin = _find_bash()
    if bash_bin:
        worker_funcs = _extract_helper_section(
            "laravel_worker_unit_base() {\n", "\nipv6_available() {\n"
        )
        worker_funcs = "laravel_worker_unit_base() {\n" + worker_funcs
        sandbox = tmp_path / "bash_sandbox"
        unit_dir = sandbox / "systemd"
        state_dir = sandbox / "systemctl_state"
        home_dir = sandbox / "home"
        site_dir = home_dir / "shostc" / "queue.shost.vn"
        proj_dir = site_dir / "public_html"
        proj_dir.mkdir(parents=True)
        unit_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)

        harness = (
            "set +o posix 2>/dev/null || true\n"
            "export PATH=\"/usr/bin:/bin:$PATH\"\n"
            "set -euo pipefail\n"
            f'HOME_ROOT="{home_dir.as_posix()}"\n'
            f'SYSTEMD_UNIT_DIR="{unit_dir.as_posix()}"\n'
            f'STATE_DIR="{state_dir.as_posix()}"\n'
            'deny() { echo "DENY: $*" >&2; exit 2; }\n'
            'require_linux_user() { [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || deny "bad user"; }\n'
            'require_managed_path() { printf "%s" "$1"; }\n'
            'require_bound_managed_path() { printf "%s" "$3"; }\n'
            'require_php_version() { [[ "$1" =~ ^8\\.[1-5]$ ]] || deny "bad php"; }\n'
            'site_open_basedir() { printf "%s/%s:/tmp" "$HOME_ROOT" "$1"; }\n'
            "id() { echo 1001; }\n"
            "install() { mkdir -p \"${@: -1}\" 2>/dev/null || true; }\n"
            "chown() { :; }\n"
            "chmod() { :; }\n"
            'command() { if [[ "${2:-}" == "php8.3" ]]; then echo "/usr/bin/php8.3"; else builtin command "$@"; fi; }\n'
            "systemctl() {\n"
            '  case "$1" in\n'
            '    daemon-reload) : ;;\n'
            '    enable) touch "$STATE_DIR/enabled_${2##*/}" ;;\n'
            '    restart) touch "$STATE_DIR/active_${2##*/}" ;;\n'
            '    disable) rm -f "$STATE_DIR/enabled_${3##*/}" "$STATE_DIR/active_${3##*/}" ;;\n'
            '    is-enabled) [[ -f "$STATE_DIR/enabled_${3##*/}" ]] ;;\n'
            '    is-active) [[ -f "$STATE_DIR/active_${3##*/}" ]] ;;\n'
            '    is-failed) return 1 ;;\n'
            "  esac\n"
            "}\n"
            + worker_funcs
            + "\nverify_laravel_app_artisan() { :; }\n"
            + f'sync_laravel_worker_unit shostc "{site_dir.as_posix()}" "{proj_dir.as_posix()}" 8.3 2 "high,default" 3 90 256 1\n'
            + f'status_laravel_worker_unit shostc "{site_dir.as_posix()}"\n'
        )
        bash_env = {
            **os.environ,
            "PATH": str(Path(bash_bin).parent) + os.pathsep + os.environ.get("PATH", ""),
        }
        script_file = sandbox / "run_worker_test.sh"
        script_file.write_bytes(harness.encode("utf-8"))
        proc = subprocess.run(
            [bash_bin, script_file.as_posix()], capture_output=True, check=True, env=bash_env
        )
        out_lines = proc.stdout.decode("utf-8")
        assert "status=running" in out_lines
        assert "configured_workers=2" in out_lines
        assert "active_workers=2" in out_lines
        assert "state=active" in out_lines

        unit_files = list(unit_dir.glob("spanel-laravel-shostc-*@.service"))
        assert len(unit_files) == 1
        unit_body = unit_files[0].read_text(encoding="utf-8")
        assert "User=shostc" in unit_body
        assert "Slice=user-1001.slice" in unit_body
        assert "MemoryMax=320M" in unit_body
        assert "TasksMax=64" in unit_body
        assert "NoNewPrivileges=yes" in unit_body
        assert "ProtectSystem=strict" in unit_body
        assert "ProtectHome=read-only" in unit_body
        assert "-d open_basedir=" in unit_body
        assert "artisan queue:work --queue=high,default --tries=3 --timeout=90 --memory=256" in unit_body

        # Now stop (`enabled_flag=0`) and verify the unit file is removed
        stop_harness = harness.replace(
            '256 1\n', '256 0\n'
        )
        script_file.write_bytes(stop_harness.encode("utf-8"))
        proc_stop = subprocess.run(
            [bash_bin, script_file.as_posix()], capture_output=True, check=True, env=bash_env
        )
        stop_out = proc_stop.stdout.decode("utf-8")
        assert "status=stopped" in stop_out
        assert "unit_present=0" in stop_out
        assert list(unit_dir.glob("spanel-laravel-shostc-*@.service")) == []


def test_p1_b3_real_7_point_security_checks_and_nginx_custom_validation(tmp_path):
    """P1-B3: Security checks reflect real disk/Nginx/.env state (no `security_overrides`), and
    `# BEGIN SPANEL LARAVEL SECURITY` passes `nginx.validate_custom_nginx` & `nginx.render_vhost`."""
    from app.services import nginx

    custom_sec = lm._update_laravel_nginx_security("", dotenv_git=True, storage_php=True)
    validated = nginx.validate_custom_nginx(custom_sec)
    assert "location ^~ /storage/" in validated
    assert "location ^~ /.git/" in validated

    # Render full PHP vhost template to prove Laravel rewrite + custom security directives compile cleanly
    managed_root = tmp_path / "shostc" / "sec-live.shost.vn"
    (managed_root / "public_html" / "public").mkdir(parents=True)
    with patch.object(lm.site_users, "is_site_root_for_domain", return_value=True), \
            patch.object(nginx, "get_website_ip", return_value="*"):
        vhost_text = nginx.render_vhost(
            domain="sec-live.shost.vn",
            root_path=str(managed_root),
            app_type="php",
            php_version="8.3",
            custom_directives=custom_sec,
            document_root="public_html/public",
            rewrite_mode="laravel",
            waf_enabled=False,
        )
    assert "public_html/public;" in vhost_text.replace("\\", "/")

    # Verify live 7-point check evaluation when flaws exist vs after fixing
    vhost_dir = tmp_path / "sites-available"
    vhost_dir.mkdir(parents=True)
    vhost_file = vhost_dir / "sec-live.shost.vn.conf"
    # Misconfigured live vhost pointing to public_html instead of public_html/public
    vhost_file.write_text("server {\n    root /home/shostc/sec-live.shost.vn/public_html;\n}\n", encoding="utf-8")

    app_dir = managed_root / "public_html"
    site = SimpleNamespace(
        id=71,
        owner_id=2,
        domain="sec-live.shost.vn",
        root_path=str(managed_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        nginx_rewrite_mode="laravel",
        nginx_custom="",  # No security directives yet
    )
    # Weak .env: APP_DEBUG=true, short APP_KEY, missing SESSION_SECURE_COOKIE, world-readable 0644 .env
    weak_env = lm._parse_env_details("APP_NAME=Weak\nAPP_DEBUG=true\nAPP_KEY=base64:dG9vc2hvcnQ=\n")
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm.settings, "nginx_sites_available", str(vhost_dir)), \
            patch.object(os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o644)):
        checks_weak = {
            c["id"]: c["passed"]
            for c in lm._evaluate_security_checks(
                site, app_dir, weak_env, linux_user="shostc", site_root=managed_root, has_env_file=True
            )
        }
    assert checks_weak == {
        "public_docroot": False,          # Live vhost lacks /public!
        "dotenv_git_blocked": False,      # Missing Nginx deny block
        "app_debug_off": False,           # APP_DEBUG=true
        "app_key_valid": False,           # < 32 decoded bytes
        "env_permissions_strict": False,  # 0644 fails (st_mode & 0o027) == 0
        "storage_php_blocked": False,     # Missing location ^~ /storage/
        "secure_session_cookie": False,   # Absent SESSION_SECURE_COOKIE defaults to False!
    }

    # Now fix vhost, nginx_custom, .env, and 0640 permissions -> all 7 pass!
    vhost_file.write_text(
        "server {\n    root /home/shostc/sec-live.shost.vn/public_html/public;\n}\n",
        encoding="utf-8",
    )
    site.nginx_custom = custom_sec
    strong_env = lm._parse_env_details(
        f"APP_NAME=Strong\nAPP_DEBUG=false\nAPP_KEY={lm._generate_laravel_app_key()}\n"
        "SESSION_SECURE_COOKIE=true\nSESSION_HTTP_ONLY=true\n"
    )
    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm.settings, "nginx_sites_available", str(vhost_dir)), \
            patch.object(os, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o640)):
        checks_strong = {
            c["id"]: c["passed"]
            for c in lm._evaluate_security_checks(
                site, app_dir, strong_env, linux_user="shostc", site_root=managed_root, has_env_file=True
            )
        }
    assert all(checks_strong.values()), checks_strong


def test_p1_b5_remove_purges_app_syncs_vhost_and_protects_sibling_wp_database(tmp_path):
    """P1-B5: `remove` purges `app_dir`, stops systemd worker & cron, rewrites Nginx vhost
    immediately, and NEVER drops a sibling WordPress database when Laravel DB name is unknown."""
    site_root = tmp_path / "home" / "shostc" / "rm.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    site = SimpleNamespace(
        id=81,
        owner_id=2,
        domain="rm.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="# keep custom comment\n\n"
        + lm._update_laravel_nginx_security("", dotenv_git=True, storage_php=True),
        created_at=None,
    )
    user = SimpleNamespace(id=2, username="shostc", role="end_user")
    wp_db = SimpleNamespace(id=1, owner_id=2, website_id=80, db_name="wp_main_db", db_user="u_wp")
    lv_db = SimpleNamespace(id=2, owner_id=2, website_id=81, db_name="lv_rm_db", db_user="u_lv")

    # Case A: Unknown DB name in both state & .env -> must NOT drop wp_main_db!
    db_unknown = MagicMock()
    db_unknown.query.return_value.filter.return_value.first.side_effect = [user, wp_db]
    state_unknown = {"instances": {"81": {"installed": True, "db_name": ""}}}

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_unknown), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm.wordpress_live, "read_wp_file_text", return_value="APP_NAME=NoDb\n"), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch("app.api.websites._rewrite_website_vhost") as mock_rewrite_a, \
            patch.object(lm.teardown, "drop_database_record") as mock_drop_a, \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as mock_priv_a:
        lm.remove_laravel_instance(81, drop_database=True, db=db_unknown, current_user=user)

    mock_drop_a.assert_not_called()
    mock_rewrite_a.assert_called_once()
    verbs_a = [c.args[0] for c in mock_priv_a.call_args_list if c.args]
    assert "laravel-worker-delete" in verbs_a
    assert "rm-site" in verbs_a
    assert "site-document-root-ensure" in verbs_a
    assert site.document_root == "public_html"
    assert site.nginx_rewrite_mode == "none"
    assert lm.LARAVEL_NGINX_SEC_BEGIN not in site.nginx_custom
    assert "# keep custom comment" in site.nginx_custom

    # Case B: Exact `lv_rm_db` in `.env` -> drops ONLY `lv_rm_db` via `teardown.drop_database_record`
    site.document_root = "public_html/public"
    site.nginx_rewrite_mode = "laravel"
    db_exact = MagicMock()
    db_exact.query.return_value.filter.return_value.first.side_effect = [user, lv_db]
    state_exact = {"instances": {"81": {"installed": True}}}

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_exact), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(
                lm.wordpress_live, "read_wp_file_text", return_value="DB_DATABASE=lv_rm_db\n"
            ), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch("app.api.websites._rewrite_website_vhost"), \
            patch.object(lm.teardown, "drop_database_record") as mock_drop_b, \
            patch.object(
                lm.shell,
                "privileged",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ):
        res_b = lm.remove_laravel_instance(81, drop_database=True, db=db_exact, current_user=user)

    mock_drop_b.assert_called_once_with(db_exact, lv_db)
    assert "lv_rm_db" in res_b["message"]


def test_p1_b6_blocks_subpath_occupied_sites_non_empty_dirs_and_cross_app_security(tmp_path):
    """P1-B6: Rejects subpath installs (400), WordPress/Node/Docker sites (409), non-empty target
    directories (409), and cross-app `manage_laravel_security` calls (409)."""
    site_root = tmp_path / "domains" / "guard.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    user = SimpleNamespace(
        id=2,
        username="shostc",
        role="end_user",
        package=None,
        website_limit=5,
        subdomain_limit=5,
        database_limit=5,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    clean_site = SimpleNamespace(
        id=91,
        owner_id=2,
        domain="guard.shost.vn",
        root_path=str(site_root),
        document_root="public_html",
        linux_user="shostc",
        php_version="8.3",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        nginx_rewrite_mode="none",
    )
    db.query.return_value.filter.return_value.all.return_value = [clean_site]

    # 1. Subpath install rejected with HTTP 400
    with patch.object(lm, "_get_accessible_website", return_value=clean_site):
        with pytest.raises(HTTPException) as exc_sub:
            lm.install_laravel_instance(
                lm.LaravelInstallPayload(website_id=91, subpath="crm"),
                db=db,
                current_user=user,
            )
    assert exc_sub.value.status_code == 400

    # 2. WordPress / Node website rejected with HTTP 409 on install & security, and skipped in scan
    wp_site = SimpleNamespace(**{**clean_site.__dict__, "app_type": "wordpress"})
    with patch.object(lm, "_get_accessible_website", return_value=wp_site):
        with pytest.raises(HTTPException) as exc_wp:
            lm.install_laravel_instance(
                lm.LaravelInstallPayload(website_id=91),
                db=db,
                current_user=user,
            )
        assert exc_wp.value.status_code == 409

        with pytest.raises(HTTPException) as exc_wp_sec:
            lm.manage_laravel_security(
                91, lm.LaravelSecurityPayload(action="secure"), db=db, current_user=user
            )
        assert exc_wp_sec.value.status_code == 409

    assert lm._build_instance_summary(wp_site, db, {"instances": {"91": {"installed": True}}}) is None

    # 3. Non-empty directory containing existing PHP file or wp-config.php rejected with HTTP 409
    (app_dir / "custom-portal.php").write_text("<?php echo 'live';", encoding="utf-8")
    with patch.object(lm, "_get_accessible_website", return_value=clean_site), \
            patch.object(lm, "_load_toolkit_state", return_value={"instances": {}}):
        with pytest.raises(HTTPException) as exc_nonempty:
            lm.install_laravel_instance(
                lm.LaravelInstallPayload(website_id=91),
                db=db,
                current_user=user,
            )
    assert exc_nonempty.value.status_code == 409

    # 4. `manage_laravel_security` on an empty PHP site without `artisan`/`composer.json` -> HTTP 409
    (app_dir / "custom-portal.php").unlink()
    with patch.object(lm, "_get_accessible_website", return_value=clean_site), \
            patch.object(lm, "_load_toolkit_state", return_value={"instances": {}}):
        with pytest.raises(HTTPException) as exc_no_lv:
            lm.manage_laravel_security(
                91, lm.LaravelSecurityPayload(action="secure"), db=db, current_user=user
            )
    assert exc_no_lv.value.status_code == 409


def test_p2_b_php_version_validation_readonly_get_and_concurrent_state_updates(tmp_path):
    """P2-B: Validates PHP 8.1-8.5 & installed versions, syncs FPM/vhost/worker on PHP switch,
    keeps `GET /instances` strictly read-only, and serializes concurrent state mutations."""
    # 1. PHP version validation (< 8.1 rejected, invalid rejected, uninstalled rejected)
    with pytest.raises(HTTPException) as exc_74:
        lm._validate_laravel_php_version("7.4")
    assert exc_74.value.status_code == 400

    with pytest.raises(HTTPException) as exc_bad:
        lm._validate_laravel_php_version("9.9; id")
    assert exc_bad.value.status_code == 400

    with patch.object(lm.settings, "command_dry_run", False), \
            patch.object(lm.php, "list_installed_php", return_value=["8.2", "8.3"]):
        assert lm._validate_laravel_php_version("8.3") == "8.3"
        with pytest.raises(HTTPException) as exc_uninst:
            lm._validate_laravel_php_version("8.5")
        assert exc_uninst.value.status_code == 400

    # 2. PHP switch in `update_laravel_env` updates runtime, vhost, and systemd worker
    site_root = tmp_path / "domains" / "php.shost.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)
    (app_dir / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (app_dir / ".env").write_text("APP_NAME=App\n", encoding="utf-8")
    site = SimpleNamespace(
        id=95,
        owner_id=2,
        domain="php.shost.vn",
        root_path=str(site_root),
        document_root="public_html/public",
        linux_user="shostc",
        php_version="8.2",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="laravel",
        nginx_custom="",
        created_at=None,
    )
    user = SimpleNamespace(
        id=2,
        username="shostc",
        role="end_user",
        package=None,
        website_limit=5,
        subdomain_limit=5,
        database_limit=5,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user
    state_php = {"instances": {"95": {"installed": True, "queue_worker": {"enabled": True, "workers": 2}}}}

    with patch.object(lm, "_get_accessible_website", return_value=site), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_php), \
            patch.object(lm, "_save_toolkit_state"), \
            patch.object(lm.site_users, "ensure_site_runtime", return_value="shostc") as mock_rt, \
            patch("app.api.websites._rewrite_website_vhost") as mock_vh, \
            patch.object(lm, "_sync_laravel_worker_unit", return_value={"status": "running"}) as mock_wk, \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm, "_run_as_site_user", return_value=(0, "OK")):
        lm.update_laravel_env(
            95,
            lm.LaravelEnvUpdatePayload(php_version="8.4", app_name="App84"),
            db=db,
            current_user=user,
        )
    assert site.php_version == "8.4"
    mock_rt.assert_called_once()
    mock_vh.assert_called_once()
    mock_wk.assert_called_once()

    # 3. `GET /instances` is strictly read-only (never calls `_save_toolkit_state` or `db.commit`)
    db_ro = MagicMock()
    db_ro.query.return_value.filter.return_value.order_by.return_value.all.return_value = [site]
    db_ro.query.return_value.filter.return_value.first.return_value = user
    db_ro.query.return_value.filter.return_value.count.return_value = 0
    with patch.object(lm, "_load_toolkit_state", return_value=state_php), \
            patch.object(lm, "_save_toolkit_state") as mock_save_ro, \
            patch.object(lm.cron, "list_cron_entries", return_value=[]):
        ro_res = lm.list_laravel_instances(db=db_ro, current_user=user)
    assert len(ro_res["instances"]) == 1
    mock_save_ro.assert_not_called()
    db_ro.commit.assert_not_called()

    # 4. Concurrent `_update_instance_state` preserves all 8 parallel updates without lost writes
    db_file = tmp_path / "concurrent_state.sqlite"
    mirror_file = tmp_path / "laravel_toolkit_state.json"
    test_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False}
    )
    errors: List[Exception] = []
    barrier = threading.Barrier(8)

    def _worker_thread(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            lm._update_instance_state(
                str(200 + idx),
                lambda inst: inst.update({"app_name": f"App-{idx}", "worker_idx": idx}),
            )
        except Exception as exc:
            errors.append(exc)

    try:
        with patch.object(lm, "engine", test_engine), \
                patch.object(lm, "_toolkit_state_path", return_value=mirror_file):
            threads = [threading.Thread(target=_worker_thread, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            final_state = lm._load_toolkit_state()
    finally:
        test_engine.dispose()

    assert errors == [], f"Concurrent state update errors: {errors}"
    for i in range(8):
        key = str(200 + i)
        assert key in final_state["instances"]
        assert final_state["instances"][key]["app_name"] == f"App-{i}"


def test_package_website_quota_enforcement_across_application_modules():
    from app.api import site_apps as sa
    from app.api import wordpress_manager as wm

    pkg_one_site = SimpleNamespace(
        id=1,
        name="Starter 1 Site",
        website_limit=1,
        subdomain_limit=0,
        database_limit=1,
        node_apps_limit=2,
    )
    user_one = SimpleNamespace(
        id=10,
        username="shostc",
        role="end_user",
        package=pkg_one_site,
        website_limit=1,
        subdomain_limit=0,
        database_limit=1,
    )
    wp_site = SimpleNamespace(
        id=101,
        owner_id=10,
        domain="shost.com.vn",
        app_type="wordpress",
        nginx_rewrite_mode="front_controller",
        document_root="public_html",
        app_id=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 1
    db.query.return_value.filter.return_value.all.return_value = [wp_site]

    wp_quota, wp_occ = wm._evaluate_user_app_quota(db, user_one, [wp_site], {"instances": {}}, {101})
    assert 101 in wp_occ
    assert wp_quota["max_total_websites"] == 1
    assert wp_quota["occupied_websites_count"] == 1
    assert wp_quota["available_websites_count"] == 0
    assert wp_quota["can_install"] is False

    lv_quota, lv_occ = lm._evaluate_user_app_quota(db, user_one, [wp_site], {"instances": {}}, set())
    assert 101 in lv_occ
    assert lv_quota["max_total_websites"] == 1
    assert lv_quota["occupied_websites_count"] == 1
    assert lv_quota["available_websites_count"] == 0
    assert lv_quota["can_install"] is False

    sa_quota = sa._website_quota_status(db, user_one, [])
    assert sa_quota["max_total_websites"] == 1
    assert sa_quota["occupied_websites_count"] == 1
    assert sa_quota["website_quota_reached"] is True

    # Upgrade package to allow 1 subdomain (max_total_websites = 2) and add an unoccupied subdomain
    pkg_two_sites = SimpleNamespace(
        id=2,
        name="Pro 1+1",
        website_limit=1,
        subdomain_limit=1,
        database_limit=2,
        node_apps_limit=2,
    )
    user_two = SimpleNamespace(
        id=10,
        username="shostc",
        role="end_user",
        package=pkg_two_sites,
        website_limit=1,
        subdomain_limit=1,
        database_limit=2,
    )
    sub_site = SimpleNamespace(
        id=102,
        owner_id=10,
        domain="app.shost.com.vn",
        app_type="php",
        nginx_rewrite_mode="none",
        document_root="public_html",
        app_id=None,
    )
    db.query.return_value.filter.return_value.all.return_value = [wp_site, sub_site]

    wp_quota2, wp_occ2 = wm._evaluate_user_app_quota(
        db, user_two, [wp_site, sub_site], {"instances": {}}, {101}
    )
    assert wp_occ2 == {101}
    assert wp_quota2["max_total_websites"] == 2
    assert wp_quota2["occupied_websites_count"] == 1
    assert wp_quota2["available_websites_count"] == 1
    assert wp_quota2["can_install"] is True

    lv_quota2, lv_occ2 = lm._evaluate_user_app_quota(
        db, user_two, [wp_site, sub_site], {"instances": {}}, set()
    )
    assert lv_occ2 == {101}
    assert lv_quota2["can_install"] is True

    sa_quota2 = sa._website_quota_status(db, user_two, [])
    assert sa_quota2["website_quota_reached"] is False


def test_asgi_router_contextvar_lifecycle_and_php85_deploy_flow(tmp_path):
    """Regression test for live ASGI HTTP requests through FastAPI's dependency injector:
    1. Proves `async def _capture_laravel_access_role` propagates `x-access-role` into sync
       threadpool endpoints and resets `ContextVar` token cleanly without HTTP 500 `ValueError`.
    2. Verifies dynamic `installed_php_versions` mapping (including PHP 8.5), Laravel 12.x/11.x
       vs PHP 8.1 compatibility guard, `--no-scripts` + `artisan package:discover` sequencing,
       PHP 8.5 `--ignore-platform-req=php+` retry, and helper CLI `open_basedir` paths.
    """
    import asyncio
    from fastapi import FastAPI

    site_root = tmp_path / "home" / "shostc" / "shost.com.vn"
    app_dir = site_root / "public_html"
    app_dir.mkdir(parents=True)

    site = SimpleNamespace(
        id=77,
        owner_id=2,
        domain="shost.com.vn",
        root_path=str(site_root),
        document_root="public_html",
        linux_user="shostc",
        php_version="8.5",
        app_type="php",
        app_id=None,
        ssl_enabled=True,
        waf_enabled=True,
        nginx_rewrite_mode="none",
        nginx_custom="",
        created_at=None,
    )
    end_user = SimpleNamespace(
        id=2,
        username="shostc",
        role="end_user",
        package=None,
        website_limit=5,
        subdomain_limit=5,
        database_limit=5,
    )
    admin_user = SimpleNamespace(
        id=1,
        username="admin",
        role="admin",
        package=None,
        website_limit=-1,
        subdomain_limit=-1,
        database_limit=-1,
    )

    def _mock_query(model):
        q = MagicMock()
        if model is lm.Website:
            q.filter.return_value.all.return_value = [site]
            q.filter.return_value.order_by.return_value.all.return_value = [site]
            q.filter.return_value.first.return_value = site
            q.filter.return_value.count.return_value = 1
        elif model is lm.User:
            q.filter.return_value.first.return_value = end_user
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
            q.filter.return_value.count.return_value = 0
        return q

    db = MagicMock()
    db.query.side_effect = _mock_query

    active_user_ref = [admin_user]
    app = FastAPI()
    app.include_router(lm.router, prefix="/api")
    app.dependency_overrides[lm.get_current_user] = lambda: active_user_ref[0]
    app.dependency_overrides[lm.get_db] = lambda: db

    async def _asgi_call(method: str, path: str, headers=None, body_obj=None):
        raw_body = json.dumps(body_obj).encode("utf-8") if body_obj is not None else b""
        hdrs = [(b"content-type", b"application/json")]
        for k, v in (headers or {}).items():
            hdrs.append((k.lower().encode("latin-1"), v.encode("latin-1")))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": hdrs,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
        sent = False
        out_status = 0
        out_chunks: List[bytes] = []

        async def _recv():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw_body, "more_body": False}
            return {"type": "http.disconnect"}

        async def _send(msg):
            nonlocal out_status
            if msg["type"] == "http.response.start":
                out_status = msg["status"]
            elif msg["type"] == "http.response.body":
                out_chunks.append(msg.get("body", b""))

        await app(scope, _recv, _send)
        raw_resp = b"".join(out_chunks).decode("utf-8")
        return out_status, (json.loads(raw_resp) if raw_resp else {})

    state_holder: Dict[str, Any] = {"instances": {}}
    executed_cmds: List[List[str]] = []
    attempt_counter = [0]

    def _fake_php85_exec(linux_user, target_dir, cmd_args, **kwargs):
        executed_cmds.append(list(cmd_args))
        if "create-project" in cmd_args:
            assert "--no-scripts" in cmd_args
            attempt_counter[0] += 1
            if attempt_counter[0] == 1:
                return 2, "Your PHP version (8.5.0) does not satisfy that requirement."
            assert "--ignore-platform-req=php+" in cmd_args
            (target_dir / "public").mkdir(parents=True, exist_ok=True)
            (target_dir / "storage" / "logs").mkdir(parents=True, exist_ok=True)
            (target_dir / "bootstrap" / "cache").mkdir(parents=True, exist_ok=True)
            (target_dir / "artisan").write_text("#!/usr/bin/env php\n<?php\n", encoding="utf-8")
            (target_dir / "composer.json").write_text(
                json.dumps({"require": {"php": "^8.2", "laravel/framework": "^12.0"}}),
                encoding="utf-8",
            )
            (target_dir / "public" / "index.php").write_text("<?php\n", encoding="utf-8")
            return 0, "Created Laravel 12.x project on PHP 8.5"
        if cmd_args[:2] == ["artisan", "package:discover"]:
            # .env must already be written BEFORE package:discover runs!
            assert (target_dir / ".env").is_file()
            return 0, "Discovered packages."
        return 0, "OK"

    with patch.object(lm.php, "list_installed_php", return_value=["7.4", "8.4", "8.5"]), \
            patch.object(lm, "_load_toolkit_state", side_effect=lambda: state_holder), \
            patch.object(lm, "_load_wp_state_readonly", return_value={"instances": {}}), \
            patch.object(lm, "_save_toolkit_state"), \
            patch("app.api.databases.enforce_user_database_limit"), \
            patch("app.api.websites._rewrite_website_vhost"), \
            patch.object(lm.site_users, "ensure_site_runtime", return_value="shostc"), \
            patch.object(
                lm.mariadb,
                "create_database",
                return_value={
                    "db_name": "lv_shost_85",
                    "db_user": "u_shost_85",
                    "db_password": "StrongPass85!",
                },
            ), \
            patch.object(lm, "_run_as_site_user", side_effect=_fake_php85_exec), \
            patch.object(lm.cron, "list_cron_entries", return_value=[]), \
            patch.object(lm.cron, "add_cron"):
        # 1. Admin without X-Access-Role -> clean 403 (never 500 ContextVar crash)
        st_403, _ = asyncio.run(_asgi_call("GET", "/api/laravel-manager/instances"))
        assert st_403 == 403

        # 2. Admin WITH X-Access-Role: user -> clean 200 and installed_php_versions == ['8.5', '8.4']
        st_200, body_list = asyncio.run(
            _asgi_call("GET", "/api/laravel-manager/instances", headers={"X-Access-Role": "user"})
        )
        assert st_200 == 200
        assert body_list["installed_php_versions"] == ["8.5", "8.4"]
        assert body_list["websites"][0]["php_version"] == "8.5"

        # 3. End user attempting Laravel 12.x on PHP 8.1 -> clean 400 compatibility guard
        active_user_ref[0] = end_user
        st_400, body_400 = asyncio.run(
            _asgi_call(
                "POST",
                "/api/laravel-manager/install",
                body_obj={
                    "website_id": 77,
                    "laravel_version": "12.x",
                    "php_version": "8.1",
                    "install_source": "composer",
                },
            )
        )
        assert st_400 == 400
        assert "8.2" in body_400["detail"]

        # 4. End user deploying Laravel 12.x on shost.com.vn (PHP 8.5) via ASGI -> 200 OK
        st_inst, body_inst = asyncio.run(
            _asgi_call(
                "POST",
                "/api/laravel-manager/install",
                body_obj={
                    "website_id": 77,
                    "app_name": "shost.com.vn App",
                    "laravel_version": "12.x",
                    "php_version": "8.5",
                    "install_source": "composer",
                    "enable_scheduler": True,
                    "enable_queue_worker": True,
                },
            )
        )
        assert st_inst == 200, body_inst
        assert site.php_version == "8.5"
        assert site.document_root == "public_html/public"
        assert attempt_counter[0] == 2
        assert any(cmd[:2] == ["artisan", "package:discover"] for cmd in executed_cmds)

    # 5. Verify spanel-helper.sh CLI open_basedir includes Composer & SSL CA paths while site_open_basedir stays strict
    helper_text = HELPER_PATH.read_text(encoding="utf-8")
    assert 'terminal_cli_open_basedir="${terminal_open_basedir}:/usr/local/bin:/usr/share/composer:/run/mysqld:/var/run/mysqld:/etc/ssl/certs:/usr/share/ca-certificates:/dev"' in helper_text
    assert 'COMPOSER_MEMORY_LIMIT=-1' in helper_text
    assert 'error_reporting=8191' in helper_text


def test_frontend_laravel_manager_no_unstable_effect_reload_loop():
    jsx_path = REPO_ROOT / "frontend" / "src" / "pages" / "LaravelManager.jsx"
    jsx_text = jsx_path.read_text(encoding="utf-8")
    assert "useRef" in jsx_text
    assert "const requestRef = useRef(request);" in jsx_text
    assert "const loadPhpVersionsRef = useRef(loadPhpVersions);" in jsx_text
    assert "const hasLoadedOnceRef = useRef(false);" in jsx_text
    assert "[request," not in jsx_text
    assert "loadPhpVersions]" not in jsx_text


def test_installer_and_updater_composer_never_prompts_as_superuser():
    for rel in ("installer/install.sh", "installer/update.sh"):
        raw = (REPO_ROOT / rel).read_bytes()
        assert b"\r" not in raw, f"CR byte found in {rel}"
        text = raw.decode("utf-8")
        assert "COMPOSER_ALLOW_SUPERUSER=1" in text, f"Missing COMPOSER_ALLOW_SUPERUSER=1 in {rel}"
        assert "COMPOSER_NO_INTERACTION=1" in text, f"Missing COMPOSER_NO_INTERACTION=1 in {rel}"
        assert "--version --no-interaction </dev/null" in text, f"Missing non-interactive stdin guard in {rel}"


def test_python312_eager_annotation_compatibility():
    import inspect
    from typing import get_type_hints
    from app.api import auth, laravel_manager, site_apps, wordpress_manager

    for mod in (auth, laravel_manager, site_apps, wordpress_manager):
        for _, obj in inspect.getmembers(mod):
            if inspect.isfunction(obj) and getattr(obj, "__module__", None) == mod.__name__:
                hints = get_type_hints(obj)
                assert isinstance(hints, dict)


if __name__ == "__main__":
    test_require_end_user_laravel_access_blocks_admin_without_user_view_header()
    test_parse_and_update_env_preserves_comments_and_quotes_special_values()
    for _fn in (
        test_build_instance_summary_reads_composer_and_env_metadata,
        test_install_laravel_instance_configures_public_docroot_env_and_scheduler,
        test_artisan_command_whitelists_and_blocks_disallowed_commands,
        test_update_laravel_env_creates_backup_and_clears_config_cache,
        test_composer_and_security_hardening_endpoints,
        test_p0_1_no_panel_unlink_and_helper_site_file_delete_blocks_symlink_and_wrong_owner,
        test_p1_b7_env_bak_written_0640_and_protected_in_helper_secret_files,
        test_p1_b1_live_terminal_exec_failure_propagates_and_rolls_back_auto_db,
        test_p1_b4_maintenance_down_secret_and_up_recovery_when_autoloader_broken,
        test_p1_b2_systemd_queue_worker_unit_generation_and_live_status,
        test_p1_b3_real_7_point_security_checks_and_nginx_custom_validation,
        test_p1_b5_remove_purges_app_syncs_vhost_and_protects_sibling_wp_database,
        test_p1_b6_blocks_subpath_occupied_sites_non_empty_dirs_and_cross_app_security,
        test_p2_b_php_version_validation_readonly_get_and_concurrent_state_updates,
        test_asgi_router_contextvar_lifecycle_and_php85_deploy_flow,
    ):
        with tempfile.TemporaryDirectory() as _td:
            _fn(Path(_td))
    test_package_website_quota_enforcement_across_application_modules()
    test_frontend_laravel_manager_no_unstable_effect_reload_loop()
    test_installer_and_updater_composer_never_prompts_as_superuser()
    test_python312_eager_annotation_compatibility()
    print("ALL LARAVEL MANAGER TESTS PASSED")
