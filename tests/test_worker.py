"""Фоновый воркер долгих AI-задач (T-0049, R-0023, R-0025, V-0064)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core import worker
from core.ai_mock import MockAIGateway
from core.config import settings
from core.models import Job, Response
from core.modules import sync_rubrics
from tests.conftest import make_user

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def push_body():
    aid, rid, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    return (
        {
            "activities": [
                {
                    "id": str(aid),
                    "module": "languages",
                    "type": "ielts_writing_task2",
                    "connectivity": "online",
                    "payload": {"prompt": "p"},
                }
            ],
            "responses": [
                {
                    "id": str(rid),
                    "activityId": str(aid),
                    "userAnswer": "I think so. " * 30,
                    "localCreatedAt": "2026-10-03T10:00:00+00:00",
                }
            ],
            "jobs": [
                {
                    "id": str(jid),
                    "type": "grade_writing",
                    "inputRef": {"responseId": str(rid), "rubricId": "ielts_writing_task2"},
                }
            ],
        },
        rid,
        jid,
    )


@pytest.fixture
def worker_mode(monkeypatch):
    monkeypatch.setattr(settings, "jobs_mode", "worker")


def test_push_in_worker_mode_only_queues_the_job(session, client, worker_mode):
    sync_rubrics(session)
    body, rid, jid = push_body()

    r = client(make_user(session)).post("/sync/push", json=body)

    assert r.status_code == 200
    assert session.get(Job, jid).status == "pending"
    assert session.get(Response, rid).grade is None  # push не ждал модель


def test_inline_mode_still_grades_on_push(session, client):
    sync_rubrics(session)
    body, rid, jid = push_body()

    client(make_user(session)).post("/sync/push", json=body)

    assert session.get(Job, jid).status == "done"


def test_worker_takes_the_queued_job_and_it_reaches_done(session, client, worker_mode):
    sync_rubrics(session)
    body, rid, jid = push_body()
    client(make_user(session)).post("/sync/push", json=body)

    job = worker.work_one(session, MockAIGateway())

    assert job is not None and job.id == jid
    assert session.get(Job, jid).status == "done"
    assert session.get(Response, rid).grade["criteria"]


def test_empty_queue_gives_nothing(session):
    assert worker.work_one(session, MockAIGateway()) is None


def test_jobs_are_taken_in_order_of_creation(session, client, worker_mode):
    sync_rubrics(session)
    user = make_user(session)
    ids = []
    for _ in range(3):
        body, _rid, jid = push_body()
        client(user).post("/sync/push", json=body)
        ids.append(jid)

    taken = [worker.work_one(session, MockAIGateway()).id for _ in range(3)]

    assert sorted(taken) == sorted(ids) and worker.work_one(session, MockAIGateway()) is None


def test_a_job_is_not_taken_twice(session, client, worker_mode):
    sync_rubrics(session)
    body, _rid, jid = push_body()
    client(make_user(session)).post("/sync/push", json=body)

    first = worker.work_one(session, MockAIGateway())
    second = worker.work_one(session, MockAIGateway())

    assert first.id == jid and second is None


def test_job_waiting_for_retry_is_not_taken_early(session, client, worker_mode):
    sync_rubrics(session)
    body, _rid, jid = push_body()
    client(make_user(session)).post("/sync/push", json=body)
    session.get(Job, jid).retry_after = datetime.now(timezone.utc) + timedelta(hours=1)
    session.flush()

    assert worker.work_one(session, MockAIGateway()) is None


def test_transient_failure_returns_the_job_to_the_queue_with_a_delay(session, client, worker_mode):
    sync_rubrics(session)
    body, _rid, jid = push_body()
    client(make_user(session)).post("/sync/push", json=body)

    class Down(MockAIGateway):
        def grade(self, *a, **k):
            raise RuntimeError("провайдер недоступен")

    worker.work_one(session, Down())

    job = session.get(Job, jid)
    assert job.status == "pending" and job.retry_after is not None and job.attempts == 1


def test_permanent_failure_marks_the_job_failed(session, worker_mode):
    user = make_user(session)
    job = Job(user_id=user.id, type="unknown_type", status="pending", input_ref={})
    session.add(job)
    session.flush()

    worker.work_one(session, MockAIGateway())

    assert session.get(Job, job.id).status == "failed"


def test_abandoned_running_job_is_requeued_after_the_timeout(session):
    user = make_user(session)
    stuck = Job(user_id=user.id, type="t", status="running", input_ref={})
    fresh = Job(user_id=user.id, type="t", status="running", input_ref={})
    session.add_all([stuck, fresh])
    session.flush()
    stuck.updated_at = datetime.now(timezone.utc) - timedelta(
        minutes=settings.job_stale_minutes + 5
    )
    session.flush()

    n = worker.requeue_stale(session)

    assert n == 1
    assert (
        session.get(Job, stuck.id).status == "pending"
        and session.get(Job, fresh.id).status == "running"
    )


def test_token_usage_is_attributed_to_the_owner_of_the_job(
    session, client, worker_mode, monkeypatch
):
    from core import usage

    seen = []
    monkeypatch.setattr(usage, "_current_user", usage._current_user)
    sync_rubrics(session)
    user = make_user(session)
    body, _rid, _jid = push_body()
    client(user).post("/sync/push", json=body)

    class Spy(MockAIGateway):
        def grade(self, *a, **k):
            seen.append(usage._current_user.get())
            return super().grade(*a, **k)

    worker.work_one(session, Spy())

    assert seen == [user.id] and usage._current_user.get() is None


def test_loop_stops_when_asked_and_counts_work(session):
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 2

    class Factory:
        def __call__(self):
            return _Ctx(session)

    class _Ctx:
        def __init__(self, s):
            self.s = s

        def __enter__(self):
            return self.s

        def __exit__(self, *a):
            return False

    assert (
        worker.run_loop(
            MockAIGateway(), session_factory=Factory(), poll_seconds=0, should_stop=stop
        )
        == 0
    )
