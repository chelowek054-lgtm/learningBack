"""Адаптивный плейсмент: выбор зонда, оценка ответа, останов (KG4-02, KG4-03)."""

from __future__ import annotations

import pytest

from modules.knowledge.answer import score_answer
from modules.knowledge.assessment import AssessmentItem, Option
from modules.knowledge.mastery import MasteryState, save_state
from modules.knowledge.models import Concept, ConceptEdge
from modules.knowledge.placement import (
    NoProbeAvailable,
    next_probe,
    placement_map,
    probe_bloom,
    rank_candidates,
    record_answer,
)
from tests.conftest import make_user

THEORY = {
    "summary": "Backprop считает градиенты по цепному правилу и обновляет веса.",
    "sections": [
        {
            "heading": "Обратный проход",
            "body": "Градиент течёт от потерь к весам через цепное правило.",
            "examples": ["двуслойная сеть"],
            "counter_examples": ["градиент считают прямым проходом"],
        }
    ],
    "references": [],
}


def _c(session, title, *, tier="derived", centrality=0.0, blooms=("remember", "understand")):
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


def _edge(session, a, b):
    session.add(ConceptEdge(from_id=a.id, to_id=b.id, type="prereq"))
    session.flush()


# ---- ступень зонда ----


def test_probe_bloom_never_exceeds_the_target(session):
    c = _c(session, "A", blooms=("remember", "understand", "apply"))

    assert probe_bloom(c, "understand") == "understand"
    assert probe_bloom(c, "remember") == "remember"


def test_probe_bloom_falls_back_to_what_the_node_supports(session):
    c = _c(session, "A", blooms=("remember",))

    assert probe_bloom(c, "create") == "remember"


def test_probe_bloom_rejects_an_unknown_target(session):
    with pytest.raises(ValueError):
        probe_bloom(_c(session, "A"), "выдумка")


# ---- выбор зонда ----


def test_locked_nodes_are_not_probed(session):
    """Спрашивать про узел, предпосылки которого не освоены, неинформативно."""
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)

    titles = [c.title for c, _, _ in rank_candidates(session, user.id, "ml")]

    assert titles == ["A"]


def test_frontier_opens_as_prerequisites_get_mastered(session):
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    save_state(session, user.id, "ml", a.id, MasteryState(alpha=9, beta=1))

    titles = [c.title for c, _, _ in rank_candidates(session, user.id, "ml")]

    assert "B" in titles


def test_more_central_node_is_probed_first(session):
    """При равной неопределённости фундаментальный узел важнее."""
    user = make_user(session)
    _c(session, "Периферия", centrality=0.0)
    _c(session, "Ядро", centrality=1.0)

    ranked = rank_candidates(session, user.id, "ml")

    assert ranked[0][0].title == "Ядро"


def test_confident_nodes_drop_out_of_the_queue(session):
    user = make_user(session)
    a = _c(session, "A")
    confident = MasteryState(alpha=20, beta=1, observations=20)
    save_state(session, user.id, "ml", a.id, confident)

    assert rank_candidates(session, user.id, "ml") == []


def test_next_probe_returns_a_grounded_item(session):
    user = make_user(session)
    _c(session, "Backprop")

    probe = next_probe(session, user.id, "ml", "understand")

    assert probe["conceptTitle"] == "Backprop"
    assert probe["bloom"] == "understand"
    assert probe["item"]["prompt"]


def test_next_probe_skips_nodes_without_theory(session):
    user = make_user(session)
    bare = _c(session, "Ярлык")
    bare.content = {"summary": "коротко"}
    _c(session, "Настоящий")
    session.flush()

    probe = next_probe(session, user.id, "ml", "remember")

    assert probe["conceptTitle"] == "Настоящий"


def test_no_probe_when_everything_is_settled(session):
    user = make_user(session)
    a = _c(session, "A")
    save_state(session, user.id, "ml", a.id, MasteryState(alpha=20, beta=1, observations=20))

    with pytest.raises(NoProbeAvailable):
        next_probe(session, user.id, "ml", "remember")


# ---- оценка ответа ----


def test_choice_answer_is_scored_locally():
    item = AssessmentItem(
        prompt="p",
        options=[Option(text="верно", correct=True), Option(text="неверно", correct=False)],
    )

    assert score_answer(item, 0)[0] == 1.0
    assert score_answer(item, 1)[0] == 0.0
    assert score_answer(item, "верно")[0] == 1.0


def test_unrecognised_choice_scores_zero():
    item = AssessmentItem(prompt="p", options=[Option(text="a", correct=True)])

    score, explanation = score_answer(item, "чего-то другое")

    assert score == 0.0
    assert "не распознан" in explanation


def test_open_answer_is_scored_against_the_expected_text():
    item = AssessmentItem(prompt="p", expected="градиент течёт через цепное правило")

    good = score_answer(item, "градиент течёт через цепное правило")[0]
    bad = score_answer(item, "совершенно посторонний текст")[0]

    assert good == 1.0
    assert bad < good


def test_empty_answer_scores_zero():
    assert score_answer(AssessmentItem(prompt="p", expected="что-то"), "")[0] == 0.0


# ---- цикл плейсмента ----


def test_answer_updates_mastery_and_narrows_uncertainty(session):
    user = make_user(session)
    a = _c(session, "A")
    before = next_probe(session, user.id, "ml", "remember")["uncertainty"]

    state = record_answer(session, user.id, "ml", a.id, "remember", 1.0)

    assert state.observations == 1
    assert state.uncertainty < before


def test_repeated_correct_answers_close_the_node(session):
    user = make_user(session)
    a = _c(session, "A")
    for _ in range(8):
        record_answer(session, user.id, "ml", a.id, "remember", 1.0)

    node = placement_map(session, user.id, "ml")["nodes"][0]

    assert node["status"] == "known"


def test_map_reports_the_frontier(session):
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)

    result = placement_map(session, user.id, "ml")
    by_title = {n["title"]: n for n in result["nodes"]}

    assert by_title["A"]["status"] == "frontier"
    assert by_title["B"]["status"] == "locked"
    assert result["summary"] == {"frontier": 1, "locked": 1}


def test_core_coverage_is_reported(session):
    user = make_user(session)
    core = _c(session, "Ядро", tier="core")

    assert placement_map(session, user.id, "ml")["coreCovered"] is False

    for _ in range(8):
        record_answer(session, user.id, "ml", core.id, "remember", 1.0)

    assert placement_map(session, user.id, "ml")["coreCovered"] is True


# ---- эндпоинты ----


def test_probe_endpoint_walks_the_graph(session, client):
    _c(session, "Backprop")
    api = client(make_user(session))

    probe = api.get("/graph/placement/ml/probe?target=understand").json()

    assert probe["conceptTitle"] == "Backprop"
    assert probe["item"]["prompt"]


def test_answer_endpoint_scores_and_offers_the_next_probe(session, client):
    a = _c(session, "A")
    _c(session, "B")
    api = client(make_user(session))
    probe = api.get("/graph/placement/ml/probe?target=remember").json()

    result = api.post(
        "/graph/placement/answer",
        json={
            "domain": "ml",
            "concept_id": probe["conceptId"],
            "bloom": probe["bloom"],
            "answer": THEORY["summary"],
        },
    ).json()

    assert result["score"] == 1.0
    assert result["mastery"]["observations"] == 1
    assert result["next"] is not None
    assert a.id is not None


def test_probe_endpoint_reports_completion(session, client):
    user = make_user(session)
    a = _c(session, "A")
    save_state(session, user.id, "ml", a.id, MasteryState(alpha=20, beta=1, observations=20))

    body = client(user).get("/graph/placement/ml/probe?target=remember").json()

    assert body["done"] is True
    assert body["map"]["summary"]["known"] == 1


def test_probe_endpoint_rejects_an_unknown_target(session, client):
    _c(session, "A")

    response = client(make_user(session)).get("/graph/placement/ml/probe?target=выдумка")

    assert response.status_code == 422


def test_map_endpoint_returns_the_whole_domain(session, client):
    _c(session, "A")
    _c(session, "B")

    body = client(make_user(session)).get("/graph/placement/ml/map").json()

    assert len(body["nodes"]) == 2
    assert body["domain"] == "ml"
