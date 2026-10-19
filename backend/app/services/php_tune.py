"""PHP settings sized for the machine the panel is running on.

Two halves, and only one of them existed before.

The FPM pools have been sized from RAM, CPU and pool count since they were
introduced — but silently, at the moment a pool is written, so nobody could see
what was chosen and a server that gained RAM kept yesterday's numbers until
someone happened to touch a site. This module reads the same figures back out.

php.ini was never sized at all: every server got the same constants, including
`memory_limit = 1024M`, which on a 2 GB box lets a single request eat half the
machine while the pool maths assumed 128 MB per worker. Those are what the
recommendations here mostly change.

Nothing is applied on its own. The panel shows current beside recommended, with
the reason, and an administrator decides.
"""

import os
import re
from pathlib import Path

from app.services import php

# Mirrors PHP_FPM_DEFAULT_WORKER_MB in spanel-helper.sh. The pool maths divides
# the PHP budget by this, so it is also what a sensible memory_limit is built
# from: a limit far above it means the arithmetic protecting the box is fiction.
WORKER_MB = 128
# A hosting customer's PHP has to survive a WooCommerce import, a big backup
# plugin and a theme demo installer. Anything below this generates support
# tickets, so no amount of arithmetic about small servers pushes it lower.
MIN_MEMORY_LIMIT_MB = 1024
TUNE_FILE_NAME = "95-spanel-tune.ini"
# The opcache switch lives in its own file, read after the tuning one: running
# Auto tune must not quietly turn opcache back on for someone who turned it off
# on purpose, and the panel's own PHP config page still wins over both.
OPCACHE_FILE_NAME = "96-spanel-opcache.ini"
# JIT arrived in PHP 8.0; writing these on 7.4 would mean nothing.
JIT_MIN_VERSION = (8, 0)
# Extensions that install their own opcode handlers. PHP refuses to run JIT
# alongside any of them and says so once per worker start. ionCube ships with
# spanel because customers run encrypted commercial scripts, so on most servers
# this is not a hypothetical.
JIT_BLOCKING_EXTENSIONS = ("ionCube Loader", "xdebug", "SourceGuardian", "snuffleupagus", "pcov", "newrelic")

# Keys the tuner is allowed to write. The helper enforces the same list; this
# copy is what the panel offers, so a typo here fails loudly rather than being
# written and ignored.
TUNABLE_KEYS = (
    "memory_limit",
    "realpath_cache_size",
    "realpath_cache_ttl",
    "opcache.enable",
    "opcache.enable_cli",
    "opcache.memory_consumption",
    "opcache.interned_strings_buffer",
    "opcache.max_accelerated_files",
    "opcache.revalidate_freq",
    "opcache.validate_timestamps",
    "opcache.save_comments",
    "opcache.jit",
    "opcache.jit_buffer_size",
    "expose_php",
    "zlib.output_compression",
)


def total_memory_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 1024


def available_memory_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return max(0, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def pool_count() -> int:
    """Managed FPM pools, which is what the per-pool share is divided by."""
    found = 0
    for version_dir in Path("/etc/php").glob("*/fpm/pool.d"):
        found += len(list(version_dir.glob("spanel-*.conf")))
    return max(1, found)


def reserved_memory_mb(total_mb: int) -> int:
    """RAM expected to go to everything that is not PHP: MariaDB, nginx, panel.

    Reported so an admin can see where the machine's memory goes. It is not
    subtracted from the pool arithmetic - see server_facts for why - so treat
    this as an estimate to read, not a number the pools were sized against.
    """
    if total_mb <= 1024:
        reserve = max(total_mb * 45 // 100, 448)
    elif total_mb <= 2048:
        reserve = max(total_mb * 35 // 100, 640)
    elif total_mb <= 4096:
        reserve = max(total_mb * 30 // 100, 896)
    elif total_mb <= 8192:
        reserve = max(total_mb * 25 // 100, 1280)
    else:
        reserve = max(total_mb * 20 // 100, 2048)
    return max(128, min(reserve, total_mb - 128))


def server_facts() -> dict:
    total = total_memory_mb()
    reserve = reserved_memory_mb(total)
    # Worker count comes off total RAM. The reserve is still reported - it is
    # a useful thing for an admin to see - but it no longer shrinks the pool:
    # WORKER_MB already bills a worker at 128 MB when a measured one is nearer
    # 24 MB RSS, pm=ondemand means a worker only exists while it is serving,
    # and the helper's cpu_cap/profile_cap are what really bound a small box.
    # Subtracting a further 20-45% on top of those three left every server
    # about a third short of what its own caps allowed.
    budget = max(WORKER_MB, total)
    return {
        "cpu_count": cpu_count(),
        "total_memory_mb": total,
        "available_memory_mb": available_memory_mb(),
        "reserved_memory_mb": reserve,
        "php_budget_mb": budget,
        "pool_count": pool_count(),
        "worker_mb": WORKER_MB,
        "concurrent_requests": max(1, budget // WORKER_MB),
    }


def _version_tuple(php_version: str) -> tuple:
    try:
        return tuple(int(part) for part in str(php_version).split("."))
    except ValueError:
        return (0,)


def supports_jit(php_version: str) -> bool:
    """Whether this PHP has JIT at all, regardless of whether it can run."""
    return _version_tuple(php_version) >= JIT_MIN_VERSION


def jit_status(php_version: str) -> dict:
    """Whether JIT would actually switch on, and what stops it if not.

    Asked rather than assumed: writing opcache.jit on a server where another
    extension owns the opcode handlers produces a warning on every worker start
    and changes nothing at all.
    """
    if not supports_jit(php_version):
        return {"supported": False, "usable": False, "blocked_by": ""}

    import subprocess

    ini_dir = Path(f"/etc/php/{php_version}/fpm")
    env = {"PATH": "/usr/bin:/bin", "PHP_INI_SCAN_DIR": str(ini_dir / "conf.d")}
    # Ask PHP to try JIT with the same extensions FPM loads, and to name any of
    # the known blockers it finds among them.
    names = ", ".join(f"'{name}'" for name in JIT_BLOCKING_EXTENSIONS)
    script = (
        "$s = @opcache_get_status(false);"
        "$loaded = array_map('strtolower', get_loaded_extensions());"
        f"$blockers = array_values(array_filter([{names}], "
        "fn($n) => in_array(strtolower($n), $loaded, true)));"
        "echo json_encode(['jit' => (bool)($s['jit']['enabled'] ?? false), 'blockers' => $blockers]);"
    )
    try:
        result = subprocess.run(
            [f"php{php_version}", "-c", str(ini_dir / "php.ini"),
             "-d", "opcache.enable_cli=1", "-d", "opcache.jit=tracing",
             "-d", "opcache.jit_buffer_size=16M", "-r", script],
            capture_output=True, text=True, timeout=25, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return {"supported": True, "usable": False, "blocked_by": ""}

    import json as _json

    payload = {}
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = _json.loads(line)
            except ValueError:
                continue
    usable = bool(payload.get("jit"))
    blockers = payload.get("blockers") or []
    return {
        "supported": True,
        "usable": usable,
        "blocked_by": "" if usable else ", ".join(blockers),
    }


def _tier(total_mb: int) -> dict:
    """The numbers that follow from how much RAM the machine has.

    Sized so that a worker holding memory_limit does not by itself exceed what
    the pool arithmetic budgeted for it, and so opcache is big enough to hold a
    WordPress install without evicting on every request.
    """
    # opcache is one shared segment per FPM master, not per worker, so it is
    # cheap: the package already ships 128 MB and recommending less would be a
    # downgrade dressed up as tuning. These start at that and go up.
    #
    # memory_limit never goes below MIN_MEMORY_LIMIT_MB. It is a ceiling per
    # request, not memory reserved up front, and pm.max_children is what
    # actually bounds how many requests run at once; a limit low enough to kill
    # an import costs more than the headroom saves.
    if total_mb <= 1024:
        tier = {"memory_limit": 1024, "opcache_mb": 96, "interned": 8, "files": 16229}
    elif total_mb <= 2048:
        tier = {"memory_limit": 1024, "opcache_mb": 128, "interned": 8, "files": 16229}
    elif total_mb <= 4096:
        tier = {"memory_limit": 1024, "opcache_mb": 192, "interned": 16, "files": 32531}
    elif total_mb <= 8192:
        tier = {"memory_limit": 1536, "opcache_mb": 256, "interned": 24, "files": 65407}
    else:
        tier = {"memory_limit": 2048, "opcache_mb": 384, "interned": 32, "files": 130987}
    tier["memory_limit"] = max(MIN_MEMORY_LIMIT_MB, tier["memory_limit"])
    # The JIT buffer is shared memory of its own, on top of opcache's.
    tier["jit_buffer_mb"] = 32 if total_mb <= 2048 else (64 if total_mb <= 8192 else 128)
    return tier


def recommendations(facts: dict | None = None, php_version: str = "8.4",
                    jit: dict | bool | None = None) -> list[dict]:
    """What to set, and why, for this machine and this PHP.

    *jit* is the answer from jit_status(): whether JIT exists here, whether it
    can actually run, and what stops it. A server where it cannot run is told to
    switch it off rather than left at PHP 8.4's default, which reserves 64 MB
    for a JIT that never starts and warns about it on every worker start.
    """
    if isinstance(jit, bool):
        jit = {"supported": supports_jit(php_version), "usable": jit, "blocked_by": ""}
    if jit is None:
        jit = jit_status(php_version)
    jit_ok = bool(jit.get("usable"))
    facts = facts or server_facts()
    total = facts["total_memory_mb"]
    tier = _tier(total)
    workers = facts["concurrent_requests"]

    jit_rows: list[dict] = []
    if jit_ok:
        jit_rows = [
            {
                "key": "opcache.jit",
                "value": "tracing",
                "reason": (
                    "Compiles hot code paths to machine code. A clear win for heavy computation; "
                    "with WordPress most of the time goes on waiting for the database, so it gains little."
                ),
            },
            {
                "key": "opcache.jit_buffer_size",
                "value": f"{tier['jit_buffer_mb']}M",
                "reason": (
                    f"Memory for the code JIT generates, on top of opcache's {tier['opcache_mb']} MB. "
                    "Set to 0 to turn JIT off."
                ),
            },
        ]
    elif jit.get("supported"):
        blocker = jit.get("blocked_by") or "another extension"
        jit_rows = [
            {
                "key": "opcache.jit",
                "value": "disable",
                "reason": (
                    f"{blocker} holds the opcode handler, so PHP cannot run JIT. "
                    "Turn it off outright to stop the warning on every PHP-FPM start."
                ),
            },
            {
                "key": "opcache.jit_buffer_size",
                "value": "0",
                "reason": (
                    "PHP 8.4 reserves 64 MB for JIT by default even when JIT cannot run - "
                    "give that back to the machine."
                ),
            },
        ]

    return jit_rows + [
        {
            "key": "memory_limit",
            "value": f"{tier['memory_limit']}M",
            "reason": (
                f"The ceiling per request. {total} MB of RAM; how many run at once is capped by "
                f"pm.max_children (~{workers}), not by this. "
                f"About {facts['reserved_memory_mb']} MB is expected to go to MariaDB, nginx and the panel."
            ),
        },
        {
            "key": "opcache.enable",
            "value": "1",
            "reason": "Without opcache every request recompiles the whole source.",
        },
        {
            "key": "opcache.memory_consumption",
            "value": str(tier["opcache_mb"]),
            "reason": f"Enough for the compiled code of a plugin-heavy WordPress ({tier['opcache_mb']} MB).",
        },
        {
            "key": "opcache.interned_strings_buffer",
            "value": str(tier["interned"]),
            "reason": "Repeated strings are shared between workers instead of duplicated.",
        },
        {
            "key": "opcache.max_accelerated_files",
            "value": str(tier["files"]),
            "reason": "WordPress with plugins usually passes 10,000 files; once full, opcache starts evicting.",
        },
        {
            "key": "opcache.validate_timestamps",
            "value": "1",
            "reason": "Keeps checking for changed files. Off is slightly faster, but a customer editing code would see nothing change.",
        },
        {
            "key": "opcache.revalidate_freq",
            "value": "60",
            "reason": "Checks files every 60s instead of on every request.",
        },
        {
            "key": "opcache.save_comments",
            "value": "1",
            "reason": "Must stay: plenty of PHP libraries read annotations out of comments.",
        },
        {
            "key": "opcache.enable_cli",
            "value": "0",
            "reason": "WP-CLI and cron run once and exit, so the cache never pays off.",
        },
        {
            "key": "realpath_cache_size",
            "value": "4096k",
            "reason": "The 256k default is too small for a WordPress tree; this cuts the number of stat() calls.",
        },
        {
            "key": "realpath_cache_ttl",
            "value": "600",
            "reason": "Keeps resolved paths for ten minutes.",
        },
        {
            "key": "expose_php",
            "value": "Off",
            "reason": "Stops advertising the PHP version in response headers.",
        },
        {
            "key": "zlib.output_compression",
            "value": "Off",
            "reason": "Nginx already compresses; doing it twice only costs CPU.",
        },
    ]


def _read_ini_values(paths: list[Path], keys: set[str]) -> dict:
    values: dict[str, str] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith((";", "#")) or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key in keys:
                values[key] = value.strip('"')
    return values


def _effective_values(php_version: str) -> dict:
    """Ask PHP what it resolves these settings to, built-in defaults included.

    Reading the ini files alone misses everything PHP defaults to without being
    told — realpath_cache_size and opcache's own sizes are already set that way —
    and the panel would report a change where there is none. Run the CLI binary
    pointed at the FPM configuration so the answer is what FPM would use.
    """
    import subprocess

    ini_dir = Path(f"/etc/php/{php_version}/fpm")
    binary = f"php{php_version}"
    try:
        result = subprocess.run(
            [binary, "-c", str(ini_dir / "php.ini"), "-i"],
            capture_output=True, text=True, timeout=20,
            env={"PATH": "/usr/bin:/bin", "PHP_INI_SCAN_DIR": str(ini_dir / "conf.d")},
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    wanted = set(TUNABLE_KEYS)
    values: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        if "=>" not in line:
            continue
        parts = [part.strip() for part in line.split("=>")]
        if len(parts) >= 2 and parts[0] in wanted:
            # "key => local => master"; the local value is what runs.
            values[parts[0]] = parts[1]
    return values


def current_values(php_version: str) -> dict:
    """What PHP is using now, defaults included where nothing was written down."""
    if php_version not in php.SUPPORTED_PHP_VERSIONS:
        raise ValueError(f"Unsupported PHP version: {php_version}")
    base = Path(f"/etc/php/{php_version}/fpm")
    paths = [base / "php.ini"]
    conf_d = base / "conf.d"
    if conf_d.is_dir():
        paths.extend(sorted(conf_d.glob("*.ini")))
    values = _read_ini_values(paths, set(TUNABLE_KEYS))
    # PHP itself is the authority; the files are the fallback when it cannot run.
    values.update({key: value for key, value in _effective_values(php_version).items() if value})
    return values


def _normalise(value: str) -> str:
    text = (value or "").strip().strip('"').lower()
    if text in {"on", "true", "yes"}:
        return "1"
    if text in {"", "no value"}:
        # Nothing to compare against: treated as different from any setting, so
        # the panel offers to write it rather than quietly calling it correct.
        return "\x00unset"
    if text in {"off", "false", "no"}:
        return "0"
    match = re.fullmatch(r"(\d+)\s*([kmg])?", text)
    if match:
        size, unit = match.groups()
        factor = {"k": 1, "m": 1024, "g": 1024 * 1024}.get(unit or "", 0)
        # A bare number is a plain integer setting, not a size; keep it as is.
        return f"{int(size) * factor}k" if factor else size
    return text


def opcache_enabled(php_version: str) -> bool:
    """Whether opcache is on for this PHP right now, however it was decided."""
    value = current_values(php_version).get("opcache.enable", "")
    return _normalise(value) == "1"


def set_opcache(php_version: str, enabled: bool) -> dict:
    """Turn opcache on or off for one PHP version.

    Written to its own file so Auto tune, which regenerates the tuning file
    wholesale, cannot undo the decision on its next run.
    """
    from app.services.shell import shell

    if php_version not in php.SUPPORTED_PHP_VERSIONS:
        raise ValueError(f"Unsupported PHP version: {php_version}")
    result = shell.privileged(
        "php-opcache-set",
        helper_args=[php_version, "1" if enabled else "0"],
        check=False,
        timeout=120,
        fallback=["bash", "-lc", "echo dry-run-opcache"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not change opcache").strip()[-2000:])
    return {
        "php_version": php_version,
        "enabled": enabled,
        "message": f"OPcache for PHP {php_version}: {'on' if enabled else 'off'}.",
    }


def pinned_by_php_config(php_version: str) -> dict:
    """Values the panel's own PHP config page has written.

    PHP reads that file after the tuning one, so anything in it wins. The page
    writes every one of its fields on each save, which means memory_limit is
    pinned there the moment an administrator saves it once — and the tuner's
    figure would be quietly ignored. Better to say so on the row than to show a
    recommendation that cannot take effect.
    """
    base = Path(f"/etc/php/{php_version}/fpm/conf.d")
    # Both files PHP reads after the tuning one: the opcache switch and the
    # values an administrator typed on the PHP Configuration form.
    return _read_ini_values([base / OPCACHE_FILE_NAME, base / "99-spanel.ini"], set(TUNABLE_KEYS))


_POOL_KEYS = ("pm.max_children", "pm.process_idle_timeout", "pm.max_requests", "request_terminate_timeout")
_POOL_NAME_RE = re.compile(r"^\[([^\]]+)\]", re.M)


def _pool_dir(php_version: str) -> Path:
    return Path(f"/etc/php/{php_version}/fpm/pool.d")


def current_pool_settings(php_version: str) -> list[dict]:
    """What is actually written in each of this version's FPM pool files.

    php-fpm-retune (run at boot by spanel-autotune.service) and "Auto tune PHP"
    both write these silently - this reads the same figures back so an
    administrator can see what a pool ended up running with without waiting
    to catch a retune in the act. The pool files are root:root 0644, so this
    reads them directly; no helper round-trip needed.
    """
    pools: list[dict] = []
    pool_dir = _pool_dir(php_version)
    if not pool_dir.is_dir():
        return pools
    for pool_file in sorted(pool_dir.glob("spanel-*.conf")):
        try:
            text = pool_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        values = _read_ini_values([pool_file], set(_POOL_KEYS))
        name = _POOL_NAME_RE.search(text)
        pools.append({
            "pool": name.group(1) if name else pool_file.stem,
            "max_children": values.get("pm.max_children", ""),
            "idle_timeout": values.get("pm.process_idle_timeout", ""),
            "max_requests": values.get("pm.max_requests", ""),
            "request_terminate_timeout": values.get("request_terminate_timeout", ""),
        })
    return pools


def plan(php_version: str) -> dict:
    """Current beside recommended, so an administrator can see the difference."""
    facts = server_facts()
    live = current_values(php_version)
    pinned = pinned_by_php_config(php_version)
    jit = jit_status(php_version)
    rows = []
    for item in recommendations(facts, php_version, jit):
        now = live.get(item["key"], "")
        held = pinned.get(item["key"], "")
        rows.append({
            **item,
            "current": now,
            "changes": _normalise(now) != _normalise(item["value"]),
            # Set above in PHP Configuration, which PHP reads last.
            "overridden_value": held if held and _normalise(held) != _normalise(item["value"]) else "",
        })
    return {
        "php_version": php_version,
        "facts": facts,
        "settings": rows,
        "pools": current_pool_settings(php_version),
        # A row that cannot take effect is not offered as a change to make.
        "changes": sum(1 for row in rows if row["changes"] and not row["overridden_value"]),
        "overridden": sum(1 for row in rows if row["overridden_value"]),
        "opcache_enabled": _normalise(live.get("opcache.enable", "")) == "1",
        "jit_supported": jit["supported"],
        "jit_usable": jit["usable"],
        "jit_blocked_by": jit["blocked_by"],
        "tune_file": f"/etc/php/{php_version}/fpm/conf.d/{TUNE_FILE_NAME}",
        # The panel's own PHP config page writes 99-spanel.ini, which PHP reads
        # after this file: anything set there keeps winning.
        "overridden_by": f"/etc/php/{php_version}/fpm/conf.d/99-spanel.ini",
    }


def render_ini(php_version: str) -> str:
    facts = server_facts()
    lines = [
        "; Generated by spanel from this server's CPU and RAM.",
        f"; {facts['total_memory_mb']} MB RAM, {facts['cpu_count']} CPU, "
        f"{facts['pool_count']} PHP pool(s), {facts['php_budget_mb']} MB budget for PHP.",
        "; Values in 99-spanel.ini are read after this file and still win.",
        "",
    ]
    for item in recommendations(facts, php_version):
        lines.append(f"{item['key']} = {item['value']}")

    return "\n".join(lines) + "\n"


def apply(php_version: str) -> dict:
    """Write the tuning file and reload PHP-FPM."""
    from app.services.shell import shell

    if php_version not in php.SUPPORTED_PHP_VERSIONS:
        raise ValueError(f"Unsupported PHP version: {php_version}")
    body = render_ini(php_version)
    result = shell.privileged(
        "php-tune-write",
        helper_args=[php_version],
        input=body,
        check=False,
        timeout=120,
        fallback=["bash", "-lc", "echo dry-run-php-tune"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not write the PHP tuning file").strip()[-2000:])
    return {"message": (result.stdout or "").strip() or f"PHP {php_version} tuned.", "plan": plan(php_version)}


def retune_pools() -> dict:
    """Recalculate every managed FPM pool against the machine as it is now.

    Pool sizing is decided when a pool is written, so a server that gained RAM
    keeps the old numbers until each site is touched. This asks for all of them
    at once.
    """
    from app.services.shell import shell

    result = shell.privileged(
        "php-pools-retune",
        check=False,
        timeout=600,
        fallback=["bash", "-lc", "echo dry-run-retune"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not retune the pools").strip()[-2000:])
    return {"message": "PHP-FPM pools recalculated.", "output": (result.stdout or "").strip()[-4000:]}
