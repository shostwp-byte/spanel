"""Kernel disk quota: fstab editing, status, enabling, per-account limits.

findmnt, setquota, quotacheck, quotaon, apt-get and mount are stubs here;
whether the kernel then really enforces the quota (quotaon on a remounted
ext4, the quota_v2 module on a cloud kernel) needs a real server.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from _isolation_bash import BASH, REPO_ROOT, helper_functions, run_bash

sys.path.insert(0, str(REPO_ROOT / "backend"))

needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")

QUOTA_FUNCTIONS = (
    "quota_mount_field", "quota_options_active", "quota_wanted_options", "fstab_quota_options",
    "fstab_with_quota_options", "quota_state", "quota_status", "quota_enable", "quota_limits",
    "apply_kernel_quota",
)

STUBS = r'''
deny() { echo "deny: $*" >&2; exit 1; }
record() { echo "$*" >>"$ROOT/calls.log"; }
findmnt() {
  case "$*" in
    --help) echo "  --verify  verify mount table content" ;;
    *--verify*) record findmnt-verify; return 0 ;;
    *"-no TARGET"*) echo "${FAKE_MP:-/}" ;;
    *"-no FSTYPE"*) echo "${FAKE_FS:-ext4}" ;;
    *"-no SOURCE"*) echo /dev/vda1 ;;
    *"-no OPTIONS"*) echo "${FAKE_OPTS:-rw,relatime}" ;;
  esac
}
setquota() { record "setquota $*"; }
quotacheck() { record "quotacheck $*"; }
quotaon() { if [[ "$1" == -pu ]]; then echo "user quota on $2 (/dev/vda1) is ${FAKE_QUOTA_ON:-off}"; else record "quotaon $*"; fi; }
apt-get() { record "apt-get $*"; }
systemctl() { record "systemctl $*"; }
mount() { record "mount $*"; }
reboot() { record reboot; }
logger() { :; }
'''

FSTAB = (
    "# /etc/fstab: static file system information.\n"
    "UUID=1111-aaaa\t/\text4\tdefaults\t0 1\n"
    "UUID=2222-bbbb /boot/efi vfat umask=0077 0 1\n"
    "/swap.img\tnone\tswap\tsw\t0\t0\n"
)


def _script(root: Path, body: str) -> str:
    return "\n".join([
        "set -euo pipefail",
        f"ROOT='{root.as_posix()}'",
        'QUOTA_HOME="/home"',
        'FSTAB_FILE="$ROOT/fstab"',
        'touch "$ROOT/calls.log"',
        helper_functions(*QUOTA_FUNCTIONS),
        STUBS,
        body,
        "",
    ])


def _calls(root: Path) -> list[str]:
    return (root / "calls.log").read_text(encoding="utf-8").splitlines()


def _kv(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


@needs_bash
def test_quota_option_detection(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, r'''
for o in rw,relatime rw,usrquota usrjquota=aquota.user,jqfmt=vfsv1,rw rw,uquota rw,grpquota rw,noquota defaults,quota; do
  if quota_options_active "$o"; then echo yes; else echo no; fi
done
''')).stdout.split()
    assert out == ["no", "yes", "yes", "yes", "no", "no", "yes"]


@needs_bash
def test_fstab_gets_quota_options_on_the_right_entry_only(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(tmp_path, _script(tmp_path, r'''
fstab_with_quota_options "$FSTAB_FILE" / ext4 >"$ROOT/ext4.out"
fstab_with_quota_options "$ROOT/ext4.out" / ext4 >"$ROOT/again.out"
fstab_with_quota_options "$FSTAB_FILE" / xfs >"$ROOT/xfs.out"
rc=0; fstab_with_quota_options "$FSTAB_FILE" /home ext4 >/dev/null || rc=$?
echo "missing=$rc"
''')).stdout
    assert "missing=2" in out
    ext4 = (tmp_path / "ext4.out").read_text(encoding="utf-8").splitlines()
    original = FSTAB.splitlines()
    assert ext4[1] == "UUID=1111-aaaa\t/\text4\tdefaults,usrjquota=aquota.user,jqfmt=vfsv1\t0\t1"
    # Everything else byte for byte, comment included.
    assert [ext4[0]] + ext4[2:] == [original[0]] + original[2:]
    assert (tmp_path / "again.out").read_text(encoding="utf-8") == (tmp_path / "ext4.out").read_text(encoding="utf-8")
    assert (tmp_path / "xfs.out").read_text(encoding="utf-8").splitlines()[1].split("\t")[3] == "defaults,usrquota"


@needs_bash
def test_quota_state_matrix(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, r'''
quota_state ext4 / yes yes yes
quota_state ext4 / yes yes no
quota_state ext4 / no yes no
quota_state ext4 /home no no no
quota_state xfs / no yes no
quota_state xfs /home yes no no
quota_state btrfs / no no no
''')).stdout.split()
    assert out == ["active", "needs-quotaon", "remount-required", "not-configured",
                   "kernel-cmdline-required", "active", "unsupported"]


@needs_bash
def test_enable_edits_fstab_and_asks_for_a_remount_instead_of_doing_it(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(tmp_path, _script(tmp_path, "quota_enable"))
    status = _kv(out.stdout)
    assert status["state"] == "remount-required" and status["mode"] == "app"
    assert status["backup"].startswith(f"{tmp_path.as_posix()}/fstab.spanel-")
    assert Path(status["backup"]).read_text(encoding="utf-8") == FSTAB
    assert "usrjquota=aquota.user,jqfmt=vfsv1" in (tmp_path / "fstab").read_text(encoding="utf-8")
    calls = _calls(tmp_path)
    assert "findmnt-verify" in calls
    assert not any(c.startswith(("mount", "reboot", "quotacheck", "quotaon")) for c in calls)
    # Run again: fstab is not touched twice.
    run_bash(tmp_path, _script(tmp_path, "quota_enable"))
    assert (tmp_path / "fstab").read_text(encoding="utf-8").count("usrjquota") == 1


@needs_bash
def test_enable_switches_quota_on_once_the_options_are_mounted(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    run_bash(tmp_path, _script(tmp_path, "quota_enable"),
             env={"FAKE_OPTS": "rw,relatime,usrjquota=aquota.user,jqfmt=vfsv1"})
    calls = _calls(tmp_path)
    assert "quotacheck -cum /" in calls
    assert "quotaon -u /" in calls
    assert not any(c.startswith(("mount", "reboot")) for c in calls)


@needs_bash
def test_enable_refuses_filesystems_without_user_quota(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    result = run_bash(tmp_path, _script(tmp_path, "quota_enable"), check=False, env={"FAKE_FS": "btrfs"})
    assert result.returncode != 0 and "ext4 or xfs" in result.stderr
    assert (tmp_path / "fstab").read_text(encoding="utf-8") == FSTAB


@needs_bash
def test_xfs_root_is_reported_not_edited(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB.replace("ext4", "xfs"), encoding="utf-8")
    out = run_bash(tmp_path, _script(tmp_path, "quota_enable"), env={"FAKE_FS": "xfs"})
    assert _kv(out.stdout)["state"] == "kernel-cmdline-required"
    assert "usrquota" not in (tmp_path / "fstab").read_text(encoding="utf-8")


@needs_bash
def test_account_limits_cover_blocks_and_inodes(tmp_path):
    out = run_bash(tmp_path, _script(tmp_path, r'''
quota_limits 5120 250000
quota_limits 0 0
apply_kernel_quota alice 1024 1000
FAKE_OPTS=rw,usrjquota=aquota.user,jqfmt=vfsv1 apply_kernel_quota alice 1024 1000
FAKE_OPTS=rw,usrjquota=aquota.user,jqfmt=vfsv1 apply_kernel_quota bob 0 0
''')).stdout.splitlines()
    assert out == ["4980736 5242880 237500 250000", "0 0 0 0"]
    assert _calls(tmp_path) == ["setquota -u alice 996147 1048576 950 1000 /", "setquota -u bob 0 0 0 0 /"]


# --- backend -----------------------------------------------------------------------

def test_status_parsing_names_the_enforcing_mode():
    from app.services import disk_quota

    active = disk_quota.parse_status("mode=kernel\nstate=active\nmount=/\nfstype=ext4\nquota_on=yes\nhint=ok\n")
    assert active["mode"] == "kernel" and active["quota_on"] and not active["can_enable"]
    pending = disk_quota.parse_status("state=remount-required\nfstab_options=yes\nbackup=/etc/fstab.spanel-1\n")
    assert pending["mode"] == "app" and pending["fstab_backup"] == "/etc/fstab.spanel-1" and not pending["can_enable"]
    assert disk_quota.parse_status("state=not-configured\n")["can_enable"] is True
    # A line the helper never prints cannot claim kernel mode.
    assert disk_quota.parse_status("mode=kernel\nstate=bogus\n") | {} == disk_quota.parse_status("state=unknown")


def test_every_account_gets_its_limit_once_kernel_quota_turns_up(tmp_path):
    from app.services import disk_quota, resource_monitor

    users = [SimpleNamespace(id=1, role="admin"), SimpleNamespace(id=2, role="end_user"), SimpleNamespace(id=3, role="reseller")]
    db = SimpleNamespace(query=lambda model: SimpleNamespace(all=lambda: users))
    marker = tmp_path / "applied"
    active = {"mode": "kernel", "mount": "/", "device": "/dev/vda1"}
    with patch.object(resource_monitor, "apply_user_cgroup_limits_async") as apply_async:
        assert disk_quota.reconcile_account_limits(db, active, marker) == 2
        assert disk_quota.reconcile_account_limits(db, active, marker) == 0
        assert disk_quota.reconcile_account_limits(db, {"mode": "app"}, marker) == 0
        assert not marker.exists()
        assert disk_quota.reconcile_account_limits(db, active, marker) == 2
    assert [c.args[0] for c in apply_async.call_args_list] == [2, 3, 2, 3]


def test_limits_apply_passes_the_inode_limit_to_the_helper():
    from app.services import resource_monitor

    user = SimpleNamespace(
        username="alice", role="end_user", cpu_quota_percent=100, memory_high_mb=768, memory_max_mb=1024,
        tasks_max=80, io_read_mbps=10, io_write_mbps=10, iops_read_max=100, iops_write_max=100,
        storage_limit_mb=5120, inode_limit=250000,
    )
    with patch.object(resource_monitor.shell, "privileged") as privileged:
        assert resource_monitor.apply_user_cgroup_limits(user) is True
    command, = privileged.call_args.args
    args = privileged.call_args.kwargs["helper_args"]
    assert command == "user-limits-apply"
    assert args[0] == "alice" and args[-2:] == ["5120", "250000"] and len(args) == 11
