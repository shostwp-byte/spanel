import re
import shlex
from pathlib import Path

from app.models.entities import Website
from app.services import site_users
from app.services.shell import shell

CRON_FIELD_RE = r"(?:\*|\d{1,2})(?:[-/,](?:\*|\d{1,2}))*"
DOMAIN_GREP_RE = re.compile(r"^[a-z0-9.\-]{3,253}$")
PHP_VERSION_RE = re.compile(r"^\d\.\d$")
PHP_INTERPRETER_RE = re.compile(r"^php(?:\d\.\d)?$")
PHP_BIN_DIR = Path("/usr/bin")
WP_CLI_PATH = Path("/usr/local/bin/wp")
CRON_JAIL_BIN = "/usr/local/bin/spanel-cron-jail"
# Matches the interpreter token of an already installed cron line so it can be
# repointed when the website switches PHP version.
CRON_PHP_TOKEN_RE = re.compile(
    r"((?:&&\s+|root\s+)?(?:/usr/local/s?bin/spanel-cron-jail\s+.+?\s+--\s+(?:/bin/sh\s+-c\s+'?)?)?)((?:/usr/bin/)?php(?:\d\.\d)?)(\s)"
)
CRON_JAIL_PREFIX_RE = re.compile(r"^(?:root\s+)?/usr/local/s?bin/spanel-cron-jail\s+.+?\s+--\s+")
# `>file`, `>>file` and `2>file` open a target; `2>&1` / `1>&2` only duplicate a
# descriptor. Cron executes commands with /bin/sh, so bash-only forms such as
# `&>` are rejected instead of silently doing nothing.
REDIRECT_OPEN_RE = re.compile(r"^(\d?>>?)(.*)$")
REDIRECT_DUP_RE = re.compile(r"^\d?>&\d$")
# Anything else that opens with a shell operator (`&>`, `&&`, `|`, `;`, `<`)
# would otherwise be quoted into a literal argument and silently do nothing.
SHELL_OPERATOR_RE = re.compile(r"^[;&|<>]")
ALLOWED_COMMAND_PREFIXES = (
    ("wp", "cron", "event", "run", "--due-now"),
    ("wp", "core", "update"),
    ("wp", "plugin", "update", "--all"),
    ("wp", "theme", "update", "--all"),
)
ALLOWED_PHP_OPTIONS = {"-q"}
CRON_DISABLED_FUNCTIONS = (
    "exec,passthru,shell_exec,system,proc_open,popen,pcntl_exec,dl,"
    "posix_kill,posix_mkfifo,posix_setpgid,posix_setsid,posix_setuid"
)


def _validate_schedule(schedule: str) -> str:
    fields = schedule.split()
    if len(fields) != 5 or not all(re.fullmatch(CRON_FIELD_RE, field) for field in fields):
        raise ValueError("Invalid cron schedule")
    return " ".join(fields)


def _validate_domain(domain: str) -> str:
    value = (domain or "").lower()
    if not DOMAIN_GREP_RE.fullmatch(value):
        raise ValueError("Invalid domain")
    return value


def php_binary(website: Website) -> str:
    """Return the PHP CLI binary a cron job for this website must use.

    A bare `php` resolves through /etc/alternatives to the newest installed
    version, so a site pinned to 8.1 would silently run its scripts on 8.4 and
    die on the first version-specific extension (ionCube, for example). Cron
    then looks "dead" even though the schedule fired correctly.
    """
    version = (website.php_version or "").strip()
    if not PHP_VERSION_RE.fullmatch(version):
        return "php"
    candidate = PHP_BIN_DIR / f"php{version}"
    return str(candidate) if candidate.exists() else "php"


def _is_php_interpreter(arg: str) -> bool:
    """True for `php`, `php8.1` or `/usr/bin/php8.1`.

    Listed cron entries show the resolved binary, so editing and re-submitting
    one has to keep validating as a PHP command.
    """
    return bool(PHP_INTERPRETER_RE.fullmatch(Path(arg).name))


def _escape_percent(command: str) -> str:
    """crontab turns an unescaped % into a newline fed to the job's stdin."""
    return re.sub(r"(?<!\\)%", r"\\%", command)


def _unescape_percent(command: str) -> str:
    return command.replace("\\%", "%")


def _split_redirection(args: list[str]) -> tuple[list[str], list[str]]:
    for index, arg in enumerate(args):
        if REDIRECT_DUP_RE.fullmatch(arg) or REDIRECT_OPEN_RE.match(arg):
            return args[:index], args[index:]
    return args, []


def _validate_redirect_target(target: str, document_root: str | Path, site_root: str | Path) -> str:
    if not target or any(char in target for char in "\r\n\x00"):
        raise ValueError("Invalid redirection target")
    if target == "/dev/null":
        return target
    script = Path(target)
    base = Path(document_root).resolve(strict=False)
    safe_root = Path(site_root).resolve(strict=False)
    candidate = (script if script.is_absolute() else base / script).resolve(strict=False)
    try:
        candidate.relative_to(safe_root)
    except ValueError as exc:
        raise ValueError(
            "Cron output can only be redirected to /dev/null or a file inside this website's folder"
        ) from exc
    return str(candidate)


def _validate_redirection(tokens: list[str], document_root: str | Path, site_root: str | Path) -> str:
    """Rebuild the trailing redirection so /bin/sh still sees it as syntax.

    Quoting these tokens the way command arguments are quoted turns
    `>/dev/null 2>&1` into two literal arguments: the redirect is lost and every
    run mails its output into a void.
    """
    parts: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if REDIRECT_DUP_RE.fullmatch(token):
            parts.append(token)
            index += 1
            continue
        match = REDIRECT_OPEN_RE.match(token)
        if not match:
            raise ValueError("Only the >, >>, 2> and 2>&1 redirections are supported")
        operator, target = match.group(1), match.group(2)
        if not target:
            index += 1
            if index >= len(tokens):
                raise ValueError("Redirection is missing a target file")
            target = tokens[index]
        parts.append(operator + shlex.quote(_validate_redirect_target(target, document_root, site_root)))
        index += 1
    return " ".join(parts)


def _validate_php_command(
    args: list[str], document_root: str | Path, php_bin: str, cron_user: str
) -> str:
    option_count = 1 if len(args) > 1 and args[1] in ALLOWED_PHP_OPTIONS else 0
    script_index = 1 + option_count
    if len(args) <= script_index or args[script_index].startswith("-"):
        raise ValueError("PHP cron commands must run a .php file; only the -q option is allowed")

    script = Path(args[script_index])
    if script.suffix.lower() != ".php":
        raise ValueError("PHP cron commands must run a .php file")

    safe_root = Path(document_root).resolve(strict=False)
    candidate = (script if script.is_absolute() else safe_root / script).resolve(strict=False)
    try:
        candidate.relative_to(safe_root)
    except ValueError as exc:
        raise ValueError("PHP cron scripts must be inside this website's public_html directory") from exc

    # The script has to live inside this website, but its CONTENTS are whatever
    # the customer wrote - so the interpreter needs the same confinement the
    # terminal and the FPM pool give it. ALLOWED_PHP_OPTIONS is {"-q"}, so the
    # caller cannot supply or override this flag.
    resolved = [
        php_bin,
        "-d", f"open_basedir={open_basedir_for(cron_user)}",
        "-d", f"disable_functions={CRON_DISABLED_FUNCTIONS}",
        *args[1:script_index],
        candidate.as_posix(),
        *args[script_index + 1:],
    ]
    return " ".join(shlex.quote(arg) for arg in resolved)


def open_basedir_for(cron_user: str) -> str:
    """The same confinement the panel terminal gives a PHP CLI.

    spanel-helper.sh:30-33 states the model: sites stay apart by the PHP-FPM
    open_basedir of each pool, by the SFTP chroot, and by the panel terminal -
    "not by these bits", the bits being the 0644/0755 modes site trees carry by
    design. A cron job is none of those three, so until this was added the
    interpreter cron started could read every other customer's files. The
    helper's own comment at :5506-5513 records that exact read being verified
    on a live server before the terminal was fixed.

    Kept byte-identical to terminal_open_basedir (spanel-helper.sh:5522) so the
    two cannot drift: the tenant's whole home, not one site root, because a
    customer with several sites still has to work across them.
    """
    user = site_users.validate_linux_user(cron_user)
    # as_posix(), not str(): HOME_ROOT is a pathlib.Path, and str() renders it
    # with the separator of whatever platform imported the module. The crontab
    # line always runs on the Linux server, so the value must not depend on
    # where the panel code happens to be read.
    return (
        f"{site_users.HOME_ROOT.as_posix()}/{user}"
        f":/var/lib/php/sessions/{user}"
        f":/var/lib/php/uploads/{user}"
        ":/tmp:/usr/share/php"
    )


def _validate_wp_command(args: list[str], php_bin: str, cron_user: str) -> str:
    normalized = [arg for arg in args if arg != "--allow-root"]
    if not any(tuple(normalized[:len(prefix)]) == prefix for prefix in ALLOWED_COMMAND_PREFIXES):
        raise ValueError("Only safe WP-CLI maintenance commands or PHP scripts inside this website are allowed")
    resolved = [*normalized, "--allow-root"]
    # Two reasons to name the interpreter explicitly rather than let WP-CLI's
    # `#!/usr/bin/env php` shebang pick one. It would take the system default
    # instead of the version this website runs on; and a shebang leaves nowhere
    # to put -d, so the interpreter would run unconfined. WP-CLI needs its own
    # directory on the path as well, since the phar it is being asked to run
    # has to be readable - the terminal's wp branch does the same
    # (spanel-helper.sh:5545).
    #
    # This used to be conditional on WP_CLI_PATH.exists(). That made the
    # rendered command depend on when it was saved rather than on what runs,
    # and the absent branch emitted an interpreter with no confinement at all.
    # install.sh:543 puts wp at exactly this path on every server.
    basedir = f"{open_basedir_for(cron_user)}:{WP_CLI_PATH.parent}"
    resolved = [
        php_bin,
        "-d", f"open_basedir={basedir}",
        str(WP_CLI_PATH),
        *resolved[1:],
    ]
    return " ".join(shlex.quote(arg) for arg in resolved)


def _validate_command(
    command: str,
    document_root: str | Path,
    site_root: str | Path,
    php_bin: str,
    cron_user: str,
) -> str:
    args = shlex.split(command)
    if not args:
        raise ValueError("Cron command is required")
    args, redirection = _split_redirection(args)
    if not args:
        raise ValueError("Cron command is required")
    for arg in args:
        if SHELL_OPERATOR_RE.match(arg):
            raise ValueError("Only the >, >>, 2> and 2>&1 redirections are supported")
    if _is_php_interpreter(args[0]):
        # Listed WP-CLI entries read back as `<php binary> /usr/local/bin/wp ...`,
        # so drop the interpreter prefix before validating them again.
        if len(args) > 1 and Path(args[1]).name == "wp":
            body = _validate_wp_command(["wp", *args[2:]], php_bin, cron_user)
        else:
            body = _validate_php_command(args, document_root, php_bin, cron_user)
    else:
        body = _validate_wp_command(args, php_bin, cron_user)
    suffix = _validate_redirection(redirection, document_root, site_root)
    return f"{body} {suffix}".strip()


def cron_user_for_website(website: Website) -> str:
    if website.linux_user:
        return site_users.validate_linux_user(website.linux_user)
    try:
        parts = Path(website.root_path).resolve().relative_to(site_users.HOME_ROOT.resolve()).parts
    except ValueError:
        return "www-data"
    if parts:
        try:
            return site_users.validate_linux_user(parts[0])
        except ValueError:
            return "www-data"
    return "www-data"


def _parse_cron_line(index: int, line: str) -> dict:
    parts = line.split(maxsplit=5)
    schedule = " ".join(parts[:5]) if len(parts) >= 5 else ""
    command = parts[5] if len(parts) >= 6 else ""
    command = re.sub(r"\s+#\s*spanel:[^\s]+\s*$", "", command).strip()
    if command.startswith("root "):
        command = command[5:].strip()
    if command.startswith("cd ") and " && " in command:
        command = command.split(" && ", 1)[1].strip()
    command = CRON_JAIL_PREFIX_RE.sub("", command).strip()
    command = _unescape_percent(command)
    if command.startswith("/bin/sh -c "):
        try:
            sh_parts = shlex.split(command)
            if len(sh_parts) == 3 and sh_parts[0] == "/bin/sh" and sh_parts[1] == "-c":
                command = sh_parts[2].strip()
        except ValueError:
            pass
    command = command.replace(" --allow-root", "").strip()
    command = _strip_open_basedir(command)
    return {"index": index, "schedule": schedule, "command": command, "line": line}


def _strip_open_basedir(command: str) -> str:
    """Hide the confinement flags the renderer adds.

    add_cron emits `php -d open_basedir=... -d disable_functions=... script.php`.
    Without this the flags would come back through the UI and be re-submitted,
    and _validate_php_command would reject them - ALLOWED_PHP_OPTIONS is {"-q"},
    so `-d` is not an option a caller may pass. They are ours to add, not the
    customer's, so they are ours to take back out.
    """
    try:
        args = shlex.split(command)
    except ValueError:
        return command
    cleaned: list[str] = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "-d" and i + 1 < len(args) and (
            args[i + 1].startswith("open_basedir=") or args[i + 1].startswith("disable_functions=")
        ):
            skip_next = True
            continue
        if arg.startswith("-dopen_basedir=") or arg.startswith("-ddisable_functions="):
            continue
        cleaned.append(arg)
    if cleaned == args:
        return command
    return " ".join(shlex.quote(arg) for arg in cleaned)


def validate_cron_write_line(cron_user: str, line: str) -> bool:
    """Verify a tenant cron line strictly matches the root-enforced spanel-cron-jail format
    with the entire command + redirection encapsulated inside a single-quoted /bin/sh -c argument.
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#") or raw.startswith("SHELL=") or raw.startswith("PATH="):
        return True
    user = site_users.validate_linux_user(cron_user)
    parts = raw.split(maxsplit=5)
    if len(parts) < 6:
        return False
    sched = " ".join(parts[:5])
    try:
        _validate_schedule(sched)
    except ValueError:
        return False
    rest = parts[5].strip()
    marker_match = re.search(r"\s+(#\s*spanel:[a-z0-9.\-]{3,253})\s*$", rest)
    if not marker_match:
        return False
    body = rest[:marker_match.start()].strip()
    # Must not contain unescaped newlines or null bytes
    if any(ch in body for ch in ("\r", "\n", "\x00")):
        return False
    # Unescape crontab \% before tokenizing with shlex
    unescaped_body = _unescape_percent(body)
    try:
        tokens = shlex.split(unescaped_body)
    except ValueError:
        return False
    # Exact 8 tokens required:
    # ['root', '/usr/local/bin/spanel-cron-jail', '<user>', '<cwd>', '--', '/bin/sh', '-c', '<cmd>']
    if len(tokens) != 8:
        return False
    if tokens[0] != "root" or tokens[1] != CRON_JAIL_BIN or tokens[2] != user:
        return False
    if tokens[4] != "--" or tokens[5] != "/bin/sh" or tokens[6] != "-c" or not tokens[7]:
        return False
    # Verify <cwd> is inside /home/<user>/
    home_root = Path(site_users.HOME_ROOT.as_posix()) / user
    cwd_path = Path(tokens[3])
    if not cwd_path.is_absolute() or ".." in cwd_path.parts:
        return False
    cwd_posix = cwd_path.as_posix()
    home_posix = home_root.as_posix()
    if cwd_posix != home_posix and not cwd_posix.startswith(home_posix + "/"):
        return False
    # Ensure the raw string after `-- /bin/sh -c ` is a single-quoted string with no trailing unquoted operators
    after_sh_c = re.split(r"\s+--\s+/bin/sh\s+-c\s+", body, maxsplit=1)
    if len(after_sh_c) != 2:
        return False
    quoted_arg = after_sh_c[1].strip()
    if not (quoted_arg.startswith("'") and quoted_arg.endswith("'")):
        return False
    return True


def _render_jailed_line(cron_user: str, safe_schedule: str, doc_root_str: str, safe_command: str, safe_domain: str) -> str:
    marker = f"# spanel:{safe_domain}"
    if cron_user != "www-data":
        return (
            f"{safe_schedule} root {CRON_JAIL_BIN} {shlex.quote(cron_user)} "
            f"{shlex.quote(doc_root_str)} -- /bin/sh -c {_escape_percent(shlex.quote(safe_command))} {marker}"
        )
    return f"{safe_schedule} cd {shlex.quote(doc_root_str)} && {_escape_percent(safe_command)} {marker}"


def _upgrade_legacy_cron_line(cron_user: str, line: str, fallback_doc_root: str = "", fallback_php_bin: str = "php") -> str:
    """Convert a legacy tenant crontab line into the strict root spanel-cron-jail /bin/sh -c format."""
    if cron_user == "www-data" or validate_cron_write_line(cron_user, line):
        return line
    raw = (line or "").strip()
    if not raw or raw.startswith("#") or raw.startswith("SHELL=") or raw.startswith("PATH="):
        return line
    marker_match = re.search(r"#\s*spanel:([a-z0-9.\-]{3,253})\s*$", raw)
    if not marker_match:
        raise ValueError("Refusing unmanaged cron line without spanel domain marker")
    safe_domain = _validate_domain(marker_match.group(1))
    parsed = _parse_cron_line(0, raw)
    safe_schedule = _validate_schedule(parsed["schedule"])
    # Extract working directory from legacy `cd <cwd> &&` or `spanel-cron-jail <user> <cwd> --`
    cwd_str = fallback_doc_root
    parts = raw.split(maxsplit=5)
    body = parts[5] if len(parts) >= 6 else ""
    cd_match = re.match(r"^(?:root\s+)?cd\s+(.+?)\s+&&\s+", body)
    jail_match = re.match(r"^(?:root\s+)?/usr/local/s?bin/spanel-cron-jail\s+\S+\s+(\S+)\s+--\s+", body)
    if jail_match:
        try:
            cwd_str = shlex.split(jail_match.group(1))[0]
        except ValueError:
            cwd_str = jail_match.group(1).strip("'\"")
    elif cd_match:
        try:
            cwd_str = shlex.split(cd_match.group(1))[0]
        except ValueError:
            cwd_str = cd_match.group(1).strip("'\"")
    if not cwd_str:
        cwd_str = f"{site_users.HOME_ROOT.as_posix()}/{cron_user}/domains/{safe_domain}/public_html"
    site_root = str(Path(cwd_str).parent) if Path(cwd_str).name == "public_html" else cwd_str
    # Determine PHP interpreter from line if present
    php_match = re.search(r"((?:/usr/bin/)?php\d\.\d)\b", raw)
    php_bin = php_match.group(1) if php_match else fallback_php_bin
    safe_command = _validate_command(parsed["command"], cwd_str, site_root, php_bin, cron_user)
    return _render_jailed_line(cron_user, safe_schedule, cwd_str, safe_command, safe_domain)


def add_cron(website: Website, schedule: str, command: str) -> str:
    safe_schedule = _validate_schedule(schedule)
    document_root = site_users.document_root(website.root_path)
    # The cron user has to be known before the command is rendered: it is what
    # open_basedir confines the interpreter to.
    cron_user = cron_user_for_website(website)
    php_bin = php_binary(website)
    safe_command = _validate_command(
        command, document_root, website.root_path, php_bin, cron_user
    )
    safe_domain = _validate_domain(website.domain)
    doc_root_str = str(document_root)
    line = _render_jailed_line(cron_user, safe_schedule, doc_root_str, safe_command, safe_domain)
    if cron_user != "www-data":
        runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
        site_users.ensure_site_runtime(website.domain, website.root_path, runtime_php_version, cron_user)
    existing_lines = [
        _upgrade_legacy_cron_line(cron_user, old_line, doc_root_str, php_bin)
        for old_line in list_cron_all(cron_user).splitlines()
        if old_line.strip()
    ]
    existing_lines.append(line)
    new_content = "\n".join(existing_lines) + "\n"
    shell.privileged(
        "cron-write",
        helper_args=[cron_user],
        input=new_content,
        fallback=["bash", "-lc", "crontab -"],
    )
    return line


def list_cron_all(cron_user: str = "www-data") -> str:
    if cron_user != "www-data":
        site_users.validate_linux_user(cron_user)
    result = shell.privileged(
        "cron-list",
        helper_args=[cron_user],
        check=False,
        fallback=["bash", "-lc", "crontab -l 2>/dev/null || true"],
    )
    return result.stdout or ""


def list_cron(domain: str, cron_user: str = "www-data") -> str:
    safe_domain = _validate_domain(domain)
    marker = f"spanel:{safe_domain}"
    return "\n".join(line for line in list_cron_all(cron_user).splitlines() if marker in line)


def list_cron_entries(domain: str, cron_user: str = "www-data") -> list[dict]:
    return [_parse_cron_line(index, line) for index, line in enumerate(list_cron(domain, cron_user).splitlines())]


def delete_cron(domain: str, index: int, cron_user: str = "www-data") -> str:
    safe_domain = _validate_domain(domain)
    matching = list_cron(safe_domain, cron_user).splitlines()
    if index < 0 or index >= len(matching):
        raise ValueError("Cron not found")
    target = matching[index]
    full = list_cron_all(cron_user).splitlines()
    new_lines = [
        _upgrade_legacy_cron_line(cron_user, line)
        for line in full
        if line.strip() and line.strip() != target.strip()
    ]
    new_content = "\n".join(new_lines) + ("\n" if new_lines else "")
    shell.privileged(
        "cron-write",
        helper_args=[cron_user],
        input=new_content,
        fallback=["bash", "-lc", "crontab -"],
    )
    return target


def retarget_php_binary(website: Website) -> int:
    """Repoint this website's cron lines at its current PHP CLI.

    Called after a PHP version switch so existing jobs do not keep executing on
    the version the site no longer runs.
    """
    safe_domain = _validate_domain(website.domain)
    php_bin = php_binary(website)
    cron_user = cron_user_for_website(website)
    doc_root_str = str(site_users.document_root(website.root_path))
    marker = f"spanel:{safe_domain}"
    lines = list_cron_all(cron_user).splitlines()
    updated: list[str] = []
    changed = 0
    for line in lines:
        if not line.strip():
            continue
        norm_line = _upgrade_legacy_cron_line(cron_user, line, doc_root_str, php_bin)
        if marker in norm_line:
            new_line = CRON_PHP_TOKEN_RE.sub(lambda match: f"{match.group(1)}{php_bin}{match.group(3)}", norm_line, count=1)
            if new_line != line:
                changed += 1
                norm_line = new_line
        elif norm_line != line:
            changed += 1
        updated.append(norm_line)
    if not changed:
        return 0
    new_content = "\n".join(updated) + ("\n" if updated else "")
    shell.privileged(
        "cron-write",
        helper_args=[cron_user],
        input=new_content,
        fallback=["bash", "-lc", "crontab -"],
    )
    return changed


def cron_security_status(db) -> dict:
    """Inspect all websites' cron jobs and report confinement coverage."""
    websites = db.query(Website).all()
    total_jobs = 0
    confined_jobs = 0
    unconfined_jobs = 0
    items = []
    seen_users: dict[str, list[str]] = {}

    for site in websites:
        c_user = cron_user_for_website(site)
        if c_user not in seen_users:
            try:
                seen_users[c_user] = list_cron_all(c_user).splitlines()
            except Exception:
                seen_users[c_user] = []
        marker = f"spanel:{_validate_domain(site.domain)}"
        for idx, line in enumerate(seen_users[c_user]):
            if marker not in line:
                continue
            total_jobs += 1
            has_open_basedir = "open_basedir=" in line
            is_wp_cli = str(WP_CLI_PATH) in line or " wp " in line
            has_disable_funcs = "disable_functions=" in line or is_wp_cli
            has_namespace_jail = (
                CRON_JAIL_BIN in line
                and "-- /bin/sh -c '" in line
                and not re.search(r"'[^']*'\s*(?:\d?>>?|\d?>&\d)", line)
            )
            fully_confined = bool(
                has_open_basedir and has_disable_funcs and has_namespace_jail and c_user != "www-data"
            )
            if fully_confined:
                confined_jobs += 1
            else:
                unconfined_jobs += 1
            parsed = _parse_cron_line(idx, line)
            items.append({
                "domain": site.domain,
                "linux_user": c_user,
                "schedule": parsed["schedule"],
                "command": parsed["command"],
                "has_open_basedir": has_open_basedir,
                "has_disable_functions": has_disable_funcs,
                "has_namespace_jail": has_namespace_jail,
                "fully_confined": fully_confined,
            })

    return {
        "total_jobs": total_jobs,
        "confined_jobs": confined_jobs,
        "unconfined_jobs": unconfined_jobs,
        "disabled_functions": CRON_DISABLED_FUNCTIONS,
        "namespace_isolation": True,
        "jail_wrapper": CRON_JAIL_BIN,
        "items": items[:100],
    }


def enforce_all_cron_confinement(db) -> dict:
    """Re-render and upgrade all existing website cron jobs with full confinement."""
    websites = db.query(Website).all()
    upgraded = 0
    checked = 0
    for site in websites:
        c_user = cron_user_for_website(site)
        try:
            entries = list_cron_entries(site.domain, c_user)
        except Exception:
            continue
        if not entries:
            continue
        # Delete and re-add any entry that isn't already fully confined
        for entry in entries:
            checked += 1
            raw_line = entry.get("line", "")
            is_wp = str(WP_CLI_PATH) in raw_line or " wp " in raw_line
            has_safe_jail = (
                CRON_JAIL_BIN in raw_line
                and "-- /bin/sh -c '" in raw_line
                and not re.search(r"'[^']*'\s*(?:\d?>>?|\d?>&\d)", raw_line)
            )
            if (
                "open_basedir=" in raw_line
                and ("disable_functions=" in raw_line or is_wp)
                and (has_safe_jail or c_user == "www-data")
            ):
                continue
            try:
                delete_cron(site.domain, 0, c_user)
                add_cron(site, entry["schedule"], entry["command"])
                upgraded += 1
            except Exception:
                continue
    status = cron_security_status(db)
    status["upgraded_jobs"] = upgraded
    status["checked_jobs"] = checked
    return status
