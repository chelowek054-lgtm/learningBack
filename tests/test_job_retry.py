"""Повтор AI-задач при временных сбоях (SPEC-03, AC-03.8)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core import modules
from core.config import settings
from core.jobs import due_jobs, process_job, retry_delay
from core.models import Activity, Job, Response, SrsCard, User
from tests.conftest import make_user

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class FlakyGateway:
    """Падает `failures` раз временной ошибкой, потом отдаёт оценку."""

    def __init__(self, failures: int, error: Exception | None = None):
        self.failures = failures
        self.error = error or RuntimeError("сеть до провайдера недоступна")
        self.calls = 0

    def grade(self, rubric, payload, answer):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return {
            "rubricId": rubric.id,
            "rubricVersion": rubric.version,
            "criteria": [],
            "overall": 5,
            "errors": [{"kind": "k", "excerpt": "x", "correction": "y", "explanation": "z"}],
        }


@pytest.fixture
def job(session):
    modules.sync_rubrics(session)
    user = make_user(session)
    activity = Activity(
        user_id=user.id,
        module="languages",
        type="ielts_writing_task2",
        connectivity="online",
        payload={"prompt": "p"},
    )
    session.add(activity)
    session.flush()
    response = Response(
        activity_id=activity.id, user_id=user.id, user_answer="text", local_created_at=NOW
    )
    session.add(response)
    session.flush()
    j = Job(
        user_id=user.id,
        type="grade_writing",
        status="pending",
        input_ref={"responseId": str(response.id), "rubricId": "ielts_writing_task2"},
    )
    session.add(j)
    session.flush()
    return j


def test_transient_failure_returns_job_to_pending_with_delay(session, job):
    process_job(session, job, FlakyGateway(failures=1))
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.result["retrying"] is True
    assert job.retry_after > datetime.now(timezone.utc)


def test_job_succeeds_on_retry_and_clears_delay(session, job):
    gw = FlakyGateway(failures=1)
    process_job(session, job, gw)
    job.retry_after = None  # срок отсрочки вышел
    process_job(session, job, gw)
    assert job.status == "done" and job.attempts == 2
    assert job.retry_after is None
    session.flush()
    assert session.query(SrsCard).filter_by(user_id=job.user_id, source="error_log").count() == 1


def test_job_fails_after_attempts_are_exhausted(session, job):
    gw = FlakyGateway(failures=99)
    for _ in range(settings.job_max_attempts):
        process_job(session, job, gw)
        job.retry_after = None
    assert job.status == "failed"
    assert job.attempts == settings.job_max_attempts
    assert "сеть" in job.result["error"]
    assert job.retry_after is None


def test_permanent_error_fails_immediately(session, job):
    job.input_ref = {**job.input_ref, "rubricId": "no_such"}
    gw = FlakyGateway(failures=0)
    process_job(session, job, gw)
    assert job.status == "failed" and job.attempts == 1 and gw.calls == 0


def test_backoff_grows_exponentially():
    base = timedelta(seconds=settings.job_retry_backoff_seconds)
    assert [retry_delay(n) for n in (1, 2, 3)] == [base, base * 2, base * 4]


def test_due_jobs_skips_those_waiting_for_retry(session, job):
    now = datetime.now(timezone.utc)
    job.retry_after = now + timedelta(minutes=5)
    session.flush()
    assert due_jobs(session, job.user_id, now) == []
    assert due_jobs(session, job.user_id, now + timedelta(minutes=6)) == [job]
    assert due_jobs(session, job.user_id, now, force=True) == [job]


def test_push_does_not_retry_before_delay(client, session, job):
    job.retry_after = datetime.now(timezone.utc) + timedelta(minutes=5)
    job.attempts = 1
    session.flush()
    client(session.get(User, job.user_id)).post("/sync/push", json={})
    assert job.status == "pending" and job.attempts == 1


def test_pull_reports_failed_jobs_with_reason(client, session, job):
    job.status = "failed"
    job.result = {"error": "провайдер недоступен"}
    session.flush()
    pulled = client(session.get(User, job.user_id)).get("/sync/pull").json()
    [j] = pulled["jobs"]
    assert j["status"] == "failed" and j["result"]["error"] == "провайдер недоступен"
    assert uuid.UUID(j["id"]) == job.id
