"""WP-CLI has to run under the site's PHP, and errors have to be logged."""

from app.services import wordpress


def test_the_site_php_version_is_passed_to_the_helper():
    """A site on 8.4 was being updated by the 8.3 CLI.

    On a server with several PHP versions the default `php` alternative is not
    the site's. That is not cosmetic: the 8.3 CLI there had no mysqli, so every
    `wp core update` died with "Your PHP installation appears to be missing the
    MySQL extension" and the panel showed a bare 500.
    """
    assert wordpress._wp_php_flag("8.4") == ["--php-version=8.4"]


def test_no_version_means_no_flag():
    # The helper falls back to the default `php` when it is not told which to
    # use, so an absent version must not become "--php-version=".
    assert wordpress._wp_php_flag(None) == []
    assert wordpress._wp_php_flag("") == []
    assert wordpress._wp_php_flag("   ") == []


def test_wp_update_sends_the_flag_before_the_wp_arguments(monkeypatch):
    """Order matters: the helper parses --php-version only where it expects it,
    immediately after the site user and before the WP-CLI arguments."""
    seen = {}

    def fake_privileged(verb, helper_args=None, fallback=None, **kwargs):
        seen["verb"] = verb
        seen["args"] = helper_args
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(wordpress.shell, "privileged", fake_privileged)

    wordpress.wp_update("/home/client/site/public_html", "core", "client", php_version="8.4")

    assert seen["verb"] == "wp-site"
    assert seen["args"][0] == "client"
    assert seen["args"][1] == "--php-version=8.4"
    assert seen["args"][2] == "core"


def test_every_wp_site_call_passes_a_php_version():
    """Including WordPress installation.

    `wp core install` writes to the database, so it fails the same way as
    `core update` when the CLI lacks mysqli - it just fails at create time
    instead, which is harder to connect to the cause.
    """
    from pathlib import Path

    lines = Path(wordpress.__file__).read_text(encoding="utf-8").splitlines()
    checked = 0
    for i, line in enumerate(lines):
        # Only the wp-site verb takes --php-version; other helper verbs on this
        # module (chmod, rm-site) also start with linux_user and must be left
        # alone, so match on the verb rather than on the argument shape.
        if '"wp-site"' not in line:
            continue
        window = "\n".join(lines[i : i + 4])
        if "helper_args=" not in window:
            continue
        checked += 1
        assert "_wp_php_flag" in window, f"wp-site call without a PHP version near line {i + 1}"

    assert checked >= 4, f"expected to find every wp-site call, saw {checked}"


def test_the_panel_logger_actually_has_somewhere_to_write():
    """uvicorn configures only the uvicorn.* loggers.

    Everything the panel logs goes to logging.getLogger("spanel"), which
    propagated to a root logger with no handler - so every logger.exception()
    in the codebase was discarded, including the one in the unhandled-error
    handler. A 500 reached the user while the journal stayed empty, which is
    what made this bug so slow to find.
    """
    from app.serve import _log_config

    config = _log_config()

    assert "spanel" in config["loggers"], "the panel's logger is not configured"
    assert config["loggers"]["spanel"]["handlers"], "configured, but with no handler"
