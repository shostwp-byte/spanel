import shlex

import pytest

from app.models.entities import Website
from app.services import cron


def _website(root, php_version="8.1", linux_user="siteuser"):
    return Website(
        domain="example.test",
        owner_id=1,
        root_path=str(root),
        linux_user=linux_user,
        php_version=php_version,
        app_type="php",
    )


@pytest.fixture
def site(tmp_path):
    public = tmp_path / "site" / "public_html"
    public.mkdir(parents=True)
    (public / "queue.php").write_text("<?php\n", encoding="utf-8")
    return tmp_path / "site"


@pytest.fixture
def captured_crontab(monkeypatch):
    """Capture what add_cron feeds to the privileged crontab writer."""
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs.get("input")))
        stdout = "" if helper_command == "cron-list" else ""
        return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

    monkeypatch.setattr(cron.shell, "privileged", fake_privileged)
    monkeypatch.setattr(cron.site_users, "ensure_site_runtime", lambda *args, **kwargs: None)
    return calls


def test_php_binary_falls_back_when_version_is_unknown(site):
    assert cron.php_binary(_website(site, php_version="")) == "php"
    assert cron.php_binary(_website(site, php_version="not-a-version")) == "php"


def test_php_binary_uses_installed_site_version(site, monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)
    assert cron.php_binary(_website(site, php_version="8.1")) == str(bin_dir / "php8.1")
    assert cron.php_binary(_website(site, php_version="8.3")) == "php"


def test_add_cron_pins_the_website_php_binary(site, monkeypatch, captured_crontab, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)

    line = cron.add_cron(_website(site), "*/5 * * * *", "php -q queue.php")

    assert f"&& {shlex.quote(str(bin_dir / 'php8.1'))} -d " in line
    assert " -q " in line
    assert shlex.quote(str(site / "public_html" / "queue.php")) in line
    assert line.endswith("# spanel:example.test")


def test_add_cron_keeps_redirections_as_shell_syntax(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    line = cron.add_cron(_website(site), "*/5 * * * *", "php queue.php >/dev/null 2>&1")

    assert line.endswith(">/dev/null 2>&1 # spanel:example.test")
    assert "'>/dev/null'" not in line


def test_add_cron_accepts_a_detached_redirect_operator(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    line = cron.add_cron(_website(site), "*/5 * * * *", "php queue.php > /dev/null 2>&1")

    assert ">/dev/null 2>&1 # spanel:example.test" in line


def test_add_cron_allows_a_log_file_inside_the_website(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    line = cron.add_cron(_website(site), "*/5 * * * *", "php queue.php >> ../logs/cron.log 2>&1")

    assert f">>{shlex.quote(str(site / 'logs' / 'cron.log'))} 2>&1" in line


def test_add_cron_rejects_a_log_file_outside_the_website(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    with pytest.raises(ValueError, match="inside this website"):
        cron.add_cron(_website(site), "*/5 * * * *", "php queue.php > ../../../cron.log")


def test_add_cron_rejects_bash_only_redirections(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    with pytest.raises(ValueError, match="redirections are supported"):
        cron.add_cron(_website(site), "*/5 * * * *", "php queue.php &>/dev/null")


def test_add_cron_escapes_percent_so_cron_does_not_split_the_command(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    line = cron.add_cron(_website(site), "0 2 * * *", "php queue.php >> ../logs/cron.log 2>&1")
    assert "%" not in line

    line = cron.add_cron(_website(site), "0 2 * * *", "php -q queue.php run=100%")
    assert "run=100\\%" in line


def test_parse_cron_line_reverses_percent_escaping():
    entry = cron._parse_cron_line(0, "0 2 * * * cd '/home/x/public_html' && /usr/bin/php8.1 a.php run=100\\% # spanel:x.test")
    assert entry["command"] == "/usr/bin/php8.1 a.php run=100%"
    assert entry["schedule"] == "0 2 * * *"


def test_wp_cli_commands_run_on_the_site_php_binary(site, monkeypatch, captured_crontab, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    wp = tmp_path / "wp"
    wp.write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)
    monkeypatch.setattr(cron, "WP_CLI_PATH", wp)

    line = cron.add_cron(_website(site), "*/5 * * * *", "wp cron event run --due-now")

    php_bin, wp_bin = shlex.quote(str(bin_dir / 'php8.1')), shlex.quote(str(wp))
    assert f"&& {php_bin} -d " in line
    assert f"{wp_bin} wp cron event run --due-now --allow-root" not in line
    assert f"{wp_bin} cron event run --due-now --allow-root" in line


def test_listed_wp_entry_can_be_resubmitted(site, monkeypatch, captured_crontab, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    wp = tmp_path / "wp"
    wp.write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)
    monkeypatch.setattr(cron, "WP_CLI_PATH", wp)

    line = cron.add_cron(
        _website(site),
        "*/5 * * * *",
        f"{shlex.quote(str(bin_dir / 'php8.1'))} {shlex.quote(str(wp))} cron event run --due-now",
    )

    php_bin, wp_bin = shlex.quote(str(bin_dir / 'php8.1')), shlex.quote(str(wp))
    assert f"{php_bin} -d " in line
    assert f"{wp_bin} cron event run --due-now --allow-root" in line


def test_add_cron_still_rejects_arbitrary_commands(site, monkeypatch, captured_crontab, tmp_path):
    monkeypatch.setattr(cron, "PHP_BIN_DIR", tmp_path / "missing")

    with pytest.raises(ValueError):
        cron.add_cron(_website(site), "*/5 * * * *", "curl https://example.test")
    with pytest.raises(ValueError, match="public_html"):
        cron.add_cron(_website(site), "*/5 * * * *", "php ../../../elsewhere.php")


def test_retarget_php_binary_rewrites_existing_lines(site, monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.3").write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)

    existing = (
        "*/5 * * * * cd '/home/siteuser/public_html' && /usr/bin/php8.1 -q '/home/siteuser/public_html/a.php' "
        "# spanel:example.test\n"
        "0 3 * * * cd '/home/other/public_html' && /usr/bin/php8.1 '/home/other/public_html/b.php' # spanel:other.test\n"
    )
    written = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        if helper_command == "cron-write":
            written.append(kwargs.get("input"))
        return type("Result", (), {"returncode": 0, "stdout": existing, "stderr": ""})()

    monkeypatch.setattr(cron.shell, "privileged", fake_privileged)

    assert cron.retarget_php_binary(_website(site, php_version="8.3")) == 1
    assert f"&& {bin_dir / 'php8.3'} -q " in written[0]
    # The unrelated website keeps its own interpreter and its .php argument is untouched.
    assert "/usr/bin/php8.1 '/home/other/public_html/b.php'" in written[0]
    assert "/home/siteuser/public_html/a.php" in written[0]


def test_cron_php_runs_under_open_basedir(site, monkeypatch, captured_crontab, tmp_path):
    """spanel: a cron interpreter must be confined like the terminal's.

    spanel-helper.sh:30-33 states the model - sites stay apart by the PHP-FPM
    open_basedir of each pool, by the SFTP chroot and by the panel terminal.
    Cron is none of those three, so until this was added the interpreter cron
    started could read every other customer's files: site trees are 0644/0755
    by design and /home/<user> is 0751, traversable once the name is known,
    which /etc/passwd supplies. The helper's own comment at :5506-5513 records
    that read being verified on a live server before the terminal was fixed.

    Cron also has no terminal_enabled gate - the flag is read only in
    api/terminal.py - so this path is open to exactly the accounts an operator
    denied a shell.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)

    line = cron.add_cron(_website(site), "*/5 * * * *", "php -q queue.php")

    assert "-d open_basedir=" in line
    # The tenant's whole home, not one site root: a customer with several sites
    # still has to work across their own. Matches terminal_open_basedir.
    assert "/home/siteuser:" in line
    assert "/var/lib/php/sessions/siteuser:" in line
    assert "/var/lib/php/uploads/siteuser:" in line
    assert "/tmp:/usr/share/php" in line


def test_cron_wp_cli_runs_under_open_basedir(site, monkeypatch, captured_crontab, tmp_path):
    """WP-CLI bootstraps the tenant's own wp-config.php and plugins."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    wp = tmp_path / "wp"
    wp.write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)
    monkeypatch.setattr(cron, "WP_CLI_PATH", wp)

    line = cron.add_cron(_website(site), "*/5 * * * *", "wp cron event run --due-now")

    # Not "-d open_basedir=" literally: the WP-CLI form appends the phar's own
    # directory, and on a Windows dev box that is a backslashed path, so
    # shlex.quote wraps the whole value. On the Linux target it is
    # /usr/local/bin and nothing is quoted. Assert the parts, not the spacing.
    assert "-d " in line
    assert "open_basedir=" in line
    assert "/home/siteuser:" in line
    # The phar itself has to be readable, so its directory is appended - the
    # terminal's wp branch does the same (spanel-helper.sh:5545).
    assert str(wp.parent) in line


def test_the_confinement_flag_is_not_echoed_back_to_the_customer(site, monkeypatch, captured_crontab, tmp_path):
    """The flag is ours to add, so it is ours to hide on read-back.

    ALLOWED_PHP_OPTIONS is {"-q"}, so if -d came back through the UI and were
    re-submitted, _validate_php_command would reject it and editing an existing
    cron entry would break.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "php8.1").write_text("", encoding="utf-8")
    monkeypatch.setattr(cron, "PHP_BIN_DIR", bin_dir)

    line = cron.add_cron(_website(site), "*/5 * * * *", "php -q queue.php")
    parsed = cron._parse_cron_line(0, line)

    assert "open_basedir" not in parsed["command"]
    # and the round trip still renders, rather than raising
    again = cron.add_cron(_website(site), "*/5 * * * *", parsed["command"])
    assert "-d open_basedir=" in again
