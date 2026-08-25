"""Замыкание петли (KG5-05): шаг курса → активности → ответ → освоенность → FSRS."""

from __future__ import annotations

from core.models import Activity, Response, SrsCard
from modules.knowledge.course import generate_course
from modules.knowledge.mastery import MasteryState, load_map, save_state
from modules.knowledge.models import Concept
from modules.knowledge.study import MODULE_ID, start_step, submit_answer, weak_nodes
from tests.conftest import make_user

THEORY = {
    "summary": "Достаточно теории, чтобы по узлу можно было построить задание.",
    "sections": [
        {
            "heading": "Механика",
            "body": "Разбор того, как это работает.",
            "examples": ["пример применения"],
            "counter_examples": ["типичное заблуждение"],
        }
    ],
    "references": [],
}


def _c(session, title="Узел", *, blooms=("remember", "understand", "apply")):
    c = Concept(
        domain="ml",
        title=title,
        tier="core",
        content=THEORY,
        bloom_levels=list(blooms),
        difficulty=1,
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


def _prepared(session, **kwargs):
    user = make_user(session)
    concept = _c(session, **kwargs)
    course = generate_course(session, user.id, "ml", "apply")
    return user, concept, course


def _recall(activities):
    return next(a for a in activities if a.type == "concept_recall")


# ---- развёртывание шага ----


def test_step_becomes_engine_activities(session):
    user, concept, course = _prepared(session)

    activities = start_step(session, user.id, course, str(concept.id))

    types = [a.type for a in activities]
    assert "concept_study" in types
    assert "concept_recall" in types
    assert all(a.module == MODULE_ID for a in activities)


def test_activities_carry_the_concept(session):
    user, concept, course = _prepared(session)

    activities = start_step(session, user.id, course, str(concept.id))

    assert all(a.payload["conceptId"] == str(concept.id) for a in activities)


def test_theory_is_offline_and_checking_needs_network(session):
    """Ось connectivity — свойство активности, а не форк приложения."""
    user, concept, course = _prepared(session)

    by_type = {a.type: a for a in start_step(session, user.id, course, str(concept.id))}

    assert by_type["concept_study"].connectivity == "offline"
    assert by_type["concept_recall"].connectivity == "online"


def test_starting_twice_does_not_duplicate(session):
    user, concept, course = _prepared(session)

    start_step(session, user.id, course, str(concept.id))
    start_step(session, user.id, course, str(concept.id))

    assert session.query(Activity).filter_by(user_id=user.id).count() == len(
        {a.type for a in session.query(Activity).filter_by(user_id=user.id).all()}
    )


def test_step_creates_a_retention_card_linked_to_the_node(session):
    user, concept, course = _prepared(session)

    start_step(session, user.id, course, str(concept.id))

    card = session.query(SrsCard).filter_by(user_id=user.id).one()
    assert card.concept_id == concept.id
    assert card.module == MODULE_ID


def test_unknown_step_is_reported(session):
    user, concept, course = _prepared(session)
    other = _c(session, "Чужой")

    try:
        start_step(session, user.id, course, str(other.id))
        raise AssertionError("ожидался LookupError")
    except LookupError:
        pass


# ---- ответ замыкает петлю ----


def test_answer_lands_in_the_event_log(session):
    """FSRS и адаптация читают только response — туда ответ и обязан попасть."""
    user, concept, course = _prepared(session)
    activity = _recall(start_step(session, user.id, course, str(concept.id)))

    submit_answer(session, user.id, course, str(concept.id), activity, "какой-то ответ")

    response = session.query(Response).filter_by(user_id=user.id).one()
    assert response.activity_id == activity.id
    assert response.grade["conceptId"] == str(concept.id)


def test_answer_moves_mastery(session):
    user, concept, course = _prepared(session)
    activity = _recall(start_step(session, user.id, course, str(concept.id)))
    before = load_map(session, user.id, "ml")[concept.id].estimate

    result = submit_answer(
        session, user.id, course, str(concept.id), activity, THEORY["sections"][0]["body"]
    )

    assert result["score"] == 1.0
    assert load_map(session, user.id, "ml")[concept.id].estimate > before


def test_weak_answer_returns_the_node_through_retention(session):
    user, concept, course = _prepared(session)
    activity = _recall(start_step(session, user.id, course, str(concept.id)))

    result = submit_answer(session, user.id, course, str(concept.id), activity, "совершенно не то")

    assert result["score"] < 0.6
    card = session.query(SrsCard).filter_by(user_id=user.id, concept_id=concept.id).one()
    assert card.source == "error_log", "непонятый узел возвращается через FSRS"


def test_weak_answer_does_not_duplicate_the_card(session):
    user, concept, course = _prepared(session)
    activity = _recall(start_step(session, user.id, course, str(concept.id)))

    for _ in range(3):
        submit_answer(session, user.id, course, str(concept.id), activity, "не то")

    assert session.query(SrsCard).filter_by(user_id=user.id, concept_id=concept.id).count() == 1


def test_step_closes_on_mastery_not_on_a_button(session):
    user, concept, course = _prepared(session)
    activity = _recall(start_step(session, user.id, course, str(concept.id)))
    right = THEORY["sections"][0]["body"]

    first = submit_answer(session, user.id, course, str(concept.id), activity, right)
    assert first["stepCompleted"] is False, "одного верного ответа мало"

    for _ in range(5):
        result = submit_answer(session, user.id, course, str(concept.id), activity, right)

    assert result["stepCompleted"] is True
    assert str(concept.id) in course.progress["completed"]


# ---- слабые узлы ----


def test_weak_nodes_list_what_came_back(session):
    user, concept, course = _prepared(session)
    activity = _recall(start_step(session, user.id, course, str(concept.id)))
    submit_answer(session, user.id, course, str(concept.id), activity, "не то")

    weak = weak_nodes(session, user.id, "ml")

    assert [n["title"] for n in weak] == ["Узел"]


def test_untouched_nodes_are_not_weak(session):
    """Неотвеченный узел не «слабый» — про него просто ничего не известно."""
    user = make_user(session)
    _c(session)

    assert weak_nodes(session, user.id, "ml") == []


def test_mastered_nodes_drop_out_of_the_weak_list(session):
    user = make_user(session)
    concept = _c(session)
    save_state(
        session,
        user.id,
        "ml",
        concept.id,
        MasteryState(alpha=20, beta=1, observations=20),
    )

    assert weak_nodes(session, user.id, "ml") == []


# ---- эндпоинты ----


def test_endpoints_run_a_step_end_to_end(session, client):
    user = make_user(session)
    concept = _c(session)
    api = client(user)
    api.post("/graph/course/ml", json={"target_bloom": "apply"})

    started = api.post(f"/graph/course/ml/step/{concept.id}/start")
    assert started.status_code == 200
    recall = next(a for a in started.json()["activities"] if a["type"] == "concept_recall")

    answered = api.post(
        f"/graph/course/ml/step/{concept.id}/answer",
        json={"activity_id": recall["id"], "answer": THEORY["sections"][0]["body"]},
    )

    assert answered.status_code == 200
    assert answered.json()["score"] == 1.0
    assert answered.json()["mastery"]["observations"] == 1


def test_starting_a_step_without_a_course_is_404(session, client):
    concept = _c(session)

    response = client(make_user(session)).post(f"/graph/course/ml/step/{concept.id}/start")

    assert response.status_code == 404
