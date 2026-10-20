"""PHP extensions: imap in the default set, and a table to install the rest.

The operator asked for php-imap on every PHP version and a way to add
extensions from the PHP config page (2026-09-27).
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import php

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
API = (PROJECT_ROOT / "backend" / "app" / "api" / "maintenance.py").read_text(encoding="utf-8")


def test_imap_is_in_every_default_set():
    """A fresh install, a PHP version added from the panel, and the dry-run
    fallback all install the same set; imap is in each."""
    assert '"php${version}-imap"' in INSTALL.split("install_php() {")[1].split("\n}\n")[0]
    assert '"php${version}-imap"' in HELPER.split("install_php_version() {")[1].split("\n}\n")[0]
    assert 'f"php{php_version}-imap"' in (PROJECT_ROOT / "backend" / "app" / "services" / "php.py").read_text(encoding="utf-8")


def test_the_helper_and_the_page_offer_the_same_list():
    helper_list = re.search(r"^PHP_EXTENSION_WHITELIST=\(([^)]*)\)", HELPER, re.M).group(1).split()
    assert tuple(helper_list) == php.PHP_EXTENSIONS


def test_nothing_on_the_list_brings_a_second_web_server_or_a_sapi():
    """php<v>-cgi and libapache2-mod-php<v> sit in the same repository and pull
    in Apache; fpm and cli are whole PHP installs, not extensions."""
    for name in ("cgi", "fpm", "cli", "dev", "phpdbg", "embed", "litespeed", "xdebug"):
        assert name not in php.PHP_EXTENSIONS, name
    assert all(re.fullmatch(r"[a-z0-9]{2,20}", name) for name in php.PHP_EXTENSIONS)


def test_the_helper_checks_the_list_and_the_version_itself():
    body = HELPER.split("install_php_extension() {")[1].split("\n}\n")[0]
    assert 'require_php_version "$version"' in body
    assert '[[ " ${PHP_EXTENSION_WHITELIST[*]} " == *" ${ext} "* ]] || deny' in body
    assert '[[ -f "/etc/php/${version}/fpm/php-fpm.conf" ]] || deny' in body
    assert 'apt-mark manual "$pkg"' in body
    assert 'systemctl reload "php${version}-fpm"' in body
    assert "php-ext-install)" in HELPER


def test_each_cell_says_what_is_there(monkeypatch):
    outputs = {
        "dpkg-query": "php8.3-imap\tinstall ok installed\nphp8.3-soap\tdeinstall ok config-files\nphp8.4-fpm\tinstall ok installed\n",
        "apt-cache": "php8.3-imap php8.3-soap php8.4-imap php8.4-soap php8.4-fpm\n",
        "php8.3": "[PHP Modules]\nimap\nZend OPcache\n",
        "php8.4": "[PHP Modules]\ncore\n",
    }
    monkeypatch.setattr(php, "list_installed_php", lambda: ["8.3", "8.4"])
    monkeypatch.setattr(php, "_command_output", lambda args: outputs[args[0]])
    table = php.list_extensions()
    rows = {row["name"]: row["versions"] for row in table["extensions"]}
    assert table["versions"] == ["8.3", "8.4"]
    assert rows["imap"] == {"8.3": "installed", "8.4": "available"}
    # Removed, config files left behind: not installed.
    assert rows["soap"] == {"8.3": "available", "8.4": "available"}
    # Loaded by PHP without a package of its own.
    assert rows["opcache"]["8.3"] == "builtin"
    assert rows["yaml"] == {"8.3": "unavailable", "8.4": "unavailable"}


def test_install_refuses_what_the_panel_does_not_offer(monkeypatch):
    calls = []
    monkeypatch.setattr(php, "list_installed_php", lambda: ["8.4"])
    monkeypatch.setattr(php.shell, "privileged", lambda cmd, helper_args=None, **kw: calls.append((cmd, helper_args)) or SimpleNamespace(returncode=0, stdout="Installed php8.4-imap; php8.4-fpm reloaded.\n", stderr=""))
    with pytest.raises(ValueError, match="not installed"):
        php.install_extension("8.1", "imap")
    with pytest.raises(ValueError, match="not offered"):
        php.install_extension("8.4", "cgi")
    assert not calls
    assert php.install_extension("8.4", "imap") == "Installed php8.4-imap; php8.4-fpm reloaded."
    assert calls == [("php-ext-install", ["8.4", "imap"])]


def test_a_failed_apt_run_is_reported(monkeypatch):
    monkeypatch.setattr(php, "list_installed_php", lambda: ["8.4"])
    monkeypatch.setattr(php.shell, "privileged", lambda cmd, helper_args=None, **kw: SimpleNamespace(returncode=1, stdout="", stderr="spanel-helper: php8.4-imap is not in the package repositories\n"))
    with pytest.raises(RuntimeError, match="not in the package repositories"):
        php.install_extension("8.4", "imap")


def test_the_endpoints_are_admin_only_and_audited():
    for route in ('@router.get("/php-extensions")', '@router.post("/php-extensions")'):
        body = API.split(route)[1].split("\n@router")[0]
        assert "ensure_role(current_user.role, Role.admin)" in body, route
    post = API.split('@router.post("/php-extensions")')[1].split("\n@router")[0]
    assert 'log_action(db, current_user.id, "php_extension_install"' in post
