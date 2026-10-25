"""Retention for the copies a schedule leaves on its destination.

Retention used to prune only this server's copies (backup.prune_user_backups),
and since a scheduled backup's local copy is deleted once it is uploaded,
there was usually nothing left to prune: with the default full_date naming
the bucket gained a file per account per day, forever.

What is pruned is decided from file names alone - nothing is downloaded,
and encrypted archives cannot be opened here anyway:

    user-<acct>-2026-10-24.tar.gz               a full (full_date naming)
    user-<acct>-2026-10-24_020000-inc.tar.gz    an incremental
    user-<acct>-20261024020000[-inc].tar.gz     the panel's local naming

Rotating names (user-<acct>.tar.gz, -Mon, -W3) bound themselves by being
overwritten and are never deleted here: that guarantee does not depend on
the machine doing the deleting, which is the point of choosing them.

Incremental chains: an incremental restores on top of its full and every
incremental between the two. Archives are ordered by when they were uploaded
(falling back to the time in the name), each incremental belongs to the
full before it, and keeping an incremental keeps everything it stands on.
So `keep` is a lower bound: the newest `keep` archives are restorable,
whatever else that takes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_DATE_FULL = re.compile(r"-(?P<d>\d{4}-\d{2}-\d{2})\.tar\.gz$")
_DATE_INC = re.compile(r"-(?P<d>\d{4}-\d{2}-\d{2})_(?P<t>\d{6})-inc\.tar\.gz$")
_LOCAL = re.compile(r"-(?P<s>\d{14})(?P<inc>-inc)?\.tar\.gz$")


@dataclass
class Archive:
    name: str
    key: str
    kind: str                 # "full" | "inc"
    stamp: datetime
    modified: str = ""
    chain: Optional[int] = None


@dataclass
class RetentionPlan:
    keep: list[Archive] = field(default_factory=list)
    protected: list[Archive] = field(default_factory=list)   # kept only because a kept incremental needs them
    delete: list[Archive] = field(default_factory=list)
    unmanaged: list[str] = field(default_factory=list)       # rotating or foreign names, never touched


def classify(name: str, account: Optional[str] = None) -> Optional[tuple[str, datetime]]:
    """(kind, time) for a dated archive of `account`, or None for anything
    retention must leave alone."""
    if not name.startswith("user-") or not name.endswith(".tar.gz"):
        return None
    if account is not None and not name.startswith(f"user-{account}-"):
        return None
    # With the account known, what follows "user-<acct>" must be exactly a
    # date suffix: "user-alice-bob-..." is not alice's.
    rest = name[len(f"user-{account}"):] if account is not None else name
    match = (lambda rx: rx.fullmatch(rest)) if account is not None else (lambda rx: rx.search(rest))
    try:
        if m := match(_DATE_INC):
            return "inc", datetime.strptime(m["d"] + m["t"], "%Y-%m-%d%H%M%S")
        if m := match(_LOCAL):
            return ("inc" if m["inc"] else "full"), datetime.strptime(m["s"], "%Y%m%d%H%M%S")
        if m := match(_DATE_FULL):
            return "full", datetime.strptime(m["d"], "%Y-%m-%d")
    except ValueError:
        return None
    return None


def _parse_modified(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def plan_retention(entries: list[dict], keep: int, account: Optional[str] = None) -> RetentionPlan:
    """Which of `entries` ([{name, key, modified}]) to keep and which to delete."""
    keep = max(int(keep or 1), 1)
    plan = RetentionPlan()
    archives: list[Archive] = []
    for entry in entries:
        found = classify(entry.get("name") or "", account)
        if not found:
            plan.unmanaged.append(entry.get("name") or "")
            continue
        archives.append(Archive(entry["name"], entry.get("key") or entry["name"], found[0], found[1],
                                entry.get("modified") or ""))

    # Upload time orders a schedule's archives exactly; a full_date name only
    # has the day. Use it when every archive has one.
    use_modified = all(_parse_modified(a.modified) for a in archives)
    archives.sort(key=lambda a: ((_parse_modified(a.modified) if use_modified else a.stamp), a.stamp, a.name))

    chain_id = None
    for index, archive in enumerate(archives):
        if archive.kind == "full":
            chain_id = index
        archive.chain = chain_id

    kept = archives[-keep:]
    kept_ids = {id(a) for a in kept}
    protected_ids: set[int] = set()
    for archive in kept:
        if archive.kind != "inc" or archive.chain is None:
            continue
        position = archives.index(archive)
        for earlier in archives[archive.chain:position]:
            if id(earlier) not in kept_ids:
                protected_ids.add(id(earlier))

    for archive in archives:
        if id(archive) in kept_ids:
            plan.keep.append(archive)
        elif id(archive) in protected_ids:
            plan.protected.append(archive)
        else:
            plan.delete.append(archive)
    return plan


def prune_remote(target, account: str, keep: int, *, hostname: Optional[str] = None) -> dict:
    """Apply retention to one account's folder on a destination."""
    from app.services import backup, backup_remote

    host = hostname or backup.get_server_hostname()
    entries = backup_remote.list_files(target, f"{host}/{account}")
    plan = plan_retention(entries, keep, account)
    deleted, errors = [], []
    for archive in plan.delete:
        try:
            backup_remote.delete(target, archive.key)
            deleted.append(archive.name)
        except backup_remote.RemoteError as exc:
            errors.append(f"{archive.name}: {exc}")
    return {"kept": [a.name for a in plan.keep], "protected": [a.name for a in plan.protected],
            "deleted": deleted, "errors": errors}
