import hashlib
from pathlib import Path

import pytest

from app.api import websites
from app.services import site_users

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def test_site_php_fpm_socket_is_scoped_to_site_root(tmp_path):
    first_root = tmp_path / "first.test"
    second_root = tmp_path / "second.test"

    first_socket = site_users.site_php_fpm_socket("siteuser", first_root, "8.3")
    second_socket = site_users.site_php_fpm_socket("siteuser", second_root, "8.3")

    first_hash = hashlib.sha256(str(first_root.resolve()).encode("utf-8")).hexdigest()[:12]
    assert first_socket == f"/run/php/spanel-siteuser-{first_hash}-8_3.sock"
    assert second_socket != first_socket


def test_site_php_fpm_socket_returns_none_without_php_version(tmp_path):
    assert site_users.site_php_fpm_socket("siteuser", tmp_path, None) is None


def test_php_fpm_socket_rejects_invalid_php_version(tmp_path):
    with pytest.raises(ValueError, match="Invalid PHP version"):
        site_users.site_php_fpm_socket("siteuser", tmp_path, "../8.3")


def test_legacy_user_php_fpm_socket_is_kept_for_callers_without_site_root():
    assert site_users.php_fpm_socket("siteuser", "8.3") == "/run/php/spanel-siteuser-8_3.sock"


def test_placeholder_page_for_linux_user_uses_site_file_write(tmp_path, monkeypatch):
    root = tmp_path / "site"
    public = root / "public_html"
    public.mkdir(parents=True)
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(websites.file_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(websites.file_manager, "_clear_fastcgi_cache", lambda: None)

    websites._write_placeholder_page("example.test", str(root), "siteuser", "8.3")

    assert calls[0][0] == "site-file-write"
    assert calls[0][1] == ["siteuser", str(root.resolve()), "public_html/index.html"]
    assert "example.test" in calls[0][2]["input"]


def test_panel_linux_users_are_sftp_chroot_only():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    for script_path in (INSTALL_SCRIPT, UPDATE_SCRIPT):
        script = script_path.read_text(encoding="utf-8")
        assert "Match Group spanel-sftp" in script
        assert "ChrootDirectory /home/%u" in script
        assert "ForceCommand internal-sftp -d /" in script
        assert "PermitTTY no" in script
        assert "AllowTcpForwarding no" in script
    assert "--shell /usr/sbin/nologin" in helper
    assert "--shell /bin/bash" not in helper
    assert 'chmod 0711 "$HOME_ROOT"' in helper
    assert 'chown "root:$user" "$home_dir"' in helper
    assert 'chmod 0751 "$home_dir"' in helper
    assert 'usermod -aG "$user" www-data' in helper


def test_panel_tools_ssl_vhosts_enable_http2_for_nginx_1_24():
    expected = "listen 443 ssl http2 default_server;"
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert expected in helper
    for script_path in (INSTALL_SCRIPT, UPDATE_SCRIPT):
        script = script_path.read_text(encoding="utf-8")
        assert expected in script


def test_site_trees_use_the_standard_644_755_modes():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert 'SITE_FILE_MODE="0644"' in helper
    assert 'SITE_DIR_MODE="0755"' in helper
    assert 'find "$target" -type d -exec chmod 755 {} +' in helper
    assert 'find "$target" -type f -exec chmod 644 {} +' in helper
    assert 'install -o "$user" -g "$spanel_SITES_GROUP" -m 0644' in helper
    assert 'chown -R "$user:$spanel_SITES_GROUP" "$target"' in helper
    assert 'harden_site_file "$target" "$user"' in helper
    # The terminal writes with the same default as the file manager.
    assert "umask 022" in helper
    assert "umask 027" not in helper
    # Nothing may put the old restrictive defaults back on a site tree.
    assert 'find "$target" -type d -exec chmod 2750 {} +' not in helper
    assert 'find "$target" -type f -exec chmod 640 {} +' not in helper
    # Existing installs are migrated by the updater.
    assert 'find "$site_dir" -type d -exec chmod 755 {} +' in update
    assert 'find "$site_dir" -type f -exec chmod 644 {} +' in update


def test_an_update_leaves_the_modes_the_owner_chose():
    """Operator, 2026-09-27: a file set to 755 in the file manager was back on
    644 "after a while" - the update had run. The sweep that reset every file
    of every site on each update was the one-time move off the pre-v1.0.106
    layout (folders 2750, files 0640), and now runs only on a site still on
    it: its own folder is 2750. Ownership and ACLs are still put right."""
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    sweep = update.split("harden_existing_panel_users() {")[1].split("\n}\n")[0]
    guard = '[[ "$(stat -c \'%a\' "$site_dir" 2>/dev/null)" == "2750" ]] || continue'
    assert sweep.count(guard) == 1
    before, after = sweep.split(guard)
    assert 'chown -R "$user:spanel-sites" "$site_dir"' in before, "ownership is still fixed on every site"
    assert 'setfacl -Rb "$site_dir"' in before
    assert "chmod" not in before.split("find \"$home_dir\" -mindepth 1")[1], "no mode is changed before the guard"
    for line in ('find "$site_dir" -type d -exec chmod 755 {} +', 'find "$site_dir" -type f -exec chmod 644 {} +',
                 'find "$site_dir" -type d -exec chmod a-s {} +'):
        assert line in after, line


def test_files_holding_database_credentials_stay_group_only():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "SITE_SECRET_FILES=(wp-config.php .env .my.cnf)" in helper
    assert "protect_site_secret_tree()" in helper
    assert 'protect_site_secret_file "$target"' in helper
    assert 'find "$target" -type f -name "$secret" -exec chmod 0640 {} +' in helper
    assert 'find "$site_dir" -type f -name "$secret" -exec chmod 640 {} +' in update
    # A site tree is never re-permissioned without the follow-up pass.
    for block in helper.split('find "$target" -type f -exec chmod 644 {} +')[1:]:
        assert block.lstrip().startswith('protect_site_secret_tree "$target"')


def test_php_upload_tmp_dir_keeps_nginx_readable_group():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "ensure_php_runtime_dirs()" in helper
    assert 'install -d -o "$user" -g "$spanel_SITES_GROUP" -m 2700 "$upload_dir"' in helper
    assert 'chmod g+s "$upload_dir"' in helper
    assert 'install -d -o "$user" -g "$user" -m 0700 "$sess_dir"' in helper
    assert 'ensure_php_runtime_dirs "$pool_user"' in helper
    assert 'chmod 2700 "/var/lib/php/uploads/$user"' in update


def test_php_fpm_pools_are_auto_tuned_for_vps_size():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "calculate_php_fpm_pool_tuning()" in helper
    assert "php_fpm_total_memory_mb()" in helper
    assert "php_fpm_cpu_count()" in helper
    assert "php_fpm_pool_count()" in helper
    assert "active_pool_divisor * active_pool_divisor < pool_count" in helper
    assert "pm.max_children = ${PHP_FPM_MAX_CHILDREN}" in helper
    assert "pm.process_idle_timeout = ${PHP_FPM_PROCESS_IDLE_TIMEOUT}s" in helper
    assert "pm.max_requests = ${PHP_FPM_MAX_REQUESTS}" in helper
    assert "request_terminate_timeout = ${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}s" in helper
    assert "spanel_PHP_FPM_WORKER_MB" in helper
    assert "spanel_PHP_FPM_MAX_CHILDREN" in helper
    assert "php-fpm-retune)" in helper
    assert "pm.max_children = 8" not in helper


def test_mariadb_is_auto_tuned_for_vps_size():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "calculate_mariadb_tuning()" in helper
    assert "write_mariadb_tuning()" in helper
    assert "mariadb-retune)" in helper
    assert "innodb_buffer_pool_size = ${MARIADB_INNODB_BUFFER_POOL_SIZE}" in helper
    assert "max_connections = ${MARIADB_MAX_CONNECTIONS}" in helper
    assert "table_open_cache = ${MARIADB_TABLE_OPEN_CACHE}" in helper
    assert "ensure_mariadb_slow_log" in helper
    assert "spanel_MARIADB_BUFFER_POOL_SIZE" in helper
    for script in (install, update):
        assert "spanel-autotune.service" in script
        assert "ExecStart=/usr/local/sbin/spanel-helper php-fpm-retune" in script
        assert "ExecStart=/usr/local/sbin/spanel-helper mariadb-retune" in script


def test_manual_ssl_helper_installs_private_key_outside_web_root():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "install_manual_ssl()" in helper
    assert "remove_manual_ssl()" in helper
    assert 'base="/etc/nginx/spanel/ssl/sites/${domain}"' in helper
    assert 'install -m 0640 -o root -g spanel "$tmpdir/privkey.key" "$base/privkey.key"' in helper
    assert "manual-ssl-install)" in helper
    assert "manual-ssl-remove)" in helper


def test_terminal_helper_rejects_paths_outside_user_home():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "require_terminal_path_args()" in helper
    assert "require_terminal_download_args()" in helper
    assert 'deny "terminal path argument is outside panel user home: $arg"' in helper
    assert 'deny "terminal path argument escapes user home: $arg"' in helper
    assert 'deny "terminal URL argument uses local file scheme: $arg"' in helper
    assert 'require_terminal_path_args "$user" "$target" "$@"' in helper
    assert 'require_terminal_download_args "$user" "$target" "$@"' in helper


def test_rm_site_helper_binds_delete_to_user_root_and_deletes_no_follow():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    assert "require_bound_managed_path()" in helper
    assert "delete_no_follow()" in helper
    assert 'target=$(require_bound_managed_path "$user" "$root" "$path")' in helper
    assert "os.path.normpath(sys.argv[1])" in helper
    assert 'root_relative="${normalized_root#${HOME_ROOT}/${user}/}"' in helper
    assert '[[ "$root_relative" == */* ]] && deny "site root must be a direct domain path: $normalized_root"' in helper
    assert '[[ "$target" == "$normalized_root" || "$target_relative" == */* ]] || deny "refusing to operate on a panel user home"' in helper
    assert 'delete_no_follow "$user" "$root" "$target"' in helper
    assert "os.O_NOFOLLOW" in helper
    assert "os.unlink(name, dir_fd=dir_fd)" in helper
    assert 'usage: rm-site <site-user> <site-root> <path>' in helper


def _verb(helper: str, name: str) -> str:
    return helper.split(f"\n  {name})\n")[1].split("\n    ;;\n")[0]


def test_only_an_explicit_request_resets_file_modes():
    """Operator, 2026-09-27: pipe.php set to 755 in the file manager was back
    on 644 "a while later". Five things reset it without being asked - an
    update, a new cron job or PHP version (site-runtime-ensure), a save,
    rename, move or extract in the file manager (site-path-fix), and the
    folders above a saved file. They now put ownership right and leave the
    modes alone; 755/644 is applied only where somebody asked for it or the
    panel has just put a whole tree there."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    own = helper.split("own_site_tree() {")[1].split("\n}\n")[0]
    assert 'chown -R "$user:$spanel_SITES_GROUP" "$target"' in own
    assert "chmod 755" not in own and "chmod 644" not in own and "SITE_FILE_MODE" not in own
    assert "-exec chmod u-s {} +" in own and "-exec chmod -t {} +" in own, "the refused special bits still go"
    for verb in ("site-path-fix", "site-runtime-ensure", "site-runtime-move"):
        body = _verb(helper, verb)
        assert "own_site_tree " in body and "fix_site_tree " not in body, verb
    extract = helper.split('install -o "$user" -g "$spanel_SITES_GROUP" -m 0644 -- "$tmp_archive" "$archive_target"')[1]
    assert extract.split("\n    ;;\n")[0].count("own_site_tree ") == 1
    # Where a full reset is still right: the button, a restore or import that
    # replaces the whole tree, one freshly uploaded file.
    callers = sorted(name for name in ("fix-permissions", "site-populate", "site-file-install", "site-path-fix",
                                       "site-runtime-ensure", "site-runtime-move", "site-file-write")
                     if "fix_site_tree " in _verb(helper, name))
    assert callers == ["fix-permissions", "site-file-install", "site-populate"]


def test_saving_a_file_keeps_its_mode_and_the_folders_above_it_keep_theirs():
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    write = _verb(helper, "site-file-write")
    assert 'if [[ -n "$existing_mode" && "$mode_arg" == "0644" ]]; then\n      # Saving a file keeps the mode it had' in write
    assert 'chmod "$existing_mode" "$tmp"' in write
    path = helper.split("harden_site_dir_path() {")[1].split("\n}\n")[0]
    assert "harden_site_dir " not in path and path.count("harden_site_dir_if_foreign ") == 2
    foreign = helper.split("harden_site_dir_if_foreign() {")[1].split("\n}\n")[0]
    assert '[[ "$(stat -c \'%U:%G\' -- "$target")" == "$user:$spanel_SITES_GROUP" ]] && return 0' in foreign


def test_the_update_refresh_does_not_reset_modes():
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    refresh = update.split('log "Refreshing managed site configuration"')[1].split("\nPY\n")[0]
    assert "site_users.fix_site_permissions(" not in refresh
    assert "site_users.ensure_site_runtime(" in refresh
