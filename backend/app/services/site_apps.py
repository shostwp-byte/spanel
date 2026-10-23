"""Applications the panel installs, runs and keeps alive.

An app belongs to a panel user, not to a website. It lives in its own directory
under the owner's home, listens on its own loopback port, and runs as a systemd
unit — either a Node process or a container. A website set to "application" mode
then points at one and nginx proxies the domain to that port.

Port allocation is the part that has to be right. A port handed out twice means
two domains silently answering for each other, so the DB holds the unique
constraint and the kernel gets the final say through `ss` before a port is used.
"""

import hashlib
import hmac
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import SiteApp, User, Website
from app.services import site_users
from app.services.shell import shell

# Deliberately above the common framework defaults (3000, 8080) so a customer
# experimenting by hand does not collide with an allocated slot.
PORT_RANGE_START = 21000
PORT_RANGE_END = 21999

APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}[a-z0-9]$|^[a-z0-9]$")
APP_KINDS = {"node", "python", "docker", "compose"}
# Every app is run by the panel now; the old routing-only kind is gone.
MANAGED_KINDS = APP_KINDS
# Apps live side by side here, one directory each, reachable over the customer's
# own SFTP because that is chrooted to their home.
APPS_DIR_NAME = "apps"
NODE_START_KINDS = {"node", "npm", "npx", "yarn"}
PYTHON_START_KINDS = {"uvicorn", "gunicorn", "python"}
START_KINDS = NODE_START_KINDS | PYTHON_START_KINDS
NODE_MAJOR_RE = re.compile(r"^(1[0-9]|[2-9][0-9])$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,159}(:[A-Za-z0-9._-]{1,127})?(@sha256:[a-f0-9]{64})?$")
CPU_LIMIT_RE = re.compile(r"^[0-9]{1,2}(\.[0-9])?$")
ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Registries a customer may pull from. Anything else has to go through an admin,
# because "pull whatever you like" on a shared host is a supply-chain decision,
# not a convenience setting.
DEFAULT_ALLOWED_REGISTRIES = ("docker.io", "ghcr.io", "quay.io", "registry.k8s.io", "public.ecr.aws")
MAX_ENV_LINES = 64
MAX_ENV_VALUE = 4096
CONTROL_ACTIONS = {"start", "stop", "restart", "status", "is-active", "is-enabled", "enable", "disable"}
DEFAULT_MEMORY_MB = 512
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 16384


def validate_name(name: str) -> str:
    value = (name or "").strip().lower()
    if not APP_NAME_RE.fullmatch(value):
        raise ValueError("App name may only use lowercase letters, digits, hyphen and underscore")
    return value


def validate_kind(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value not in APP_KINDS:
        raise ValueError(f"Unsupported app kind. Allowed: {', '.join(sorted(APP_KINDS))}")
    return value


def validate_port(port: int | str) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be a number") from exc
    if not PORT_RANGE_START <= value <= PORT_RANGE_END:
        raise ValueError(f"Port must be between {PORT_RANGE_START} and {PORT_RANGE_END}")
    return value


def app_directory(owner_linux_user: str, name: str) -> Path:
    """Where an app's files live. Derived, never supplied by the caller."""
    safe_user = site_users.validate_linux_user(owner_linux_user)
    return site_users.HOME_ROOT / safe_user / APPS_DIR_NAME / validate_name(name)


def validate_memory_mb(memory_mb: int | str | None, ceiling: int | None = None) -> int:
    if memory_mb in (None, ""):
        # Fall back to the package ceiling when it is below our own default,
        # otherwise a package allowing less than 512 MB refuses every app the
        # customer creates — including ones that asked for no limit at all.
        value = min(DEFAULT_MEMORY_MB, ceiling) if ceiling else DEFAULT_MEMORY_MB
    else:
        try:
            value = int(memory_mb)
        except (TypeError, ValueError) as exc:
            raise ValueError("Memory limit must be a number of MB") from exc
    if not MIN_MEMORY_MB <= value <= MAX_MEMORY_MB:
        raise ValueError(f"Memory limit must be between {MIN_MEMORY_MB} and {MAX_MEMORY_MB} MB")
    if ceiling and value > ceiling:
        raise ValueError(f"This package allows at most {ceiling} MB per app")
    return value


def validate_node_major(node_major: str | None) -> str | None:
    if node_major in (None, ""):
        return None
    value = str(node_major).strip()
    if not NODE_MAJOR_RE.fullmatch(value):
        raise ValueError("Node version must be a major version number, for example 20 or 22")
    return value


def validate_start(start_kind: str | None, start_arg: str | None) -> tuple[str, str]:
    """Structured start command: a known launcher plus one argument.

    Never a free-form shell string — this ends up inside a systemd unit written
    by root, so the launcher is picked from a fixed set and the argument is
    checked for shell metacharacters and path escapes.
    """
    kind = (start_kind or "").strip().lower()
    if kind not in START_KINDS:
        raise ValueError(f"Start command must be one of: {', '.join(sorted(START_KINDS))}")
    arg = (start_arg or "").strip()
    if not arg:
        raise ValueError("Start command needs an argument, for example a script name or entry file")
    if any(char in arg for char in " \t\r\n\x00'\"\\;&|<>$`()*?[]{}!#~"):
        raise ValueError("Start argument may not contain spaces or shell characters")

    if kind == "node":
        if not arg.endswith(".js") and not arg.endswith(".mjs") and not arg.endswith(".cjs"):
            raise ValueError("node must be given a .js, .mjs or .cjs entry file")
        # The entry file is resolved inside the app directory by the helper, so
        # anything that climbs out has to be refused here.
        base = Path("/app").resolve(strict=False)
        target = (base / arg).resolve(strict=False)
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError("Entry file must be inside the app directory") from exc
    elif kind == "python":
        if not arg.endswith(".py"):
            raise ValueError("python must be given a .py entry file")
        base = Path("/app").resolve(strict=False)
        target = (base / arg).resolve(strict=False)
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError("Entry file must be inside the app directory") from exc
    elif kind in {"uvicorn", "gunicorn"}:
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", arg) or ".." in arg:
            raise ValueError("WSGI/ASGI module target must be like main:app or wsgi:application")
    elif not re.fullmatch(r"[A-Za-z0-9._@/-]{1,120}", arg):
        raise ValueError("Script or package name contains characters that are not allowed")
    return kind, arg


def listening_ports() -> set[int]:
    """Ports the kernel currently has a TCP listener on.

    Best effort: if `ss` is missing the DB constraint still prevents handing the
    same port to two apps, we just lose the check against unmanaged processes.
    """
    result = shell.run(["ss", "-ltnH"], check=False)
    if result.returncode != 0:
        return set()
    ports: set[int] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        _, _, port = local.rpartition(":")
        if port.isdigit():
            ports.add(int(port))
    return ports


def reserved_ports(db: Session, exclude_app_id: int | None = None) -> set[int]:
    query = select(SiteApp.port)
    if exclude_app_id is not None:
        query = query.where(SiteApp.id != exclude_app_id)
    return {row for row in db.scalars(query) if row}


def allocate_port(db: Session, preferred: int | None = None, exclude_app_id: int | None = None) -> int:
    taken = reserved_ports(db, exclude_app_id) | listening_ports()
    if preferred is not None:
        candidate = validate_port(preferred)
        if candidate in taken:
            raise ValueError(f"Port {candidate} is already in use")
        return candidate
    for candidate in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if candidate not in taken:
            return candidate
    raise ValueError("No free application port left on this server")


def app_limit_for(user: User) -> int:
    package = getattr(user, "package", None)
    if not package:
        return 0
    raw = getattr(package, "node_apps_limit", None)
    return int(0 if raw is None else raw)


def memory_ceiling_for(user: User) -> int:
    package = getattr(user, "package", None)
    ceiling = int(getattr(package, "node_app_memory_mb", 0) or 0) if package else 0
    return ceiling or DEFAULT_MEMORY_MB


def count_apps_for_owner(db: Session, owner_id: int) -> int:
    return db.query(SiteApp).filter(SiteApp.owner_id == owner_id).count()


def ensure_app_quota(db: Session, user: User, is_admin: bool = False) -> None:
    if is_admin:
        return
    limit = app_limit_for(user)
    if limit == 0:
        raise ValueError("Application hosting is not enabled for this package")
    if limit > 0 and count_apps_for_owner(db, user.id) >= limit:
        raise ValueError(f"This package allows at most {limit} application(s)")


def app_port_for_website(website: Website) -> Optional[int]:
    app = getattr(website, "app", None)
    return app.port if app else None


# --- container and environment validation -----------------------------------

def allowed_registries() -> tuple[str, ...]:
    raw = os.environ.get("spanel_ALLOWED_REGISTRIES", "")
    if not raw.strip():
        return DEFAULT_ALLOWED_REGISTRIES
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def validate_image(image: str, enforce_registry: bool = True) -> str:
    """Validate a container image reference and where it comes from."""
    value = (image or "").strip()
    if not value:
        raise ValueError("Container image is required")
    if not IMAGE_RE.fullmatch(value) or value.startswith("-") or ".." in value:
        raise ValueError("Container image reference contains characters that are not allowed")
    if not enforce_registry:
        return value
    # The first path component is a registry host only when something follows it
    # and it looks like a host. Without that check "nginx:1.27" reads as the
    # registry "nginx" with port "1.27", and a plain Docker Hub image is refused.
    head = value.split("/", 1)[0]
    is_registry = "/" in value and ("." in head or ":" in head or head == "localhost")
    registry = head if is_registry else "docker.io"
    if registry not in allowed_registries():
        raise ValueError(f"Images from {registry} are not allowed. Allowed: {', '.join(allowed_registries())}")
    return value


def validate_container_port(port: int | str | None) -> int:
    if port in (None, ""):
        return 3000
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Container port must be a number") from exc
    if not 1 <= value <= 65535:
        raise ValueError("Container port is out of range")
    return value


def validate_cpu_limit(cpu_limit: str | float | None) -> str:
    if cpu_limit in (None, ""):
        return "1"
    value = str(cpu_limit).strip()
    if not CPU_LIMIT_RE.fullmatch(value) or float(value) <= 0:
        raise ValueError("CPU limit must be a number such as 0.5, 1 or 2")
    return value


def validate_env(env: str | None) -> str:
    """Normalise KEY=value lines for the app's EnvironmentFile.

    The helper re-validates every line before root writes it; this is the copy
    that produces a readable error for the person typing it.
    """
    lines: list[str] = []
    for raw in (env or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Environment line is missing '=': {line[:40]}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"Environment name must be UPPER_SNAKE_CASE: {key[:40]}")
        if any(char in value for char in "\r\n\x00"):
            raise ValueError(f"Environment value for {key} contains a line break")
        if len(value) > MAX_ENV_VALUE:
            raise ValueError(f"Environment value for {key} is too long")
        lines.append(f"{key}={value}")
    if len(lines) > MAX_ENV_LINES:
        raise ValueError(f"At most {MAX_ENV_LINES} environment variables are supported")
    return "\n".join(lines)


# --- runtime lifecycle ------------------------------------------------------

def owner_linux_user(app: SiteApp) -> str:
    """The Linux account an app runs as: its owner's panel account."""
    owner = getattr(app, "owner", None)
    username = getattr(owner, "username", "") if owner else ""
    if not username:
        raise ValueError("This application has no owner, so it cannot be run")
    return site_users.validate_linux_user(site_users.linux_user_for_panel_username(username))


def unit_name(app: SiteApp, name: str | None = None) -> str:
    """Mirror of the helper's own derivation, for display only.

    Control commands pass the owner and the app name and let the helper build the
    unit name, so a caller can never aim systemctl at a unit it does not own.
    Pass *name* to ask about a name the app used to have, which is how a rename
    finds the unit it has to tear down.
    """
    return f"spanel-app-{owner_linux_user(app)}-{validate_name(name or app.name)}"


def directory_for(app: SiteApp) -> Path:
    return app_directory(owner_linux_user(app), app.name)


class AppFileTarget:
    """Adapter letting the file manager browse an app like it browses a site.

    file_manager only ever reads root_path and linux_user off its target, so an
    app can be handed to it directly instead of duplicating the whole module.
    """

    def __init__(self, app: SiteApp):
        # No `id`: a file job keys off target_key so an app can never be mistaken
        # for the website that happens to share its row id.
        self.id = None
        self.app_id = app.id
        self.name = app.name
        self.linux_user = owner_linux_user(app)
        self.root_path = str(directory_for(app))
        # file_manager logs against a label; an app has no domain of its own.
        self.domain = f"app:{app.name}"
        # The storage quota is charged to whoever owns the app.
        self.owner = app.owner
        self.owner_id = app.owner_id


def file_target(app: SiteApp) -> AppFileTarget:
    return AppFileTarget(app)


def ensure_directory(app: SiteApp) -> str:
    """Create the app's directory now, not at first deploy.

    The customer has to upload code before there is anything to deploy, so the
    directory has to exist as soon as the app does. Also repairs ownership on
    directories made by an earlier release, which the panel user could not read.
    """
    result = shell.privileged(
        "site-app-dir-ensure",
        helper_args=[owner_linux_user(app), validate_name(app.name)],
        check=False,
        fallback=["bash", "-lc", "true"],
    )
    return (result.stdout or "").strip()


def rename_directory(app: SiteApp, previous_name: str) -> str:
    """Carry an app's files across a rename.

    The directory is derived from the name, so without this the customer's code
    stays behind in the old path and the renamed app starts against nothing.
    """
    if not previous_name or previous_name == app.name:
        return ""
    result = shell.privileged(
        "site-app-rename",
        helper_args=[owner_linux_user(app), validate_name(previous_name), validate_name(app.name)],
        check=False,
        fallback=["bash", "-lc", "true"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not move the application directory").strip())
    return (result.stdout or "").strip()


def owner_ids(app: SiteApp) -> tuple[int, int]:
    """uid and gid of the account an app runs as."""
    import pwd

    try:
        record = pwd.getpwnam(owner_linux_user(app))
        return record.pw_uid, record.pw_gid
    except (KeyError, ImportError, AttributeError):
        # Development machines have no such account; the helper resolves the
        # real ids again before anything runs.
        return 0, 0


def compose_variables(app: SiteApp) -> dict[str, str]:
    """What a compose file's ${...} references resolve to for this app."""
    from app.services import compose  # local import keeps the module cycle open

    public_url, domain = public_address(app)
    values = compose.read_variables(app.env or "")
    # The panel's own two names win: they describe where the app is reachable,
    # which is the panel's business, not the customer's to redefine.
    if public_url:
        values["spanel_URL"] = public_url
        values["spanel_DOMAIN"] = domain
    return values


def compose_plan(app: SiteApp):
    """Re-read the customer's compose file into the panel's own model."""
    from app.services import compose  # local import keeps the module cycle open

    return compose.analyse(app.compose_source or "", app.web_service or "",
                           variables=compose_variables(app),
                           web_port=app.container_port or None)


def public_address(app: SiteApp) -> tuple[str, str]:
    """The URL visitors reach an app on, and the bare domain.

    An app only ever sees a loopback port, so anything it has to print or hand
    back to a browser — an OAuth callback, a webhook — has to be told the address
    the website answers on. Empty when no website points here yet.
    """
    site = next(iter(app.websites or []), None)
    if not site:
        return "", ""
    scheme = "https" if getattr(site, "ssl_enabled", False) else "http"
    return f"{scheme}://{site.domain}", site.domain


def compose_project(app: SiteApp) -> str:
    return f"spanel-{owner_linux_user(app)}-{validate_name(app.name)}"


def render_compose(app: SiteApp) -> str:
    """Build the compose file the server runs, from the imported one.

    Regenerated on every deploy rather than stored, so a port move or a new
    memory cap lands without the customer importing their file again.
    """
    from app.services import compose  # local import keeps the module cycle open

    plan = compose_plan(app)
    if not plan.ok:
        raise ValueError("; ".join(issue.message for issue in plan.issues) or "Compose file is not usable")
    uid, gid = owner_ids(app)
    return compose.render(
        plan,
        project=compose_project(app),
        published_port=validate_port(app.port),
        uid=uid,
        gid=gid,
        memory_mb=validate_memory_mb(app.memory_limit_mb),
        cpus=validate_cpu_limit(app.cpu_limit),
    )


def compose_pull(app: SiteApp) -> str:
    """Fetch every image the compose file names, before the unit starts."""
    result = shell.privileged(
        "site-app-compose-pull",
        helper_args=[owner_linux_user(app), validate_name(app.name)],
        check=False,
        timeout=1800,
        fallback=["bash", "-lc", "echo dry-run-compose-pull"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "docker compose pull failed").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]


def fetch_images(app: SiteApp) -> str:
    """Download whatever the app needs before it is asked to run.

    Both unit kinds pull implicitly when they start, but that happens with the
    old containers already gone: the site is down for as long as the download
    takes, and nothing reports why. Pulling first keeps what is running now
    serving, and a tag that does not exist is reported without stopping it.
    """
    if app.kind == "compose":
        return compose_pull(app)
    if app.kind == "docker":
        return pull_image(app)
    return ""


def write_runtime(app: SiteApp) -> str:
    """Generate and reload the systemd unit for an app."""
    linux_user = owner_linux_user(app)
    helper_args = [
        linux_user,
        validate_name(app.name),
        validate_kind(app.kind),
        f"--port={validate_port(app.port)}",
        f"--memory={validate_memory_mb(app.memory_limit_mb)}",
        f"--cpus={validate_cpu_limit(app.cpu_limit)}",
    ]
    if app.kind == "node":
        start_kind, start_arg = validate_start(app.start_kind, app.start_arg)
        helper_args += [
            f"--node-major={validate_node_major(app.node_major) or '22'}",
            f"--exec={start_kind}",
            f"--arg={start_arg}",
        ]
    elif app.kind == "python":
        start_kind, start_arg = validate_start(app.start_kind or "uvicorn", app.start_arg or "main:app")
        helper_args += [
            f"--exec={start_kind}",
            f"--arg={start_arg}",
        ]
    elif app.kind == "docker":
        helper_args += [
            f"--image={validate_image(app.image)}",
            f"--container-port={validate_container_port(app.container_port)}",
        ]
    # A compose app carries its environment inside the generated file, so that
    # is what goes on stdin instead of an environment file.
    payload = render_compose(app) if app.kind == "compose" else validate_env(app.env) + "\n"
    result = shell.privileged(
        "site-app-write",
        helper_args=helper_args,
        input=payload,
        fallback=["bash", "-lc", "cat >/dev/null && echo dry-run-unit"],
    )
    return (result.stdout or "").strip()


def control(app: SiteApp, action: str) -> str:
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"Unsupported action. Allowed: {', '.join(sorted(CONTROL_ACTIONS))}")
    result = shell.privileged(
        "site-app-control",
        helper_args=[owner_linux_user(app), validate_name(app.name), action],
        check=False,
        fallback=["bash", "-lc", "echo dry-run"],
    )
    return ((result.stdout or "") + (result.stderr or "")).strip()


def active_state(app: SiteApp) -> str:
    try:
        return control(app, "is-active").splitlines()[0].strip()
    except (RuntimeError, ValueError, IndexError):
        return "unknown"


def is_running(app: SiteApp) -> bool:
    return active_state(app) == "active"


def compose_service_states(app: SiteApp) -> Optional[list[dict]]:
    """What each container in a compose app is actually doing.

    The unit only says whether `docker compose up` is still attached, and it is:
    a container that crash-loops leaves the unit active, so without asking Docker
    itself the panel would report a dead application as running.
    """
    result = shell.privileged(
        "site-app-compose-ps",
        helper_args=[owner_linux_user(app), validate_name(app.name)],
        check=False,
        timeout=90,
        fallback=["bash", "-lc", "echo ''"],
    )
    if result.returncode != 0:
        # Docker could not be asked. Saying nothing is wrong is better than
        # reporting an outage the panel has no evidence for.
        return None
    states: list[dict] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries = json.loads(line)
        except json.JSONDecodeError:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            if isinstance(entry, dict) and entry.get("service"):
                states.append(entry)
    return states


# A container Docker has restarted this many times, and which has been up for
# less than a moment, is going round in a loop rather than having had a bad day.
CRASH_LOOP_RESTARTS = 3
CRASH_LOOP_UPTIME_SECONDS = 180


def _container_uptime(started: str) -> float:
    """Seconds since a container last started, or a large number if unreadable."""
    from datetime import datetime, timezone

    text = (started or "").replace("Z", "+00:00")
    if "." in text:  # Docker prints nanoseconds; datetime stops at microseconds
        head, _, tail = text.partition(".")
        fraction, sign, offset = tail.partition("+")
        text = f"{head}.{fraction[:6]}{sign}{offset}" if sign else f"{head}.{fraction[:6]}"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return float("inf")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def compose_trouble(app: SiteApp) -> str:
    """A sentence naming the container that is not up, or an empty string."""
    states = compose_service_states(app)
    if states is None:
        return ""
    if not states:
        # `docker compose up` reaches this state while it downloads an image it
        # was never given beforehand: unit active, not one container to show.
        return "no container is running yet (an image may still be downloading)"

    problems: list[str] = []
    for entry in sorted(states, key=lambda item: str(item.get("service"))):
        name = str(entry.get("service"))
        state = str(entry.get("state") or "unknown")
        restarts = int(entry.get("restarts") or 0)
        if state != "running":
            problems.append(f"container '{name}' is {state}")
        elif restarts >= CRASH_LOOP_RESTARTS and _container_uptime(entry.get("started", "")) < CRASH_LOOP_UPTIME_SECONDS:
            # Up right now, but only because Docker just restarted it again.
            reason = " (out of memory)" if entry.get("oom") else f" (last exit code {entry.get('exit')})"
            problems.append(f"container '{name}' keeps restarting — {restarts} times so far{reason}")
    return "; ".join(problems)


def settled_state(app: SiteApp, checks: int = 4, delay: float = 1.5) -> str:
    """The unit's state once it has had a moment to fall over.

    systemd calls a Type=simple unit active the instant it forks, so an app that
    dies on startup still reads "active" for a second and a deploy would report
    success on a crash loop. Sampling a few times catches the restart.
    """
    state = active_state(app)
    for _ in range(max(0, checks - 1)):
        if state != "active":
            return state
        time.sleep(delay)
        state = active_state(app)
    return state


def logs(app: SiteApp, lines: int = 200) -> str:
    safe_lines = max(1, min(2000, int(lines or 200)))
    result = shell.privileged(
        "site-app-logs",
        helper_args=[owner_linux_user(app), validate_name(app.name), str(safe_lines)],
        check=False,
        fallback=["bash", "-lc", "echo 'no journal in development'"],
    )
    return (result.stdout or "") or (result.stderr or "")


def delete_runtime(app: SiteApp, name: str | None = None) -> None:
    """Remove an app's unit, container and environment file.

    *name* lets a rename tear down the unit the app used to run under; its files
    are never touched either way.
    """
    try:
        linux_user = owner_linux_user(app)
    except ValueError:
        return
    shell.privileged(
        "site-app-delete",
        helper_args=[linux_user, validate_name(name or app.name)],
        check=False,
        fallback=["bash", "-lc", "true"],
    )


def install_dependencies(app: SiteApp) -> str:
    if app.kind == "python":
        result = shell.privileged(
            "site-app-python-deps",
            helper_args=[
                owner_linux_user(app),
                validate_name(app.name),
            ],
            check=False,
            timeout=960,
            fallback=["bash", "-lc", "echo dry-run-python-install"],
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "pip install failed").strip()[-4000:])
        return (result.stdout or "").strip()[-4000:]
    if app.kind != "node":
        raise ValueError("Only Node.js and Python applications install dependencies")
    result = shell.privileged(
        "site-app-install-deps",
        helper_args=[
            owner_linux_user(app),
            validate_name(app.name),
            validate_node_major(app.node_major) or "22",
        ],
        check=False,
        timeout=960,
        fallback=["bash", "-lc", "echo dry-run-install"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "npm install failed").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]


def webhook_token_for(app: SiteApp) -> str:
    """Deterministic HMAC-SHA256 webhook token for 1-Click Git Auto-Deploy."""
    secret = (settings.secret_key or "spanel-default-secret").encode("utf-8")
    msg = f"site-app-webhook:{app.id}:{app.owner_id}:{app.name}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:32]


def verify_webhook_token(app: SiteApp, token: str) -> bool:
    expected = webhook_token_for(app)
    provided = (token or "").strip()
    return bool(provided) and hmac.compare_digest(expected, provided)


def git_auto_deploy(app: SiteApp) -> dict:
    """Pull latest git commit in the application directory, install dependencies, and restart unit."""
    linux_user = owner_linux_user(app)
    safe_name = validate_name(app.name)
    pull_res = shell.privileged(
        "site-app-git-pull",
        helper_args=[linux_user, safe_name],
        check=False,
        timeout=300,
        fallback=["bash", "-lc", "echo 'Already up to date.'"],
    )
    git_output = ((pull_res.stdout or "") + "\n" + (pull_res.stderr or "")).strip()[-2000:]
    deps_output = ""
    try:
        if app.kind in {"node", "python"}:
            deps_output = install_dependencies(app)
        elif app.kind in {"docker", "compose"}:
            deps_output = fetch_images(app)
    except Exception as exc:
        deps_output = str(exc)

    write_runtime(app)
    if app.autostart:
        control(app, "enable")
    control(app, "restart")
    state = settled_state(app, checks=2, delay=0.5)
    return {
        "ok": state == "active" or settings.command_dry_run,
        "status": "running" if (state == "active" or settings.command_dry_run) else "error",
        "git_output": git_output,
        "deps_output": deps_output[:1000],
    }


def pull_image(app: SiteApp) -> str:
    if app.kind != "docker":
        raise ValueError("Only container applications pull an image")
    result = shell.privileged(
        "site-app-pull",
        helper_args=[validate_image(app.image)],
        check=False,
        timeout=960,
        fallback=["bash", "-lc", "echo dry-run-pull"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "docker pull failed").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]


# --- server side runtime availability ---------------------------------------

def docker_status() -> dict:
    result = shell.privileged("docker-status", check=False, fallback=["bash", "-lc", "echo installed=no"])
    parsed = {}
    disk = []
    for line in (result.stdout or "").splitlines():
        if line.startswith("df="):
            # Type|Size|Reclaimable, straight from `docker system df`.
            parts = line[3:].split("|")
            if len(parts) == 3:
                disk.append({"type": parts[0], "size": parts[1], "reclaimable": parts[2]})
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip()
    return {
        "installed": parsed.get("installed") == "yes",
        "version": parsed.get("version", ""),
        "active": parsed.get("active", ""),
        # Server-wide, not per customer: one image serves every tenant using it.
        "disk": disk,
    }


def export_payload(app: SiteApp, destination: str) -> int:
    """Write everything an application owns to *destination*, and say how big.

    Its directory and its volumes are both out of the panel's reach — parts of
    the directory belong to container users, the volumes live under /var/lib/
    docker — so this is the only way a backup can contain them.
    """
    result = shell.privileged(
        "site-app-export",
        helper_args=[owner_linux_user(app), validate_name(app.name), str(destination)],
        check=False,
        timeout=3600,
        fallback=["bash", "-lc", f"printf '' > {shlex.quote(str(destination))}; echo 0"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not export the application").strip()[-4000:])
    text = (result.stdout or "0").strip().splitlines()
    return int(text[-1]) if text and text[-1].isdigit() else 0


def import_payload(app: SiteApp, source: str) -> str:
    """Put a backed-up application's directory and volumes back."""
    result = shell.privileged(
        "site-app-import",
        helper_args=[owner_linux_user(app), validate_name(app.name), str(source)],
        check=False,
        timeout=3600,
        fallback=["bash", "-lc", "echo dry-run-app-import"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not restore the application").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]


def prune_docker() -> str:
    """Reclaim dead layers and build cache. Nothing in use is removed."""
    result = shell.privileged(
        "docker-prune",
        check=False,
        timeout=900,
        fallback=["bash", "-lc", "echo dry-run-docker-prune"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Docker prune failed").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]


def install_docker() -> str:
    result = shell.privileged(
        "docker-install",
        check=False,
        timeout=1800,
        fallback=["bash", "-lc", "echo dry-run-docker-install"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Docker install failed").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]


def installed_node_majors() -> list[str]:
    result = shell.privileged("node-list", check=False, fallback=["bash", "-lc", "true"])
    majors = [line.strip() for line in (result.stdout or "").splitlines() if NODE_MAJOR_RE.fullmatch(line.strip())]
    return sorted(majors, key=int)


def install_node(major: str) -> str:
    safe_major = validate_node_major(major)
    if not safe_major:
        raise ValueError("Node version is required")
    result = shell.privileged(
        "node-install",
        helper_args=[safe_major],
        check=False,
        timeout=900,
        fallback=["bash", "-lc", "echo dry-run-node-install"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Node install failed").strip()[-4000:])
    return (result.stdout or "").strip()[-4000:]
