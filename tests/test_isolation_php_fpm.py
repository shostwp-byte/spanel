"""Per-account PHP-FPM masters: naming, rendering, start/fallback, migration.

The helper half runs the real spanel-helper.sh functions under bash against a
scratch tree (see _isolation_bash.py); systemctl, id and the php-fpm binary are
stubs that record what they were asked to do. What these cannot show - systemd
really starting the unit, the workers landing in user-<uid>.slice - needs a
real Ubuntu server.
"""
import hashlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from _isolation_bash import BASH, REPO_ROOT, helper_assignment, helper_functions, run_bash

sys.path.insert(0, str(REPO_ROOT / "backend"))

needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")

SITE = "/home/alice/example.com"
SITE_HASH = hashlib.sha256(SITE.encode("utf-8")).hexdigest()[:12]
POOL = f"spanel-alice-{SITE_HASH}-8_3"

FPM_FUNCTIONS = (
    "site_root_hash", "site_php_pool_glob", "php_pool_name", "user_php_fpm_unit", "user_php_fpm_dir",
    "user_php_fpm_socket", "legacy_php_pool_file", "managed_php_pool_files", "php_pool_file_is_legacy",
    "php_pool_file_version", "php_pool_file_user", "php_pool_listen", "php_pool_file_unit",
    "php_fpm_unique_pool_count", "php_fpm_pool_count", "write_file_if_changed", "render_user_php_fpm_unit",
    "render_user_php_fpm_slice_dropin", "render_user_php_fpm_conf", "render_php_pool_conf",
    "sync_user_php_fpm_master", "reload_user_php_fpm_masters", "reload_php_pool_owners",
    "prune_legacy_php_pools", "delete_site_php_pools", "ensure_php_pool",
    "apply_saved_user_limits_to_pool_file", "php_fpm_set_directive", "ensure_php_fpm_cgroup_dropin",
    "require_php_version", "require_linux_user", "is_allowed_service", "is_in",
)

STUBS = r'''
# Remembers which units it "started"; FAKE_MASTER_UP=0 makes every start fail.
systemctl() {
  echo "$*" >>"$ROOT/systemctl.log"
  local unit="${*: -1}"
  case "$1" in
    is-active) grep -qxF "$unit" "$ROOT/active" ;;
    restart|start)
      if [[ "${FAKE_MASTER_UP:-1}" == 1 ]]; then
        grep -qxF "$unit" "$ROOT/active" || echo "$unit" >>"$ROOT/active"
      else
        grep -vxF "$unit" "$ROOT/active" >"$ROOT/active.new" || true
        mv "$ROOT/active.new" "$ROOT/active"
        return 1
      fi ;;
    disable|stop)
      grep -vxF "$unit" "$ROOT/active" >"$ROOT/active.new" || true
      mv "$ROOT/active.new" "$ROOT/active" ;;
    *) return 0 ;;
  esac
}
id() { [[ "$1" == -u ]] && echo 1001; }
logger() { :; }
ensure_php_runtime_dirs() { :; }
ensure_php_fpm_logrotate() { :; }
classify_all_tenant_workers() { echo classify >>"$ROOT/calls.log"; }
calculate_php_fpm_pool_tuning() {
  PHP_FPM_PM_MODE=ondemand PHP_FPM_MAX_CHILDREN=6 PHP_FPM_PROCESS_IDLE_TIMEOUT=10
  PHP_FPM_MAX_REQUESTS=500 PHP_FPM_REQUEST_TERMINATE_TIMEOUT=300
}
'''


def _script(root: Path, body: str) -> str:
    preamble = "\n".join([
        "set -euo pipefail",
        f"ROOT='{root.as_posix()}'",
        'deny() { echo "deny: $*" >&2; exit 1; }',
        'SPANEL_PHP_FPM_ROOT="$ROOT/etc/spanel/php-fpm"',
        'SPANEL_PHP_RUN_ROOT="/run/spanel-php"',
        'SYSTEMD_UNIT_DIR="$ROOT/systemd"',
        'PHP_ETC_ROOT="$ROOT/etc/php"',
        'PHP_LOG_DIR="/var/log/php"',
        'PHP_FPM_SBIN_DIR="$ROOT/sbin"',
        'NGINX_CONF_DIR="$ROOT/nginx"',
        "ALLOWED_SERVICES=(nginx)",
        helper_assignment("PHP_FPM_SOCKET_GREP"),
        'mkdir -p "$SYSTEMD_UNIT_DIR" "$PHP_ETC_ROOT/8.3/fpm/pool.d" "$NGINX_CONF_DIR" "$PHP_FPM_SBIN_DIR"',
        "printf '#!/bin/sh\\nexit ${FAKE_FPM_TEST_RC:-0}\\n' >\"$PHP_FPM_SBIN_DIR/php-fpm8.3\"",
        'chmod +x "$PHP_FPM_SBIN_DIR/php-fpm8.3"',
        'touch "$ROOT/systemctl.log" "$ROOT/calls.log" "$ROOT/active"',
    ])
    return "\n".join([preamble, helper_functions(*FPM_FUNCTIONS), STUBS, body, ""])


def _systemctl_log(root: Path) -> list[str]:
    return (root / "systemctl.log").read_text(encoding="utf-8").splitlines()


def _user_pool(root: Path) -> Path:
    return root / "etc/spanel/php-fpm/8.3/alice/pool.d" / f"{POOL}.conf"


def _legacy_pool(root: Path) -> Path:
    return root / "etc/php/8.3/fpm/pool.d" / f"{POOL}.conf"


def _ini(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith((";", "#", "[")):
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), []).append(value.strip())
    return values


# --- naming: one source of truth --------------------------------------------------

@needs_bash
def test_helper_and_backend_name_pools_and_sockets_alike(tmp_path):
    from app.services import site_users

    out = run_bash(tmp_path, _script(tmp_path, f'''
pool="$(php_pool_name alice {SITE} 8.3)"
echo "$pool"
user_php_fpm_socket alice "$pool"
user_php_fpm_unit 8.3 alice
legacy_php_pool_file 8.3 "$pool"
''')).stdout.split()
    user_root = tmp_path / "etc/spanel/php-fpm"
    with patch.object(site_users, "_resolved_site_root", lambda p: str(p)), \
            patch.object(site_users, "PHP_FPM_USER_ROOT", user_root):
        assert out[0] == site_users.php_fpm_pool_name("alice", "8.3", SITE) == POOL
        assert out[2] == site_users.php_fpm_user_unit("alice", "8.3")
        # No pool with the account master yet: the distro socket.
        assert site_users.site_php_fpm_socket("alice", SITE, "8.3") == f"/run/php/{POOL}.sock"
        assert out[3].endswith(f"/etc/php/8.3/fpm/pool.d/{POOL}.conf")
        pool_file = site_users.php_fpm_user_pool_file("alice", "8.3", SITE)
        assert pool_file == _user_pool(tmp_path)
        pool_file.parent.mkdir(parents=True)
        pool_file.write_text("[x]\n", encoding="utf-8")
        assert site_users.site_php_fpm_socket("alice", SITE, "8.3") == out[1] == f"/run/spanel-php/alice/{POOL}.sock"


def test_socket_validation_accepts_both_layouts_only():
    from app.services import site_users

    good = [
        "/run/php/php8.4-fpm.sock",
        f"/run/php/{POOL}.sock",
        f"/run/spanel-php/alice/{POOL}.sock",
        "/run/spanel-php/bp_example_com_1a2b3c4d/spanel-bp_example_com_1a2b3c4d-0123456789ab-8_4.sock",
    ]
    bad = [
        "", "/run/php/../etc/x.sock", "/run/spanel-php/../x.sock", "/run/spanel-php/alice/sub/x.sock",
        "/run/spanel-php/Alice/x.sock", "/tmp/x.sock", "/run/spanel-php/alice/x.sock;", "/run/php/x.sock\n",
        "/run/spanel-php/ab/x.sock",
    ]
    assert all(site_users.is_valid_php_fpm_socket(s) for s in good)
    assert not any(site_users.is_valid_php_fpm_socket(s) for s in bad)


def test_nginx_switches_a_vhost_to_the_account_socket_and_refuses_junk():
    from app.services import nginx

    vhost = "location ~ \\.php$ {\n    fastcgi_pass unix:/run/php/old.sock;\n}\n"
    new = f"/run/spanel-php/alice/{POOL}.sock"
    assert f"fastcgi_pass unix:{new};" in nginx._replace_fastcgi_socket(vhost, new)
    for junk in ("/run/spanel-php/alice/../../etc/passwd.sock", "/run/spanel-php/alice/x.sock; root /"):
        with pytest.raises(ValueError):
            nginx._replace_fastcgi_socket(vhost, junk)


def test_slowlog_pool_names_still_resolve_to_their_account():
    from app.services import site_users

    assert site_users.linux_user_from_pool_name(POOL) == "alice"
    assert site_users.linux_user_from_pool_name("spanel-bp_x-y_1a2b3c4d-0123456789ab-8_4") == "bp_x-y_1a2b3c4d"
    for other in ("www", "spanel-alice-8_3", "spanel-alice-0123456789ab-9_9", ""):
        assert site_users.linux_user_from_pool_name(other) == ""


# --- rendering -------------------------------------------------------------------

@needs_bash
def test_unit_template_runs_one_master_per_account_with_the_distro_ini(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, "render_user_php_fpm_unit 8.3")).stdout
    unit = _ini(out)
    fpm = f"{tmp_path.as_posix()}/sbin/php-fpm8.3"
    conf = f"{tmp_path.as_posix()}/etc/spanel/php-fpm/8.3/%i/php-fpm.conf"
    assert unit["Type"] == ["notify"]
    assert unit["ExecStart"] == [f"{fpm} --nodaemonize --fpm-config {conf}"]
    assert f"{fpm} --test --fpm-config {conf}" in unit["ExecStartPre"]
    assert unit["Environment"] == [f"PHP_INI_SCAN_DIR={tmp_path.as_posix()}/etc/php/8.3/fpm/conf.d"]
    assert unit["ExecReload"] == ["/bin/kill -USR2 $MAINPID"]
    assert "/usr/bin/install -d -m 0750 -o root -g www-data /run/spanel-php/%i" in unit["ExecStartPre"]
    assert unit["Restart"] == ["on-failure"]
    assert unit["WantedBy"] == ["multi-user.target"]
    # Two versions of one account share /run/spanel-php/<user>; systemd would
    # remove a RuntimeDirectory under the other when one stopped.
    assert "RuntimeDirectory" not in unit


@needs_bash
def test_slice_dropin_and_master_config(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, '''
render_user_php_fpm_slice_dropin 1001
echo ---
render_user_php_fpm_conf 8.3 alice
''')).stdout
    dropin, conf = out.split("---\n")
    assert _ini(dropin) == {"Slice": ["user-1001.slice"]}
    values = _ini(conf)
    assert values["include"] == [f"{tmp_path.as_posix()}/etc/spanel/php-fpm/8.3/alice/pool.d/*.conf"]
    assert values["pid"] == ["/run/spanel-php/alice/php8.3-fpm.pid"]
    assert values["error_log"] == ["/var/log/php/spanel-fpm-alice-8.3.log"]


@needs_bash
def test_pool_keeps_its_name_slowlog_and_sandbox(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, f'''
calculate_php_fpm_pool_tuning
render_php_pool_conf {POOL} alice {SITE} /run/spanel-php/alice/{POOL}.sock
''')).stdout
    assert out.startswith(f"[{POOL}]\n")
    values = _ini(out)
    assert values["listen"] == [f"/run/spanel-php/alice/{POOL}.sock"]
    assert values["user"] == values["group"] == ["alice"]
    assert values["listen.owner"] == ["www-data"] and values["listen.mode"] == ["0660"]
    assert values["slowlog"] == [f"/var/log/php/spanel-slow-{POOL}.log"]
    assert values["php_admin_value[open_basedir]"] == [
        f"{SITE}:/var/lib/php/sessions/alice:/var/lib/php/uploads/alice:/usr/share/php"
    ]
    assert values["pm.max_children"] == ["6"]


# --- ensure / fallback / delete ----------------------------------------------------

@needs_bash
def test_ensure_starts_the_account_master_inside_its_slice(tmp_path):
    run_bash(tmp_path, _script(tmp_path, f"ensure_php_pool alice {SITE} 8.3"))

    assert _user_pool(tmp_path).is_file()
    assert not _legacy_pool(tmp_path).exists()
    assert f"listen = /run/spanel-php/alice/{POOL}.sock" in _user_pool(tmp_path).read_text(encoding="utf-8")
    assert (tmp_path / "systemd/spanel-php8.3-fpm@.service").is_file()
    dropin = tmp_path / "systemd/spanel-php8.3-fpm@alice.service.d/10-spanel-slice.conf"
    assert "Slice=user-1001.slice" in dropin.read_text(encoding="utf-8")
    assert (tmp_path / "etc/spanel/php-fpm/8.3/alice/php-fpm.conf").is_file()
    log = _systemctl_log(tmp_path)
    assert "daemon-reload" in log
    assert "enable spanel-php8.3-fpm@alice.service" in log
    assert "restart spanel-php8.3-fpm@alice.service" in log
    # PHP on its own master is born in the slice: nothing to classify, and the
    # distro master is not touched.
    assert not any("php8.3-fpm" in line and "@" not in line for line in log)
    assert "classify" not in (tmp_path / "calls.log").read_text(encoding="utf-8")


@needs_bash
def test_a_second_site_reloads_the_running_master(tmp_path):
    run_bash(tmp_path, _script(tmp_path, f'''
ensure_php_pool alice {SITE} 8.3
: >"$ROOT/systemctl.log"
ensure_php_pool alice /home/alice/second.example 8.3
'''))
    log = _systemctl_log(tmp_path)
    assert "reload spanel-php8.3-fpm@alice.service" in log
    assert "restart spanel-php8.3-fpm@alice.service" not in log
    assert "daemon-reload" not in log  # unit and drop-in unchanged
    assert len(list((tmp_path / "etc/spanel/php-fpm/8.3/alice/pool.d").glob("*.conf"))) == 2


@needs_bash
def test_a_master_that_will_not_start_leaves_the_site_on_the_distro_master(tmp_path):
    run_bash(tmp_path, _script(tmp_path, f"ensure_php_pool alice {SITE} 8.3"), env={"FAKE_MASTER_UP": "0"})

    assert not _user_pool(tmp_path).exists()
    legacy = _legacy_pool(tmp_path).read_text(encoding="utf-8")
    assert f"listen = /run/php/{POOL}.sock" in legacy
    log = _systemctl_log(tmp_path)
    assert "reload php8.3-fpm" in log
    # Nothing left for the account master: it is disabled again, not left failing.
    assert "disable --now spanel-php8.3-fpm@alice.service" in log
    assert not (tmp_path / "etc/spanel/php-fpm/8.3/alice").exists()
    assert "classify" in (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert (tmp_path / "systemd/php8.3-fpm.service.d/50-spanel-cgroup.conf").is_file()


@needs_bash
def test_a_config_test_failure_falls_back_without_touching_the_running_master(tmp_path):
    run_bash(tmp_path, _script(tmp_path, f"ensure_php_pool alice {SITE} 8.3"), env={"FAKE_FPM_TEST_RC": "1"})
    assert _legacy_pool(tmp_path).is_file() and not _user_pool(tmp_path).exists()
    assert not any(line.startswith(("restart", "reload spanel")) for line in _systemctl_log(tmp_path))


@needs_bash
def test_an_already_moved_site_is_never_moved_back_behind_nginx(tmp_path):
    """Its vhost names the account socket; falling back would point nginx at
    nothing. The previous pool is restored and the error reported."""
    result = run_bash(tmp_path, _script(tmp_path, f'''
ensure_php_pool alice {SITE} 8.3
echo "; marker" >>"$SPANEL_PHP_FPM_ROOT/8.3/alice/pool.d/{POOL}.conf"
export FAKE_FPM_TEST_RC=1
ensure_php_pool alice {SITE} 8.3
'''), check=False)
    assert result.returncode != 0
    assert "previous pool was restored" in result.stderr
    assert _user_pool(tmp_path).read_text(encoding="utf-8").endswith("; marker\n")
    assert not _legacy_pool(tmp_path).exists()


@needs_bash
def test_deleting_the_last_site_stops_and_removes_the_account_master(tmp_path):
    run_bash(tmp_path, _script(tmp_path, f'''
ensure_php_pool alice {SITE} 8.3
ensure_php_pool alice /home/alice/second.example 8.3
: >"$ROOT/systemctl.log"
delete_site_php_pools alice {SITE}
echo "after-first" >>"$ROOT/systemctl.log"
delete_site_php_pools alice /home/alice/second.example
'''))
    log = _systemctl_log(tmp_path)
    first, second = log[: log.index("after-first")], log[log.index("after-first") + 1:]
    assert "reload spanel-php8.3-fpm@alice.service" in first
    assert "disable --now spanel-php8.3-fpm@alice.service" in second
    assert not (tmp_path / "etc/spanel/php-fpm/8.3/alice").exists()
    assert not (tmp_path / "systemd/spanel-php8.3-fpm@alice.service.d").exists()


# --- migration off the distro master ----------------------------------------------

def _seed_moved_site(tmp_path: Path, vhost_socket: str | None) -> str:
    return f'''
calculate_php_fpm_pool_tuning
render_php_pool_conf {POOL} alice {SITE} /run/php/{POOL}.sock >"$PHP_ETC_ROOT/8.3/fpm/pool.d/{POOL}.conf"
ensure_php_pool alice {SITE} 8.3
''' + (f'echo "fastcgi_pass unix:{vhost_socket};" >"$NGINX_CONF_DIR/example.com.conf"\n' if vhost_socket else "")


@needs_bash
def test_legacy_pool_is_retired_once_nginx_names_the_new_socket(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, _seed_moved_site(tmp_path, f"/run/spanel-php/alice/{POOL}.sock")
                                     + "prune_legacy_php_pools\n")).stdout
    assert f"retired legacy pool {POOL}" in out
    assert not _legacy_pool(tmp_path).exists() and _user_pool(tmp_path).is_file()
    assert "reload php8.3-fpm" in _systemctl_log(tmp_path)


@needs_bash
def test_legacy_pool_stays_while_its_socket_is_still_in_use(tmp_path):
    run_bash(tmp_path, _script(tmp_path, _seed_moved_site(tmp_path, f"/run/php/{POOL}.sock") + "prune_legacy_php_pools\n"))
    assert _legacy_pool(tmp_path).is_file()


@needs_bash
def test_prune_refuses_when_nginx_names_no_socket_at_all(tmp_path):
    run_bash(tmp_path, _script(tmp_path, _seed_moved_site(tmp_path, None) + "prune_legacy_php_pools\n"))
    assert _legacy_pool(tmp_path).is_file()


@needs_bash
def test_prune_keeps_a_legacy_pool_that_never_moved(tmp_path):
    # Fallback site: only on the distro master, nothing to retire it in favour of.
    run_bash(tmp_path, _script(tmp_path, f'''
ensure_php_pool alice {SITE} 8.3
echo "fastcgi_pass unix:/run/spanel-php/alice/other.sock;" >"$NGINX_CONF_DIR/a.conf"
prune_legacy_php_pools
'''), env={"FAKE_MASTER_UP": "0"})
    assert _legacy_pool(tmp_path).is_file()


@needs_bash
def test_a_pool_mid_move_counts_once_for_sizing(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, _seed_moved_site(tmp_path, None) + f'''
php_fpm_unique_pool_count
php_fpm_pool_count "$SPANEL_PHP_FPM_ROOT/8.3/alice/pool.d/{POOL}.conf"
php_fpm_pool_count "$PHP_ETC_ROOT/8.3/fpm/pool.d/{POOL}.conf"
php_fpm_pool_count "$SPANEL_PHP_FPM_ROOT/8.3/alice/pool.d/new.conf"
''')).stdout.split()
    assert out == ["1", "1", "1", "2"]


@needs_bash
def test_reload_owners_reaches_each_master_once(tmp_path):
    run_bash(tmp_path, _script(tmp_path, _seed_moved_site(tmp_path, None) + '''
ensure_php_pool alice /home/alice/second.example 8.3
: >"$ROOT/systemctl.log"
files="$(managed_php_pool_files)"
reload_php_pool_owners <<<"$files"
'''))
    log = _systemctl_log(tmp_path)
    assert log.count("reload php8.3-fpm") == 1
    assert log.count("reload spanel-php8.3-fpm@alice.service") == 1


@needs_bash
def test_service_allowlist_takes_an_account_master_only_once_it_exists(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, f'''
check() {{ if is_allowed_service "$1"; then echo yes; else echo no; fi; }}
check spanel-php8.3-fpm@alice
ensure_php_pool alice {SITE} 8.3
check spanel-php8.3-fpm@alice
check spanel-php8.3-fpm@bob
check "spanel-php8.3-fpm@alice;reboot"
check spanel-php9.9-fpm@alice
''')).stdout.split()
    assert out == ["no", "yes", "no", "no", "no"]


# --- backend views of the masters ---------------------------------------------------

def _make_user_master(root: Path, version: str, user: str, pools: int = 1) -> None:
    pool_dir = root / version / user / "pool.d"
    pool_dir.mkdir(parents=True)
    (root / version / user / "php-fpm.conf").write_text("[global]\n", encoding="utf-8")
    for i in range(pools):
        (pool_dir / f"spanel-{user}-{i:012x}-{version.replace('.', '_')}.conf").write_text(
            f"[spanel-{user}-{i:012x}-{version.replace('.', '_')}]\npm.max_children = 4\n", encoding="utf-8")


def test_watchdog_and_service_actions_cover_account_masters(tmp_path):
    from app.services import site_users, system

    _make_user_master(tmp_path, "8.3", "alice")
    _make_user_master(tmp_path, "8.4", "bob_site")
    _make_user_master(tmp_path, "8.4", "carol", pools=0)  # no pool: nothing to watch
    with patch.object(site_users, "PHP_FPM_USER_ROOT", tmp_path), \
            patch.object(system, "list_services", return_value=["nginx", "php8.4-fpm"]):
        assert system.watched_services() == ["nginx", "php8.4-fpm", "spanel-php8.3-fpm@alice", "spanel-php8.4-fpm@bob_site"]
        with patch.object(system.shell, "privileged") as privileged:
            system.service_action("spanel-php8.3-fpm@alice", "restart")
        assert privileged.call_args.kwargs["helper_args"] == ["spanel-php8.3-fpm@alice", "restart"]
        with pytest.raises(ValueError):
            system.service_action("spanel-php8.4-fpm@carol", "restart")
        assert system._validate_service_config("spanel-php8.3-fpm@alice")[0] is True


def test_php_tune_counts_and_lists_pools_across_both_masters(tmp_path):
    from app.services import php_tune, site_users

    user_root, etc_php = tmp_path / "spanel", tmp_path / "php"
    _make_user_master(user_root, "8.3", "alice", pools=2)
    legacy_dir = etc_php / "8.3/fpm/pool.d"
    legacy_dir.mkdir(parents=True)
    moving = next((user_root / "8.3/alice/pool.d").glob("*.conf"))
    (legacy_dir / moving.name).write_text(moving.read_text(encoding="utf-8"), encoding="utf-8")
    (legacy_dir / "spanel-dave-00000000000a-8_3.conf").write_text("[spanel-dave-00000000000a-8_3]\n", encoding="utf-8")
    with patch.object(site_users, "PHP_FPM_USER_ROOT", user_root), patch.object(php_tune, "PHP_ETC_DIR", etc_php):
        assert php_tune.pool_count() == 3
        pools = php_tune.current_pool_settings("8.3")
    served = {p["pool"]: p["served_by"] for p in pools}
    assert len(pools) == 3
    assert served[moving.stem] == "spanel-php8.3-fpm@alice"
    assert served["spanel-dave-00000000000a-8_3"] == "php8.3-fpm"


def test_update_retires_moved_pools_only_after_the_site_refresh():
    text = (REPO_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    refresh = text.index("nginx.rewrite_vhost(")
    prune = text.index("spanel-helper php-fpm-legacy-prune")
    assert refresh < prune
    # Sites left on a distro master keep being retried by later updates.
    assert '! step_inputs_changed site-refresh "${SITE_REFRESH_INPUTS[@]}" && ! legacy_php_pools_left' in text
