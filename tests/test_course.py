"""Генерация курса (KG5): укоренение, дифференциация, ветвление, спираль."""

from __future__ import annotations

import pytest

from modules.knowledge.course import (
    BRANCH,
    DIFFERENTIATION,
    ROOTING,
    SPIRAL,
    build_path,
    course_view,
    generate_course,
    mark_completed,
)
from modules.knowledge.mastery import MasteryState, save_state
from modules.knowledge.models import Concept, ConceptEdge
from tests.conftest import make_user

THEORY = {
    "summary": "Достаточно теории, чтобы узел считался пригодным для заданий.",
    "sections": [
        {"heading": "Раздел", "body": "Разбор", "examples": ["пример"], "counter_examples": []}
    ],
    "references": [],
}


def _c(session, title, *, tier="derived", centrality=0.0, blooms=("remember", "understand", "apply")):
    c = Concept(
        domain="ml",
        title=title,
        tier=tier,
        centrality=centrality,
        content=THEORY,
        bloom_levels=list(blooms),
        difficulty=1,
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


def _edge(session, a, b, kind="prereq"):
    session.add(ConceptEdge(from_id=a.id, to_id=b.id, type=kind))
    session.flush()


def _mastered(session, user, concept, *, bloom="understand"):
    save_state(
        session, user.id, "ml", concept.id,
        MasteryState(alpha=20, beta=1, observations=20, bloom_reached=bloom),
    )


def _titles(path):
    return [s["title"] for s in path]


def _by_title(path):
    return {s["title"]: s for s in path}


# ---- укоренение ----


def test_core_comes_before_the_goal(session):
    """Вести к цели в обход непокрытого ядра нельзя — инвариант слоя."""
    user = make_user(session)
    _c(session, "Ядро", tier="core", centrality=1.0)
    goal = _c(session, "Цель", centrality=0.0)

    path = build_path(session, user.id, "ml", "understand", interests=[goal.id])

    assert _titles(path).index("Ядро") < _titles(path).index("Цель")
    assert _by_title(path)["Ядро"]["reason"] == ROOTING
    assert _by_title(path)["Цель"]["reason"] == BRANCH


def test_mastered_core_is_not_repeated(session):
    user = make_user(session)
    core = _c(session, "Ядро", tier="core")
    _mastered(session, user, core)

    path = build_path(session, user.id, "ml", "understand")

    assert [s for s in path if s["reason"] == ROOTING] == []


def test_core_is_ordered_by_prerequisites(session):
    user = make_user(session)
    first = _c(session, "Первый", tier="core", centrality=0.1)
    second = _c(session, "Второй", tier="core", centrality=0.9)
    _edge(session, first, second)

    titles = _titles(build_path(session, user.id, "ml", "understand"))

    assert titles.index("Первый") < titles.index("Второй"), "предпосылка идёт раньше"


# ---- дифференциация и ЗБР ----


def test_node_never_precedes_its_prerequisite(session):
    user = make_user(session)
    a, b, c = _c(session, "A"), _c(session, "B"), _c(session, "C")
    _edge(session, a, b)
    _edge(session, b, c)

    path = build_path(session, user.id, "ml", "understand")

    assert _titles(path) == ["A", "B", "C"]
    assert {s["reason"] for s in path} == {DIFFERENTIATION}, "ни ядра, ни интересов — чистая дифференциация"


def test_specializes_also_orders_the_path(session):
    """Общее → частное: прогрессивная дифференциация."""
    user = make_user(session)
    general, specific = _c(session, "Общее"), _c(session, "Частное")
    _edge(session, general, specific, kind="specializes")

    titles = _titles(build_path(session, user.id, "ml", "understand"))

    assert titles.index("Общее") < titles.index("Частное")


def test_more_central_node_goes_first_when_both_are_ready(session):
    user = make_user(session)
    _c(session, "Периферия", centrality=0.1)
    _c(session, "Фундамент", centrality=0.9)

    assert _titles(build_path(session, user.id, "ml", "understand"))[0] == "Фундамент"


def test_known_nodes_are_skipped(session):
    user = make_user(session)
    known = _c(session, "Известное")
    _c(session, "Новое")
    _mastered(session, user, known)

    assert _titles(build_path(session, user.id, "ml", "understand")) == ["Новое"]


def test_mastered_prerequisite_unblocks_its_dependant(session):
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    _mastered(session, user, a)

    assert _titles(build_path(session, user.id, "ml", "understand")) == ["B"]


def test_cycle_does_not_hang_the_builder(session):
    """Граф от LLM может прийти с циклом — построение обязано завершиться."""
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    _edge(session, b, a)

    path = build_path(session, user.id, "ml", "understand")

    assert path == [], "взаимно заблокированные узлы просто не попадают в путь"


# ---- спираль ----


def test_core_is_raised_when_the_goal_is_higher(session):
    user = make_user(session)
    core = _c(session, "Ядро", tier="core")
    _mastered(session, user, core, bloom="remember")

    path = build_path(session, user.id, "ml", "apply")

    step = _by_title(path)["Ядро"]
    assert step["reason"] == SPIRAL
    assert step["bloom"] == "apply"


def test_no_spiral_when_the_goal_is_already_reached(session):
    user = make_user(session)
    core = _c(session, "Ядро", tier="core")
    _mastered(session, user, core, bloom="understand")

    assert build_path(session, user.id, "ml", "understand") == []


def test_spiral_skips_the_theory_again(session):
    user = make_user(session)
    core = _c(session, "Ядро", tier="core")
    _mastered(session, user, core, bloom="remember")

    step = _by_title(build_path(session, user.id, "ml", "apply"))["Ядро"]

    assert "concept_study" not in [a["type"] for a in step["activities"]]


# ---- цепочка активностей ----


def test_chain_rises_through_the_blooms(session):
    user = make_user(session)
    _c(session, "A")

    activities = build_path(session, user.id, "ml", "apply")[0]["activities"]

    assert [a["type"] for a in activities][:3] == [
        "concept_study",
        "concept_recall",
        "concept_apply",
    ]
    assert activities[-1]["type"] == "srs", "удержание замыкает цепочку"


def test_apply_is_absent_below_the_apply_goal(session):
    user = make_user(session)
    _c(session, "A")

    activities = build_path(session, user.id, "ml", "understand")[0]["activities"]

    assert "concept_apply" not in [a["type"] for a in activities]


def test_misconception_adds_a_contrast_activity(session):
    """Заблуждение чинится противопоставлением, а не повторением."""
    user = make_user(session)
    wrong, right = _c(session, "Заблуждение"), _c(session, "Верное")
    _edge(session, wrong, right, kind="misconception")

    step = _by_title(build_path(session, user.id, "ml", "understand"))["Верное"]

    assert "concept_contrast" in [a["type"] for a in step["activities"]]


def test_bloom_never_exceeds_what_the_node_supports(session):
    user = make_user(session)
    _c(session, "A", blooms=("remember",))

    assert build_path(session, user.id, "ml", "create")[0]["bloom"] == "remember"


def test_interleaving_brings_earlier_material_back(session):
    user = make_user(session)
    for i in range(5):
        _c(session, f"Узел{i}", centrality=1 - i / 10)

    path = build_path(session, user.id, "ml", "understand")

    revisits = [a for s in path for a in s["activities"] if a.get("conceptId")]
    assert revisits, "материал должен возвращаться, а не оставаться позади"


def test_unknown_bloom_is_rejected(session):
    with pytest.raises(ValueError):
        build_path(session, make_user(session).id, "ml", "выдумка")


def test_empty_domain_gives_an_empty_path(session):
    assert build_path(session, make_user(session).id, "пусто", "understand") == []


# ---- сохранение и прогресс ----


def test_course_is_persisted_and_replaced_not_duplicated(session):
    user = make_user(session)
    _c(session, "A")

    first = generate_course(session, user.id, "ml", "understand")
    second = generate_course(session, user.id, "ml", "apply")

    assert first.id == second.id
    assert second.target["bloom"] == "apply"


def test_progress_survives_a_rebuild(session):
    """Ретест пересобирает путь, но пройденное остаётся пройденным."""
    user = make_user(session)
    a = _c(session, "A", centrality=0.9)
    _c(session, "B", centrality=0.1)
    course = generate_course(session, user.id, "ml", "understand")
    mark_completed(session, course, str(a.id))

    rebuilt = generate_course(session, user.id, "ml", "understand")

    assert str(a.id) in rebuilt.progress["completed"]


def test_view_points_at_the_next_step(session):
    user = make_user(session)
    a = _c(session, "A", centrality=0.9)
    _c(session, "B", centrality=0.1)
    course = generate_course(session, user.id, "ml", "understand")

    view = course_view(course)
    assert view["current"]["title"] == "A"
    assert view["completed"] == 0

    mark_completed(session, course, str(a.id))
    view = course_view(course)
    assert view["current"]["title"] == "B"
    assert view["completed"] == 1


# ---- эндпоинты ----


def test_endpoints_build_read_and_advance(session, client):
    a = _c(session, "A", centrality=0.9)
    _c(session, "B", centrality=0.1)
    api = client(make_user(session))

    created = api.post("/graph/course/ml", json={"target_bloom": "understand"})
    assert created.status_code == 200
    assert created.json()["total"] == 2

    assert api.get("/graph/course/ml").json()["current"]["title"] == "A"

    advanced = api.post("/graph/course/ml/complete", json={"concept_id": str(a.id)})
    assert advanced.json()["current"]["title"] == "B"


def test_reading_a_course_that_was_never_built_is_404(session, client):
    assert client(make_user(session)).get("/graph/course/ml").status_code == 404


def test_endpoint_rejects_an_unknown_bloom(session, client):
    _c(session, "A")

    response = client(make_user(session)).post(
        "/graph/course/ml", json={"target_bloom": "выдумка"}
    )

    assert response.status_code == 422
