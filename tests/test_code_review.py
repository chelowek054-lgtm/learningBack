"""Ревью задач на код: рубрика ml_code_review v2 и job grade_code (T-0010, T-0012)."""

from __future__ import annotations

import uuid

from core.ai_gateway import get_rubric
from core.models import Activity, Job, Response, Rubric, SrsCard
from core.modules import sync_rubrics
from modules.ml.rubrics import CODE_REVIEW_CAVEAT
from tests.conftest import make_user

NOW = "2026-10-01T10:00:00+00:00"


def _code_push(code="def f(xs):\n    # recieve input\n    return sum(xs)\n"):
    aid, rid, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    return (
        {
            "activities": [
                {
                    "id": str(aid),
                    "module": "ml",
                    "type": "code_task",
                    "connectivity": "online",
                    "payload": {"statement": "Сложите числа списка."},
                }
            ],
            "responses": [
                {
                    "id": str(rid),
                    "activityId": str(aid),
                    "userAnswer": code,
                    "localCreatedAt": NOW,
                }
            ],
            "jobs": [
                {
                    "id": str(jid),
                    "type": "grade_code",
                    "inputRef": {"responseId": str(rid), "rubricId": "ml_code_review"},
                }
            ],
        },
        aid,
        rid,
        jid,
    )


def test_rubric_is_added_as_version_2_next_to_the_old_stub(session):
    # В живой базе версия 1 — заглушка Ф0; перезаписывать её нельзя (NFR-06).
    session.add(
        Rubric(id="ml_code_review", version=1, module="ml", model="", prompt="stub", schema={})
    )
    session.flush()

    sync_rubrics(session)

    latest = get_rubric(session, "ml_code_review")
    assert latest.version == 2
    assert [c["name"] for c in latest.schema["criteria"]] == [
        "Correctness",
        "Numerical stability / Efficiency",
        "Idiomatic style",
        "Explanation",
    ]
    assert all(c["max"] == 5 for c in latest.schema["criteria"])
    assert session.get(Rubric, ("ml_code_review", 1)).prompt == "stub"


def test_prompt_forbids_claiming_test_results():
    from modules.ml.rubrics import ML_CODE_REVIEW

    assert "БЕЗ запуска" in ML_CODE_REVIEW["prompt"]
    assert ML_CODE_REVIEW["schema"]["caveat"] == CODE_REVIEW_CAVEAT


def test_grade_code_job_grades_and_carries_static_review_caveat(client, session):
    sync_rubrics(session)
    user = make_user(session)
    body, _aid, rid, jid = _code_push()

    assert client(user).post("/sync/push", json=body).status_code == 200

    assert session.get(Job, jid).status == "done"
    grade = session.get(Response, rid).grade
    assert grade["rubricId"] == "ml_code_review" and grade["rubricVersion"] == 2
    assert grade["caveat"] == CODE_REVIEW_CAVEAT


def test_code_review_errors_become_error_log_cards_of_ml_module(client, session):
    sync_rubrics(session)
    user = make_user(session)
    body, *_ = _code_push()

    client(user).post("/sync/push", json=body)

    cards = session.query(SrsCard).filter_by(user_id=user.id, source="error_log").all()
    assert cards and all(c.module == "ml" for c in cards)


def test_pull_returns_caveat_to_the_client(client, session):
    sync_rubrics(session)
    user = make_user(session)
    body, *_ = _code_push()
    api = client(user)
    api.post("/sync/push", json=body)

    grade = api.get("/sync/pull").json()["responses"][0]["grade"]

    assert grade["caveat"] == CODE_REVIEW_CAVEAT


def test_other_rubrics_get_no_caveat(client, session):
    sync_rubrics(session)
    user = make_user(session)
    aid, rid, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    body = {
        "activities": [
            {
                "id": str(aid),
                "module": "ml",
                "type": "concept_recall",
                "connectivity": "online",
                "payload": {},
            }
        ],
        "responses": [
            {"id": str(rid), "activityId": str(aid), "userAnswer": "ответ", "localCreatedAt": NOW}
        ],
        "jobs": [
            {
                "id": str(jid),
                "type": "grade_concept",
                "inputRef": {"responseId": str(rid), "rubricId": "concept_check"},
            }
        ],
    }
    client(user).post("/sync/push", json=body)

    assert "caveat" not in session.get(Response, rid).grade
    assert session.query(Activity).filter_by(id=aid).count() == 1
