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
