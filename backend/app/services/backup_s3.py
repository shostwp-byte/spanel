"""Object storage as a backup destination, and what the stored file is called.

The panel could already push a backup to an SSH server. Most hosts have object
storage instead - Wasabi, Backblaze B2, DigitalOcean Spaces, Cloudflare R2, a
MinIO box, or S3 proper - and they all speak the same API.

`minio` rather than `boto3`: boto3 pulls botocore, which carries the service
definitions for every AWS product and adds roughly 100 MB to a venv that is
162 MB to begin with. That lands on every customer machine, for one feature.

**Naming is the interesting part.** What a scheduled backup is called at the
far end decides how many files accumulate there, and on metered storage that
is a bill:

    none            user-alice.tar.gz             one file, overwritten
    day_of_week     user-alice-Mon.tar.gz         seven, rotating
    week_of_month   user-alice-W3.tar.gz          five, rotating
    full_date       user-alice-2026-09-23.tar.gz  one a day, pruned by retention

The first three bound storage without deleting anything - the oldest copy is
simply overwritten when its turn comes round again. That is a different
guarantee from retention, which deletes, and it is the one you want when the
thing doing the deleting is the machine that might be compromised.
"""

from __future__ import annotations

import io as _io
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Local archives are named "<stem>-<YYYYMMDDHHMMSS>.tar.gz" or
# "<stem>-<YYYYMMDDHHMMSS>-inc.tar.gz"; the stem is the account or domain.
STAMP_RE = re.compile(r"-(\d{14})(?:-(inc|full))?(?=\.tar\.gz$)")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

SUFFIX_MODES = ("none", "day_of_week", "week_of_month", "full_date")
DEFAULT_SUFFIX = "full_date"


class S3Error(RuntimeError):
    """Anything the object store refused, phrased for the person reading it."""


def week_of_month(when: date) -> int:
    """Which week of its own month a date falls in, 1-5.

    Counted from the first of the month rather than by ISO week, so the answer
    does not depend on which weekday the month happens to start on: the 1st to
    the 7th is week 1 whatever day that is. Five buckets, because a 31-day
    month starting late reaches a fifth.
    """
    return min(5, (when.day - 1) // 7 + 1)


def stored_name(
    local_name: str,
    mode: str,
    *,
    when: Optional[datetime] = None,
    preserve_chain_base: bool = False,
) -> str:
    """What to call this archive at the far end.

    Takes the local file name, strips the timestamp the panel puts on every
    archive, and appends whatever the mode asks for. Incremental archives
    always keep a unique timestamp + '-inc.tar.gz' so they never overwrite
    the base Full backup or earlier incrementals in the chain.
    """
    mode = (mode or DEFAULT_SUFFIX).strip().lower()
    if mode not in SUFFIX_MODES:
        mode = DEFAULT_SUFFIX
    when = when or datetime.utcnow()

    base = os.path.basename(local_name)
    if not base.endswith(".tar.gz"):
        # Not one of ours. Leave it exactly as it is rather than guess.
        return base

    is_inc = base.endswith("-inc.tar.gz")
    m = STAMP_RE.search(base)
    if m and m.group(1):
        try:
            when = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    stem = STAMP_RE.sub("", base)[: -len(".tar.gz")]
    if stem.endswith("-inc"):
        stem = stem[:-4]

    if is_inc:
        return f"{stem}-{when.strftime('%Y-%m-%d_%H%M%S')}-inc.tar.gz"

    if preserve_chain_base:
        mode = "full_date"

    if mode == "none":
        suffix = ""
    elif mode == "day_of_week":
        suffix = "-" + WEEKDAYS[when.weekday()]
    elif mode == "week_of_month":
        suffix = "-W%d" % week_of_month(when.date())
    else:
        suffix = "-" + when.strftime("%Y-%m-%d")
    return f"{stem}{suffix}.tar.gz"


def object_key(prefix: Optional[str], name: str, subpath: Optional[str] = None) -> str:
    """Join a bucket prefix and optional hierarchy subpath (<hostname>/<user>) to a file name."""
    parts = [p.strip().strip("/") for p in (prefix, subpath, name) if p and p.strip().strip("/")]
    return "/".join(parts) if parts else name


def _client(target):
    """A minio client for this target, or a clear reason why not."""
    try:
        from minio import Minio
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise S3Error(
            "The minio package is not installed. Run spanel-update to install it."
        ) from exc

    from app.core.secrets import decrypt

    endpoint = (target.endpoint or "").strip()
    if not endpoint:
        raise S3Error("This target has no endpoint")
    # minio wants host[:port], never a scheme; `secure` carries http vs https.
    endpoint = re.sub(r"^https?://", "", endpoint).rstrip("/")
    if not target.bucket:
        raise S3Error("This target has no bucket")
    if not target.access_key or not target.secret_key:
        raise S3Error("This target has no access key")

    try:
        secret = decrypt(target.secret_key)
    except RuntimeError as exc:
        raise S3Error(
            "Could not decrypt this target's secret key; save the target again "
            "in panel settings"
        ) from exc

    return Minio(
        endpoint,
        access_key=target.access_key,
        secret_key=secret,
        secure=bool(target.secure),
        region=(target.region or None),
    )


def _wrap(exc: Exception) -> S3Error:
    from minio.error import S3Error as MinioS3Error

    if isinstance(exc, MinioS3Error):
        code = getattr(exc, "code", "") or ""
        if code in {"NoSuchBucket"}:
            return S3Error("That bucket does not exist")
        if code in {"AccessDenied", "SignatureDoesNotMatch", "InvalidAccessKeyId"}:
            return S3Error("The object store refused these credentials")
        return S3Error(f"The object store refused the request ({code or exc})")
    return S3Error(str(exc))


def check(target) -> dict:
    """Prove the credentials work before a schedule depends on them.

    Reads rather than writes: bucket_exists is enough to tell an endpoint
    typo, a wrong key and a missing bucket apart, and it leaves nothing behind
    if the operator is only testing.
    """
    client = _client(target)
    try:
        if not client.bucket_exists(target.bucket):
            raise S3Error(f"Bucket {target.bucket} does not exist, or this key cannot see it")
    except S3Error:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return {"ok": True, "bucket": target.bucket, "endpoint": target.endpoint}


def upload(target, local_file: str, *, remote_name: Optional[str] = None, subpath: Optional[str] = None) -> dict:
    """Put one archive in the bucket. Returns where it landed."""
    path = Path(local_file).resolve()
    if not path.is_file():
        raise FileNotFoundError("Local backup file not found")

    client = _client(target)
    key = object_key(target.prefix, remote_name or path.name, subpath=subpath)
    try:
        client.fput_object(target.bucket, key, str(path),
                           content_type="application/gzip")
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return {"remote_file": f"{target.bucket}/{key}", "key": key, "bucket": target.bucket}


def listing(target, *, limit: int = 500) -> list[dict]:
    """What is in the bucket under this target's prefix, newest first."""
    client = _client(target)
    prefix = (target.prefix or "").strip().strip("/")
    try:
        found = client.list_objects(target.bucket, prefix=(prefix + "/") if prefix else None,
                                    recursive=True)
        rows = []
        for item in found:
            name = os.path.basename(item.object_name or "")
            if not name.endswith(".tar.gz"):
                continue
            rows.append({
                "name": name,
                "key": item.object_name,
                "size": int(item.size or 0),
                "modified": item.last_modified.isoformat() if item.last_modified else "",
            })
            if len(rows) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    rows.sort(key=lambda row: row["modified"], reverse=True)
    return rows


def download(target, key: str, destination: str) -> str:
    """Fetch one object to a local path, so it can be restored from."""
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise S3Error("Not a valid object key")
    client = _client(target)
    out = Path(destination).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.fget_object(target.bucket, key, str(out))
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return str(out)


def remove(target, key: str) -> None:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise S3Error("Not a valid object key")
    client = _client(target)
    try:
        client.remove_object(target.bucket, key)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
