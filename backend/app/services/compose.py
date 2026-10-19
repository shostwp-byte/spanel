"""Import a customer's docker-compose file onto the panel's own model.

The file a customer pastes is never the file that runs. It is parsed, checked
against an allowlist, and a fresh compose file is generated from what survived.
That direction matters: a blocklist would have to keep up with every key Compose
adds, and one missed key (`privileged`, `network_mode`, a host bind mount) undoes
every guardrail around the container. Anything this module does not recognise is
refused by name so the customer can see exactly what to change.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

from app.services import site_apps

# Keys the panel understands. Everything else is refused, including keys that
# have not been invented yet.
ALLOWED_TOP_LEVEL = {"services", "volumes", "version", "name"}
ALLOWED_SERVICE_KEYS = {
    "image", "environment", "command", "entrypoint", "depends_on",
    "ports", "expose", "volumes", "user", "working_dir", "hostname",
    "healthcheck", "labels", "restart", "stop_grace_period",
    "tty", "stdin_open", "container_name",
    # Accepted and then overridden by the panel's own limits.
    "mem_limit", "memswap_limit", "cpus", "pids_limit",
}
# Refused with a specific explanation rather than the generic message, because
# these are the ones people actually reach for.
EXPLAINED_SERVICE_KEYS = {
    "privileged": "runs the container privileged",
    "network_mode": "sets network_mode (host networking skips the firewall guard)",
    "cap_add": "adds a capability",
    "devices": "attaches a host device",
    "pid": "shares the host PID namespace",
    "ipc": "shares the IPC namespace",
    "userns_mode": "changes the user namespace",
    "security_opt": "changes security options",
    "sysctls": "sets sysctls",
    "build": "builds an image here; the panel only runs prebuilt images",
    "extra_hosts": "overrides host name resolution",
    "volumes_from": "borrows another container's volumes",
    "cgroup_parent": "changes the parent cgroup",
    "group_add": "adds supplementary groups",
    "env_file": "reads variables from a file; paste them into Environment instead",
    "networks": "declares its own networks; the panel creates one per app",
    "deploy": "declares swarm deploy settings",
}
SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
# An application behind the panel's proxy never sees its own public address:
# it listens on a loopback port nobody types. These stand in for the domain the
# website serves it on, so a value like WEBHOOK_URL can be written once and
# still be right after the domain changes.
PLACEHOLDERS = {"spanel_URL", "spanel_DOMAIN"}
# Compose's own interpolation, because a project's compose file is written
# against a .env sitting next to it and would mean something different here
# otherwise: ${VAR}, ${VAR:-default}, ${VAR:?message}, $VAR, and $$ for a
# literal dollar.
VARIABLE_RE = re.compile(
    r"\$\$"
    r"|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}"
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"
)
VOLUME_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_SERVICES = 8
MAX_SOURCE_BYTES = 64 * 1024
# The minimum an image needs to drop from root to its own account at startup —
# what the official Postgres and MySQL entrypoints do through gosu. Without
# these the container cannot initialise its data directory.
PRIVILEGE_DROP_CAPS = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]


@dataclass
class Issue:
    service: str
    message: str

    def as_dict(self) -> dict:
        return {"service": self.service, "message": self.message}


@dataclass
class Service:
    name: str
    image: str
    environment: dict[str, str] = field(default_factory=dict)
    command: Any = None
    entrypoint: Any = None
    depends_on: list[str] = field(default_factory=list)
    container_port: int | None = None          # the one the domain reaches
    container_ports: list[int] = field(default_factory=list)   # every one declared
    named_volumes: list[str] = field(default_factory=list)   # "data:/var/lib/x"
    bind_mounts: list[str] = field(default_factory=list)      # "./src:/app/src"
    user: str | None = None
    working_dir: str | None = None
    healthcheck: dict | None = None
    memory_mb: int | None = None

    @property
    def touches_app_files(self) -> bool:
        return bool(self.bind_mounts)


@dataclass
class Plan:
    services: list[Service] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    web_service: str = ""
    issues: list[Issue] = field(default_factory=list)
    # Things worth saying that are not reasons to refuse the file.
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and bool(self.services)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "web_service": self.web_service,
            "issues": [issue.as_dict() for issue in self.issues],
            "notes": list(self.notes),
            "services": [
                {
                    "name": service.name,
                    "image": service.image,
                    "container_port": service.container_port,
                    "container_ports": service.container_ports,
                    "volumes": service.named_volumes + service.bind_mounts,
                    "environment": sorted(service.environment),
                    "web": service.name == self.web_service,
                }
                for service in self.services
            ],
            "volumes": self.volumes,
        }


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_environment(raw: Any, service: str, issues: list[Issue]) -> dict[str, str]:
    entries: dict[str, str] = {}
    if raw is None:
        return entries
    items: list[tuple[str, Any]] = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        for line in raw:
            text = str(line)
            if "=" not in text:
                # `- FOO` inherits from the host environment, which for us is the
                # panel's own process. Never pass that through.
                issues.append(Issue(service, f"environment variable {text[:40]} has no value"))
                continue
            key, value = text.split("=", 1)
            items.append((key, value))
    else:
        issues.append(Issue(service, "environment must be a list or a mapping"))
        return entries

    for key, value in items:
        name = str(key).strip()
        if not ENV_KEY_RE.fullmatch(name):
            issues.append(Issue(service, f"not a valid environment variable name: {name[:40]}"))
            continue
        text = "" if value is None else str(value)
        if any(char in text for char in "\r\n\x00"):
            issues.append(Issue(service, f"the value of {name} contains a newline"))
            continue
        entries[name] = text
    return entries


def _parse_port(raw: Any, service: str, issues: list[Issue]) -> int | None:
    """Read the port the process listens on inside the container.

    Host publishing from the file is ignored: the panel decides the loopback port
    so two customers cannot claim the same one.
    """
    if isinstance(raw, dict):
        target = raw.get("target")
    else:
        text = str(raw).split("/", 1)[0]          # drop /tcp
        parts = text.split(":")
        target = parts[-1]
    try:
        port = int(str(target))
    except (TypeError, ValueError):
        issues.append(Issue(service, f"could not read the port: {str(raw)[:40]}"))
        return None
    if not 1 <= port <= 65535:
        issues.append(Issue(service, f"port out of range: {port}"))
        return None
    return port


def _parse_volume(raw: Any, service: str, declared: set[str], issues: list[Issue]) -> tuple[str, str] | None:
    """Returns (kind, spec) where kind is 'named' or 'bind'."""
    if isinstance(raw, dict):
        kind = raw.get("type", "volume")
        source = str(raw.get("source", ""))
        target = str(raw.get("target", ""))
        read_only = bool(raw.get("read_only"))
        if not source or not target:
            issues.append(Issue(service, "volume is missing its source or target"))
            return None
        spec = f"{source}:{target}" + (":ro" if read_only else "")
        if kind == "bind":
            return _parse_volume(spec, service, declared, issues)
        if kind != "volume":
            issues.append(Issue(service, f"unsupported volume type: {kind}"))
            return None
        return _parse_volume(spec, service, declared, issues)

    text = str(raw)
    parts = text.split(":")
    if len(parts) < 2:
        issues.append(Issue(service, f"a volume must read source:target - {text[:50]}"))
        return None
    source, target = parts[0], parts[1]
    suffix = f":{parts[2]}" if len(parts) > 2 and parts[2] in {"ro", "rw"} else ""
    if not target.startswith("/"):
        issues.append(Issue(service, f"a volume target must be an absolute path - {text[:50]}"))
        return None

    if source.startswith("/"):
        issues.append(Issue(
            service,
            f"mounting a host path is not allowed - {text[:50]}; "
            "use a path inside the app directory (./data) or a named volume",
        ))
        return None
    if source.startswith(".") or "/" in source:
        # A path relative to the project, which is the application directory.
        relative = PurePosixPath(source)
        if any(part == ".." for part in relative.parts):
            issues.append(Issue(service, f"this volume points outside the app directory - {text[:50]}"))
            return None
        cleaned = "/".join(part for part in relative.parts if part not in (".", ""))
        return "bind", f"./{cleaned}:{target}{suffix}" if cleaned else f".:{target}{suffix}"

    if not VOLUME_NAME_RE.fullmatch(source):
        issues.append(Issue(service, f"not a valid volume name: {source[:40]}"))
        return None
    declared.add(source)
    return "named", f"{source}:{target}{suffix}"


def home_for(service: "Service") -> tuple[str, bool]:
    """Where a service run under a forced uid should keep its home.

    Overriding `user` with a numeric id leaves the container with no matching
    passwd entry, so HOME falls back to `/` and the first thing the process
    writes there fails. Point it at the directory the customer mounted, which is
    the one place inside the container they own.

    A mount at `/home/node/.n8n` is the app's own dot-directory inside a home
    the image owns, so the home is its parent — and that parent belongs to the
    image's account, not ours. Returns True alongside it to say the home itself
    needs to be made writable; the mount underneath it stays persistent either
    way, because Docker mounts the deeper path last.
    """
    target = service.bind_mounts[0].split(":")[1]
    path = PurePosixPath(target)
    if path.name.startswith("."):
        return str(path.parent), True
    return str(path), False


def parse_memory(value: Any) -> int | None:
    """Read a compose memory value — 512m, 1g, or a plain byte count — as MB."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    units = {"k": 1 / 1024, "m": 1, "g": 1024, "b": 1 / (1024 * 1024)}
    factor = units.get(text[-1], None)
    number = text[:-1] if factor is not None else text
    try:
        megabytes = float(number) * (factor if factor is not None else 1 / (1024 * 1024))
    except ValueError:
        return None
    return max(1, int(megabytes))


def _parse_service(name: str, raw: Any, declared_volumes: set[str], issues: list[Issue],
                   enforce_registry: bool = True) -> Service | None:
    if not SERVICE_NAME_RE.fullmatch(name):
        issues.append(Issue(name, "a service name may only use lower-case letters, digits, hyphens and underscores"))
        return None
    if not isinstance(raw, dict):
        issues.append(Issue(name, "a service must be a mapping"))
        return None

    for key in raw:
        if key in ALLOWED_SERVICE_KEYS:
            continue
        reason = EXPLAINED_SERVICE_KEYS.get(str(key))
        if reason:
            issues.append(Issue(name, f"{key}: {reason}"))
        else:
            issues.append(Issue(name, f"unsupported key: {key}"))

    image = str(raw.get("image", "") or "").strip()
    if not image:
        issues.append(Issue(name, "image is missing"))
        return None
    try:
        image = site_apps.validate_image(image, enforce_registry=enforce_registry)
    except ValueError as exc:
        issues.append(Issue(name, str(exc)))
        return None

    service = Service(name=name, image=image)
    service.memory_mb = parse_memory(raw.get("mem_limit"))
    service.environment = _parse_environment(raw.get("environment"), name, issues)
    service.command = raw.get("command")
    service.entrypoint = raw.get("entrypoint")
    service.working_dir = raw.get("working_dir")
    healthcheck = raw.get("healthcheck")
    service.healthcheck = healthcheck if isinstance(healthcheck, dict) else None

    depends = raw.get("depends_on")
    if isinstance(depends, dict):
        service.depends_on = [str(key) for key in depends]
    else:
        service.depends_on = [str(item) for item in _as_list(depends)]

    declared = _as_list(raw.get("ports")) or _as_list(raw.get("expose"))
    for entry in declared:
        port = _parse_port(entry, name, issues)
        if port and port not in service.container_ports:
            service.container_ports.append(port)
    # Which one the domain reaches is decided later, once the web service is
    # known; until then the first is the sensible guess.
    service.container_port = service.container_ports[0] if service.container_ports else None

    for entry in _as_list(raw.get("volumes")):
        parsed = _parse_volume(entry, name, declared_volumes, issues)
        if not parsed:
            continue
        kind, spec = parsed
        (service.named_volumes if kind == "named" else service.bind_mounts).append(spec)

    user = raw.get("user")
    if user is not None:
        text = str(user).strip()
        if text in {"root", "0", "0:0"} or text.startswith("0:"):
            issues.append(Issue(name, "a service may not run as root"))
        elif not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", text):
            issues.append(Issue(name, f"not a valid user value: {text[:30]}"))
        else:
            service.user = text
    return service


def analyse(source: str, web_service: str = "", enforce_registry: bool = True,
            variables: dict[str, str] | None = None, web_port: int | None = None) -> Plan:
    """Read a customer's compose file and report what the panel can run."""
    plan = Plan()
    if not (source or "").strip():
        plan.issues.append(Issue("", "no docker-compose content yet"))
        return plan
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        plan.issues.append(Issue("", "the compose file is too large"))
        return plan
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        plan.issues.append(Issue("", f"invalid YAML: {str(exc).splitlines()[0][:120]}"))
        return plan
    if not isinstance(document, dict):
        plan.issues.append(Issue("", "the content must be a YAML mapping"))
        return plan

    missing: dict[str, str] = {}
    document = _interpolate_tree(document, dict(variables or {}), missing)
    for name, note in sorted(missing.items()):
        if name in PLACEHOLDERS:
            plan.issues.append(Issue(
                "",
                f"${{{name}}} only exists once the app is attached to a website; "
                "point a website at this app first",
            ))
        else:
            plan.issues.append(Issue(
                "",
                f"{name} is not set - add {name}=... in the .env box" + (f" ({note})" if note else ""),
            ))
    if missing:
        return plan

    for key in document:
        name = str(key)
        if name in ALLOWED_TOP_LEVEL or name.startswith("x-"):
            continue
        reason = EXPLAINED_SERVICE_KEYS.get(name)
        plan.issues.append(Issue("", f"{name}: {reason}" if reason else f"unsupported key: {name}"))

    raw_services = document.get("services")
    if not isinstance(raw_services, dict) or not raw_services:
        plan.issues.append(Issue("", "no services found"))
        return plan
    if len(raw_services) > MAX_SERVICES:
        plan.issues.append(Issue("", f"at most {MAX_SERVICES} services per app"))
        return plan

    declared: set[str] = set()
    for name, raw in raw_services.items():
        service = _parse_service(str(name), raw, declared, plan.issues, enforce_registry)
        if service:
            plan.services.append(service)

    declared_top = document.get("volumes")
    if isinstance(declared_top, dict):
        for name, options in declared_top.items():
            if not VOLUME_NAME_RE.fullmatch(str(name)):
                plan.issues.append(Issue("", f"not a valid volume name: {str(name)[:40]}"))
                continue
            if isinstance(options, dict) and options.get("driver_opts"):
                # driver_opts with o=bind is a host mount wearing a disguise.
                plan.issues.append(Issue("", f"volume {name}: driver_opts is not allowed"))
                continue
            declared.add(str(name))
    plan.volumes = sorted(declared)

    plan.web_service = _pick_web_service(plan, web_service)
    if plan.web_service:
        _pick_web_port(plan, web_port)
    return plan


def _pick_web_port(plan: Plan, requested: int | None) -> None:
    """Decide which of a service's ports the domain reaches.

    A service may listen on several — an API and a console, an app and its
    metrics — and only one can be behind the domain. Picking silently, as this
    used to, leaves someone staring at the wrong screen; the rest stay reachable
    between the containers, which is where a metrics or admin port belongs.
    """
    service = next(item for item in plan.services if item.name == plan.web_service)
    if requested:
        if requested not in service.container_ports:
            plan.issues.append(Issue(
                service.name,
                f"this service does not expose port {requested}; it exposes: "
                + ", ".join(str(port) for port in service.container_ports),
            ))
            return
        service.container_port = requested
    others = [port for port in service.container_ports if port != service.container_port]
    if others:
        plan.notes.append(
            f"{service.name} exposes {len(service.container_ports)} ports; the domain goes to port "
            f"{service.container_port}, and {', '.join(str(port) for port in others)} are "
            "only used between containers."
        )
    for other in plan.services:
        if other.name != plan.web_service and other.container_ports:
            plan.notes.append(
                f"{other.name} exposes port {', '.join(str(port) for port in other.container_ports)}"
                " - internal only, never published."
            )


def _pick_web_service(plan: Plan, requested: str) -> str:
    names = [service.name for service in plan.services]
    if requested:
        if requested not in names:
            plan.issues.append(Issue("", f"no service called {requested}"))
            return ""
        chosen = requested
    else:
        with_ports = [service for service in plan.services if service.container_port]
        if len(with_ports) == 1:
            chosen = with_ports[0].name
        elif with_ports:
            plan.issues.append(Issue("", "several services expose ports; choose which one serves the domain"))
            return ""
        elif plan.services:
            plan.issues.append(Issue("", "no service exposes a port; choose the service that serves the domain and its port"))
            return ""
        else:
            return ""
    service = next(item for item in plan.services if item.name == chosen)
    if not service.container_port:
        plan.issues.append(Issue(chosen, "this service exposes no listening port"))
        return ""
    return chosen


def read_variables(env_text: str) -> dict[str, str]:
    """Read the KEY=value box the way Compose reads a .env file."""
    values: dict[str, str] = {}
    for raw in (env_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if ENV_KEY_RE.fullmatch(key):
            values[key] = value
    return values


def interpolate(text: str, values: dict[str, str], missing: dict[str, str]) -> str:
    """Substitute a compose file's variables from the .env box.

    A project's compose file is written against the .env beside it, so the panel
    has to resolve the same references or the pasted file quietly means something
    else here. Anything with no value is collected in *missing* rather than
    silently becoming an empty string, which is what Compose itself would do.
    """
    def replace(match: re.Match) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(4)
        operator, argument = match.group(2), match.group(3)
        if name in values and (values[name] or operator not in (":-", ":?")):
            return values[name]
        if operator in ("-", ":-"):
            return argument or ""
        if operator in ("?", ":?"):
            missing.setdefault(name, argument or "")
            return ""
        missing.setdefault(name, "")
        return ""

    return VARIABLE_RE.sub(replace, text)


def _interpolate_tree(node, values: dict[str, str], missing: dict[str, str]):
    if isinstance(node, str):
        return interpolate(node, values, missing)
    if isinstance(node, list):
        return [_interpolate_tree(item, values, missing) for item in node]
    if isinstance(node, dict):
        return {key: _interpolate_tree(value, values, missing) for key, value in node.items()}
    return node


def _escape_tree(node):
    """Stop Compose interpolating the file the panel generated.

    Every reference has already been resolved by this point, so a dollar left in
    a value is part of the value — a password, a shell command — and has to reach
    the container as one.
    """
    if isinstance(node, str):
        return node.replace("$", "$$")
    if isinstance(node, list):
        return [_escape_tree(item) for item in node]
    if isinstance(node, dict):
        return {key: _escape_tree(value) for key, value in node.items()}
    return node


def render(
    plan: Plan,
    *,
    project: str,
    published_port: int,
    uid: int,
    gid: int,
    memory_mb: int,
    cpus: str,
) -> str:
    """Build the compose file the server actually runs."""
    if not plan.ok:
        raise ValueError("Cannot build the compose file while issues remain")

    services: dict[str, Any] = {}
    for service in plan.services:
        entry: dict[str, Any] = {"image": service.image}
        if service.environment:
            entry["environment"] = dict(service.environment)
        if service.command is not None:
            entry["command"] = service.command
        if service.entrypoint is not None:
            entry["entrypoint"] = service.entrypoint
        if service.depends_on:
            entry["depends_on"] = service.depends_on
        if service.working_dir:
            entry["working_dir"] = service.working_dir
        if service.healthcheck:
            entry["healthcheck"] = service.healthcheck

        volumes = service.named_volumes + service.bind_mounts
        if volumes:
            entry["volumes"] = volumes

        if service.name == plan.web_service:
            entry["ports"] = [f"127.0.0.1:{published_port}:{service.container_port}"]

        if service.user:
            entry["user"] = service.user
        elif service.touches_app_files:
            # It writes into the customer's own directory, so it has to write as
            # the customer or the files come out owned by someone else.
            entry["user"] = f"{uid}:{gid}"
            if "HOME" not in service.environment:
                home, ephemeral = home_for(service)
                entry.setdefault("environment", {})["HOME"] = home
                if ephemeral:
                    # A scratch home, so caches and lockfiles the process expects
                    # to write beside its data have somewhere to go. Nothing that
                    # has to survive a restart belongs here — that is what the
                    # customer's mount underneath it is for.
                    entry.setdefault("volumes", []).insert(0, {
                        "type": "tmpfs",
                        "target": home,
                        "tmpfs": {"size": 64 * 1024 * 1024, "mode": 0o1777},
                        # Compose reads mode as a number, and its own docs
                        # write 01777 for it; without one the mount lands
                        # 0755 root-owned and we are back where we started.
                    })

        entry["cap_drop"] = ["ALL"]
        if not entry.get("user"):
            # Images that start as root and drop to their own account — every
            # official database does — need these to set up their data directory.
            entry["cap_add"] = list(PRIVILEGE_DROP_CAPS)
        entry["security_opt"] = ["no-new-privileges:true"]
        if service.container_port and service.container_port < 1024:
            # Nothing in the container holds CAP_NET_BIND_SERVICE once the
            # capabilities are dropped, and an image told to listen on 80 would
            # just fail. The setting is scoped to this container's own network
            # namespace, where a low port means nothing.
            entry["sysctls"] = {"net.ipv4.ip_unprivileged_port_start": 0}
        # The application's cap is a ceiling per service, not a budget shared
        # between them. A file that asks for less keeps its own number, so a
        # database beside a heavy web service does not have to be given the same
        # room just to let the web service have enough.
        limit = min(service.memory_mb, memory_mb) if service.memory_mb else memory_mb
        entry["mem_limit"] = f"{limit}m"
        entry["memswap_limit"] = f"{limit}m"
        entry["cpus"] = float(cpus)
        entry["pids_limit"] = 256
        entry["restart"] = "unless-stopped"
        entry["logging"] = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
        services[service.name] = entry

    document: dict[str, Any] = {"name": project, "services": _escape_tree(services)}
    if plan.volumes:
        document["volumes"] = {name: None for name in plan.volumes}

    header = (
        "# Generated by spanel from the imported docker-compose file.\n"
        "# Edits here are overwritten on the next import.\n"
    )
    return header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
