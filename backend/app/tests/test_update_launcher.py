"""spanel-update fetches the updater of the release it installs, then runs it.

It used to be a full copy of update.sh from the release already installed, so
an update ran the steps of the release it was leaving. A server built from a
July image updated straight to v1.0.170 with its July steps: the iptables
firewall migration never ran, UFW stayed in charge, and the panel said the
firewall was off (2026-09-29). The operator's rule since: the command on a
server only fetches the updater from git, so what an update installs or
removes is decided in the repository.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = (PROJECT_ROOT / "installer" / "files" / "spanel-update").read_text(encoding="utf-8")
UPDATE = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
INSTALL = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")


def test_the_launcher_fetches_the_updater_of_the_ref_being_installed():
    assert "raw.githubusercontent.com/${url#https://github.com/}/${url_ref}/installer/update.sh" in LAUNCHER
    assert 'url_ref="refs/tags/${ref}"' in LAUNCHER and 'url_ref="refs/tags/${tag}"' in LAUNCHER
    assert 'url_ref="refs/heads/${branch}"' in LAUNCHER
    # The newest release, found as update.sh finds it.
    assert 'git ls-remote --tags --refs "$repo_url" "refs/tags/${pattern}"' in LAUNCHER
    assert 'RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*' in LAUNCHER and "sort -V | tail -n 1" in LAUNCHER


def test_the_launcher_runs_nothing_it_has_not_checked():
    fetch = LAUNCHER.index('curl -fsSL')
    run = LAUNCHER.index('/bin/bash "$script" "${args[@]}"')
    checks = LAUNCHER[fetch:run]
    assert '[[ "$(head -n 1 "$script")" != "#!/usr/bin/env bash" ]]' in checks
    assert "grep -q 'spanel_UPDATE_STABLE_COPY' \"$script\"" in checks
    assert 'bash -n "$script"' in checks
    # The ref names a git ref, nothing else.
    assert '[[ "$ref" =~ ^[A-Za-z0-9._/-]+$ && "$ref" != -* && "$ref" != *..* ]]' in LAUNCHER


def test_the_launcher_hands_on_every_argument_and_says_why_it_failed():
    assert 'local args=("$@")' in LAUNCHER and '"${args[@]}"' in LAUNCHER
    # A failure the panel's Update page can show, rather than an update
    # that seems to run forever.
    assert 'last_update_status="failed"' in LAUNCHER and "UPDATE_STATE_FILE" in LAUNCHER


def test_the_launcher_can_be_replaced_while_it_runs():
    # bash reads a script as it goes: all of it sits in main, called and
    # exited on one line, so the update it starts may replace the file.
    lines = [line for line in LAUNCHER.strip().splitlines() if line.strip() and not line.startswith("#")]
    assert lines[-1] == 'main "$@"; exit $?'
    assert re.search(r"^main\(\) \{$", LAUNCHER, re.M)


def test_install_and_update_leave_the_launcher_as_the_command():
    assert 'install -m 0755 -o root -g root "${SCRIPT_DIR}/files/spanel-update" /usr/local/sbin/spanel-update' in INSTALL
    assert '"${SCRIPT_DIR}/update.sh" /usr/local/sbin/spanel-update' not in INSTALL
    body = UPDATE.split("install_update_command() {")[1].split("\n}\n")[0]
    assert '"$SOURCE_DIR/installer/files/spanel-update" /usr/local/sbin/spanel-update' in body
    # Twice: once the source is readable, and again with the other commands.
    assert UPDATE.count("\ninstall_update_command\n") == 2
