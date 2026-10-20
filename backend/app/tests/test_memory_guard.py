"""Keeping a memory spike from taking the web server down with it.

Written after a real outage. On a 7.9 GB server with no swap, clamd had grown
to 2.1 GB; a customer's PHP cron job asked for memory; the kernel's OOM killer
took clamd, then an nginx worker. Twenty-six customer sites stopped answering
for two and a half minutes. Nothing was misconfigured - the machine had no
slack, and no policy about who should die first.

The guard does three things, and the tests here are mostly about the ways each
one could be worse than doing nothing:

  a cap clamd cannot live inside turns one outage into a restart loop, so the
  ceiling has a floor;

  a swapfile created on the wrong filesystem, or inside a container, or on a
  nearly full disk, is a new outage rather than a fix, so every one of those is
  a skip;

  and the whole thing runs inside an installer, so it must never be able to
  fail one.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
GUARD = PROJECT_ROOT / "installer" / "files" / "spanel-memory-guard"
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def _guard() -> str:
    return GUARD.read_text(encoding="utf-8")


def _code() -> str:
    """The script with its comment lines removed.

    The script explains both of the bugs below in its own comments, quoting the
    wrong code verbatim so the next reader understands why it is written the
    way it is. A test that greps the raw file would match that explanation and
    fail against a correct script.
    """
    return "\n".join(
        line for line in _guard().split("\n") if not line.lstrip().startswith("#")
    )


def test_the_guard_is_shipped_and_run_by_both_installers():
    install = INSTALL_SCRIPT.read_text(encoding="utf-8")
    update = UPDATE_SCRIPT.read_text(encoding="utf-8")
    assert "/usr/local/sbin/spanel-memory-guard" in install
    assert "/usr/local/sbin/spanel-memory-guard" in update, (
        "the servers that need this most are the ones already deployed, so the "
        "update path has to install and run it too"
    )
    for name, src in (("install.sh", install), ("update.sh", update)):
        assert re.search(r"/usr/local/sbin/spanel-memory-guard \|\| true", src), (
            f"{name} must not let the memory guard fail the run - it is a "
            "best-effort hardening step on somebody's production server"
        )


def test_the_script_has_unix_line_endings():
    """A CRLF shebang is 'bad interpreter' on every Linux box.

    This repository has been bitten by CRLF before, and a shell script is the
    one file type where it is fatal rather than cosmetic.
    """
    raw = GUARD.read_bytes()
    assert b"\r\n" not in raw, "spanel-memory-guard must be LF-only"
    assert raw.startswith(b"#!/usr/bin/env bash\n")


def test_the_clamd_ceiling_has_a_floor():
    """ClamAV's signature database is over a gigabyte resident.

    A MemoryMax below what clamd needs to start does not protect anything: it
    makes clamd die, restart, reload the database and die again, which is worse
    than the outage it was meant to prevent.
    """
    src = _guard()
    assert "(( max < 2048 )) && max=2048" in src, (
        "the cap must never fall below what the signature database needs"
    )
    assert "MemoryMax=${max}M" in src and "MemoryHigh=${high}M" in src, (
        "MemoryHigh should throttle and reclaim before MemoryMax kills"
    )


def test_the_ceiling_never_promises_more_than_the_machine_has():
    src = _guard()
    assert "max=$(( ram - 2048 ))" in src, (
        "on a small server a 35% cap can still exceed what is actually spare"
    )


def test_the_kernel_is_told_to_prefer_clamd_over_nginx():
    """In the incident it chose the other way round, by default."""
    src = _guard()
    assert "OOMScoreAdjust=500" in src, "clamd should be an early victim"
    assert "OOMScoreAdjust=-500" in src, "nginx should be a late one"
    clamd_block = src.split("ensure_clamav_limits()", 1)[1].split("ensure_nginx_protection", 1)[0]
    nginx_block = src.split("ensure_nginx_protection()", 1)[1]
    assert "OOMScoreAdjust=500" in clamd_block
    assert "OOMScoreAdjust=-500" in nginx_block


def test_clamd_comes_back_after_being_killed():
    """It stayed `failed` after the incident, so the scanner was simply off."""
    src = _guard()
    block = src.split("ensure_clamav_limits()", 1)[1].split("ensure_nginx_protection", 1)[0]
    assert "Restart=on-failure" in block


def test_the_packaged_clamav_dropin_is_not_clobbered():
    """extend.conf belongs to the Ubuntu clamav package, not to spanel.

    It carries the ExecStartPre lines that create /run/clamav. Overwriting it
    would stop clamd from starting at all.
    """
    src = _guard()
    assert "50-spanel-memory.conf" in src, "the guard writes its own numbered drop-in"
    written = re.findall(r'dropin\s+\S+\s+(\S+\.conf)', src)
    assert written, "no drop-in filenames found; this test is reading the wrong thing"
    assert "extend.conf" not in written, "the guard must never write extend.conf"


def test_swap_is_skipped_everywhere_it_would_do_harm():
    src = _guard()
    block = src.split("ensure_swap()", 1)[1].split("\n}", 1)[0]
    # A container shares the host kernel: swapon is a guaranteed failure.
    assert "systemd-detect-virt --container" in block
    # btrfs needs a nocow uncompressed file; zfs cannot host one safely.
    assert "ext2|ext3|ext4|xfs" in block
    # Somebody else's swap policy is not ours to override.
    assert "swapon --show" in block
    # Filling the disk trades one outage for a worse one.
    assert "size + 10240" in block


def test_a_failed_swapfile_is_cleaned_up_rather_than_left_behind():
    """A half-made swapfile is dead disk space on a customer's server."""
    src = _guard()
    block = src.split("ensure_swap()", 1)[1].split("\n}", 1)[0]
    assert block.count('rm -f "$SWAPFILE"') >= 3, (
        "allocation, mkswap and swapon can each fail; each has to clean up"
    )


def test_swappiness_is_lowered_so_swap_stays_an_emergency():
    """The default of 60 would page out a busy PHP-FPM pool's working set."""
    src = _guard()
    assert "vm.swappiness=10" in src


def test_there_is_a_way_out_for_an_operator_who_disagrees():
    src = _guard()
    assert "/etc/spanel/no-memory-guard" in src
    assert "/etc/spanel/no-swap" in src, (
        "swap is the invasive half; it needs its own opt-out"
    )


# --- two ways this script silently did nothing ------------------------------
#
# Both of these shipped in the first draft and were caught only by running it
# on a real server, where it printed "done", exited 0, and had changed nothing.
# That is the worst failure shape available to an installer step, so both are
# nailed down here.

def test_the_container_check_does_not_use_an_echo_fallback():
    """systemd-detect-virt prints "none" AND exits 1 when there is no container.

    So `$(systemd-detect-virt --container || echo none)` captures "none\nnone",
    which is not equal to "none", and every bare-metal server is mistaken for a
    container. Measured: the first draft skipped swap on a KVM guest.
    """
    src = _code()
    block = src.split("ensure_swap()", 1)[1].split("\n}", 1)[0]
    assert "systemd-detect-virt" in block
    assert "|| echo none" not in block, (
        "the exit code must be ignored by reading stdout, not patched over with "
        "a fallback that appends a second value"
    )
    assert re.search(r'virt="\$\(systemd-detect-virt --container 2>/dev/null\)"', block)


def test_no_unit_check_pipes_systemctl_into_grep_q():
    """`systemctl list-unit-files | grep -q ...` is wrong under `set -o pipefail`.

    grep -q exits as soon as it matches, systemctl takes SIGPIPE, and pipefail
    reports the pipeline as 141 - so a match reads as a miss. Measured: exit=141
    on a server where the unit was plainly installed.
    """
    src = _code()
    assert "set -uo pipefail" in src, "this test is only meaningful with pipefail on"
    assert not re.search(r"systemctl[^\n|]*\|\s*grep\s+-q", src), (
        "use `systemctl cat <unit>`, which needs no pipeline"
    )
    assert "systemctl cat clamav-daemon.service" in src
    assert "systemctl cat nginx.service" in src


def test_the_oom_preference_is_applied_to_processes_already_running():
    """A drop-in only takes effect at the next start.

    The server that most needs this is the one already running - the one that
    just had the outage. Measured: after writing the drop-in, nginx's live
    workers still read oom_score_adj=0.
    """
    src = _code()
    assert "apply_oom_now" in src
    assert "/proc/$pid/oom_score_adj" in src
    assert "apply_oom_now nginx -500 nginx" in src
    assert "apply_oom_now clamd 500 clamd" in src


def test_the_guard_is_run_only_after_it_has_been_installed():
    """install.sh ships the guard inside install_privileged_helper.

    The first version called it from setup_sftp_access, which main() runs six
    steps earlier - so on every fresh install the shell said "No such file or
    directory" and the `|| true` swallowed it. CI stayed green and the guard
    had never run. Caught by reading the smoke test's log, not by a test, which
    is why there is one now.
    """
    src = INSTALL_SCRIPT.read_text(encoding="utf-8")
    body = src.split("\nmain() {", 1)[1].split("\n}", 1)[0]

    installs_at = body.index("install_privileged_helper")
    runs_at = body.index("/usr/local/sbin/spanel-memory-guard")
    assert installs_at < runs_at, (
        "main() invokes spanel-memory-guard before install_privileged_helper has "
        "put it on disk; the `|| true` will hide that the guard never ran"
    )


def test_the_update_path_installs_the_guard_before_running_it():
    src = UPDATE_SCRIPT.read_text(encoding="utf-8")
    installs_at = src.index('install -m 0755 -o root -g root "$SOURCE_DIR/installer/files/spanel-memory-guard"')
    runs_at = src.index("/usr/local/sbin/spanel-memory-guard || true")
    assert installs_at < runs_at
