"""Choosing "day of week" and getting a full date anyway.

    Đặt lịch backup chưa êm. Chọn Append là Day of week nó vẫn ra
    user-sieunhim-2026-09-23.tar.gz

The naming mode is the whole point of the setting: `none` overwrites one file
per account, `day_of_week` rotates through seven, `week_of_month` through
five. Each bounds what accumulates in a bucket without anything having to
delete. `full_date` does not - it keeps one a day and grows until retention
prunes it, which on metered object storage is a bill.

The panel sent the choice, the schema validated it against its four allowed
values, and then the endpoint built the row without it. The column kept its
own "full_date" default, so every schedule ever created was a full date
whatever the operator picked, and the response schema left the field out as
well - so the list could not have shown the discrepancy either.
"""

import json
from pathlib import Path

import pytest

from app.api import maintenance
from app.models.entities import BackupSchedule, User
from app.schemas.schemas import BackupScheduleCreate, BackupScheduleOut

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Enough of a session for the create endpoint, and it keeps the row."""

    def __init__(self, users):
        self._users = users
        self.added = []

    def query(self, model):
        return _Query(self._users if model is User else [])

    def add(self, item):
        self.added.append(item)

    def commit(self):
        pass

    def refresh(self, item):
        pass


@pytest.fixture
def admin():
    return User(id=1, username="admin", email="admin@example.test", role="admin",
                hashed_password="x", is_active=True)


@pytest.fixture
def db(admin, monkeypatch):
    account = User(id=16, username="sieunhim", email="s@example.test", role="end_user",
                   hashed_password="x", is_active=True)
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    return _Session([account])


@pytest.mark.parametrize("mode", ["none", "day_of_week", "week_of_month", "full_date"])
def test_the_naming_mode_the_operator_picked_reaches_the_row(mode, db, admin):
    payload = BackupScheduleCreate(user_ids=[16], schedule="0 2 * * *", name_suffix=mode)

    maintenance.create_backup_schedule(payload, request=None, db=db, current_user=admin)

    row = next(item for item in db.added if isinstance(item, BackupSchedule))
    assert row.name_suffix == mode, (
        f"picked {mode}, stored {row.name_suffix} - the column default won"
    )


def test_the_list_says_which_mode_a_schedule_uses():
    """The panel reads item.name_suffix; leaving it out made that undefined."""
    assert "name_suffix" in BackupScheduleOut.model_fields


def test_nothing_the_form_sends_is_accepted_and_then_dropped():
    """The class of bug, not just this instance.

    A field on the create schema that the endpoint never reads is worse than
    one that does not exist: it validates, it returns 200, and it silently
    does nothing.
    """
    source = Path(maintenance.__file__).read_text(encoding="utf-8")
    body = source.split("def create_backup_schedule(")[1].split("\n@router")[0]
    for field in BackupScheduleCreate.model_fields:
        assert f"payload.{field}" in body, (
            f"create_backup_schedule accepts {field} and never reads it"
        )


def test_the_frontend_and_the_schema_offer_the_same_four_modes():
    """A fifth option in the form would fail validation with a 422."""
    app_jsx = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    # Matched on the label going through t(), not on a bare attribute: the
    # translation sweep rewrote every aria-label, and this test found it.
    block = app_jsx.split("aria-label={t('Stored file name')}")[1].split("</select>")[0]
    offered = set(part.split('"')[0] for part in block.split('<option value="')[1:])
    allowed = set(json.loads(
        BackupScheduleCreate.model_json_schema()["properties"]["name_suffix"]["pattern"]
        .strip("^$").replace("(", "[\"").replace(")", "\"]").replace("|", "\",\"")
    ))
    assert offered == allowed, f"form offers {sorted(offered)}, API allows {sorted(allowed)}"
