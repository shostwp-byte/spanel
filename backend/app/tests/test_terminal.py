"""Tests for terminal service."""

import pytest

from app.services import terminal as terminal_service
from app.services.terminal import (
    ALLOWED_COMMANDS,
    MAX_OUTPUT_BYTES,
    CommandResult,
    _truncate_output,
    exec_batch,
    exec_command,
    default_cwd,
    is_command_allowed,
    resolve_cwd,
    split_command,
)


class TestIsCommandAllowed:
    """Tests for command whitelist validation."""

    def test_allowed_commands(self):
        """All whitelisted commands should be allowed."""
        allowed = [
            "php",
            "composer",
            "wp",
            "node",
            "npm",
            "yarn",
            "git",
            "ls",
            "cat",
            "mkdir",
            "rm",
            "cp",
            "mv",
            "chmod",
            "chown",
            "pwd",
            "touch",
            "grep",
            "find",
            "tar",
            "zip",
            "unzip",
            "curl",
            "wget",
            "diff",
            "head",
            "tail",
            "less",
        ]
        for cmd in allowed:
            assert is_command_allowed(cmd), f"Command {cmd} should be allowed"

    def test_disallowed_commands(self):
        """Commands not in whitelist should not be allowed.

        Note: is_command_allowed only checks the first word (the executable).
        Full command safety is enforced in spanel-helper.sh.
        """
        dangerous = [
            "dd if=/dev/zero of=/dev/sda",
            "nc -e /bin/bash",
            "python -c 'import os'",
            "bash -c 'rm -rf /'",
            "sh -c 'cat /etc/passwd'",
            "sudo su",
            "vim",
            "nano",
            "emacs",
            "chroot",
        ]
        for cmd in dangerous:
            assert not is_command_allowed(cmd), f"Command {cmd} should NOT be allowed"

    def test_empty_command(self):
        """Empty command should not be allowed."""
        assert not is_command_allowed("")
        assert not is_command_allowed("   ")

    def test_artisan_command(self):
        """Artisan should be allowed (it's a PHP script executed via php)."""
        assert is_command_allowed("artisan")

    def test_multi_word_command(self):
        """Only the executable is checked; args are passed without a shell."""
        assert is_command_allowed("php artisan migrate --force")
        assert split_command("composer create-project laravel/laravel .") == [
            "composer",
            "create-project",
            "laravel/laravel",
            ".",
        ]

    def test_invalid_shell_syntax(self):
        """Malformed quoting is rejected before reaching the helper."""
        assert not is_command_allowed("composer install '")

    def test_phpunit_command(self):
        """PHPUnit should be allowed."""
        assert is_command_allowed("phpunit")

    def test_wp_cli_command(self):
        """WP-CLI should be allowed in website terminals."""
        assert is_command_allowed("wp option get siteurl")

    def test_whitelist_size(self):
        """Whitelist should contain a reasonable number of commands."""
        assert len(ALLOWED_COMMANDS) >= 20
        assert len(ALLOWED_COMMANDS) <= 80

    def test_php_toolchain_commands_allowed(self):
        """Everything a WordPress / Laravel / PHP site needs day to day."""
        for cmd in ("wp plugin list", "php artisan migrate", "composer install",
                    "artisan queue:work", "phpunit --testsuite=Unit", "npm run build"):
            assert is_command_allowed(cmd), f"{cmd} should be allowed"

    def test_text_utilities_allowed(self):
        """Log and config wrangling that PHP work needs."""
        for cmd in ("sed -n 1,20p wp-config.php", "awk '{print $1}' access.log",
                    "wc -l error.log", "sort -u list.txt", "uniq list.txt",
                    "stat index.php", "file index.php", "rmdir cache"):
            assert is_command_allowed(cmd), f"{cmd} should be allowed"

    def test_shell_builtins_still_blocked(self):
        """Adding utilities must not open a shell escape."""
        for cmd in ("bash", "sh", "zsh", "python3", "perl", "ruby", "ssh",
                    "su", "sudo", "systemctl", "crontab", "mysql", "ln"):
            assert not is_command_allowed(cmd), f"{cmd} should NOT be allowed"


class TestTruncateOutput:
    """Tests for output truncation."""

    def test_small_output_not_truncated(self):
        """Small output should not be truncated."""
        small = "Hello, World!"
        assert _truncate_output(small) == small

    def test_exact_limit_not_truncated(self):
        """Output at exact limit should not be truncated."""
        exact = "x" * MAX_OUTPUT_BYTES
        assert _truncate_output(exact) == exact

    def test_large_output_truncated(self):
        """Large output should be truncated with notice."""
        large = "x" * (MAX_OUTPUT_BYTES + 1000)
        result = _truncate_output(large)
        assert "..." in result
        assert len(result.encode("utf-8")) <= MAX_OUTPUT_BYTES + 50  # Small buffer for notice


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_command_result_fields(self):
        """CommandResult should have correct fields."""
        result = CommandResult(exit_code=0, stdout="hello", stderr="")
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == ""

    def test_command_result_with_stderr(self):
        """CommandResult should capture stderr."""
        result = CommandResult(exit_code=1, stdout="", stderr="error message")
        assert result.exit_code == 1
        assert result.stderr == "error message"


class TestExecCommand:
    def test_exec_passes_cwd_and_split_argv(self, monkeypatch):
        captured = {}

        def fake_privileged(helper_command, helper_args=None, check=True, **kwargs):
            captured["helper_command"] = helper_command
            captured["helper_args"] = helper_args
            captured["check"] = check
            return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        monkeypatch.setattr(terminal_service.shell, "privileged", fake_privileged)
        result = exec_command("siteuser", "php artisan migrate --force", cwd="/home/siteuser/example.com")

        assert result.exit_code == 0
        assert captured == {
            "helper_command": "terminal-exec",
            "helper_args": [
                "siteuser",
                "/home/siteuser/example.com",
                "--timeout=900",
                "php",
                "artisan",
                "migrate",
                "--force",
            ],
            "check": False,
        }

    def test_exec_passes_php_version_before_command(self, monkeypatch):
        captured = {}

        def fake_privileged(helper_command, helper_args=None, check=True, **kwargs):
            captured["helper_command"] = helper_command
            captured["helper_args"] = helper_args
            captured["check"] = check
            return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        monkeypatch.setattr(terminal_service.shell, "privileged", fake_privileged)
        result = exec_command(
            "siteuser",
            "composer install --no-dev",
            cwd="/home/siteuser/example.com/public_html",
            php_version="8.3",
        )

        assert result.exit_code == 0
        assert captured == {
            "helper_command": "terminal-exec",
            "helper_args": [
                "siteuser",
                "/home/siteuser/example.com/public_html",
                "--timeout=900",
                "--php-version=8.3",
                "composer",
                "install",
                "--no-dev",
            ],
            "check": False,
        }

    def test_exec_allows_wp_cli_with_php_version(self, monkeypatch):
        captured = {}

        def fake_privileged(helper_command, helper_args=None, check=True, **kwargs):
            captured["helper_command"] = helper_command
            captured["helper_args"] = helper_args
            captured["check"] = check
            return type("Result", (), {"returncode": 0, "stdout": "https://example.com\n", "stderr": ""})()

        monkeypatch.setattr(terminal_service.shell, "privileged", fake_privileged)
        result = exec_command(
            "siteuser",
            "wp option get siteurl",
            cwd="/home/siteuser/example.com/public_html",
            php_version="8.3",
        )

        assert result.exit_code == 0
        assert captured == {
            "helper_command": "terminal-exec",
            "helper_args": [
                "siteuser",
                "/home/siteuser/example.com/public_html",
                "--timeout=900",
                "--php-version=8.3",
                "wp",
                "option",
                "get",
                "siteurl",
            ],
            "check": False,
        }

    def test_exec_rejects_cd_outside_session(self):
        result = exec_command("siteuser", "cd public_html", cwd="/home/siteuser/example.com")
        assert result.exit_code == 2


class TestResolveCwd:
    def test_default_cwd_prefers_public_html(self, tmp_path):
        root = tmp_path / "example.com"
        public = root / "public_html"
        public.mkdir(parents=True)
        assert default_cwd(str(root)) == str(public.resolve())

    def test_default_cwd_falls_back_to_site_root(self, tmp_path):
        root = tmp_path / "example.com"
        root.mkdir()
        assert default_cwd(str(root)) == str(root.resolve())

    def test_resolve_inside_site(self, tmp_path):
        root = tmp_path / "example.com"
        public = root / "public_html"
        public.mkdir(parents=True)
        assert resolve_cwd(str(root), str(root), "public_html") == str(public.resolve())

    def test_rejects_outside_site(self, tmp_path):
        root = tmp_path / "example.com"
        other = tmp_path / "other"
        root.mkdir()
        other.mkdir()
        with pytest.raises(ValueError):
            resolve_cwd(str(root), str(root), "../other")
