"""Terminal entitlement and sandboxing — issue #119 (spanel-2026-001/002)."""

import pytest
from fastapi import HTTPException

from app.api import terminal as terminal_api


class _User:
    def __init__(self, role="end_user", terminal_enabled=False, user_id=2):
        self.role = role
        self.terminal_enabled = terminal_enabled
        self.id = user_id


def test_an_end_user_without_the_entitlement_is_refused():
    """UserPackage.terminal_enabled existed but nothing read it.

    Both terminal doors checked website ownership only, so any end user could
    open a shell on their own site regardless of the package they were paying
    for. Hiding the button in the frontend is not access control - curl with a
    valid session cookie reached the same endpoint.
    """
    assert terminal_api.may_use_terminal(_User(terminal_enabled=False)) is False


def test_an_end_user_with_the_entitlement_is_allowed():
    assert terminal_api.may_use_terminal(_User(terminal_enabled=True)) is True


def test_an_admin_always_may():
    # Admins administer the server; the entitlement is a per-package sales
    # boundary, not a security boundary against the operator.
    assert terminal_api.may_use_terminal(_User(role="admin", terminal_enabled=False)) is True


def test_the_rest_endpoint_refuses_before_touching_the_site():
    """The check has to live in get_user_website, which /exec goes through.

    Driven with asyncio.run rather than pytest-asyncio: the suite has no async
    plugin and one coroutine is not worth adding a dependency for.
    """
    import asyncio

    class _Website:
        id = 1
        owner_id = 2
        linux_user = "client"

    class _Query:
        def filter(self, *_a):
            return self

        def first(self):
            return _Website()

    class _DB:
        def query(self, *_a):
            return _Query()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(terminal_api.get_user_website(1, _DB(), _User(terminal_enabled=False)))

    assert exc.value.status_code == 403
    assert "not enabled" in str(exc.value.detail).lower()


def test_assigning_a_package_copies_its_terminal_flag():
    """The flag is copied onto the user, the way website_limit already is, so
    enforcement reads one column and a user without a package still resolves."""
    from app.api.users import _apply_package_limits

    class _Package:
        id = 1
        website_limit = 10
        storage_limit_mb = 2048
        terminal_enabled = True
        # Every limit _apply_package_limits copies has to exist on the stub, or
        # this test fails for a reason that has nothing to do with terminals.
        sftp_accounts_limit = 3
        mail_accounts_limit = 10

    class _Target:
        package_id = None
        website_limit = 5
        storage_limit_mb = 1024
        terminal_enabled = False
        sftp_accounts_limit = 0

    user = _Target()
    _apply_package_limits(user, _Package())

    assert user.terminal_enabled is True


def test_php_run_through_the_terminal_is_confined_to_the_tenant():
    """spanel-2026-002: PHP CLI from the terminal had no open_basedir at all.

    The FPM pool for the same site has always had one; the terminal is a
    separate pipeline and was missed. Because it runs as the site user and site
    files are world-readable by design, `php -r "readfile('/home/other/...')"`
    read another customer's files. Confirmed against a running server before
    the fix - it printed /etc/passwd.

    Asserted against the shipped helper rather than a copy, so the guarantee
    cannot drift away from what actually runs.
    """
    from pathlib import Path

    helper = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"
    script = helper.read_text(encoding="utf-8")

    start = script.index("  terminal-exec)")
    end = script.index("  nginx-upgrade-map-ensure)", start)
    block = script[start:end]

    # The value is built once from the tenant's home, not a single site root:
    # a customer with several sites still has to work across their own. It now
    # lives in one shared function because the terminal was not the only
    # pipeline that starts a PHP CLI as a site user - wp-site and cron both
    # did, unconfined, for as long as this assertion only looked in here.
    assert 'site_open_basedir() {' in script
    assert '"$HOME_ROOT/$user:/var/lib/php/sessions/$user:' in script
    assert 'terminal_open_basedir="$(site_open_basedir "$user")"' in block

    # Every PHP entry point uses it. node/npm/npx/yarn/git are deliberately
    # absent: open_basedir is a PHP mechanism and does nothing for them.
    for line in block.splitlines():
        if '"$php_bin"' in line and line.strip().startswith("exec "):
            assert "open_basedir" in line, f"unconfined PHP invocation: {line.strip()[:90]}"


def test_wp_site_runs_under_the_same_confinement_as_the_terminal():
    """The verb the terminal's own guarantee did not cover.

    wp-site execs WP-CLI as the site user, and WP-CLI bootstraps that install's
    wp-config.php and active plugins - tenant-authored PHP. It carried no
    open_basedir while the terminal's near-identical wp branch set it twice.
    The old assertion above could not see it, because it sliced the helper down
    to the terminal-exec block first.
    """
    from pathlib import Path

    helper = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"
    script = helper.read_text(encoding="utf-8")

    start = script.index("  wp-site)")
    end = script.index("  cron-list)", start)
    block = script[start:end]

    assert "site_open_basedir" in block, "wp-site must use the shared confinement"
    # Both halves: WP-CLI honours WP_CLI_PHP_ARGS for the PHP it spawns, and the
    # interpreter flags cover the phar bootstrap itself.
    assert "WP_CLI_PHP_ARGS=" in block and "open_basedir=" in block

    # The exec wraps across lines, so join backslash continuations before
    # scanning - otherwise this reads only the first physical line and passes
    # on a command whose confinement sits on the next one.
    joined = block.replace("\\\n", " ")
    for line in joined.splitlines():
        if line.strip().startswith("exec runuser"):
            assert "open_basedir" in line, f"unconfined WP-CLI: {line.strip()[:90]}"


def test_terminal_command_execution_does_not_block_the_event_loop():
    """spanel: one tenant must not be able to stall the whole control panel.

    Both terminal doors are `async def`, so FastAPI runs them ON the event
    loop rather than in the threadpool. terminal.exec_command is subprocess.run
    with a timeout of up to 915s for composer/npm/git/curl and friends, and
    serve.py runs a single uvicorn worker with no `workers` option - so calling
    it inline froze every other tenant's request, the admin UI and /auth/login
    for that whole time. `curl <host-that-never-answers>` was enough.

    17 of 18 API modules use plain `def` and get the threadpool for free;
    terminal.py is the outlier that has to ask for it.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "api" / "terminal.py"
    ).read_text(encoding="utf-8")

    assert "from starlette.concurrency import run_in_threadpool" in source

    # Every call to the blocking helper must go through the threadpool.
    for line_no, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("result = terminal.exec_command("):
            raise AssertionError(
                f"terminal.py:{line_no} calls exec_command inline on the event "
                "loop; wrap it in run_in_threadpool"
            )

    assert source.count("run_in_threadpool(\n        terminal.exec_command,") + source.count(
        "run_in_threadpool(\n                    terminal.exec_command,"
    ) == 2, "both the REST door and the WebSocket door must offload"


def test_the_terminal_runs_in_a_mount_namespace_that_hides_other_tenants():
    """open_basedir is a PHP mechanism; the terminal is not only PHP.

    node, npm, npx, yarn and git get no open_basedir - deliberately, and the
    assertion above says so. But `find . -maxdepth 0 -exec sh -c '<cmd>' \;`
    also walks straight through require_terminal_path_args, which skips every
    argument matching -*, so the allowlist itself is bypassable and the tenant
    gets an arbitrary shell as their own uid.

    Argument filtering cannot close that. The tenant already runs their own
    code as their own uid through npm lifecycle scripts and git hooks, and no
    scanner models program text. So the filesystem is confined instead: /home
    becomes a tmpfs holding exactly one directory, the caller's own.
    """
    from pathlib import Path

    helper = Path(__file__).resolve().parents[3] / "installer" / "files" / "spanel-helper.sh"
    script = helper.read_text(encoding="utf-8")

    start = script.index("  terminal-exec)")
    end = script.index("  nginx-upgrade-map-ensure)", start)
    block = script[start:end]

    # A private namespace, so nothing done inside it is visible to the host.
    assert "unshare --mount --propagation private" in block

    # /home is replaced wholesale and only the caller's own directory moved
    # back. Binding the tenant's home somewhere would leave the others in
    # place; the tmpfs is what removes them.
    assert 'mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$jail_root"' in block
    assert 'mount --move "$hold" "$jail_home"' in block

    # Every arm goes through it: the jail is part of terminal_runner, which all
    # of them exec, rather than something the PHP arms opt into.
    joined = block.replace("\\n", " ")
    runner_start = joined.index("terminal_runner=(")
    runner = joined[runner_start : joined.index(")", runner_start)]
    assert "unshare" in runner
    assert "runuser -u" in runner

    for line in joined.splitlines():
        if line.strip().startswith('exec "${terminal_runner[@]}"'):
            break
    else:
        raise AssertionError("no arm execs through terminal_runner any more")


def test_wp_cli_never_runs_as_www_data_for_a_website():
    """www-data is the widest identity on the box.

    usermod -aG puts it in EVERY site's group (spanel-helper.sh:3596) and in
    spanel-sites (:3513), while wp-config.php, .env and .my.cnf are 0640
    group-readable - verified on a live server, where www-data read another
    tenant's wp-config.php. WP-CLI also loads the target install's own plugins,
    which is tenant-authored PHP. So WP-CLI must never run as www-data for a
    website; it runs as that site's own Linux user through wp-site.

    The old `wp` verb took arbitrary WP-CLI argv as www-data and was reached
    whenever a Website row had no linux_user. It is now narrowed to --info,
    which is the only thing still using it: install.sh and update.sh call it to
    prove the sudo trampoline works.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    helper = (root / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
    wordpress = (root / "backend" / "app" / "services" / "wordpress.py").read_text(encoding="utf-8")

    start = helper.index("\n  wp)\n")
    block = helper[start : helper.index("  wp-site)", start)]
    assert '"${1:-}" == "--info"' in block, "the wp verb must accept only --info"
    assert '"$@"' not in block, "the wp verb must not forward caller argv"

    # And nothing in the panel routes a website through it any more.
    assert 'privileged("wp"' not in wordpress
    assert '"wp-site" if linux_user else "wp"' not in wordpress
    assert "_site_user_for_path" in wordpress


def test_the_installer_health_check_still_matches_the_narrowed_verb():
    """The one caller that is left. If it ever needs more than --info, the verb
    needs a real allowlist rather than a quiet widening."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for name in ("install.sh", "update.sh"):
        text = (root / "installer" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "spanel-helper wp " in line:
                assert "wp --info" in line, f"{name}: {line.strip()[:90]}"
