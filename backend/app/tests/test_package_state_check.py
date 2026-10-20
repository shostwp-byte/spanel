"""A removed package must not read as installed.

`dpkg -s <pkg>` exits 0 for a package that has been removed but kept its
config files - dpkg calls that state "deinstall ok config-files". The binaries
are gone; only /etc leftovers remain. Every caller in this repo means "is this
usable", so for such a package the honest answer is no.

Found on a live server right after v1.0.147 removed clamav-daemon: dpkg -s
still reported it, so `install_clamav_daemon` would have skipped `apt-get
install` and then failed on `systemctl enable --now clamav-daemon`, because
/usr/sbin/clamd no longer existed. Turning scan-on-upload back on would have
been broken on exactly the machines that had removed the daemon.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = {
    "spanel-helper.sh": PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh",
    "install.sh": PROJECT_ROOT / "installer" / "install.sh",
    "update.sh": PROJECT_ROOT / "installer" / "update.sh",
}

DEFINITION = re.compile(r"^pkg_installed\(\) \{", re.MULTILINE)
# A call, not the definition and not a mention inside a comment.
CALL = re.compile(r"^(?!#)(?!pkg_installed\(\)).*\bpkg_installed\s+\S", re.MULTILINE)


def _lines(text, pattern):
    return [text[:m.start()].count("\n") + 1 for m in pattern.finditer(text)]


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_no_script_asks_dpkg_s_whether_a_package_is_installed(name):
    for number, line in enumerate(SCRIPTS[name].read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "dpkg -s " not in stripped, (
            f"{name}:{number} uses `dpkg -s`, which says yes for a removed "
            f"package that still has config files:\n  {stripped}"
        )


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_check_asks_for_the_actual_state(name):
    text = SCRIPTS[name].read_text(encoding="utf-8")
    assert DEFINITION.search(text), f"{name} has no pkg_installed()"
    body = text[DEFINITION.search(text).start():]
    body = body[:body.index("\n}")]
    assert "dpkg-query -W -f='${Status}'" in body
    assert '"install ok installed"' in body, (
        "only that exact state means the binaries are on disk"
    )


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_the_definition_comes_before_every_call(name):
    """Bash tolerates the reverse; a reader does not."""
    text = SCRIPTS[name].read_text(encoding="utf-8")
    defined = _lines(text, DEFINITION)
    assert len(defined) == 1, f"{name} defines pkg_installed {len(defined)} times"
    calls = _lines(text, CALL)
    assert calls, f"{name} defines pkg_installed but never uses it"
    assert defined[0] < min(calls), (
        f"{name}: defined at line {defined[0]}, first used at line {min(calls)}"
    )


def test_the_clamav_daemon_install_would_reinstall_a_removed_package():
    """The bug this file exists for, at its exact site."""
    text = SCRIPTS["spanel-helper.sh"].read_text(encoding="utf-8")
    start = text.index("install_clamav_daemon() {")
    body = text[start:text.index("\n}\n", start)]
    assert "if ! pkg_installed clamav-daemon; then" in body
    assert "apt-get install -y clamav-daemon" in body
