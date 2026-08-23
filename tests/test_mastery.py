"""Модель освоенности: приор от предпосылок, байесовское обновление, статусы (KG4-01)."""

from __future__ import annotations

import pytest

from modules.knowledge.mastery import (
    CONFIDENT_ENOUGH,
    KNOWN_THRESHOLD,
    MasteryState,
    load_map,
    load_state,
    prerequisite_map,
    prerequisites_known,
    prior_from_prerequisites,
    save_state,
    status_for,
    update,
)
from modules.knowledge.models import Concept, ConceptEdge, UserConcept
from tests.conftest import make_user


def _c(session, title, *, domain="ml", tier="derived", blooms=("remember",)):
    c = Concept(
        domain=domain,
        title=title,
        tier=tier,
        content={"summary": "s"},
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


# ---- приор ----


def test_prior_is_low_without_mastered_prerequisites():
    state = prior_from_prerequisites([])

    assert state.estimate < 0.25


def test_mastered_prerequisites_raise_the_prior():
    weak = prior_from_prerequisites([0.0])
    strong = prior_from_prerequisites([1.0])

    assert strong.estimate > weak.estimate
    assert strong.estimate > 0.6


def test_unmet_prerequisite_lowers_the_prior_below_a_root(session):
    """Регрессия: аддитивная формула давала глубоким узлам приор ВЫШЕ корневого."""
    root = prior_from_prerequisites([])
    blocked = prior_from_prerequisites([root.estimate])

    assert blocked.estimate < root.estimate


def test_the_weakest_prerequisite_decides(session):
    """Одной непройденной предпосылки достаточно, чтобы узел был недоступен."""
    mixed = prior_from_prerequisites([1.0, 0.1])
    weakest = prior_from_prerequisites([0.1])

    assert mixed.estimate == pytest.approx(weakest.estimate)


def test_prior_carries_no_confidence():
    """Приор — догадка, а не свидетельство: он не должен считаться знанием."""
    assert prior_from_prerequisites([1.0]).confidence < CONFIDENT_ENOUGH


# ---- обновление ----


def test_correct_answer_raises_the_estimate():
    before = MasteryState()
    after = update(before, 1.0, "remember")

    assert after.estimate > before.estimate
    assert after.observations == 1


def test_wrong_answer_lowers_the_estimate():
    before = MasteryState()

    assert update(before, 0.0, "remember").estimate < before.estimate


def test_evidence_increases_confidence():
    state = MasteryState()
    for _ in range(6):
        state = update(state, 1.0, "remember")

    assert state.confidence > MasteryState().confidence
    assert state.estimate > KNOWN_THRESHOLD


def test_partial_score_moves_both_sides():
    after = update(MasteryState(), 0.5, "remember")

    assert after.alpha > 1.0
    assert after.beta > 1.0


def test_bloom_reached_only_grows():
    state = update(MasteryState(), 1.0, "understand")
    assert state.bloom_reached == "understand"

    state = update(state, 1.0, "remember")
    assert state.bloom_reached == "understand", "низкая ступень не понижает достигнутую"


def test_failed_answer_does_not_claim_a_bloom():
    assert update(MasteryState(), 0.0, "apply").bloom_reached is None


@pytest.mark.parametrize("score,expected", [(-5, 0.0), (5, 1.0)])
def test_score_is_clamped(score, expected):
    after = update(MasteryState(alpha=1, beta=1), score, "remember")

    assert after.alpha - 1.0 == pytest.approx(expected)


# ---- статусы ----


def test_locked_when_prerequisites_are_unknown():
    assert status_for(MasteryState(), prerequisites_known=False) == "locked"


def test_frontier_when_prerequisites_known_and_untouched():
    assert status_for(MasteryState(), prerequisites_known=True) == "frontier"


def test_learning_after_the_first_observation():
    state = update(MasteryState(), 0.0, "remember")

    assert status_for(state, prerequisites_known=True) == "learning"


def test_known_requires_both_estimate_and_confidence():
    confident = MasteryState()
    for _ in range(8):
        confident = update(confident, 1.0, "remember")
    assert status_for(confident, prerequisites_known=True) == "known"

    lucky = update(MasteryState(), 1.0, "remember")
    assert status_for(lucky, prerequisites_known=True) != "known", "одного ответа мало"


# ---- карта по графу ----


def test_prior_propagates_along_prerequisites(session):
    """Освоенная предпосылка поднимает приор зависимого узла."""
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    strong = MasteryState(alpha=9, beta=1)
    save_state(session, user.id, "ml", a.id, strong)

    states = load_map(session, user.id, "ml")

    assert states[b.id].estimate > prior_from_prerequisites([]).estimate


def test_saved_state_wins_over_the_prior(session):
    user = make_user(session)
    a = _c(session, "A")
    save_state(session, user.id, "ml", a.id, MasteryState(alpha=9, beta=1))

    assert load_map(session, user.id, "ml")[a.id].estimate > 0.8


def test_probing_creates_a_personal_row_without_copying_content(session):
    """Освоенность персональна — под неё заводится строка COW, но не копия канона."""
    user = make_user(session)
    a = _c(session, "A")

    save_state(session, user.id, "ml", a.id, MasteryState(alpha=2, beta=1))

    row = session.query(UserConcept).filter_by(user_id=user.id, base_concept_id=a.id).one()
    assert row.content_override is None
    assert row.origin == "inherited"


def test_prerequisites_known_reads_the_whole_chain(session):
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    prereqs = prerequisite_map(session, "ml")

    states = load_map(session, user.id, "ml")
    assert prerequisites_known(b.id, prereqs, states) is False

    save_state(session, user.id, "ml", a.id, MasteryState(alpha=9, beta=1))
    states = load_map(session, user.id, "ml")
    assert prerequisites_known(b.id, prereqs, states) is True


def test_cyclic_prerequisites_do_not_hang(session):
    user = make_user(session)
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    _edge(session, b, a)

    states = load_map(session, user.id, "ml")

    assert set(states) == {a.id, b.id}


def test_corrupt_mastery_falls_back_to_the_prior():
    assert load_state({"мусор": 1}).observations == 0
    assert load_state(None).observations == 0
