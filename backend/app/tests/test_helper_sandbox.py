"""What the panel's own sandbox has to let the privileged helper do.

spanel-api runs under a tight systemd sandbox. spanel-helper runs as root
through sudo, but it starts as a *child of that process*, and several systemd
restrictions are inherited by children regardless of privilege. So every
capability the helper needs has to be granted on the unit, and
10-spanel-helper.conf is the list of exactly that.

The list went stale. The security audit gave terminal-exec a mount-namespace
jail - `unshare --mount`, so a tenant's /home holds only their own directory -
and nobody added the matching grant. The unit said RestrictNamespaces=true,
which denies every namespace type to the process and everything it starts, so
the unshare failed with EPERM.

What that broke, from v1.0.141 until this fix: the file manager could list a
directory, because listing is a plain filesystem read in the API process, but
every file read and every save went through terminal-exec and returned 500.
The editor opened blank. Reported by a customer.

It was not caught because the jail had only ever been exercised from a root
SSH shell - the one context on the machine where this restriction does not
apply. A guarantee tested in the wrong context is not tested.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SCRIPT = PROJECT_ROOT / "installer" / "install.sh"
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"
HELPER = PROJECT_ROOT / "installer" / "files" / "spanel-helper.sh"


def _helper_dropin() -> str:
    """The block whose whole purpose is 'what the helper needs'.

    Comment lines are stripped: the block explains the bug by quoting the wrong
    value verbatim, and a test that grepped the raw text would match the
    explanation instead of the setting.
    """
    src = UPDATE_SCRIPT.read_text(encoding="utf-8")
    block = src.split("10-spanel-helper.conf <<'SERVICE'", 1)[1].split("\nSERVICE", 1)[0]
    return "\n".join(
        line for line in block.split("\n") if not line.lstrip().startswith("#")
    )


def test_the_helper_uses_a_mount_namespace():
    """If this stops being true, the grant below can go - and not before."""
    helper = HELPER.read_text(encoding="utf-8")
    assert "unshare --mount" in helper, (
        "the terminal jail is what needs the namespace grant; if the jail is "
        "gone, revisit the sandbox rather than leaving a grant nothing uses"
    )


def test_the_api_sandbox_permits_the_namespace_the_helper_needs():
    """RestrictNamespaces=true denies it to children too, sudo or not."""
    dropin = _helper_dropin()
    assert "RestrictNamespaces=mnt" in dropin, (
        "spanel-helper's terminal jail runs `unshare --mount`; without this "
        "grant every terminal-exec call from the panel fails with EPERM and "
        "the file manager cannot read or save a single file"
    )


def test_the_installed_unit_does_not_deny_every_namespace():
    src = INSTALL_SCRIPT.read_text(encoding="utf-8")
    hits = re.findall(r"^RestrictNamespaces=(\S+)", src, re.MULTILINE)
    assert hits, "the API unit no longer sets RestrictNamespaces at all"
    for value in hits:
        assert value not in {"true", "yes", "1"}, (
            "RestrictNamespaces=true denies the mount namespace the helper's "
            "jail needs; use an explicit allow-list such as `mnt`"
        )


def test_the_grant_is_narrow():
    """Permitting one namespace type, not switching the restriction off."""
    dropin = _helper_dropin()
    assert "RestrictNamespaces=no" not in dropin
    assert "RestrictNamespaces=false" not in dropin
    value = re.search(r"RestrictNamespaces=(\S+)", dropin).group(1)
    allowed = set(value.split())
    assert allowed == {"mnt"}, (
        f"only the mount namespace is needed; this grants {sorted(allowed)}"
    )


def test_every_relaxation_lives_in_the_one_file_that_documents_them():
    """So the next person adding a helper requirement has one place to look."""
    dropin = _helper_dropin()
    for directive in (
        "NoNewPrivileges=false",
        "ProtectSystem=false",
        "RestrictSUIDSGID=false",
        "SystemCallFilter=",
        "RestrictNamespaces=mnt",
    ):
        assert directive in dropin, f"{directive} is missing from the helper drop-in"
