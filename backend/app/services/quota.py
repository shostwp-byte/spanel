"""Unified package-first count quota contract across SPanel.

Numeric convention for all count-based limits:
  * ``-1`` (any negative value normalized to ``-1``): Unlimited (``∞``)
  * ``0``: Feature disabled / 0 resources allowed
  * ``N > 0``: Maximum ``N`` resources allowed

Resolution precedence in ``effective_limit(user, field)``:
  1. ``user is None`` or ``is_admin_role(user.role)`` -> ``-1`` (Unlimited).
  2. When ``user.package`` is attached and defines ``field`` (not ``None``), the
     package attribute is the single source of truth (falling back to
     ``ftp_accounts_limit`` on ``package`` only when ``sftp_accounts_limit`` is
     ``None``).
  3. When ``user.package`` is ``None`` (or the package attribute is ``None``),
     read ``getattr(user, field, None)``:
     - ``None`` -> ``-1`` (Unlimited)
     - ``< 0`` -> ``-1``
     - ``0`` -> ``0``
     - ``> 0`` -> ``int(val)``
"""

from typing import Any, Optional, Tuple

from app.core.permissions import is_admin_role

QUOTA_FIELDS: Tuple[str, ...] = (
    "website_limit",
    "subdomain_limit",
    "database_limit",
    "sftp_accounts_limit",
    "mail_accounts_limit",
)


def normalize_quota_value(value: Any, *, default: int = -1) -> int:
    """Normalize a raw limit value into the canonical ``{-1, 0, N}`` contract."""
    if value is None:
        return default
    iv = int(value)
    return -1 if iv < 0 else iv


def effective_limit(user: Any, field: str) -> int:
    """Return the authoritative count limit for ``user`` on ``field``."""
    if field not in QUOTA_FIELDS:
        raise ValueError(f"Unknown quota field: {field!r}")
    if user is None or is_admin_role(getattr(user, "role", None)):
        return -1

    pkg = getattr(user, "package", None)
    if pkg is not None:
        pkg_val = getattr(pkg, field, None)
        if pkg_val is None and field == "sftp_accounts_limit":
            pkg_val = getattr(pkg, "ftp_accounts_limit", None)
        if pkg_val is not None:
            return normalize_quota_value(pkg_val, default=-1)

    user_val = getattr(user, field, None)
    return normalize_quota_value(user_val, default=-1)


def is_quota_unlimited(limit: Optional[int]) -> bool:
    """Return True when ``limit`` represents Unlimited (``None`` or ``< 0``)."""
    return limit is None or int(limit) < 0


def is_quota_disabled(limit: Optional[int]) -> bool:
    """Return True when ``limit`` explicitly disables creation (``0``)."""
    return limit is not None and int(limit) == 0


def is_quota_reached(used: int, limit: Optional[int], *, adding: int = 1) -> bool:
    """Return True if adding ``adding`` item(s) on top of ``used`` exceeds ``limit``."""
    if is_quota_unlimited(limit):
        return False
    lim = int(limit)
    if lim == 0:
        return True
    return (int(used) + max(1, int(adding))) > lim
