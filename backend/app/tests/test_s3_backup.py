"""Object storage as a backup destination, and what the stored file is called.

The naming mode is the part that carries a guarantee. A schedule set to
`none` overwrites one file per account; `day_of_week` rotates through seven;
`week_of_month` through five. Those three bound what accumulates in a bucket
without anything having to delete - which is the guarantee you want when the
machine that would do the deleting is the one that might be compromised.
`full_date` keeps one a day and grows until retention prunes it.
"""

from datetime import date, datetime
from pathlib import Path

import pytest

from app.services import backup, backup_s3

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _Target:
    """Enough of a target row for the functions under test."""

    kind = "s3"
    endpoint = "s3.wasabisys.com"
    region = "us-east-1"
    bucket = "my-backups"
    access_key = "AK"
    secret_key = "ciphertext"
    prefix = "spanel/nightly"
    secure = True


# --- what the file is called ------------------------------------------------

WEDNESDAY = datetime(2026, 9, 23, 8, 15)  # a Wednesday, in the fourth week


@pytest.mark.parametrize("mode,expected", [
    ("none", "user-alice.tar.gz"),
    ("day_of_week", "user-alice-Wed.tar.gz"),
    ("week_of_month", "user-alice-W4.tar.gz"),
    ("full_date", "user-alice-2026-09-23.tar.gz"),
])
def test_each_mode_names_the_file_its_own_way(mode, expected):
    assert backup_s3.stored_name("user-alice-20260923081500.tar.gz", mode, when=WEDNESDAY) == expected


def test_the_timestamp_the_panel_adds_is_stripped_first():
    """Otherwise every rotating name would still be unique, defeating the point."""
    name = backup_s3.stored_name("user-alice-20260923081500.tar.gz", "day_of_week", when=WEDNESDAY)
    assert "20260923081500" not in name


def test_a_name_with_no_timestamp_still_works():
    assert backup_s3.stored_name("user-alice.tar.gz", "day_of_week", when=WEDNESDAY) == "user-alice-Wed.tar.gz"


def test_an_unknown_mode_falls_back_to_the_safest_one():
    """Keeping one a day loses nothing. Overwriting on a guess could."""
    assert backup_s3.stored_name("user-alice-20260923081500.tar.gz", "sideways", when=WEDNESDAY) \
        == "user-alice-2026-09-23.tar.gz"
    assert backup_s3.stored_name("user-alice-20260923081500.tar.gz", None, when=WEDNESDAY) \
        == "user-alice-2026-09-23.tar.gz"


def test_something_that_is_not_ours_is_left_alone():
    assert backup_s3.stored_name("someone-elses-file.zip", "none") == "someone-elses-file.zip"


def test_a_path_never_survives_into_the_stored_name():
    assert "/" not in backup_s3.stored_name("/tmp/x/user-alice-20260923081500.tar.gz", "none")


@pytest.mark.parametrize("day,week", [(1, 1), (7, 1), (8, 2), (14, 2), (15, 3), (22, 4), (28, 4), (30, 5)])
def test_week_of_month_counts_from_the_first(day, week):
    """Not ISO weeks: the 1st to the 7th is week 1 whatever weekday that is."""
    assert backup_s3.week_of_month(date(2026, 9, day)) == week


def test_week_of_month_never_exceeds_five():
    assert backup_s3.week_of_month(date(2026, 8, 31)) == 5


def test_rotation_reuses_exactly_one_name_per_bucket():
    """Seven weekdays means seven files, and the eighth day reuses the first."""
    names = {backup_s3.stored_name("user-alice-20260101000000.tar.gz", "day_of_week",
                                   when=datetime(2026, 9, 21 + offset))
             for offset in range(8)}
    assert len(names) == 7


# --- where it goes in the bucket -------------------------------------------

@pytest.mark.parametrize("prefix,expected", [
    ("spanel/nightly", "spanel/nightly/a.tar.gz"),
    ("/spanel/", "spanel/a.tar.gz"),
    ("", "a.tar.gz"),
    (None, "a.tar.gz"),
])
def test_the_prefix_never_produces_a_leading_slash(prefix, expected):
    assert backup_s3.object_key(prefix, "a.tar.gz") == expected


# --- refusing bad input before it reaches the network ----------------------

def test_a_target_with_no_endpoint_says_so():
    target = _Target()
    target.endpoint = ""
    with pytest.raises(backup_s3.S3Error) as exc:
        backup_s3._client(target)
    assert "endpoint" in str(exc.value)


def test_a_scheme_in_the_endpoint_is_stripped(monkeypatch):
    """minio wants host[:port]; `secure` already carries http vs https."""
    seen = {}

    class _Minio:
        def __init__(self, endpoint, **kwargs):
            seen["endpoint"] = endpoint
            seen.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "minio",
                        type("M", (), {"Minio": _Minio}))
    monkeypatch.setattr("app.core.secrets.decrypt", lambda value: "plaintext")

    target = _Target()
    target.endpoint = "https://s3.wasabisys.com/"
    backup_s3._client(target)
    assert seen["endpoint"] == "s3.wasabisys.com"
    assert seen["secure"] is True


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../../etc/passwd", "a/../../b"])
def test_download_refuses_a_key_that_climbs(key):
    with pytest.raises(backup_s3.S3Error):
        backup_s3.download(_Target(), key, "/tmp/x")


@pytest.mark.parametrize("key", ["", "/x", "../x"])
def test_remove_refuses_the_same(key):
    with pytest.raises(backup_s3.S3Error):
        backup_s3.remove(_Target(), key)


# --- reading an account out of a file name ---------------------------------

@pytest.mark.parametrize("name,expected", [
    ("user-alice-20260923081500.tar.gz", "alice"),
    ("user-alice-Mon.tar.gz", "alice"),
    ("user-alice-W3.tar.gz", "alice"),
    ("user-alice-2026-09-23.tar.gz", "alice"),
    ("user-alice.tar.gz", "alice"),
    ("user-some-long-name-Fri.tar.gz", "some-long-name"),
    ("website-example.com-20260101000000.tar.gz", ""),
    ("junk", ""),
])
def test_the_account_is_guessed_from_the_name_for_listing_only(name, expected):
    assert backup.username_from_archive_name(name) == expected


# --- staging something fetched from a bucket -------------------------------

def test_staging_refuses_anything_that_is_not_a_backup_name():
    for bad in ("../escape.tar.gz", "sub/dir.tar.gz", "notanarchive.txt", ""):
        with pytest.raises(ValueError):
            backup.stage_remote_backup(lambda destination: None, bad)


def test_staging_refuses_an_empty_download(tmp_path, monkeypatch):
    """A download that produced nothing must not be handed to restore."""
    monkeypatch.setattr(backup, "_user_restore_dir", lambda: tmp_path)
    with pytest.raises(ValueError) as exc:
        backup.stage_remote_backup(lambda destination: Path(destination).write_bytes(b""),
                                   "user-alice-Mon.tar.gz")
    assert "no file" in str(exc.value)


def test_staging_does_not_overwrite_something_already_there(tmp_path, monkeypatch):
    """Two targets can hold a file of the same name."""
    monkeypatch.setattr(backup, "_user_restore_dir", lambda: tmp_path)
    existing = tmp_path / "user-alice-Mon.tar.gz"
    existing.write_bytes(b"the good one")

    staged = backup.stage_remote_backup(
        lambda destination: Path(destination).write_bytes(b"the new one"),
        "user-alice-Mon.tar.gz",
    )
    assert staged != existing.name
    assert existing.read_bytes() == b"the good one"
    assert (tmp_path / staged).read_bytes() == b"the new one"


# --- the schedule carries the mode -----------------------------------------

def test_the_scheduler_names_the_upload_before_sending_it():
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "backup_scheduler.py").read_text(encoding="utf-8")
    body = source[source.index("def _upload_if_configured"):]
    body = body[:body.index("\ndef _decode_user_ids")]
    assert "backup_s3.stored_name" in body
    assert body.index("stored_name") < body.index("backup_s3.upload"), (
        "the name has to be decided before the upload, not after"
    )
    assert "remote_name=remote_name" in body, "and the SFTP path takes it too"


def test_both_kinds_of_target_go_through_the_same_naming():
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "backup_scheduler.py").read_text(encoding="utf-8")
    body = source[source.index("def _upload_if_configured"):]
    body = body[:body.index("\ndef _decode_user_ids")]
    assert body.count("remote_name") >= 2


# --- Run now ---------------------------------------------------------------
#
# A test run that took a different path would prove nothing about the run you
# actually depend on. The only thing Run now is allowed to skip is the cron
# expression.

def _scheduler_source():
    return (PROJECT_ROOT / "backend" / "app" / "services" / "backup_scheduler.py").read_text(encoding="utf-8")


def _maintenance_source():
    return (PROJECT_ROOT / "backend" / "app" / "api" / "maintenance.py").read_text(encoding="utf-8")


def test_the_timer_and_run_now_share_one_implementation():
    source = _scheduler_source()
    assert "def run_one(" in source
    loop = source[source.index("def run_due_schedules"):]
    assert "run_one(db, schedule, now)" in loop, (
        "the timer has to call the same function Run now does"
    )
    assert "backup.create_user_backup" not in loop, (
        "the work belongs in run_one, or the two paths drift apart"
    )


def test_run_now_skips_the_cron_and_nothing_else():
    source = _scheduler_source()
    body = source[source.index("def run_one("):source.index("def run_due_schedules")]
    assert "_cron_due" not in body, "the schedule expression is the one thing it ignores"
    for expected in ("_schedule_users", "backup.create_user_backup",
                     "_upload_if_configured", "backup.prune_user_backups"):
        assert expected in body, f"{expected} has to happen on a manual run too"


def test_the_result_lands_on_the_same_row_the_timer_writes():
    body = _scheduler_source()
    body = body[body.index("def run_one("):body.index("def run_due_schedules")]
    for field in ("last_status", "last_message", "last_run_at"):
        assert field in body


def test_run_now_does_not_hold_the_request_open():
    """A schedule covering every account takes minutes."""
    source = _maintenance_source()
    body = source[source.index("def run_backup_schedule_now"):source.index("def _run_schedule_now_job")]
    assert "_backup_job_executor.submit" in body
    assert "run_one" not in body, "the request thread must not do the work itself"


def test_the_row_says_running_before_the_work_starts():
    source = _maintenance_source()
    body = source[source.index("def run_backup_schedule_now"):source.index("def _run_schedule_now_job")]
    assert 'last_status = "running"' in body
    assert body.index('last_status = "running"') < body.index("_backup_job_executor.submit")


def test_the_worker_owns_its_own_session():
    """The request's session is closed by the time the job runs."""
    source = _maintenance_source()
    body = source[source.index("def _run_schedule_now_job"):]
    body = body[:body.index('@router.delete("/backup-schedules/{schedule_id}")')]
    assert "SessionLocal()" in body and "db.close()" in body


def test_a_failure_is_written_to_the_schedule_not_swallowed():
    source = _maintenance_source()
    body = source[source.index("def _run_schedule_now_job"):]
    body = body[:body.index('@router.delete("/backup-schedules/{schedule_id}")')]
    assert 'last_status = "error"' in body, "a crash must be visible on the row"


def test_the_button_is_not_offered_while_a_run_is_in_flight():
    page = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    # The handler is declared on a line carrying the same name; the button is
    # the one that also has an onClick.
    line = next(l for l in page.splitlines()
                if "runBackupScheduleNow(item)" in l and "onClick" in l)
    assert "item.last_status === 'running'" in line


def test_the_handler_only_calls_helpers_it_can_actually_see():
    """Run now did nothing at all, and said nothing about why.

    The handler sits at component level; the label helper it called was a
    `const` inside renderBackups(). Clicking threw a ReferenceError on the
    first line - before the confirm dialog, before the request - so the button
    was inert and the console was the only place that knew. Both helpers are
    hoisted now; this pins them there.
    """
    page = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    for helper in ("const scheduleUserLabel =", "const userNameById ="):
        declarations = [line for line in page.splitlines() if helper in line]
        assert len(declarations) == 1, f"{helper} declared more than once"
        assert declarations[0].startswith("  const"), (
            f"{helper} is nested inside a render function; "
            "runBackupScheduleNow cannot reach it from there"
        )


def test_the_row_is_polled_until_the_run_stops():
    """Nothing pushes the result: the work outlives the request that queued it."""
    page = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    handler = page[page.index("async function runBackupScheduleNow"):]
    handler = handler[:handler.index("\n  async function deleteBackupSchedule")]
    assert "pollBackupSchedule(item.id)" in handler
    assert "last_status === 'running'" in handler, "it has to know when to stop"


def test_run_now_and_delete_share_one_row():
    """The row is a two-column grid; a third child wraps to a second line."""
    page = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    block = page[page.index("runBackupScheduleNow(item)}"):]
    block = block[:block.index("</div>")]
    assert "deleteBackupSchedule(item.id)" in block, (
        "both buttons must sit inside the same actions container"
    )


def test_a_bad_name_is_refused_before_anything_touches_the_disk():
    """The order is the bug, not the checks.

    `safe = Path(name).name` ran first, so it stripped the traversal before
    the checks went looking for it - "/" in safe and ".." in safe could never
    be true. "../escape.tar.gz" passed validation as "escape.tar.gz" and the
    function carried on to create the staging directory. On Linux that raised

        PermissionError: [Errno 13] Permission denied: '/var/backups/spanel'

    which is only why it was ever noticed: on Windows the same call cheerfully
    creates C:\var\backups\spanel and the test passed. CI on Linux caught it
    the first time this branch was ever put through it.

    `fetch` raises here, so if validation ever lets one of these through the
    test fails with that instead of quietly passing.
    """
    def must_not_run(destination):
        raise AssertionError(f"validation let it through and staged {destination}")

    for bad in ("../escape.tar.gz", "sub/dir.tar.gz", "..\escape.tar.gz",
                "/abs/path.tar.gz", "notanarchive.txt", ""):
        with pytest.raises(ValueError):
            backup.stage_remote_backup(must_not_run, bad)


def test_the_name_the_listing_gives_is_still_accepted(tmp_path, monkeypatch):
    """Rejecting paths must not reject the names the feature actually uses."""
    monkeypatch.setattr(backup, "_user_restore_dir", lambda: tmp_path)
    staged = backup.stage_remote_backup(
        lambda destination: Path(destination).write_bytes(b"x"),
        "user-alice-Mon.tar.gz",
    )
    assert staged.startswith("user-alice-Mon-") and staged.endswith(".tar.gz")
