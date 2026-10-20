from pathlib import Path

import pytest

from app.models.entities import SiteApp, User, UserPackage, Website
from app.services import site_apps


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class _FakeSession:
    """Just enough Session for the port allocator and quota checks."""

    def __init__(self, ports=(), app_count=0):
        self._ports = list(ports)
        self._app_count = app_count

    def scalars(self, _query):
        return _FakeScalars(self._ports)

    def query(self, *_args):
        return self

    def join(self, *_args):
        return self

    def filter(self, *_args):
        return self

    def count(self):
        return self._app_count


def _package(**kwargs):
    return UserPackage(name="Test", **kwargs)


def _user(package=None):
    user = User(id=1, username="tester", email="t@example.test", role="end_user")
    user.package = package
    return user


# --- names, kinds, ports ----------------------------------------------------

@pytest.mark.parametrize("name", ["app", "n8n", "my-app", "a1_b2", "x"])
def test_valid_app_names(name):
    assert site_apps.validate_name(name) == name


@pytest.mark.parametrize("name", ["", "-app", "app-", "App Name", "app/../x", "a" * 40, "ứng-dụng"])
def test_invalid_app_names(name):
    with pytest.raises(ValueError):
        site_apps.validate_name(name)


def test_port_must_sit_inside_the_managed_range():
    assert site_apps.validate_port(21000) == 21000
    assert site_apps.validate_port("21999") == 21999
    for bad in (80, 20999, 22000, 65536, "abc", None):
        with pytest.raises(ValueError):
            site_apps.validate_port(bad)


def test_allocate_port_skips_reserved_and_listening_ports(monkeypatch):
    monkeypatch.setattr(site_apps, "listening_ports", lambda: {21001})
    db = _FakeSession(ports=[21000])

    assert site_apps.allocate_port(db) == 21002


def test_allocate_port_honours_a_free_preference(monkeypatch):
    monkeypatch.setattr(site_apps, "listening_ports", lambda: set())

    assert site_apps.allocate_port(_FakeSession(), preferred=21500) == 21500


def test_allocate_port_rejects_a_taken_preference(monkeypatch):
    monkeypatch.setattr(site_apps, "listening_ports", lambda: {21500})

    with pytest.raises(ValueError, match="already in use"):
        site_apps.allocate_port(_FakeSession(), preferred=21500)


def test_allocate_port_reports_an_exhausted_range(monkeypatch):
    monkeypatch.setattr(site_apps, "listening_ports", lambda: set())
    db = _FakeSession(ports=list(range(site_apps.PORT_RANGE_START, site_apps.PORT_RANGE_END + 1)))

    with pytest.raises(ValueError, match="No free application port"):
        site_apps.allocate_port(db)


def test_listening_ports_parses_ss_output(monkeypatch):
    sample = (
        "LISTEN 0      511          0.0.0.0:80         0.0.0.0:*\n"
        "LISTEN 0      4096      127.0.0.1:21000      0.0.0.0:*\n"
        "LISTEN 0      4096         [::1]:21001          [::]:*\n"
        "garbage line\n"
    )
    monkeypatch.setattr(
        site_apps.shell,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": sample, "stderr": ""})(),
    )

    assert site_apps.listening_ports() == {80, 21000, 21001}


def test_listening_ports_is_empty_when_ss_is_missing(monkeypatch):
    monkeypatch.setattr(
        site_apps.shell,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 127, "stdout": "", "stderr": "not found"})(),
    )

    assert site_apps.listening_ports() == set()


# --- start command ----------------------------------------------------------

def test_start_command_accepts_the_supported_launchers():
    assert site_apps.validate_start("npm", "start") == ("npm", "start")
    assert site_apps.validate_start("npx", "n8n") == ("npx", "n8n")
    assert site_apps.validate_start("yarn", "start") == ("yarn", "start")
    assert site_apps.validate_start("node", "server.js") == ("node", "server.js")


@pytest.mark.parametrize(
    "start_kind,start_arg",
    [
        ("bash", "start"),
        ("npm", ""),
        ("npm", "start; rm -rf /"),
        ("npm", "start && curl evil"),
        ("npm", "$(whoami)"),
        ("npm", "start`id`"),
        ("node", "server.php"),
        ("node", "../../../etc/passwd.js"),
    ],
)
def test_start_command_rejects_anything_shell_shaped(start_kind, start_arg):
    with pytest.raises(ValueError):
        site_apps.validate_start(start_kind, start_arg)


def test_node_entry_file_must_stay_inside_the_app_directory():
    assert site_apps.validate_start("node", "src/index.mjs")[1] == "src/index.mjs"
    with pytest.raises(ValueError, match="inside the app directory"):
        site_apps.validate_start("node", "../outside.js")


# --- app directory, memory, node version ------------------------------------

def test_app_directory_sits_under_the_owners_home():
    assert site_apps.app_directory("siteuser", "n8n").as_posix().endswith("/home/siteuser/apps/n8n")


def test_app_directory_rejects_an_unsafe_user_or_name():
    for user, name in (("root", "app"), ("../etc", "app"), ("siteuser", "../escape"), ("siteuser", "a b")):
        with pytest.raises(ValueError):
            site_apps.app_directory(user, name)


def test_memory_limit_respects_the_package_ceiling():
    assert site_apps.validate_memory_mb(None) == site_apps.DEFAULT_MEMORY_MB
    assert site_apps.validate_memory_mb(1024, ceiling=2048) == 1024
    with pytest.raises(ValueError, match="at most 512 MB"):
        site_apps.validate_memory_mb(1024, ceiling=512)
    for bad in (0, 32, 99999, "lots"):
        with pytest.raises(ValueError):
            site_apps.validate_memory_mb(bad)


def test_no_memory_given_falls_back_to_a_low_package_ceiling():
    """A package below the default must not refuse every app the customer makes."""
    assert site_apps.validate_memory_mb(None, ceiling=256) == 256
    assert site_apps.validate_memory_mb("", ceiling=128) == 128
    # A generous ceiling still leaves the ordinary default in place.
    assert site_apps.validate_memory_mb(None, ceiling=4096) == site_apps.DEFAULT_MEMORY_MB


def test_node_major_validation():
    assert site_apps.validate_node_major("22") == "22"
    assert site_apps.validate_node_major(None) is None
    for bad in ("22.1", "v22", "9", "latest"):
        with pytest.raises(ValueError):
            site_apps.validate_node_major(bad)


# --- quota ------------------------------------------------------------------

def test_apps_are_off_without_a_package():
    with pytest.raises(ValueError, match="not enabled"):
        site_apps.ensure_app_quota(_FakeSession(), _user(), is_admin=False)


def test_apps_are_off_when_the_package_limit_is_zero():
    user = _user(_package(node_apps_limit=0))

    with pytest.raises(ValueError, match="not enabled"):
        site_apps.ensure_app_quota(_FakeSession(), user, is_admin=False)


def test_quota_blocks_once_the_limit_is_reached():
    user = _user(_package(node_apps_limit=2))

    site_apps.ensure_app_quota(_FakeSession(app_count=1), user, is_admin=False)
    with pytest.raises(ValueError, match="at most 2"):
        site_apps.ensure_app_quota(_FakeSession(app_count=2), user, is_admin=False)


def test_admins_are_not_held_to_a_package_quota():
    site_apps.ensure_app_quota(_FakeSession(app_count=99), _user(), is_admin=True)


# --- website helpers --------------------------------------------------------

def test_app_port_for_website_follows_the_app_pointer():
    website = Website(domain="example.test", owner_id=1, root_path="/home/u/example.test")
    assert site_apps.app_port_for_website(website) is None

    website.app = SiteApp(owner_id=1, name="app", kind="node", port=21007)
    assert site_apps.app_port_for_website(website) == 21007


# --- container images -------------------------------------------------------

@pytest.mark.parametrize(
    "image",
    [
        "n8nio/n8n",
        "n8nio/n8n:1.60.0",
        "ghcr.io/open-webui/open-webui:main",
        "quay.io/org/app:v2",
        "nginx@sha256:" + "a" * 64,
        "nginx:1.27",
        "redis",
    ],
)
def test_allowed_images_pass(image):
    assert site_apps.validate_image(image) == image


@pytest.mark.parametrize(
    "image",
    [
        "",
        "-v/etc:/etc",
        "org/../../etc/passwd",
        "Org/Image",
        "org/app:tag with space",
        "org/app;rm -rf /",
    ],
)
def test_malformed_images_are_rejected(image):
    with pytest.raises(ValueError):
        site_apps.validate_image(image)


def test_images_from_unlisted_registries_are_rejected():
    with pytest.raises(ValueError, match="are not allowed"):
        site_apps.validate_image("evil.example.com/backdoor:latest")


def test_admins_can_bypass_the_registry_allowlist():
    assert site_apps.validate_image("evil.example.com/app:1", enforce_registry=False) == "evil.example.com/app:1"


def test_registry_allowlist_is_configurable(monkeypatch):
    monkeypatch.setenv("spanel_ALLOWED_REGISTRIES", "registry.spanel.io, ghcr.io")

    assert site_apps.allowed_registries() == ("registry.spanel.io", "ghcr.io")
    assert site_apps.validate_image("registry.spanel.io/app:1")
    with pytest.raises(ValueError):
        site_apps.validate_image("n8nio/n8n")


def test_container_port_and_cpu_limits():
    assert site_apps.validate_container_port(None) == 3000
    assert site_apps.validate_container_port("5678") == 5678
    for bad in (0, 70000, "abc"):
        with pytest.raises(ValueError):
            site_apps.validate_container_port(bad)
    assert site_apps.validate_cpu_limit(None) == "1"
    assert site_apps.validate_cpu_limit("0.5") == "0.5"
    for bad in ("0", "-1", "abc", "1.25"):
        with pytest.raises(ValueError):
            site_apps.validate_cpu_limit(bad)


# --- environment ------------------------------------------------------------

def test_environment_is_normalised():
    assert site_apps.validate_env("FOO=bar\n\n# comment\n  BAZ=a=b") == "FOO=bar\nBAZ=a=b"


@pytest.mark.parametrize("env", ["novalue", "lower=1", "1BAD=x", "FOO"])
def test_malformed_environment_is_rejected(env):
    with pytest.raises(ValueError):
        site_apps.validate_env(env)


def test_environment_line_count_is_capped():
    with pytest.raises(ValueError, match="At most"):
        site_apps.validate_env("\n".join(f"K{index}=v" for index in range(site_apps.MAX_ENV_LINES + 1)))


# --- runtime plumbing -------------------------------------------------------

def _managed_app(kind="node", owner_username="siteuser", **overrides):
    owner = User(id=1, username=owner_username, email="s@example.test", role="end_user")
    app = SiteApp(
        owner_id=1,
        name="app",
        kind=kind,
        port=21005,
        memory_limit_mb=512,
        cpu_limit="1",
        container_port=3000,
        env="FOO=bar",
        start_kind="npm",
        start_arg="start",
        node_major="22",
        image="n8nio/n8n:1.60.0",
    )
    app.owner = owner
    app.websites = []
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


def _capture_privileged(monkeypatch, stdout="unit-name"):
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append({"command": helper_command, "args": list(helper_args or []), "input": kwargs.get("input")})
        return type("R", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(site_apps.shell, "privileged", fake_privileged)
    return calls


def test_write_runtime_sends_validated_node_flags(monkeypatch):
    app = _managed_app("node")
    calls = _capture_privileged(monkeypatch)

    site_apps.write_runtime(app)

    assert calls[0]["command"] == "site-app-write"
    args = calls[0]["args"]
    assert args[:3] == ["siteuser", "app", "node"]
    assert "--port=21005" in args
    assert "--exec=npm" in args
    assert "--arg=start" in args
    assert "--node-major=22" in args
    # Environment travels on stdin, never as an argument.
    assert calls[0]["input"].startswith("FOO=bar")
    assert not any(arg.startswith("--env") for arg in args)


def test_write_runtime_sends_validated_docker_flags(monkeypatch):
    app = _managed_app("docker")
    calls = _capture_privileged(monkeypatch)

    site_apps.write_runtime(app)

    args = calls[0]["args"]
    assert args[2] == "docker"
    assert "--image=n8nio/n8n:1.60.0" in args
    assert "--container-port=3000" in args
    assert "--cpus=1" in args


def test_write_runtime_needs_an_owner(monkeypatch):
    app = _managed_app("node")
    app.owner = None
    _capture_privileged(monkeypatch)

    with pytest.raises(ValueError, match="no owner"):
        site_apps.write_runtime(app)


def test_write_runtime_rejects_a_bad_image_before_reaching_root(monkeypatch):
    app = _managed_app("docker", image="evil.example.com/x:1")
    calls = _capture_privileged(monkeypatch)

    with pytest.raises(ValueError):
        site_apps.write_runtime(app)
    assert calls == []


def test_control_only_passes_the_owner_and_app_name(monkeypatch):
    """The unit name is derived by the helper, so no caller can aim at nginx."""
    app = _managed_app("node")
    calls = _capture_privileged(monkeypatch, stdout="active")

    site_apps.control(app, "restart")

    assert calls[0]["command"] == "site-app-control"
    assert calls[0]["args"] == ["siteuser", "app", "restart"]


def test_control_rejects_an_action_outside_the_allowed_set(monkeypatch):
    app = _managed_app("node")
    _capture_privileged(monkeypatch)

    for action in ("mask", "kill", "reload-or-restart", "start; rm -rf /"):
        with pytest.raises(ValueError, match="Unsupported action"):
            site_apps.control(app, action)


def test_is_running_reads_systemd_output(monkeypatch):
    app = _managed_app("node")
    _capture_privileged(monkeypatch, stdout="active\n")
    assert site_apps.is_running(app) is True

    _capture_privileged(monkeypatch, stdout="inactive\n")
    assert site_apps.is_running(app) is False


def test_unit_name_is_scoped_to_the_owner_and_app():
    app = _managed_app("node")

    assert site_apps.unit_name(app) == "spanel-app-siteuser-app"

    other_owner = _managed_app("node", owner_username="tenant2")
    assert site_apps.unit_name(other_owner) == "spanel-app-tenant2-app"

    renamed = _managed_app("node", name="n8n")
    assert site_apps.unit_name(renamed) == "spanel-app-siteuser-n8n"


def test_logs_clamps_the_requested_line_count(monkeypatch):
    app = _managed_app("node")
    calls = _capture_privileged(monkeypatch, stdout="log line")

    site_apps.logs(app, 99999)
    assert calls[0]["args"][-1] == "2000"

    site_apps.logs(app, -5)
    assert calls[1]["args"][-1] == "1"


def test_docker_status_parses_helper_output(monkeypatch):
    monkeypatch.setattr(
        site_apps.shell,
        "privileged",
        lambda *a, **k: type("R", (), {
            "returncode": 0,
            "stdout": "installed=yes\nversion=Docker version 27.1.1\nactive=active\n",
            "stderr": "",
        })(),
    )

    assert site_apps.docker_status() == {
        "installed": True,
        "version": "Docker version 27.1.1",
        "active": "active",
        # `docker system df` output, empty when the helper reports no df= lines.
        "disk": [],
    }


def test_installed_node_majors_ignores_junk(monkeypatch):
    monkeypatch.setattr(
        site_apps.shell,
        "privileged",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "22\n20\nnot-a-version\n", "stderr": ""})(),
    )

    assert site_apps.installed_node_majors() == ["20", "22"]


def test_settled_state_catches_a_crash_loop(monkeypatch):
    """systemd reports a Type=simple unit active the moment it forks."""
    states = iter(["active", "activating"])
    monkeypatch.setattr(site_apps, "active_state", lambda _app: next(states))
    monkeypatch.setattr(site_apps.time, "sleep", lambda _seconds: None)

    assert site_apps.settled_state(_managed_app("node"), checks=2) == "activating"


def test_settled_state_accepts_a_unit_that_stays_up(monkeypatch):
    monkeypatch.setattr(site_apps, "active_state", lambda _app: "active")
    monkeypatch.setattr(site_apps.time, "sleep", lambda _seconds: None)

    assert site_apps.settled_state(_managed_app("node"), checks=4) == "active"


def test_settled_state_returns_early_on_a_dead_unit(monkeypatch):
    seen = []

    def fake_state(_app):
        seen.append(1)
        return "failed"

    monkeypatch.setattr(site_apps, "active_state", fake_state)
    monkeypatch.setattr(site_apps.time, "sleep", lambda _seconds: None)

    assert site_apps.settled_state(_managed_app("node"), checks=4) == "failed"
    assert len(seen) == 1


# --- file manager target ----------------------------------------------------

def test_file_target_exposes_what_the_file_manager_reads():
    app = _managed_app("node", name="n8n")
    target = site_apps.file_target(app)

    assert target.root_path.replace("\\", "/").endswith("/home/siteuser/apps/n8n")
    assert target.linux_user == "siteuser"
    assert target.domain == "app:n8n"
    # No id: a file job keys off target_key, never a bare number that a website
    # could also claim.
    assert target.id is None
    assert target.app_id == app.id


def test_unit_name_can_be_asked_about_a_previous_name():
    """A rename moves the unit; the old one still has to be findable."""
    app = _managed_app("node", name="n8n")

    assert site_apps.unit_name(app) == "spanel-app-siteuser-n8n"
    assert site_apps.unit_name(app, "old-name") == "spanel-app-siteuser-old-name"


def test_delete_runtime_can_target_a_previous_name(monkeypatch):
    app = _managed_app("node", name="n8n")
    calls = _capture_privileged(monkeypatch)

    site_apps.delete_runtime(app, "old-name")

    assert calls[0]["command"] == "site-app-delete"
    assert calls[0]["args"] == ["siteuser", "old-name"]


def test_write_runtime_carries_the_memory_cap(monkeypatch):
    app = _managed_app("node", memory_limit_mb=256)
    calls = _capture_privileged(monkeypatch)

    site_apps.write_runtime(app)

    assert "--memory=256" in calls[0]["args"]


def test_rename_moves_the_application_directory(monkeypatch):
    """The directory is derived from the name, so files must follow a rename."""
    app = _managed_app("node", name="demoapp")
    calls = _capture_privileged(monkeypatch, stdout="/home/siteuser/apps/demoapp")

    site_apps.rename_directory(app, "nodeapp")

    assert calls[0]["command"] == "site-app-rename"
    assert calls[0]["args"] == ["siteuser", "nodeapp", "demoapp"]


def test_rename_is_a_no_op_when_the_name_did_not_change(monkeypatch):
    app = _managed_app("node", name="demoapp")
    calls = _capture_privileged(monkeypatch)

    assert site_apps.rename_directory(app, "demoapp") == ""
    assert site_apps.rename_directory(app, "") == ""
    assert calls == []
