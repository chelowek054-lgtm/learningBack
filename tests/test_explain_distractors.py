"""Разбор дистракторов фоновой задачей (T-0038, V-0059)."""

from __future__ import annotations

from core.jobs import process_job
from core.models import Activity, Job
from modules.languages import DEMO_READING_PAYLOAD
from tests.conftest import make_user


class FakeGateway:
    def __init__(self, items):
        self.items = items

    def structured(self, *a, **k):
        return {"items": self.items}


def _job(session, user, activity_id, question_id="q1"):
    job = Job(
        user_id=user.id,
        type="explain_distractors",
        status="pending",
        input_ref={"activityId": str(activity_id), "questionId": question_id},
    )
    session.add(job)
    session.flush()
    return job


def _activity(session, user):
    a = Activity(
        user_id=user.id,
        module="languages",
        type="reading_drill",
        connectivity="offline",
        payload=DEMO_READING_PAYLOAD,
    )
    session.add(a)
    session.flush()
    return a


def test_wrong_options_are_explained_by_model(session):
    user = make_user(session)
    a = _activity(session, user)
    job = _job(session, user, a.id)
    wrong = "make soil cleaner"
    process_job(session, job, FakeGateway([{"option": wrong, "why": "Текст про почву молчит."}]))
    assert job.status == "done"
    assert job.result["fromModel"] is True
    assert job.result["items"] == [{"option": wrong, "why": "Текст про почву молчит."}]


def test_without_model_falls_back_to_authored_explanation(session):
    user = make_user(session)
    a = _activity(session, user)
    job = _job(session, user, a.id)
    process_job(session, job, FakeGateway([]))
    assert job.status == "done" and job.result["fromModel"] is False
    assert len(job.result["items"]) == 3


def test_foreign_or_missing_activity_fails_permanently(session):
    user, other = make_user(session), make_user(session)
    a = _activity(session, other)
    job = _job(session, user, a.id)
    process_job(session, job, FakeGateway([]))
    assert job.status == "failed"
    gap = _job(session, user, _activity(session, user).id, "q4")  # вопрос не с выбором
    process_job(session, gap, FakeGateway([]))
    assert gap.status == "failed"
