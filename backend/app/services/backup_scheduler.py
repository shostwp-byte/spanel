from datetime import datetime
import json

from app.core.database import SessionLocal
from app.core.secrets import decrypt
from app.models.entities import BackupSchedule, SftpBackupTarget, User
from app.services import backup, backup_s3


def _field_matches(field: str, value: int) -> bool:
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = max(int(step_text or "1"), 1)
        if part == "*":
            start, end = 0, 59
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _cron_due(schedule: str, now: datetime) -> bool:
    minute, hour, day, month, weekday = schedule.split()
    cron_weekday = (now.weekday() + 1) % 7
    return (
        _field_matches(minute, now.minute)
        and _field_matches(hour, now.hour)
        and _field_matches(day, now.day)
        and _field_matches(month, now.month)
        and (_field_matches(weekday, cron_weekday) or (cron_weekday == 0 and _field_matches(weekday, 7)))
    )


def _upload_if_configured(db, schedule: BackupSchedule, archive: str) -> str:
    """Send the archive wherever the schedule points, under whatever name.

    The name is not cosmetic. A schedule set to `none` overwrites one file per
    account; `day_of_week` rotates through seven; `week_of_month` through five.
    Those three bound what accumulates in the bucket without anything having to
    delete, which is the guarantee you want when the machine that would do the
    deleting is the one that might be compromised.
    """
    if not schedule.target_id:
        return archive
    target = db.query(SftpBackupTarget).filter(SftpBackupTarget.id == schedule.target_id, SftpBackupTarget.is_active == True).first()  # noqa: E712
    if not target:
        raise ValueError("Backup target not found")

    remote_name = backup_s3.stored_name(archive, getattr(schedule, "name_suffix", None))

    if (target.kind or "sftp") == "s3":
        result = backup_s3.upload(target, archive, remote_name=remote_name)
        return f"{target.name}:{result['remote_file']}"

    try:
        password = decrypt(target.password) if target.password else None
    except RuntimeError:
        raise RuntimeError(
            "Failed to decrypt SFTP target password; please re-save the target in panel settings"
        )
    try:
        private_key = decrypt(target.private_key) if target.private_key else None
    except RuntimeError:
        raise RuntimeError(
            "Failed to decrypt SFTP target private key; please re-save the target in panel settings"
        )
    result = backup.upload_to_sftp(
        archive,
        remote_name=remote_name,
        host=target.host,
        port=target.port,
        username=target.username,
        password=password,
        private_key=private_key,
        remote_path=target.remote_path,
        expected_host_key_type=target.host_key_type,
        expected_host_key_fingerprint=target.host_key_fingerprint,
    )
    if not target.host_key_fingerprint and result.get("host_key_fingerprint"):
        target.host_key_type = result["host_key_type"]
        target.host_key_fingerprint = result["host_key_fingerprint"]
        db.commit()
    return f"{target.name}:{result['remote_file']}"


def _decode_user_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [item for item in raw.split(",") if item]
    if isinstance(value, int):
        value = [value]
    return [int(item) for item in value if int(item) > 0]


def _schedule_users(db, schedule: BackupSchedule) -> list[User]:
    if schedule.all_users:
        return db.query(User).filter(User.is_active == True).order_by(User.id.asc()).all()  # noqa: E712
    user_ids = _decode_user_ids(schedule.user_ids)
    if not user_ids and schedule.user_id:
        user_ids = [schedule.user_id]
    if not user_ids:
        return []
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    by_id = {user.id: user for user in users}
    return [by_id[user_id] for user_id in user_ids if user_id in by_id]


def _short_message(parts: list[str]) -> str:
    message = "; ".join(parts)
    return message[:4000]


def run_one(db, schedule: BackupSchedule, now: datetime | None = None) -> bool:
    """Run one schedule, exactly as the timer would. Returns whether it worked.

    Extracted so "Run now" is not a second implementation. A test run that
    took a different path would prove nothing about the real one - the cron
    expression is the only thing it is allowed to skip.
    """
    now = (now or datetime.now()).replace(second=0, microsecond=0)

    users = _schedule_users(db, schedule)
    if not users:
        schedule.last_run_at = now
        schedule.last_status = "error"
        schedule.last_message = "No users selected"
        db.commit()
        _notify_failure(schedule, ["No users selected"], now)
        return False

    messages = []
    errors = []
    for user in users:
        try:
            archive = backup.create_user_backup(user, db)
            target = _upload_if_configured(db, schedule, archive)
            if schedule.target_id:
                # The copies live on the destination now; keeping them here
                # too doubled the disk a destination is meant to spare. A
                # failed upload raised above and leaves the archive in place.
                backup.discard_local_copy(archive)
            backup.prune_user_backups(user.username, schedule.retention)
            messages.append(f"{user.username}: {target}")
        except Exception as exc:  # pragma: no cover - operational path
            errors.append(f"{user.username}: {exc}")

    ok = not errors
    schedule.last_status = "ok" if ok else "error"
    schedule.last_message = _short_message(
        [f"ok {len(messages)} user(s)"] + (messages if ok else errors)
    )
    schedule.last_run_at = now
    db.commit()
    if not ok:
        _notify_failure(schedule, errors, now)
    return ok


def _notify_failure(schedule: BackupSchedule, errors: list[str], when: datetime) -> None:
    """Administrators hear about every failure (customers get no notifications)."""
    try:
        from app.services import notifications

        notifications.notify("backup_failed_admin", {"when": when.strftime("%d/%m/%Y %H:%M"), "errors": errors},
                             admins=True, dedupe_key=f"backup:{schedule.id}:{when:%Y%m%d%H%M}")
    except Exception:  # noqa: BLE001 - the schedule's own record already says it failed
        pass


def run_due_schedules(now: datetime | None = None) -> int:
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    db = SessionLocal()
    ran = 0
    try:
        schedules = db.query(BackupSchedule).filter(BackupSchedule.is_active == True).all()  # noqa: E712
        for schedule in schedules:
            if not _cron_due(schedule.schedule, now):
                continue
            if schedule.last_run_at and schedule.last_run_at.replace(second=0, microsecond=0) == now:
                continue
            if run_one(db, schedule, now):
                ran += 1
    finally:
        db.close()
    return ran


if __name__ == "__main__":
    count = run_due_schedules()
    print(f"spanel backup scheduler ran {count} job(s).")
