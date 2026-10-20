import pytest

from app.api import maintenance
from app.core.permissions import Role
from app.models.entities import User


@pytest.fixture
def job_store(monkeypatch):
    store: dict[str, dict] = {}
    monkeypatch.setattr(maintenance, "_file_jobs", store)
    return store


def _job(job_id, status, user_id=1, website_id=7):
    return {
        "job_id": job_id,
        "kind": "extract",
        "status": status,
        "website_id": website_id,
        "user_id": user_id,
        "created_at": f"2026-08-16T00:00:0{job_id[-1]}Z",
    }


def _user(user_id=1, role=Role.end_user.value):
    return User(id=user_id, username="tester", email="tester@example.test", role=role)


def test_finished_jobs_are_not_listed(job_store):
    for job in (_job("a1", "done"), _job("a2", "running"), _job("a3", "error"), _job("a4", "queued")):
        job_store[job["job_id"]] = job

    listed = maintenance._list_file_jobs(_user(), website_id=7)  # noqa: SLF001

    assert {item["job_id"] for item in listed} == {"a2", "a3", "a4"}


def test_jobs_of_other_users_and_websites_stay_hidden(job_store):
    job_store["b1"] = _job("b1", "running", user_id=2)
    job_store["b2"] = _job("b2", "running", website_id=99)
    job_store["b3"] = _job("b3", "running")

    listed = maintenance._list_file_jobs(_user(), website_id=7)  # noqa: SLF001

    assert [item["job_id"] for item in listed] == ["b3"]


def test_a_completed_job_is_still_reachable_by_id(job_store):
    """The poller needs the finished job to observe the transition to done."""
    job_store["c1"] = _job("c1", "done")

    assert maintenance._get_file_job("c1")["status"] == "done"  # noqa: SLF001
