"""Contract tests for the bash side of the managed app runtimes.

The helper is the only thing that runs as root, so the properties that keep a
tenant contained live in a shell script no Python test executes. These assertions
pin the ones that matter, so a future edit cannot quietly drop them.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER_SCRIPT = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


@pytest.fixture(scope="module")
def helper() -> str:
    return HELPER_SCRIPT.read_text(encoding="utf-8")


# --- container hardening ----------------------------------------------------

def test_containers_publish_on_loopback_only(helper):
    """A published container port must never be reachable from the Internet."""
    assert "--publish 127.0.0.1:${port}:${container_port}" in helper
    assert "--publish ${port}" not in helper
    assert "--publish 0.0.0.0" not in helper


def test_containers_run_as_the_site_user_with_no_capabilities(helper):
    for flag in ("--user ${uid}:${gid}", "--cap-drop ALL", "--security-opt no-new-privileges"):
        assert flag in helper


def test_containers_are_resource_capped(helper):
    for flag in ("--memory ${memory}m", "--memory-swap ${memory}m", "--cpus ${cpus}", "--pids-limit 256"):
        assert flag in helper


def test_the_helper_never_grants_a_container_escape(helper):
    for forbidden in ("--privileged", "--network host", "--network=host", "--pid host", "--cap-add"):
        assert forbidden not in helper


def test_no_site_user_is_ever_added_to_the_docker_group(helper):
    """Membership of the docker group is equivalent to root on the host."""
    assert "usermod -aG docker" not in helper
    assert "adduser" not in helper or "docker" not in helper.split("adduser")[1][:80]
    assert "gpasswd -a" not in helper


def test_only_the_app_directory_is_mounted(helper):
    assert "--volume ${app_dir}:/app" in helper
    assert "--volume /:" not in helper
    assert "/var/run/docker.sock" not in helper


def test_container_logs_are_rotated(helper):
    assert "--log-opt max-size=10m" in helper
    assert '"max-size": "10m"' in helper


# --- docker firewall guard --------------------------------------------------

def test_docker_inbound_guard_uses_the_docker_user_chain(helper):
    # Docker's published ports bypass spanel-INPUT entirely; DOCKER-USER is the
    # only chain the operator owns.
    assert "iptables -w -N DOCKER-USER" in helper
    assert 'iptables -w -I DOCKER-USER 1 -i "$iface" -j DROP' in helper
    assert 'ctstate ESTABLISHED,RELATED -j RETURN' in helper


def test_docker_guard_is_reapplied_whenever_the_firewall_is_rebuilt(helper):
    apply_block = helper.split("firewall_apply() {", 1)[1].split("\n}", 1)[0]
    assert "install_docker_firewall_guard" in apply_block


# --- node unit hardening ----------------------------------------------------

def test_node_apps_are_forced_onto_loopback(helper):
    assert "Environment=HOST=127.0.0.1" in helper


def test_node_unit_caps_memory_and_drops_privileges(helper):
    for directive in (
        "MemoryMax=${memory}M",
        "MemoryAccounting=yes",
        "TasksMax=256",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ReadWritePaths=${app_dir}",
        "RestrictSUIDSGID=yes",
    ):
        assert directive in helper


def test_node_unit_runs_as_the_site_user(helper):
    assert "User=${user}" in helper
    assert "Group=${user}" in helper


# --- unit naming ------------------------------------------------------------

def test_the_unit_name_is_derived_not_accepted(helper):
    """No caller may hand systemctl a unit name of its choosing."""
    control_block = helper.split("  site-app-control)", 1)[1].split("    ;;", 1)[0]
    assert 'unit_name="$(app_unit_name "$user" "$app_name")"' in control_block
    # The action is checked against a fixed list; mask/kill are not on it.
    assert "is_in \"$app_action\" start stop restart status is-active is-enabled enable disable" in control_block


def test_control_refuses_a_missing_unit(helper):
    control_block = helper.split("  site-app-control)", 1)[1].split("    ;;", 1)[0]
    assert 'deny "application unit not found' in control_block


def test_environment_lines_are_validated_before_root_writes_them(helper):
    env_block = helper.split("write_app_env_file() {", 1)[1].split("\n}", 1)[0]
    assert '[[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]]' in env_block
    assert 'chmod 0600 "$target"' in env_block
    assert 'chown root:root "$target"' in env_block


def test_environment_file_lives_outside_the_site_tree(helper):
    """Inside the site it was file-manager editable, went into every backup, and
    the update.sh hardening pass handed it back to the site user."""
    path_block = helper.split("app_env_file() {", 1)[1].split("\n}", 1)[0]
    assert "$spanel_DATA_DIR" in path_block
    assert "/apps/" in path_block

    write_block = helper.split("  site-app-write)", 1)[1].split("\n    ;;", 1)[0]
    assert 'env_file="$(app_env_file "$user" "$app_name")"' in write_block


def test_deleting_an_app_removes_its_environment_file(helper):
    delete_block = helper.split("  site-app-delete)", 1)[1].split("\n    ;;", 1)[0]
    assert 'rm -f "$(app_env_file "$user" "$app_name")"' in delete_block


def test_deleting_an_app_leaves_the_customers_files_alone(helper):
    delete_block = helper.split("  site-app-delete)", 1)[1].split("\n    ;;", 1)[0]
    assert "rm -rf" not in delete_block


def test_app_directories_live_under_the_owners_home(helper):
    path_block = helper.split("app_directory() {", 1)[1].split("\n}", 1)[0]
    assert "$HOME_ROOT" in path_block
    assert "/apps/" in path_block

    ensure_block = helper.split("ensure_app_directory() {", 1)[1].split("\n}", 1)[0]
    assert 'require_linux_user "$user"' in ensure_block
    assert 'require_app_name "$name"' in ensure_block


def test_image_references_cannot_look_like_a_docker_flag(helper):
    image_block = helper.split("require_docker_image() {", 1)[1].split("\n}", 1)[0]
    assert "-*|*..*) deny" in image_block


def test_dependency_install_is_time_limited(helper):
    deps_block = helper.split("  site-app-install-deps)", 1)[1].split("\n    ;;", 1)[0]
    assert "timeout 900" in deps_block
    assert 'runuser -u "$user"' in deps_block


# --- installer wiring -------------------------------------------------------

def test_installers_write_the_websocket_upgrade_map():
    for script in (INSTALL_SCRIPT, UPDATE_SCRIPT):
        text = script.read_text(encoding="utf-8")
        assert "00-spanel-upgrade-map.conf" in text, script.name
        assert "map $http_upgrade $connection_upgrade" in text, script.name
        assert "configure_proxy_upgrade_map\n" in text, script.name


# --- websocket map self-heal ------------------------------------------------

def test_helper_can_write_the_upgrade_map_itself(helper):
    """A panel updated from an older release never ran the installer step."""
    assert "nginx-upgrade-map-ensure)" in helper
    block = helper.split("ensure_proxy_upgrade_map() {", 1)[1].split("\n}", 1)[0]
    assert "map $http_upgrade $connection_upgrade" in block
    assert "00-spanel-upgrade-map.conf" in block


def test_proxied_vhost_writes_ensure_the_map_first():
    service = (PROJECT_ROOT / "backend" / "app" / "services" / "nginx.py").read_text(encoding="utf-8")
    rewrite = service.split("def rewrite_vhost(", 1)[1]
    assert "ensure_proxy_upgrade_map()" in rewrite.split("content = render_vhost(", 1)[0]


def test_legacy_cleanup_survives_finding_nothing(helper):
    """Under `set -o pipefail` a grep with no match kills the helper silently."""
    block = helper.split("remove_legacy_app_units() {", 1)[1].split("\n}", 1)[0]
    grep_lines = [line for line in block.splitlines() if "grep -E" in line]
    assert grep_lines, "expected a grep over existing container names"
    assert all("|| true" in line for line in grep_lines), grep_lines


# --- file manager reaching into an app directory ----------------------------

def test_managed_roots_cover_both_sites_and_apps(helper):
    """The file manager runs its privileged steps through the same path checks."""
    block = helper.split("managed_root_depth() {", 1)[1].split("\n}", 1)[0]
    assert 'require_app_name "$second"' in block
    assert 'require_site_domain_segment "$first"' in block
    # apps on its own is not a root: an operation must name an application.
    assert 'deny "application path is missing an application name"' in block


def test_a_bound_app_root_must_be_exactly_two_components(helper):
    block = helper.split("require_bound_managed_path() {", 1)[1].split("\n}", 1)[0]
    assert 'deny "application root must be apps/<name>' in block
    assert 'deny "site root must be a direct domain path' in block


def test_managed_paths_still_reject_the_bare_home(helper):
    block = helper.split("require_managed_path() {", 1)[1].split("\n}", 1)[0]
    assert 'deny "path is not owned by panel Linux user' in block
    assert 'deny "path outside managed site roots' in block


def test_delete_accepts_an_application_root_too(helper):
    """delete_no_follow keeps its own root-shape check, separate from the
    managed-path validator, so it has to know about apps/<name> as well."""
    block = helper.split("delete_no_follow() {", 1)[1].split("\nPY\n", 1)[0]
    assert 'os.path.join(base, "apps")' in block


def test_rename_refuses_to_overwrite_an_existing_directory(helper):
    block = helper.split("  site-app-rename)", 1)[1].split("\n    ;;", 1)[0]
    assert 'deny "a directory already exists at' in block
    assert 'require_app_name "$app_new_name"' in block
    # mv -T so a rename into an existing name cannot nest one app inside another.
    assert "mv -T --" in block
