"""Extra SFTP logins, each pinned to one website.

A panel user's own Linux account already reaches every site they own, with the
panel password. That is the wrong credential to hand a freelancer: it opens
every other site on the account, and it is the same secret that logs into the
panel.

A sub-account is a real Linux user - OpenSSH has no virtual ones - that shares
the site owner's uid and is chrooted to a root-owned directory holding a bind
mount of exactly one website.

Two decisions carry the security of this module, and both are deliberate:

  the shared uid    Files the sub-account uploads land with exactly the
                    ownership the owner's own upload would produce, so PHP-FPM
                    keeps write access, WordPress can still update itself in
                    place, and disk quota counts against the right account. The
                    cost is that the uid grants nothing less than the owner
                    has, which is why the chroot is the boundary and the
                    account gets no shell, no exec and no forwarding.

  the chroot root   /var/lib/spanel-sftp, owned by root, NOT under
                    /var/lib/spanel. That directory belongs to the panel
                    account, and OpenSSH is right to refuse a chroot whose
                    parents are writable by anyone but root: such a chroot is
                    not a boundary at all.

The login name is generated rather than chosen. It has to be unique across the
whole machine, fit Linux's 32 characters, and stay inside the sftp_ namespace
that keeps it from ever colliding with a panel user.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Optional

from app.services import site_users
from app.services.shell import shell

CHROOT_ROOT = PurePosixPath("/var/lib/spanel-sftp")
PREFIX = site_users.SFTP_SUB_ACCOUNT_PREFIX

# What the helper accepts. Kept here as the same expression so a change on one
# side fails the tests rather than the server.
SUB_USER_RE = re.compile(r"^sftp_[a-z0-9_]{3,26}$")

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{1,31}$")

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 72


def validate_label(label: str) -> str:
    """What the customer types to name the account. Not the login."""
    cleaned = (label or "").strip()
    if not LABEL_RE.fullmatch(cleaned):
        raise ValueError(
            "Label must be 2-32 characters: letters, digits, spaces, hyphens or underscores"
        )
    return cleaned


def _slug(value: str, limit: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return slug[:limit]


def linux_user_for(owner_username: str, website_id: int, label: str) -> str:
    """Generate the login name.

    Deterministic so that re-running a create with the same inputs converges on
    the same account instead of stranding the previous one, and hashed so that
    two users who both call an account "deploy" do not collide.
    """
    owner = _slug(owner_username, 10) or "user"
    tag = _slug(label, 8) or "sftp"
    seed = f"{owner_username}|{website_id}|{label.strip().lower()}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
    username = f"{PREFIX}{owner}_{tag}_{digest}"
    if not SUB_USER_RE.fullmatch(username):  # pragma: no cover - defensive
        raise ValueError(f"Generated an invalid SFTP sub-account name: {username}")
    return username


def chroot_path_for(linux_user: str) -> str:
    return str(CHROOT_ROOT / require_sub_user(linux_user))


def require_sub_user(linux_user: str) -> str:
    if not SUB_USER_RE.fullmatch(linux_user or ""):
        raise ValueError("Invalid SFTP sub-account name")
    return linux_user


def validate_password(password: str) -> str:
    """Mirror the helper's own rule, so a bad password fails before it is sent.

    The ':' and newline bans are not style: chpasswd reads `user:password` one
    line at a time, so either character would let a password describe a second
    account.
    """
    value = password or ""
    if not (MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH):
        raise ValueError(
            f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters"
        )
    if ":" in value or "\r" in value or "\n" in value:
        raise ValueError("Password cannot contain ':', carriage returns or newlines")
    return value


def generate_password() -> str:
    """A password the panel invents when the customer does not supply one.

    Delegates so that the main account and its sub-accounts cannot drift apart
    on strength or on which characters are safe for chpasswd.
    """
    return site_users.generate_login_password()


def ensure_account(owner_linux_user: str, linux_user: str, site_root: str) -> str:
    """Create or re-converge the Linux user, the chroot and the bind mount."""
    owner = site_users.validate_linux_user(owner_linux_user)
    sub = require_sub_user(linux_user)
    shell.privileged(
        "sftp-account-ensure",
        helper_args=[owner, sub, site_root],
        fallback=["true"],
    )
    return chroot_path_for(sub)


def set_password(linux_user: str, password: str) -> None:
    sub = require_sub_user(linux_user)
    value = validate_password(password)
    shell.privileged(
        "sftp-account-password",
        helper_args=[sub],
        input=f"{value}\n",
        sensitive=True,
        fallback=["true"],
    )


def set_locked(linux_user: str, locked: bool) -> None:
    """Suspending a panel account has to reach its sub-accounts too.

    Otherwise a suspended customer keeps a working file credential, which is
    most of what the suspension was for.
    """
    sub = require_sub_user(linux_user)
    shell.privileged(
        "sftp-account-lock",
        helper_args=[sub, "lock" if locked else "unlock"],
        check=False,
        fallback=["true"],
    )


def delete_account(linux_user: str) -> None:
    sub = require_sub_user(linux_user)
    shell.privileged(
        "sftp-account-delete",
        helper_args=[sub],
        check=False,
        fallback=["true"],
    )


def connection_hint(linux_user: str, domain: str, host: Optional[str] = None) -> dict:
    """What the customer needs to type into an SFTP client.

    The path is what they land in: the chroot holds one directory, named after
    the site, and nothing else is reachable from it.
    """
    return {
        "username": linux_user,
        "host": host or "",
        "port": 22,
        "protocol": "sftp",
        "path": f"/{domain}",
    }
