"""Уведомления «понятие проверено» и «правка изменила смысл пройденного» (T-0085, R-0044)."""

from __future__ import annotations

import pytest

from core.models import Activity
from modules.knowledge import notifications, provenance, review
from modules.knowledge.course import generate_course, mark_completed
from modules.knowledge.models import Concept, Notification
from tests.conftest import make_user
from tests.test_notifications import DOMAIN, As, concept


def learner_with(session, *titles, status="draft"):
    user = make_user(session)
    concepts = [concept(session, t, status=status) for t in titles]
    course = generate_course(session, user.id, DOMAIN, "understand", [])
    session.flush()
    return user, course, concepts


def approve(session, c, admin=None):
    provenance.review_concept(
        session, c, (admin or make_user(session, superuser=True)).id, "approve"
    )


def kinds(session, user):
    return [n["kind"] for n in notifications.listing(session, user.id)]


# ---- «понятие проверено» ----


def test_approval_tells_everyone_who_has_the_concept_in_the_course(session):
    user, _, (group,) = learner_with(session, "Group")
    stranger = make_user(session)  # курса нет

    approve(session, group)

    got = notifications.listing(session, user.id)
    assert [n["kind"] for n in got] == ["concept_verified"]
    assert "«Group»" in got[0]["body"] and "Черновик" in got[0]["body"]
    assert notifications.listing(session, stranger.id) == []


def test_repeated_approval_does_not_notify_again(session):
    user, _, (group,) = learner_with(session, "Group")
    approve(session, group)
    notifications.mark_read(session, user.id)

    approve(session, group)  # уже проверено

    assert notifications.listing(session, user.id) == []


def test_rejection_does_not_notify(session):
    user, _, (group,) = learner_with(session, "Group")
    provenance.review_concept(session, group, None, "reject", "ошибка в определении")
    assert notifications.listing(session, user.id) == []


def test_several_verified_concepts_accumulate_into_one_notice(session):
    user, _, concepts = learner_with(session, "Group", "Ring", "Field", "Module", "Algebra")

    for c in concepts:
        approve(session, c)

    rows = session.query(Notification).filter_by(user_id=user.id, kind="concept_verified").all()
    assert len(rows) == 1 and len(rows[0].data["concepts"]) == 5
    assert "и ещё 2" in rows[0].body


def test_concept_outside_the_course_does_not_notify(session):
    user, _, _ = learner_with(session, "Group")
    other = concept(session, "Elsewhere")  # создан после курса, в путь не попал
    approve(session, other)
    assert notifications.listing(session, user.id) == []


def test_issued_step_tasks_get_the_new_status_without_restart(session):
    user, _, (group,) = learner_with(session, "Group")
    act = Activity(
        user_id=user.id,
        module="knowledge",
        type="concept_study",
        connectivity="offline",
        payload={"conceptId": str(group.id), "status": "draft", "title": "Group"},
    )
    session.add(act)
    session.flush()

    approve(session, group)
    session.refresh(act)

    assert act.payload["status"] == "verified" and act.payload["title"] == "Group"


# ---- «правка изменила смысл» ----


def test_summary_edit_tells_only_those_who_already_passed_the_concept(session):
    passed, course, (group,) = learner_with(session, "Group")
    mark_completed(session, course, str(group.id))
    studying, _, _ = learner_with(session, "Other")  # другой курс без Group
    admin = make_user(session, superuser=True)

    review.edit_concept(
        session, group, admin, summary="Новое, исправленное изложение понятия.", note="была ошибка"
    )

    got = notifications.listing(session, passed.id)
    assert [n["kind"] for n in got] == ["concept_changed"]
    assert (
        "«Group»" in got[0]["body"]
        and "была ошибка" in got[0]["body"]
        and "повторить" in got[0]["body"]
    )
    assert notifications.listing(session, studying.id) == []


def test_not_yet_passed_concept_does_not_notify_on_edit(session):
    user, _, (group,) = learner_with(session, "Group")  # в пути, но не пройдено
    review.edit_concept(
        session,
        group,
        make_user(session, superuser=True),
        summary="Исправленное изложение понятия.",
    )
    assert notifications.listing(session, user.id) == []


def test_rename_alone_is_not_a_change_of_meaning(session):
    user, course, (group,) = learner_with(session, "Group")
    mark_completed(session, course, str(group.id))
    review.edit_concept(session, group, make_user(session, superuser=True), title="Группа")
    assert notifications.listing(session, user.id) == []


def test_two_edits_before_reading_make_one_notice(session):
    user, course, (group,) = learner_with(session, "Group")
    mark_completed(session, course, str(group.id))
    admin = make_user(session, superuser=True)

    review.edit_concept(session, group, admin, summary="Первая правка изложения понятия.")
    review.edit_concept(session, group, admin, summary="Вторая правка изложения понятия.")

    rows = session.query(Notification).filter_by(user_id=user.id, kind="concept_changed").all()
    assert len(rows) == 1 and len(rows[0].data["concepts"]) == 1


# ---- API ----


def test_api_approval_by_specialist_reaches_the_learner(session, client):
    user, _, (group,) = learner_with(session, "Group")
    admin = make_user(session, superuser=True)

    r = As(client, admin).post(f"/graph/canon/nodes/{group.id}/review", json={"action": "approve"})

    assert r.status_code == 200
    got = As(client, user).get("/graph/notifications").json()
    assert [n["kind"] for n in got] == ["concept_verified"]


@pytest.mark.parametrize("status", ["approved"])
def test_already_verified_concept_in_a_new_course_has_nothing_to_announce(session, status):
    user, _, (c,) = learner_with(session, "Group", status=status)
    assert isinstance(c, Concept) and notifications.listing(session, user.id) == []
