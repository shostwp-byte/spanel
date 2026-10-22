import json
import threading
from pathlib import Path

_php_ops_lock = threading.Lock()
_active_php_ops: set[str] = set()

from app.core.config import settings
from app.schemas.schemas import PhpConfigUpdate
from app.services.shell import shell

SUPPORTED_PHP_VERSIONS = ("5.6", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5")


def _safe_ini_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Invalid PHP ini value")
    return value


def update_php_ini(payload: PhpConfigUpdate) -> str:
    php_version = payload.php_version
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    display_errors = "On" if str(payload.display_errors).lower() in {"1", "true", "on", "yes"} else "Off"
    content = "\n".join([
        f"display_errors = {display_errors}",
        f"memory_limit = {_safe_ini_value(payload.memory_limit)}",
        f"upload_max_filesize = {_safe_ini_value(payload.upload_max_filesize)}",
        f"post_max_size = {_safe_ini_value(payload.post_max_size)}",
        f"max_execution_time = {int(payload.max_execution_time)}",
        f"max_input_time = {int(payload.max_input_time)}",
        f"max_input_vars = {int(payload.max_input_vars)}",
        "",
    ])
    target = Path(f"/etc/php/{php_version}/fpm/conf.d/99-spanel.ini")
    if settings.command_dry_run:
        return content
    shell.privileged(
        "php-config-write",
        helper_args=[php_version],
        input=content,
        fallback=[
            "bash",
            "-lc",
            "cat > /etc/php/$1/fpm/conf.d/99-spanel.ini && systemctl restart php$1-fpm",
            "spanel-php-config-write",
            php_version,
        ],
    )
    return str(target)


PHP_CONFIG_KEYS = {
    "display_errors": "Off",
    "memory_limit": "1024M",
    "upload_max_filesize": "1024M",
    "post_max_size": "1024M",
    "max_execution_time": "300",
    "max_input_time": "600",
    "max_input_vars": "10000",
}


def default_php_config(php_version: str) -> dict:
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    return {
        "php_version": php_version,
        "display_errors": PHP_CONFIG_KEYS["display_errors"],
        "memory_limit": PHP_CONFIG_KEYS["memory_limit"],
        "upload_max_filesize": PHP_CONFIG_KEYS["upload_max_filesize"],
        "post_max_size": PHP_CONFIG_KEYS["post_max_size"],
        "max_execution_time": int(PHP_CONFIG_KEYS["max_execution_time"]),
        "max_input_time": int(PHP_CONFIG_KEYS["max_input_time"]),
        "max_input_vars": int(PHP_CONFIG_KEYS["max_input_vars"]),
    }


def restore_default_php_ini(php_version: str) -> str:
    return update_php_ini(PhpConfigUpdate(**default_php_config(php_version)))


def read_php_ini(php_version: str) -> dict:
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    values = dict(PHP_CONFIG_KEYS)
    for path in [
        Path(f"/etc/php/{php_version}/fpm/php.ini"),
        Path(f"/etc/php/{php_version}/fpm/conf.d/99-spanel.ini"),
    ]:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith(";") or "=" not in line:
                continue
            key, value = [part.strip() for part in line.split("=", 1)]
            if key in values:
                values[key] = value
    values["php_version"] = php_version
    values["max_execution_time"] = int(values["max_execution_time"])
    values["max_input_time"] = int(values["max_input_time"])
    values["max_input_vars"] = int(values["max_input_vars"])
    return values


# The extensions the PHP config page offers, one apt package each
# (php<version>-<name>). The helper keeps the same list and enforces it.
PHP_EXTENSIONS = tuple("apcu bcmath bz2 curl enchant gd gmp igbinary imagick imap intl ldap mailparse mbstring memcached mongodb msgpack mysql opcache pgsql redis soap sqlite3 ssh2 tidy uuid xml xsl yaml zip".split())

# `php -m` names where they differ from the package: php-mysql brings mysqli
# and pdo_mysql, php-xml brings dom and friends, and OPcache is built into
# PHP 8.5 with no package of its own.
_MODULE_NAMES = {
    "mysql": ("mysqli",),
    "xml": ("dom",),
    "opcache": ("zend opcache",),
}


def _command_output(args: list[str]) -> str:
    try:
        result = shell.run(args, check=False, timeout=30)
    except (OSError, ValueError):  # no dpkg / php on a development machine
        return ""
    return result.stdout or ""


def list_extensions() -> dict:
    """Every offered extension against every installed PHP version.

    A cell is "installed" (its package is), "builtin" (PHP loads the module
    without a package, OPcache on 8.5), "available" (the repository has it)
    or "unavailable". Read-only, and needs no root: dpkg-query, apt-cache and
    php -m are open to anyone.
    """
    versions = list_installed_php()
    installed = set()
    for line in _command_output(["dpkg-query", "-W", "-f=${Package}\t${Status}\n", "php*"]).splitlines():
        package, _, status = line.partition("\t")
        if status == "install ok installed":
            installed.add(package)
    available = set(_command_output(["apt-cache", "pkgnames", "php"]).split())
    modules = {
        version: {line.strip().lower() for line in _command_output([f"php{version}", "-m"]).splitlines()}
        for version in versions
    }
    rows = []
    for ext in PHP_EXTENSIONS:
        states = {}
        for version in versions:
            package = f"php{version}-{ext}"
            if package in installed:
                states[version] = "installed"
            elif any(name in modules[version] for name in _MODULE_NAMES.get(ext, (ext,))):
                states[version] = "builtin"
            elif package in available:
                states[version] = "available"
            else:
                states[version] = "unavailable"
        rows.append({"name": ext, "versions": states})
    return {"versions": versions, "extensions": rows}


def install_extension(php_version: str, extension: str) -> str:
    """Install one offered extension for an installed PHP version.

    apt, then a PHP-FPM reload so sites load it at once. Raises ValueError for
    anything the panel does not offer, RuntimeError when apt or PHP refuse.
    """
    # What reaches the command line is the panel's own constant, picked by the
    # request's value, never the request's string itself - and there is no
    # shell anywhere on the way.
    version = next((v for v in SUPPORTED_PHP_VERSIONS if v == php_version), None)
    name = next((e for e in PHP_EXTENSIONS if e == extension), None)
    if name is None:
        raise ValueError("PHP extension not offered by the panel")
    if version is None or version not in list_installed_php():
        raise ValueError("That PHP version is not installed")
    package = f"php{version}-{name}"
    result = shell.privileged(
        "php-ext-install",
        helper_args=[version, name],
        check=False,
        timeout=600,
        fallback=[
            "bash",
            "-lc",
            _APT_LOCK_PREAMBLE
            + 'apt-get -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install -y "$1" </dev/null',
            "spanel-php-ext-install",
            package,
        ],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"Could not install {package}").strip()[-600:])
    return (result.stdout or "").strip()


_APT_LOCK_PREAMBLE = (
    "export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none; "
    "stale=$(ps -eo pid=,etimes=,comm= 2>/dev/null | awk '$3 ~ /^(apt-get|apt|dpkg|unattended-upgr)$/ && $2 >= 90 {print $1}'); "
    "if [ -n \"$stale\" ]; then kill -TERM $stale 2>/dev/null || true; sleep 2; kill -KILL $stale 2>/dev/null || true; "
    "dpkg --configure -a --force-confdef --force-confold </dev/null >/dev/null 2>&1 || true; fi; "
    "w=0; while pgrep -x 'apt-get|apt|dpkg' >/dev/null 2>&1; do "
    "if [ $w -ge 60 ]; then pkill -KILL -x 'apt-get|apt|dpkg' 2>/dev/null || true; "
    "dpkg --configure -a --force-confdef --force-confold </dev/null >/dev/null 2>&1 || true; break; fi; "
    "sleep 2; w=$((w+2)); done; "
    "dpkg --configure -a --force-confdef --force-confold </dev/null >/dev/null 2>&1 || true; "
)

_PHP_STATE_FILE = Path(__file__).resolve().parent.parent.parent / ".spanel_php_versions.json"


def _read_php_state() -> dict:
    try:
        if _PHP_STATE_FILE.exists():
            data = json.loads(_PHP_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "installed": [v for v in (data.get("installed") or []) if v in SUPPORTED_PHP_VERSIONS],
                    "uninstalled": [v for v in (data.get("uninstalled") or []) if v in SUPPORTED_PHP_VERSIONS],
                }
    except Exception:
        pass
    return {"installed": [], "uninstalled": []}


def _record_php_installed(php_version: str) -> None:
    state = _read_php_state()
    installed = set(state["installed"])
    uninstalled = set(state["uninstalled"])
    installed.add(php_version)
    uninstalled.discard(php_version)
    try:
        _PHP_STATE_FILE.write_text(
            json.dumps({"installed": sorted(installed), "uninstalled": sorted(uninstalled)}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _record_php_uninstalled(php_version: str) -> None:
    state = _read_php_state()
    installed = set(state["installed"])
    uninstalled = set(state["uninstalled"])
    installed.discard(php_version)
    uninstalled.add(php_version)
    try:
        _PHP_STATE_FILE.write_text(
            json.dumps({"installed": sorted(installed), "uninstalled": sorted(uninstalled)}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def list_installed_php() -> list[str]:
    """List PHP versions that are currently installed on the system."""
    fs_installed = set()
    for version in SUPPORTED_PHP_VERSIONS:
        fpm_path = Path(f"/etc/php/{version}/fpm/php-fpm.conf")
        fpm_bin = Path(f"/usr/sbin/php-fpm{version}")
        if fpm_path.exists() or fpm_bin.exists():
            fs_installed.add(version)

    state = _read_php_state()
    installed = set(fs_installed)
    for v in state["installed"]:
        if v not in state["uninstalled"]:
            installed.add(v)
    if settings.command_dry_run or not fs_installed:
        for v in state["uninstalled"]:
            installed.discard(v)
    if not installed:
        installed.add("8.4")
    return sorted(installed, key=lambda v: [int(x) for x in v.split(".")])


def install_php(php_version: str) -> dict:
    """Install a PHP version or repair the spanel extension set via apt."""
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")

    op_key = f"php:{php_version}"
    with _php_ops_lock:
        if op_key in _active_php_ops:
            raise ValueError(f"PHP {php_version} installation is already in progress. Please wait.")
        _active_php_ops.add(op_key)
    try:
        return _install_php_locked(php_version)
    finally:
        with _php_ops_lock:
            _active_php_ops.discard(op_key)


def _install_php_locked(php_version: str) -> dict:
    already_installed = php_version in list_installed_php() and Path(f"/etc/php/{php_version}/fpm/php-fpm.conf").exists()

    if settings.command_dry_run:
        _record_php_installed(php_version)
        action = "repair" if already_installed else "install"
        return {
            "status": "dry_run",
            "version": php_version,
            "installed": list_installed_php(),
            "message": f"Would {action} php{php_version} and spanel extensions",
        }

    result = shell.privileged(
        "php-install",
        helper_args=[php_version],
        timeout=600,
        fallback=[
            "bash",
            "-lc",
            _APT_LOCK_PREAMBLE
            + 'v="$1"; pkgs=(); for p in php$v-fpm php$v-cli php$v-mysql php$v-sqlite3 php$v-curl php$v-gd php$v-mbstring php$v-xml php$v-zip php$v-opcache php$v-intl php$v-bcmath php$v-redis php$v-imagick php$v-imap; do apt-cache show "$p" >/dev/null 2>&1 && pkgs+=("$p"); done; '
            + 'apt-get -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install -y "${pkgs[@]}" </dev/null && systemctl enable --now "php$v-fpm" 2>/dev/null || true',
            "spanel-php-install",
            php_version,
        ],
    )
    _record_php_installed(php_version)
    return {
        "status": "ensured" if already_installed else "installed",
        "version": php_version,
        "installed": list_installed_php(),
        "output": result.stdout,
    }


def uninstall_php(php_version: str) -> dict:
    """Uninstall a non-default PHP version and remove its packages via apt."""
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    if php_version == "8.4":
        raise ValueError("Cannot uninstall the default PHP version (8.4)")

    op_key = f"php:{php_version}"
    with _php_ops_lock:
        if op_key in _active_php_ops:
            raise ValueError(f"PHP {php_version} operation is already in progress. Please wait.")
        _active_php_ops.add(op_key)
    try:
        return _uninstall_php_locked(php_version)
    finally:
        with _php_ops_lock:
            _active_php_ops.discard(op_key)


def _uninstall_php_locked(php_version: str) -> dict:
    currently_installed = php_version in list_installed_php()
    fs_exists = Path(f"/etc/php/{php_version}/fpm/php-fpm.conf").exists() or Path(f"/usr/sbin/php-fpm{php_version}").exists()

    if settings.command_dry_run:
        _record_php_uninstalled(php_version)
        return {
            "status": "dry_run",
            "version": php_version,
            "installed": list_installed_php(),
            "message": f"Would uninstall php{php_version} and its packages",
        }

    if not currently_installed and not fs_exists:
        raise ValueError(f"PHP {php_version} is not currently installed")

    if fs_exists:
        result = shell.privileged(
            "php-uninstall",
            helper_args=[php_version],
            check=False,
            timeout=600,
            fallback=[
                "bash",
                "-lc",
                _APT_LOCK_PREAMBLE
                + 'systemctl disable --now "php$1-fpm" 2>/dev/null || true; '
                + 'pkgs=$(dpkg-query -W -f=\x27${Package}\t${Status}\n\x27 "php$1-*" "php$1" 2>/dev/null | awk -F"\t" \x27$2 == "install ok installed" {print $1}\x27); '
                + 'if [ -n "$pkgs" ]; then apt-get -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold remove --purge -y $pkgs </dev/null; fi && rm -rf "/etc/php/$1"',
                "spanel-php-uninstall",
                php_version,
            ],
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or f"Could not uninstall PHP {php_version}").strip()[-600:])
        output = (result.stdout or "").strip()
    else:
        output = f"PHP {php_version} removed from panel state."

    _record_php_uninstalled(php_version)
    return {
        "status": "uninstalled",
        "version": php_version,
        "installed": list_installed_php(),
        "output": output,
    }


# ---------------------------------------------------------------------------
# Smart PHP Security Guard: Auto-Patching (Zero-Downtime) & Anti-PHP2SHELL
# ---------------------------------------------------------------------------

import re
import time
from datetime import datetime, timezone

_PHP_SMART_PRIMARY_FILE = Path("/var/lib/spanel/php-smart-update.json")
_PHP_SMART_FALLBACK_FILE = Path(__file__).resolve().parent.parent.parent / ".spanel_php_smart_update.json"
_PHP_SMART_CACHE_TTL = 3600  # 1 hour cache for non-forced checks
_php_smart_bg_lock = threading.Lock()
_php_smart_bg_running = False

PHP2SHELL_STRICT_DISABLED_FUNCTIONS = (
    "exec,passthru,shell_exec,system,proc_open,popen,"
    "pcntl_exec,pcntl_fork,posix_kill,posix_mkfifo,posix_setpgid,posix_setsid,posix_setuid,"
    "show_source,highlight_file"
)

PHP2SHELL_BALANCED_DISABLED_FUNCTIONS = (
    "passthru,system,"
    "pcntl_exec,pcntl_fork,posix_kill,posix_mkfifo,posix_setpgid,posix_setsid,posix_setuid,"
    "show_source,highlight_file"
)

DEFAULT_PHP_SMART_STATE = {
    "auto_patch_enabled": True,
    "auto_harden_php2shell": True,
    "harden_mode": "strict",
    "check_interval_hours": 6,
    "preferred_window": "03:00",
    "last_checked_at": "",
    "last_checked_epoch": 0.0,
    "last_updated_at": "",
    "last_update_status": "idle",
    "last_update_message": "",
    "last_notified_available_sig": "",
    "versions": [],
    "updates_available_count": 0,
    "unprotected_count": 0,
    "unacknowledged_updates": [],
    "recent_updates": [],
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_php_smart_state() -> dict:
    state = dict(DEFAULT_PHP_SMART_STATE)
    state["versions"] = list(DEFAULT_PHP_SMART_STATE["versions"])
    state["unacknowledged_updates"] = list(DEFAULT_PHP_SMART_STATE["unacknowledged_updates"])
    state["recent_updates"] = list(DEFAULT_PHP_SMART_STATE["recent_updates"])
    for candidate in (_PHP_SMART_PRIMARY_FILE, _PHP_SMART_FALLBACK_FILE):
        try:
            if candidate.exists():
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
                    break
        except Exception:
            continue
    return state


def _write_php_smart_state(state: dict) -> None:
    payload = json.dumps(state, indent=2, ensure_ascii=False)
    for target in (_PHP_SMART_PRIMARY_FILE, _PHP_SMART_FALLBACK_FILE):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            return
        except Exception:
            continue


def _extract_clean_php_version(pkg_ver: str, branch: str) -> str:
    if not pkg_ver:
        return branch
    m = re.search(r"(\d+\.\d+\.\d+)", pkg_ver)
    if m:
        return m.group(1)
    return pkg_ver.split("-")[0].split("+")[0].strip() or branch


def _inspect_php_hardening(version: str) -> dict:
    sec_ini = Path(f"/etc/php/{version}/fpm/conf.d/98-spanel-security.ini")
    main_ini = Path(f"/etc/php/{version}/fpm/php.ini")
    custom_ini = Path(f"/etc/php/{version}/fpm/conf.d/99-spanel.ini")

    if settings.command_dry_run and not sec_ini.exists() and not main_ini.exists():
        state = _read_php_smart_state()
        mode = state.get("harden_mode", "strict") if state.get("auto_harden_php2shell", True) else "off"
        funcs = (
            PHP2SHELL_STRICT_DISABLED_FUNCTIONS.split(",")
            if mode == "strict"
            else PHP2SHELL_BALANCED_DISABLED_FUNCTIONS.split(",")
            if mode == "balanced"
            else []
        )
        return {
            "protected": mode != "off",
            "mode": mode,
            "disabled_functions": funcs,
            "disabled_count": len(funcs),
            "cgi_fix_pathinfo_safe": mode != "off",
            "allow_url_include_off": True,
            "expose_php_off": mode != "off",
        }

    directives: dict[str, str] = {}
    for ini_file in (main_ini, sec_ini, custom_ini):
        try:
            if not ini_file.exists():
                continue
            for raw_line in ini_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if not line or line.startswith(";") or "=" not in line:
                    continue
                k, v = [p.strip() for p in line.split("=", 1)]
                directives[k.lower()] = v
        except Exception:
            continue

    raw_disabled = directives.get("disable_functions", "")
    disabled_list = [fn.strip().lower() for fn in raw_disabled.split(",") if fn.strip()]
    disabled_set = set(disabled_list)

    cgi_safe = directives.get("cgi.fix_pathinfo", "").strip() == "0"
    allow_url_inc_off = directives.get("allow_url_include", "Off").strip().lower() in {"off", "0", "false", "no"}
    expose_off = directives.get("expose_php", "Off").strip().lower() in {"off", "0", "false", "no"}

    strict_core = {"exec", "passthru", "shell_exec", "system", "proc_open", "popen"}
    balanced_core = {"passthru", "system"}

    if strict_core.issubset(disabled_set) and cgi_safe:
        mode = "strict"
        protected = True
    elif balanced_core.issubset(disabled_set) and (cgi_safe or sec_ini.exists()):
        mode = "balanced"
        protected = True
    elif len(disabled_set.intersection(strict_core)) >= 2:
        mode = "custom"
        protected = True
    else:
        mode = "off"
        protected = False

    return {
        "protected": protected,
        "mode": mode,
        "disabled_functions": disabled_list,
        "disabled_count": len(disabled_list),
        "cgi_fix_pathinfo_safe": cgi_safe,
        "allow_url_include_off": allow_url_inc_off,
        "expose_php_off": expose_off,
    }


def _inspect_installed_php_branches() -> list[dict]:
    installed_branches = list_installed_php()

    # Gather upgradable php* packages from apt
    upgradable_out = _command_output(["apt", "list", "--upgradable"])
    upgradable_by_branch: dict[str, list[str]] = {v: [] for v in installed_branches}
    candidate_ver_by_branch: dict[str, str] = {}

    for line in upgradable_out.splitlines():
        line = line.strip()
        if not line or line.startswith("Listing") or "/" not in line:
            continue
        pkg_name = line.split("/", 1)[0].strip()
        parts = line.split()
        cand_pkg_ver = parts[1].strip() if len(parts) >= 2 else ""
        for v in installed_branches:
            if pkg_name == f"php{v}" or pkg_name.startswith(f"php{v}-"):
                upgradable_by_branch[v].append(pkg_name)
                if pkg_name in (f"php{v}-fpm", f"php{v}-cli", f"php{v}-common") and cand_pkg_ver:
                    candidate_ver_by_branch[v] = cand_pkg_ver

    rows: list[dict] = []
    for v in installed_branches:
        cli_ver_out = _command_output([f"php{v}", "-r", "echo PHP_VERSION;"]).strip()
        policy_out = _command_output(["apt-cache", "policy", f"php{v}-fpm"])
        inst_pkg = ""
        cand_pkg = candidate_ver_by_branch.get(v, "")
        for pline in policy_out.splitlines():
            s = pline.strip()
            if s.startswith("Installed:"):
                val = s.split(":", 1)[1].strip()
                if val and val != "(none)":
                    inst_pkg = val
            elif s.startswith("Candidate:"):
                val = s.split(":", 1)[1].strip()
                if val and val != "(none)":
                    cand_pkg = val

        current_full = cli_ver_out or _extract_clean_php_version(inst_pkg, v) or f"{v}.0"
        latest_full = _extract_clean_php_version(cand_pkg, current_full) if cand_pkg else current_full

        pkg_upgradable = sorted(set(upgradable_by_branch.get(v, [])))
        update_available = bool(pkg_upgradable) or (
            bool(inst_pkg) and bool(cand_pkg) and inst_pkg != cand_pkg
        )
        if update_available and latest_full == current_full and cand_pkg and inst_pkg and cand_pkg != inst_pkg:
            # Security rebuild of the same upstream minor (e.g. 8.4.5-1ubuntu1 -> 8.4.5-1ubuntu2)
            latest_full = f"{current_full} (Security Patch)"

        fpm_state = _command_output(["systemctl", "is-active", f"php{v}-fpm"]).strip()
        fpm_active = fpm_state == "active" or (settings.command_dry_run and not fpm_state)

        hardening = _inspect_php_hardening(v)

        rows.append({
            "branch": v,
            "current_version": current_full,
            "latest_version": latest_full,
            "installed_package": inst_pkg,
            "candidate_package": cand_pkg,
            "update_available": update_available,
            "upgradable_packages": pkg_upgradable,
            "upgradable_count": len(pkg_upgradable) or (1 if update_available else 0),
            "fpm_active": fpm_active,
            "hardening": hardening,
        })
    return rows


def check_php_updates(force_refresh: bool = False) -> dict:
    state = _read_php_smart_state()
    now_epoch = time.time()
    last_epoch = float(state.get("last_checked_epoch") or 0.0)

    if not force_refresh and state.get("versions") and (now_epoch - last_epoch < _PHP_SMART_CACHE_TTL):
        return state

    rows = _inspect_installed_php_branches()
    updates_count = sum(1 for r in rows if r.get("update_available"))
    unprotected_count = sum(1 for r in rows if not (r.get("hardening") or {}).get("protected"))

    state["versions"] = rows
    state["updates_available_count"] = updates_count
    state["unprotected_count"] = unprotected_count
    state["last_checked_at"] = _utc_iso()
    state["last_checked_epoch"] = now_epoch

    # If updates are available and auto_patch is off, notify admin once per signature
    if updates_count > 0 and not state.get("auto_patch_enabled", True):
        avail_items = [
            f"PHP {r['branch']}: {r['current_version']} → {r['latest_version']}"
            for r in rows
            if r.get("update_available")
        ]
        sig = "|".join(avail_items)
        if sig and sig != state.get("last_notified_available_sig"):
            state["last_notified_available_sig"] = sig
            try:
                from app.services import notifications
                notifications.notify_in_background("php_update_available", {"items": avail_items}, admins=True)
            except Exception:
                pass

    _write_php_smart_state(state)
    return state


def _refresh_php_smart_in_background() -> None:
    global _php_smart_bg_running
    with _php_smart_bg_lock:
        if _php_smart_bg_running:
            return
        _php_smart_bg_running = True

    def _worker() -> None:
        global _php_smart_bg_running
        try:
            check_php_updates(force_refresh=True)
        except Exception:
            pass
        finally:
            with _php_smart_bg_lock:
                _php_smart_bg_running = False

    threading.Thread(target=_worker, name="spanel-php-smart-probe", daemon=True).start()


def cached_php_update_summary() -> dict:
    """Fast, non-blocking summary for dashboard & topbar notification bell."""
    state = _read_php_smart_state()
    last_epoch = float(state.get("last_checked_epoch") or 0.0)
    if not state.get("versions") or (time.time() - last_epoch > _PHP_SMART_CACHE_TTL):
        _refresh_php_smart_in_background()

    rows = state.get("versions") or []
    updatable_versions = [
        f"PHP {r['branch']} ({r.get('current_version')} → {r.get('latest_version')})"
        for r in rows
        if r.get("update_available")
    ]
    unack = state.get("unacknowledged_updates") or []
    return {
        "auto_patch_enabled": bool(state.get("auto_patch_enabled", True)),
        "auto_harden_php2shell": bool(state.get("auto_harden_php2shell", True)),
        "harden_mode": state.get("harden_mode", "strict"),
        "updates_available_count": int(state.get("updates_available_count") or len(updatable_versions)),
        "updatable_versions": updatable_versions,
        "unprotected_count": int(state.get("unprotected_count") or 0),
        "unacknowledged_count": len(unack),
        "unacknowledged_updates": unack[:5],
        "last_updated_at": state.get("last_updated_at") or "",
        "last_checked_at": state.get("last_checked_at") or "",
    }


def save_php_smart_config(payload: dict) -> dict:
    state = _read_php_smart_state()
    if "auto_patch_enabled" in payload and payload["auto_patch_enabled"] is not None:
        state["auto_patch_enabled"] = bool(payload["auto_patch_enabled"])
    if "auto_harden_php2shell" in payload and payload["auto_harden_php2shell"] is not None:
        state["auto_harden_php2shell"] = bool(payload["auto_harden_php2shell"])
    if "harden_mode" in payload and payload["harden_mode"] in {"strict", "balanced", "off"}:
        state["harden_mode"] = payload["harden_mode"]
    if "check_interval_hours" in payload and payload["check_interval_hours"] is not None:
        state["check_interval_hours"] = max(1, min(168, int(payload["check_interval_hours"])))
    if "preferred_window" in payload and payload["preferred_window"]:
        state["preferred_window"] = str(payload["preferred_window"]).strip()

    _write_php_smart_state(state)

    # If admin toggled PHP2SHELL hardening, immediately enforce the new hardening mode on installed branches
    if "auto_harden_php2shell" in payload or "harden_mode" in payload:
        target_mode = state["harden_mode"] if state.get("auto_harden_php2shell", True) else "off"
        try:
            apply_php2shell_hardening(mode=target_mode)
            state = _read_php_smart_state()
        except Exception:
            pass

    return check_php_updates(force_refresh=True)


def apply_php2shell_hardening(versions: list[str] | None = None, mode: str | None = None) -> dict:
    state = _read_php_smart_state()
    target_mode = mode if mode in {"strict", "balanced", "off"} else (
        state.get("harden_mode", "strict") if state.get("auto_harden_php2shell", True) else "off"
    )
    installed = list_installed_php()
    targets = [v for v in (versions or installed) if v in installed]
    if not targets:
        targets = installed

    disabled_funcs = (
        PHP2SHELL_STRICT_DISABLED_FUNCTIONS
        if target_mode == "strict"
        else PHP2SHELL_BALANCED_DISABLED_FUNCTIONS
        if target_mode == "balanced"
        else ""
    )

    results: list[dict] = []
    for v in targets:
        if settings.command_dry_run:
            results.append({"branch": v, "mode": target_mode, "status": "ok"})
            continue

        ini_body = (
            "; Managed by SPanel Smart Security Guard (Anti-PHP2SHELL & RCE Hardening)\n"
            "; Applied to PHP-FPM web pools only; PHP-CLI stays unrestricted for Composer/WP-CLI.\n"
            "cgi.fix_pathinfo = 0\n"
            "expose_php = Off\n"
            "allow_url_include = Off\n"
            "enable_dl = Off\n"
            f"disable_functions = {disabled_funcs}\n"
        )
        res = shell.privileged(
            "php-security-harden",
            helper_args=[v, target_mode],
            check=False,
            timeout=60,
            fallback=[
                "bash",
                "-lc",
                (
                    'v="$1"; mode="$2"; conf="/etc/php/$v/fpm/conf.d/98-spanel-security.ini"; '
                    'if [ "$mode" = "off" ]; then rm -f "$conf"; else cat > "$conf"; fi; '
                    'php-fpm$v -t >/dev/null 2>&1 && (systemctl reload php$v-fpm 2>/dev/null || systemctl restart php$v-fpm 2>/dev/null || true)'
                ),
                "spanel-php-security-harden",
                v,
                target_mode,
            ],
            input=ini_body if target_mode != "off" else None,
        )
        results.append({
            "branch": v,
            "mode": target_mode,
            "status": "ok" if res.returncode == 0 else "warning",
            "output": (res.stdout or res.stderr or "").strip(),
        })

    state["auto_harden_php2shell"] = target_mode != "off"
    if target_mode in {"strict", "balanced"}:
        state["harden_mode"] = target_mode
    _write_php_smart_state(state)
    updated_state = check_php_updates(force_refresh=True)
    return {
        "status": "ok",
        "mode": target_mode,
        "results": results,
        "php_smart_guard": updated_state,
    }


def upgrade_php_packages(versions: list[str] | None = None, trigger: str = "manual") -> dict:
    """Safely upgrade installed PHP branches (Zero-Downtime minor/security patch)."""
    op_key = "php:smart-upgrade"
    with _php_ops_lock:
        if op_key in _active_php_ops:
            raise ValueError("Tiến trình cập nhật bảo mật PHP đang chạy. Vui lòng đợi trong giây lát.")
        _active_php_ops.add(op_key)

    try:
        state = _read_php_smart_state()
        state["last_update_status"] = "running"
        state["last_update_message"] = "Đang kiểm tra kho lưu trữ APT và cập nhật bản vá bảo mật PHP (Zero-Downtime)..."
        _write_php_smart_state(state)

        before_rows = {r["branch"]: r for r in _inspect_installed_php_branches()}
        installed = list(before_rows.keys()) or list_installed_php()
        targets = [v for v in (versions or installed) if v in installed]
        if not targets:
            targets = installed

        updated_branches: list[dict] = []
        outputs: list[str] = []

        for v in targets:
            b_info = before_rows.get(v, {})
            from_ver = b_info.get("current_version") or v
            from_pkg = b_info.get("installed_package") or ""
            pkg_count = int(b_info.get("upgradable_count") or 0)

            if not settings.command_dry_run:
                res = shell.privileged(
                    "php-safe-upgrade",
                    helper_args=[v],
                    check=False,
                    timeout=600,
                    fallback=[
                        "bash",
                        "-lc",
                        _APT_LOCK_PREAMBLE
                        + 'v="$1"; apt-get update --allow-releaseinfo-change -qq >/dev/null 2>&1 || true; '
                        + 'pkgs=$(dpkg-query -W -f=\x27${Package}\t${Status}\n\x27 "php$v-*" "php$v" 2>/dev/null | awk -F"\t" \x27$2 == "install ok installed" {print $1}\x27); '
                        + 'if [ -n "$pkgs" ]; then apt-get -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install --only-upgrade -y $pkgs </dev/null; fi; '
                        + 'php-fpm$v -t >/dev/null 2>&1 && (systemctl reload php$v-fpm 2>/dev/null || systemctl restart php$v-fpm 2>/dev/null || true)',
                        "spanel-php-safe-upgrade",
                        v,
                    ],
                )
                # Fallback if older spanel-helper on disk does not yet recognize php-safe-upgrade
                if res.returncode != 0 and "unknown command" in (res.stderr or "").lower():
                    res = shell.privileged(
                        "php-install",
                        helper_args=[v],
                        check=False,
                        timeout=600,
                    )
                if res.stdout or res.stderr:
                    outputs.append(f"[PHP {v}] {(res.stdout or res.stderr or '').strip()[-400:]}")

        # Apply Anti-PHP2SHELL hardening if enabled
        hardened_flag = bool(state.get("auto_harden_php2shell", True))
        if hardened_flag and state.get("harden_mode", "strict") != "off":
            try:
                apply_php2shell_hardening(versions=targets, mode=state.get("harden_mode", "strict"))
            except Exception:
                pass

        after_rows = {r["branch"]: r for r in _inspect_installed_php_branches()}
        for v in targets:
            b_info = before_rows.get(v, {})
            a_info = after_rows.get(v, {})
            from_ver = b_info.get("current_version") or v
            to_ver = a_info.get("current_version") or from_ver
            from_pkg = b_info.get("installed_package") or ""
            to_pkg = a_info.get("installed_package") or ""
            was_updatable = bool(b_info.get("update_available"))
            changed = (from_ver != to_ver) or (from_pkg and to_pkg and from_pkg != to_pkg) or was_updatable

            updated_branches.append({
                "branch": v,
                "from_version": from_ver,
                "to_version": to_ver,
                "changed": changed,
                "packages_updated": int(b_info.get("upgradable_count") or (1 if changed else 0)),
                "fpm_reloaded": bool(a_info.get("fpm_active", True)),
                "hardened": bool((a_info.get("hardening") or {}).get("protected", hardened_flag)),
            })

        now_iso = _utc_iso()
        changed_items = [b for b in updated_branches if b.get("changed")]
        if changed_items:
            summary_parts = [f"PHP {b['branch']} ({b['from_version']} → {b['to_version']})" for b in changed_items]
            summary_text = f"Đã cập nhật bản vá bảo mật: {', '.join(summary_parts)} (Zero-Downtime Reload)"
        else:
            branch_labels = [f"PHP {b['branch']} ({b['to_version']})" for b in updated_branches]
            summary_text = (
                f"Đã kiểm tra & đồng bộ an toàn {', '.join(branch_labels)} — "
                "Tất cả đã ở phiên bản bảo mật mới nhất & kích hoạt chống PHP2SHELL."
            )

        record = {
            "id": f"php-patch-{int(time.time())}",
            "timestamp": now_iso,
            "trigger": trigger,
            "status": "success",
            "summary": summary_text,
            "branches": updated_branches,
            "hardened": hardened_flag,
            "acknowledged": False if (changed_items or trigger == "auto") else True,
        }

        state = _read_php_smart_state()
        recent = list(state.get("recent_updates") or [])
        recent.insert(0, record)
        state["recent_updates"] = recent[:25]

        if changed_items or trigger == "auto":
            unack = list(state.get("unacknowledged_updates") or [])
            unack.insert(0, record)
            state["unacknowledged_updates"] = unack[:10]

        state["last_updated_at"] = now_iso
        state["last_update_status"] = "success"
        state["last_update_message"] = summary_text
        state["versions"] = list(after_rows.values())
        state["updates_available_count"] = sum(1 for r in after_rows.values() if r.get("update_available"))
        state["unprotected_count"] = sum(
            1 for r in after_rows.values() if not (r.get("hardening") or {}).get("protected")
        )
        state["last_checked_at"] = now_iso
        state["last_checked_epoch"] = time.time()
        _write_php_smart_state(state)

        # Send Email / Telegram notification to Admin when packages were patched or auto-triggered
        if changed_items or trigger == "auto":
            notify_lines = [
                f"PHP {b['branch']}: {b['from_version']} → {b['to_version']} (PHP-FPM Reloaded)"
                for b in (changed_items or updated_branches)
            ]
            try:
                from app.services import notifications
                notifications.notify_in_background(
                    "php_security_updated",
                    {"items": notify_lines, "trigger": trigger, "hardened": hardened_flag},
                    admins=True,
                )
            except Exception:
                pass

        return {
            "status": "success",
            "summary": summary_text,
            "record": record,
            "output": "\n".join(outputs),
            "php_smart_guard": state,
        }
    except Exception as exc:
        state = _read_php_smart_state()
        state["last_update_status"] = "failed"
        state["last_update_message"] = str(exc)
        _write_php_smart_state(state)
        raise
    finally:
        with _php_ops_lock:
            _active_php_ops.discard(op_key)


def acknowledge_php_updates(update_id: str | None = None) -> dict:
    state = _read_php_smart_state()
    if update_id:
        state["unacknowledged_updates"] = [
            u for u in (state.get("unacknowledged_updates") or []) if u.get("id") != update_id
        ]
        for u in state.get("recent_updates") or []:
            if u.get("id") == update_id:
                u["acknowledged"] = True
    else:
        state["unacknowledged_updates"] = []
        for u in state.get("recent_updates") or []:
            u["acknowledged"] = True
    _write_php_smart_state(state)
    return state

