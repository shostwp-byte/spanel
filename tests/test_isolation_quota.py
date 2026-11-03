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
    *--verify*) record findmnt-verify; return "${FAKE_FINDMNT_VERIFY_RC:-0}" ;;
    *"-no TARGET"*) echo "${FAKE_MP:-/}" ;;
    *"-no FSTYPE"*) echo "${FAKE_FS:-ext4}" ;;
    *"-no SOURCE"*) echo "${FAKE_SRC:-/dev/vda1}" ;;
    *"-no MAJ:MIN"*"-T /home"*) echo "${FAKE_HOME_MAJMIN:-252:1}" ;;
    *"-no MAJ:MIN"*"-T /"*) echo "${FAKE_ROOT_MAJMIN:-252:1}" ;;
    *"-no OPTIONS"*) echo "${FAKE_OPTS:-rw,relatime}" ;;
  esac
}
setquota() { record "setquota $*"; }
quotacheck() {
  record "quotacheck $*"
  if [[ "$1" == -cum && "${FAKE_QUOTACHECK_CUM_RC:-0}" != "0" ]]; then
    return "${FAKE_QUOTACHECK_CUM_RC}"
  fi
  if [[ "$1" == -cfum && "${FAKE_QUOTACHECK_CFUM_RC:-0}" != "0" ]]; then
    echo "quotacheck: superblock quota active" >&2
    return "${FAKE_QUOTACHECK_CFUM_RC}"
  fi
}
quotaoff() { record "quotaoff $*"; }
quotaon() {
  if [[ "$1" == -pu ]]; then
    if [[ -f "$ROOT/quota_switched_on" ]]; then
      echo "user quota on $2 (/dev/vda1) is on"
    else
      echo "user quota on $2 (/dev/vda1) is ${FAKE_QUOTA_ON:-off}"
    fi
  else
    record "quotaon $*"
    local count_file="$ROOT/quotaon_u_attempts"
    local attempt=0
    [[ -f "$count_file" ]] && attempt="$(cat "$count_file")"
    attempt=$(( attempt + 1 ))
    echo "$attempt" >"$count_file"
    if (( attempt <= ${FAKE_QUOTAON_FAILS_BEFORE_OK:-0} )); then
      echo "${FAKE_QUOTAON_ERR:-quotaon: Quota format not supported in kernel.}" >&2
      return 1
    fi
    touch "$ROOT/quota_switched_on"
  fi
}
apt-get() { record "apt-get $*"; }
modinfo() { record "modinfo $*"; return "${FAKE_MODINFO_RC:-0}"; }
modprobe() { record "modprobe $*"; }
depmod() { record "depmod $*"; }
uname() { echo "${FAKE_UNAME:-6.8.0-100-generic}"; }
systemctl() { record "systemctl $*"; }
mount() {
  case "$*" in
    *-fav*) record fstab-mount-verify; return "${FAKE_MOUNT_FAV_RC:-0}" ;;
    *) record "mount $*" ;;
  esac
}
reboot() { record reboot; }
logger() { if [[ "${RECORD_LOGGER:-0}" == 1 ]]; then record "logger $*"; fi; }
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
        'QUOTA_MODULES_CONF="$ROOT/modules-load.d/spanel-quota.conf"',
        'QUOTA_LOCK_FILE="$ROOT/quota.lock"',
        'QUOTA_ROOT_PREFIX="$ROOT"',
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
    assert "fstab-mount-verify" in calls
    assert not any(c.startswith(("mount", "reboot", "quotacheck", "quotaon")) for c in calls)
    # Run again: fstab is not touched twice.
    run_bash(tmp_path, _script(tmp_path, "quota_enable"))
    assert (tmp_path / "fstab").read_text(encoding="utf-8").count("usrjquota") == 1


@needs_bash
def test_enable_warns_instead_of_denying_when_mount_fav_fails_after_findmnt_verify(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        env={"FAKE_MOUNT_FAV_RC": "1", "RECORD_LOGGER": "1"},
    )
    status = _kv(out.stdout)
    assert status["state"] == "remount-required"
    assert "usrjquota=aquota.user,jqfmt=vfsv1" in (tmp_path / "fstab").read_text(encoding="utf-8")
    calls = _calls(tmp_path)
    assert "findmnt-verify" in calls
    assert "fstab-mount-verify" in calls
    assert any("reported warnings during mount -fav check" in c for c in calls)

    # A broken fstab rejected by findmnt --verify is still hard-refused.
    bad_dir = tmp_path / "bad_verify"
    bad_dir.mkdir()
    (bad_dir / "fstab").write_text(FSTAB, encoding="utf-8")
    refused = run_bash(
        bad_dir,
        _script(bad_dir, "quota_enable"),
        check=False,
        env={"FAKE_FINDMNT_VERIFY_RC": "1"},
    )
    assert refused.returncode != 0
    assert "did not verify; nothing was changed" in refused.stderr
    assert (bad_dir / "fstab").read_text(encoding="utf-8") == FSTAB


@needs_bash
def test_enable_resolves_home_bind_mount_to_root_in_fstab(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        env={
            "FAKE_MP": "/home",
            "FAKE_SRC": "/dev/vda1[/home]",
            "FAKE_HOME_MAJMIN": "252:1",
            "FAKE_ROOT_MAJMIN": "252:1",
        },
    )
    status = _kv(out.stdout)
    assert status["mount"] == "/"
    assert status["state"] == "remount-required"
    assert "usrjquota=aquota.user,jqfmt=vfsv1" in (tmp_path / "fstab").read_text(encoding="utf-8")


@needs_bash
def test_enable_refuses_separate_home_disk_without_fstab_entry(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    result = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        check=False,
        env={
            "FAKE_MP": "/home",
            "FAKE_SRC": "/dev/vdb1",
            "FAKE_HOME_MAJMIN": "252:16",
            "FAKE_ROOT_MAJMIN": "252:1",
        },
    )
    assert result.returncode != 0
    assert "/home has no entry in" in result.stderr
    assert (tmp_path / "fstab").read_text(encoding="utf-8") == FSTAB

    # Even if SOURCE has a bracketed subpath on a different block device, refuse to touch /.
    result_diff_dev = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        check=False,
        env={
            "FAKE_MP": "/home",
            "FAKE_SRC": "/dev/vdb1[/home]",
            "FAKE_HOME_MAJMIN": "252:16",
            "FAKE_ROOT_MAJMIN": "252:1",
        },
    )
    assert result_diff_dev.returncode != 0
    assert "/home has no entry in" in result_diff_dev.stderr
    assert (tmp_path / "fstab").read_text(encoding="utf-8") == FSTAB



@needs_bash
def test_enable_switches_quota_on_once_the_options_are_mounted(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(tmp_path, _script(tmp_path, "quota_enable"),
                   env={"FAKE_OPTS": "rw,relatime,usrjquota=aquota.user,jqfmt=vfsv1"})
    status = _kv(out.stdout)
    assert status["state"] == "active" and status["mode"] == "kernel"
    calls = _calls(tmp_path)
    assert "quotacheck -cum /" in calls
    assert "quotaon -u /" in calls
    assert not any(c.startswith(("mount", "reboot")) for c in calls)


@needs_bash
def test_enable_preinstalls_linux_modules_extra_and_writes_modules_load_conf(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        env={
            "QUOTA_FORCE_MODULE_CHECK": "1",
            "FAKE_MODINFO_RC": "1",
            "FAKE_UNAME": "6.8.0-51-generic",
        },
    )
    status = _kv(out.stdout)
    assert status["state"] == "remount-required"
    mod_conf = tmp_path / "modules-load.d" / "spanel-quota.conf"
    assert mod_conf.read_text(encoding="utf-8").splitlines() == ["quota_v1", "quota_v2"]
    calls = _calls(tmp_path)
    assert "modinfo quota_v2" in calls
    assert "apt-get -o DPkg::Lock::Timeout=120 install -y linux-modules-extra-6.8.0-51-generic" in calls
    assert "depmod -a" in calls
    assert "modprobe quota_v1" in calls
    assert "modprobe quota_v2" in calls


@needs_bash
def test_enable_recovers_missing_quota_module_and_live_quotacheck_after_reboot(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    out = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        env={
            "FAKE_OPTS": "rw,relatime,usrjquota=aquota.user,jqfmt=vfsv1",
            "FAKE_SRC": "/dev/mapper/ubuntu--vg-ubuntu--lv",
            "FAKE_QUOTACHECK_CUM_RC": "1",
            "FAKE_QUOTAON_FAILS_BEFORE_OK": "2",
            "FAKE_UNAME": "6.8.0-100-generic",
        },
    )
    status = _kv(out.stdout)
    assert status["state"] == "active" and status["mode"] == "kernel"
    calls = _calls(tmp_path)
    assert "quotacheck -cum /" in calls
    assert "quotaoff -u /" in calls
    assert "quotacheck -cfum /" in calls
    assert "apt-get -o DPkg::Lock::Timeout=120 install -y linux-modules-extra-6.8.0-100-generic" in calls
    assert calls.count("quotaon -u /") == 3


@needs_bash
def test_enable_rebuilds_corrupt_aquota_file_or_surfaces_quotaon_error(tmp_path):
    (tmp_path / "fstab").write_text(FSTAB, encoding="utf-8")
    (tmp_path / "aquota.user").write_bytes(b"corrupt-header")
    out = run_bash(
        tmp_path,
        _script(tmp_path, "quota_enable"),
        env={
            "FAKE_OPTS": "rw,relatime,usrjquota=aquota.user,jqfmt=vfsv1",
            "FAKE_QUOTAON_FAILS_BEFORE_OK": "3",
        },
    )
    status = _kv(out.stdout)
    assert status["state"] == "active" and status["mode"] == "kernel"
    calls = _calls(tmp_path)
    assert "quotacheck -cum /" not in calls
    assert "quotacheck -cfum /" in calls
    assert calls.count("quotaon -u /") == 4

    fail_dir = tmp_path / "unrecoverable"
    fail_dir.mkdir()
    (fail_dir / "fstab").write_text(FSTAB, encoding="utf-8")
    failed = run_bash(
        fail_dir,
        _script(fail_dir, "quota_enable"),
        check=False,
        env={
            "FAKE_OPTS": "rw,relatime,usrjquota=aquota.user,jqfmt=vfsv1",
            "FAKE_QUOTAON_FAILS_BEFORE_OK": "99",
            "FAKE_QUOTAON_ERR": "quotaon: Quota format not supported in kernel.",
        },
    )
    assert failed.returncode != 0
    assert "quotaon failed on /: quotaon: Quota format not supported in kernel." in failed.stderr


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


def test_post_reboot_auto_enable_completes_once_and_does_not_loop_on_error(tmp_path):
    from contextlib import contextmanager
    from app.services import disk_quota, resource_monitor

    users = [SimpleNamespace(id=1, role="admin"), SimpleNamespace(id=2, role="end_user")]
    marker = tmp_path / "applied_marker"

    @contextmanager
    def fake_db_factory():
        yield SimpleNamespace(query=lambda model: SimpleNamespace(all=lambda: users))

    with disk_quota._state_lock:
        disk_quota._auto_enable_attempted_for.clear()
        disk_quota._last_auto_enable_error.clear()
        disk_quota._reconciling = False

    needs_qon = (
        "mode=app\nstate=needs-quotaon\nmount=/\nfstype=ext4\n"
        "device=/dev/mapper/ubuntu--vg-ubuntu--lv\ntools=yes\n"
        "mounted_options=yes\nfstab_options=yes\nquota_on=no\n"
    )
    active_out = (
        "mode=kernel\nstate=active\nmount=/\nfstype=ext4\n"
        "device=/dev/mapper/ubuntu--vg-ubuntu--lv\ntools=yes\n"
        "mounted_options=yes\nfstab_options=yes\nquota_on=yes\n"
    )

    responses = [
        SimpleNamespace(returncode=0, stdout=needs_qon, stderr=""),
        SimpleNamespace(returncode=0, stdout=active_out, stderr=""),
        SimpleNamespace(returncode=0, stdout=active_out, stderr=""),
    ]
    with (
        patch.object(disk_quota, "APPLIED_MARKER", marker),
        patch.object(disk_quota.shell, "privileged", side_effect=responses) as priv,
        patch.object(resource_monitor, "apply_user_cgroup_limits_async") as apply_async,
    ):
        final = disk_quota.reconcile_on_startup(db_factory=fake_db_factory)

    assert final["mode"] == "kernel" and final["state"] == "active"
    assert [c.args[0] for c in priv.call_args_list] == ["quota-status", "quota-enable", "quota-status"]
    assert [c.args[0] for c in apply_async.call_args_list] == [2]

    # Failure case: auto-enable records last_error and refuses to re-trigger in a loop
    # until the administrator clicks Enable manually (manual=True).
    with disk_quota._state_lock:
        disk_quota._auto_enable_attempted_for.clear()
        disk_quota._last_auto_enable_error.clear()

    fail_responses = [
        SimpleNamespace(returncode=0, stdout=needs_qon, stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr="deny: quotaon failed on /: Quota format not supported"),
        SimpleNamespace(returncode=0, stdout=needs_qon, stderr=""),
    ]
    with patch.object(disk_quota.shell, "privileged", side_effect=fail_responses):
        after_fail = disk_quota.reconcile_on_startup(db_factory=fake_db_factory)

    assert after_fail["state"] == "needs-quotaon"
    assert "Quota format not supported" in after_fail["last_error"]
    assert disk_quota.maybe_trigger_auto_enable(after_fail, db_factory=fake_db_factory, async_run=False) is False

    # Manual enable resets the one-shot guard and clears stale error once active.
    with patch.object(
        disk_quota.shell,
        "privileged",
        return_value=SimpleNamespace(returncode=0, stdout=active_out, stderr=""),
    ):
        manual_ok = disk_quota.enable(manual=True)
    assert manual_ok["mode"] == "kernel" and manual_ok["last_error"] == ""
