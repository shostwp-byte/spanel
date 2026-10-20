"""The PHP MySQL repair that could never have run.

update.sh carries a block whose whole job is to notice that a PHP version has
lost its MySQL extension and put it back - written after a customer VPS lost
php8.3-mysql and three WordPress sites with it. It calls `apt_get`, which is
defined in install.sh and nowhere in update.sh. bash answers "apt_get: command
not found", the `|| log WARNING` around it swallows that into a line nobody
reads, and the repair has never once worked:

    ==> PHP 8.5 is missing the MySQL extension; installing php8.5-mysql
    ==> WARNING: could not install php8.5-mysql; WordPress on PHP 8.5 will
        not reach its database

seen on a live server on 2026-09-23, where `apt-get install -s php8.5-mysql`
proved the package was perfectly installable.

The two scripts do not share a library, so anything update.sh calls, update.sh
has to define.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"

FUNCTION_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.M)


def _defined_in(path: Path) -> set[str]:
    return set(FUNCTION_RE.findall(path.read_text(encoding="utf-8")))


def test_update_defines_the_apt_wrapper_it_calls():
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "apt_get" in update, "the repair still needs an apt call"
    assert "apt_get() {" in update, (
        "update.sh calls apt_get; install.sh's copy is not in scope here"
    )


def test_it_waits_for_the_dpkg_lock_like_the_installer_does():
    """An update runs on a live box, where unattended-upgrades holds locks."""
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    body = update.split("apt_get() {")[1].split("\n}\n")[0]
    assert "lock-frontend" in body
    assert "DEBIAN_FRONTEND=noninteractive apt-get" in body


def test_update_borrows_nothing_else_from_the_installer():
    """Whatever else drifts in, it must not be a function only install.sh has."""
    update_text = UPDATE_SCRIPT.read_text(encoding="utf-8")
    installer_only = _defined_in(INSTALL_SCRIPT) - _defined_in(UPDATE_SCRIPT)
    for name in sorted(installer_only):
        # A bare call: the name at the start of a command, not part of a
        # longer word and not the definition itself.
        called = re.search(rf"(?m)^\s*{re.escape(name)}(\s|$)", update_text)
        assert not called, f"update.sh calls {name}, which only install.sh defines"


def test_a_failed_repair_says_why():
    """"could not install" on its own is what hid this for so long."""
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    block = update.split("is missing the MySQL extension; installing")[1][:800]
    assert 'apt_get install -y "php${php_ver}-mysql" >/dev/null 2>&1' not in block, (
        "discarding the output is how a missing function looked like a missing package"
    )
    assert "php_mysql_log" in block
