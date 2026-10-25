"""The WHMCS server module: PHP syntax, its behaviour (through the PHP
harness in tests/php) and that every API path it calls exists."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import api_testkit as kit

MODULE_DIR = kit.REPO_ROOT / "modules"
SPANEL_PHP = MODULE_DIR / "servers" / "spanel" / "spanel.php"
HARNESS = kit.REPO_ROOT / "tests" / "php" / "spanel_module_test.php"


def _php() -> str | None:
    return os.environ.get("PHP_BIN") or shutil.which("php")


needs_php = pytest.mark.skipif(_php() is None, reason="php CLI not installed")


@needs_php
@pytest.mark.parametrize("path", sorted(MODULE_DIR.rglob("*.php")) + sorted((kit.REPO_ROOT / "tests" / "php").glob("*.php")),
                         ids=lambda p: p.name)
def test_php_files_lint(path):
    result = subprocess.run([_php(), "-l", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@needs_php
def test_module_harness_passes():
    result = subprocess.run([_php(), str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK")


def _module_paths() -> set[str]:
    source = SPANEL_PHP.read_text(encoding="utf-8")
    # '/accounts/' alone is the prefix spanel_account_path() builds on.
    paths = {
        "/api/provisioning/v1" + p
        for p in re.findall(r"SPANEL_API \. '([^']+)'", source)
        if not p.endswith("/")
    }
    for suffix in re.findall(r"spanel_account_path\(\$params(?:, '([^']*)')?\)", source):
        paths.add("/api/provisioning/v1/accounts/{external_id}" + suffix)
    return paths


def test_every_path_the_module_calls_exists():
    app = kit.build_app()
    routes = kit.app_paths(app)
    paths = _module_paths()
    assert "/api/provisioning/v1/accounts/{external_id}/sftp-password" in paths
    missing = sorted(p for p in paths if p not in routes)
    assert not missing, f"module calls paths the API does not serve: {missing}"


def test_module_stays_php74_compatible():
    """No syntax newer than PHP 7.4: WHMCS 8.x still runs there."""
    source = SPANEL_PHP.read_text(encoding="utf-8") + (MODULE_DIR / "servers" / "spanel" / "hooks.php").read_text(encoding="utf-8")
    for pattern, what in [
        (r"\bmatch\s*\(", "match expression (8.0)"),
        (r"\?->", "nullsafe operator (8.0)"),
        (r"\bstr_contains\(|\bstr_starts_with\(|\bstr_ends_with\(", "PHP 8 string functions"),
        (r"\benum\s+\w+", "enums (8.1)"),
        (r"\breadonly\b", "readonly (8.1)"),
        (r"\w+:\s*\$", "named arguments (8.0)"),
    ]:
        assert not re.search(pattern, source), what
