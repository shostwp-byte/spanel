"""The updater must apply its own changes in the run that brings them in.

bash reads a script as it runs, so update.sh re-execs a copy of itself from
/tmp - a copy of the *previous* release. Without a handover, anything an update
changes about updating itself lands one release late, which is how a server
once kept running the old panel start-up script after the release that replaced
it.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"


def _script() -> str:
    return UPDATE_SCRIPT.read_text(encoding="utf-8")


def _handover_block() -> str:
    script = _script()
    start = script.index("# bash reads a script as it runs")
    return script[start : script.index("# --- Sync code into APP_DIR")]


def test_the_updater_hands_over_to_the_one_from_the_new_release():
    block = _handover_block()
    assert 'exec /bin/bash "$stage2_copy"' in block
    assert 'cp "$SOURCE_DIR/installer/update.sh" "$stage2_copy"' in block
    # Only when it actually differs, so an unchanged updater keeps going.
    assert 'cmp -s "$SOURCE_DIR/installer/update.sh"' in block


def test_the_handover_cannot_loop():
    block = _handover_block()
    assert '[[ -z "${spanel_UPDATE_STAGE2:-}"' in block
    assert "spanel_UPDATE_STAGE2=1" in block


def test_the_second_stage_does_not_fetch_the_release_again():
    block = _handover_block()
    assert "SKIP_PULL=true" in block
    assert 'SOURCE_DIR="$SOURCE_DIR"' in block
    # The release name has to survive, or the panel would report the update as
    # coming from a temporary directory.
    assert 'spanel_UPDATE_REF_OVERRIDE="${UPDATE_REF:-}"' in _handover_block()
    assert 'UPDATE_REF="${spanel_UPDATE_REF_OVERRIDE:-local:${SOURCE_DIR}}"' in _script()


def test_the_handover_leaves_nothing_behind():
    script = _script()
    block = _handover_block()
    # exec skips the EXIT trap, so the second stage inherits the cleanup.
    assert 'spanel_UPDATE_PREVIOUS_COPY="${spanel_UPDATE_STABLE_COPY:-}"' in block
    assert 'RELEASE_WORK_DIR="${RELEASE_WORK_DIR:-}"' in block
    assert (
        'rm -f "${spanel_UPDATE_STABLE_COPY:-}" "${spanel_UPDATE_PREVIOUS_COPY:-}"' in script
    )
    # ...and the work dir it was handed must not be wiped out on the way in.
    assert 'RELEASE_WORK_DIR=""' not in script


def test_the_second_stage_does_not_snapshot_the_database_twice():
    script = _script()
    assert '''if [[ -n "${spanel_UPDATE_STAGE2:-}" ]]; then
  log "Continuing with the updater shipped in this release"
else
  log "Backing up SQLite DB before update"
  backup_db''' in script


def test_a_stuck_apt_dependency_does_not_leave_ipset_missing_forever():
    # A stuck package pin (seen in the field: libsystemd-shared) can leave
    # ipset "not going to be installed" - apt itself suggests --fix-broken.
    # An update that only warns and moves on means every server that hit this
    # once stays without an enforced firewall on every future update too.
    script = _script()
    start = script.index("# --- Firewall: move IP blocking")
    block = script[start : script.index("if id -u spanel", start)]
    assert "apt-get --fix-broken install -y" in block
    # The repair is a retry, not the only attempt - it must not run unless the
    # plain install actually failed.
    assert "if ! DEBIAN_FRONTEND=noninteractive apt-get install -y iptables ipset" in block


def test_a_fresh_install_repairs_the_same_stuck_apt_dependency():
    # install_base_packages hits the same class of stuck-pin failure a fresh
    # VPS image can ship with; without a repair+retry the whole install dies
    # on this one line instead of a targeted, recoverable failure.
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    start = script.index("install_base_packages() {")
    block = script[start : script.index("\n}", start)]
    # `apt_get`, not `apt-get`: every call goes through a wrapper that waits
    # out the dpkg lock a freshly-booted image holds. The repair-and-retry
    # this test is about is unchanged.
    assert "apt_get --fix-broken install -y" in block
    assert 'if ! apt_get install -y "${pkgs[@]}"; then' in block


def test_every_apt_call_waits_for_the_dpkg_lock():
    # A fresh Ubuntu runs unattended-upgrades on boot. Any apt call that races
    # it dies on "Could not get lock /var/lib/dpkg/lock-frontend" and leaves a
    # half-configured box, so no call site may reach apt-get directly.
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "fuser /var/lib/dpkg/lock-frontend" in script
    direct = [
        line.strip()
        for line in script.splitlines()
        # The wrapper body is the one place the real binary is invoked.
        if "apt-get " in line and not line.strip().startswith("#")
        and 'DEBIAN_FRONTEND=noninteractive apt-get "$@"' not in line
    ]
    assert direct == []
