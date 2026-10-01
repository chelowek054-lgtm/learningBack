"""TOEFL: рубрики и выбор типа письма по предмету (T-0018)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from core.ai_gateway import get_rubric
from core.models import Activity, Job, Response
from core.modules import provision_subject, sync_rubrics
from tests.conftest import make_user

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _types(session, user):
    session.flush()  # сессия тестов без autoflush
    return {a.type for a in session.query(Activity).filter_by(user_id=user.id, module="languages")}


def test_toefl_rubrics_are_registered_with_their_criteria(session):
    sync_rubrics(session)

    independent = get_rubric(session, "toefl_writing_independent")
    integrated = get_rubric(session, "toefl_writing_integrated")

    assert [c["name"] for c in independent.schema["criteria"]] == [
        "Development",
        "Organization",
        "Language Use",
    ]
    assert [c["name"] for c in integrated.schema["criteria"]] == [
        "Content Accuracy",
        "Organization",
        "Language Use",
    ]
    assert all(c["max"] == 5 for r in (independent, integrated) for c in r.schema["criteria"])


def test_toefl_subject_gets_toefl_writing_with_its_rubric(session):
    user = make_user(session)

    provision_subject(session, user.id, {"id": "toefl", "title": "TOEFL iBT"}, NOW)

    assert _types(session, user) == {"toefl_writing_independent"}
    payload = session.query(Activity).filter_by(user_id=user.id).one().payload
    assert payload["rubricId"] == "toefl_writing_independent"
    assert payload["prompt"]


def test_ielts_subject_still_gets_ielts_writing(session):
    user = make_user(session)

    provision_subject(session, user.id, {"id": "ielts", "title": "IELTS"}, NOW)

    assert _types(session, user) == {"ielts_writing_task2"}


def test_provisioning_twice_does_not_duplicate(session):
    user = make_user(session)
    subject = {"id": "toefl", "title": "TOEFL"}

    provision_subject(session, user.id, subject, NOW)
    session.flush()  # между запросами API сессия сбрасывается; в тесте — вручную
    provision_subject(session, user.id, subject, NOW)
    session.flush()

    assert session.query(Activity).filter_by(user_id=user.id).count() == 1


def test_toefl_essay_is_graded_by_toefl_rubric(client, session):
    sync_rubrics(session)
    user = make_user(session)
    aid, rid, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    body = {
        "activities": [
            {
                "id": str(aid),
                "module": "languages",
                "type": "toefl_writing_independent",
                "connectivity": "online",
                "payload": {"prompt": "p"},
            }
        ],
        "responses": [
            {
                "id": str(rid),
                "activityId": str(aid),
                "userAnswer": "I recieve it",
                "localCreatedAt": "2026-10-01T10:00:00+00:00",
            }
        ],
        "jobs": [
            {
                "id": str(jid),
                "type": "grade_writing",
                "inputRef": {"responseId": str(rid), "rubricId": "toefl_writing_independent"},
            }
        ],
    }

    client(user).post("/sync/push", json=body)

    assert session.get(Job, jid).status == "done"
    grade = session.get(Response, rid).grade
    assert grade["rubricId"] == "toefl_writing_independent"
    assert [c["name"] for c in grade["criteria"]] == ["Development", "Organization", "Language Use"]
