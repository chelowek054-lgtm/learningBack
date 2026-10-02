"""Reading-дрилл: пробное задание и форма payload (T-0036, R-0022)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.models import Activity
from core.modules import provision_subject
from modules.languages import DEMO_READING_PAYLOAD
from tests.conftest import make_user

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def _drills(session, user):
    session.flush()
    return session.query(Activity).filter_by(user_id=user.id, type="reading_drill").all()


@pytest.mark.parametrize(
    "subject", [{"id": "ielts", "title": "IELTS"}, {"id": "toefl", "title": "TOEFL"}]
)
def test_language_subjects_get_an_offline_reading_drill(session, subject):
    user = make_user(session)

    provision_subject(session, user.id, subject, NOW)

    [drill] = _drills(session, user)
    assert drill.connectivity == "offline"  # проверка локальная: сеть не нужна
    assert drill.payload["timeLimitSec"] > 0 and drill.payload["passage"]


def test_reading_drill_is_not_duplicated(session):
    user = make_user(session)
    subject = {"id": "ielts", "title": "IELTS"}

    provision_subject(session, user.id, subject, NOW)
    session.flush()
    provision_subject(session, user.id, subject, NOW)

    assert len(_drills(session, user)) == 1


def test_non_language_subject_gets_no_reading_drill(session):
    user = make_user(session)

    provision_subject(session, user.id, {"id": "history", "title": "История"}, NOW)

    assert _drills(session, user) == []


def test_every_question_has_an_answer_the_client_can_check():
    qs = DEMO_READING_PAYLOAD["questions"]

    assert len({q["id"] for q in qs}) == len(qs)
    kinds = {q["type"] for q in qs}
    assert kinds == {"mcq", "tfng", "gap"}  # три формата из требования
    for q in qs:
        assert q["answer"]
        if q["type"] == "mcq":
            assert q["answer"] in q["options"]  # верный вариант действительно среди вариантов
        if q["type"] == "tfng":
            assert q["answer"] in {"true", "false", "not given"}
