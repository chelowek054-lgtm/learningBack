"""Способы изучения как модули: контракт, отключение, добавление без правки ядра (T-0053, V-0081)."""

from __future__ import annotations

import uuid

import pytest

from core import modules
from core.evidence import Evidence, dispatch
from core.manifest import ModuleManifest
from core.methods import (
    APPLY,
    PURPOSES,
    READ,
    RECALL,
    REMEMBER,
    MethodError,
    StudyMethod,
    check_methods,
    for_purpose,
)
from modules.knowledge import api as kg
from modules.knowledge.course import build_path
from modules.knowledge.models import Concept
from modules.srs import RATING_SCORE, evidence_from_review
from tests.conftest import make_user

THEORY = {
    "summary": "Теория узла. " * 12,
    "sections": [
        {"heading": "Идея", "body": "Подробно.", "examples": ["а"], "counter_examples": ["б"]}
    ],
    "references": [],
}


@pytest.fixture(autouse=True)
def _clean_state():
    modules.reset_state_cache()
    yield
    modules.reset_state_cache()


def node(session, domain="d", title="Узел"):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content=THEORY,
        bloom_levels=["remember", "understand", "apply"],
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


def types_of(session, user, domain="d", bloom="apply"):
    return [a["type"] for a in build_path(session, user.id, domain, bloom)[0]["activities"]]


# ---- контракт способа ----


def test_methods_come_from_modules_with_their_owner(session):
    modules.sync_module_state(session)

    by_id = {m.id: m for m in modules.study_methods()}

    assert {"srs", "writing", "code_review", "concept_study", "concept_recall"} <= set(by_id)
    assert by_id["srs"].module == "srs" and by_id["srs"].purpose == REMEMBER
    assert by_id["writing"].module == "languages" and by_id["writing"].in_course is False


def test_every_method_names_a_known_step_and_activity_type():
    check_methods(modules.study_methods())  # не бросает
    assert all(m.purpose in PURPOSES and m.activity_type for m in modules.study_methods())


def test_contract_rejects_duplicate_unknown_and_empty_methods():
    def code(*methods):
        with pytest.raises(MethodError) as e:
            check_methods(list(methods))
        return e.value.code

    ok = StudyMethod("a", "A", REMEMBER, "t")
    assert code(ok, ok) == "duplicate_method"
    assert code(StudyMethod("b", "B", "teleport", "t")) == "unknown_purpose"
    assert code(StudyMethod("c", "C", REMEMBER, " ")) == "no_activity_type"
    assert code(StudyMethod("d", "  ", REMEMBER, "t")) == "no_title"


def test_preferred_method_wins_otherwise_first_in_course_one():
    a = StudyMethod("a", "A", REMEMBER, "ta")
    b = StudyMethod("b", "B", REMEMBER, "tb")
    hidden = StudyMethod("h", "H", REMEMBER, "th", in_course=False)

    assert for_purpose([hidden, a, b], REMEMBER).id == "a"
    assert for_purpose([a, b], REMEMBER, preferred="b").id == "b"
    assert for_purpose([a, b], REMEMBER, preferred="gone").id == "a"
    assert for_purpose([a], APPLY) is None


def test_modules_cannot_declare_the_same_method_twice():
    class A(modules.BackendModule):
        id = "mod_a"
        manifest = ModuleManifest("mod_a", "A", "1.0", provides=frozenset({"study_methods"}))

        def study_methods(self):
            return [StudyMethod("dup", "Д", REMEMBER, "x")]

    class B(A):
        id = "mod_b"
        manifest = ModuleManifest("mod_b", "B", "1.0", provides=frozenset({"study_methods"}))

    with pytest.raises(Exception) as e:
        modules.validate_modules([A(), B()])
    assert "duplicate_method" in getattr(e.value, "code", "")


# ---- курс собирается из способов ----


def test_course_chain_comes_from_enabled_methods(session):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)

    assert types_of(session, user) == ["concept_study", "concept_recall", "concept_apply", "srs"]


def test_disabling_the_repetition_module_drops_its_step_and_nothing_else(session):
    modules.sync_module_state(session)
    user = make_user(session)
    n = node(session)
    kg.record_evidence(session, user.id, "d", Evidence(n.id, "understand", 0.9, "t"))
    before = kg.get_mastery(session, user.id, "d")

    modules.set_enabled(session, "srs", False)

    types = types_of(session, user)
    assert "srs" not in types and types[0] == "concept_study"
    # Граф и данные пользователя целы: отключение способа их не трогает.
    assert kg.get_mastery(session, user.id, "d") == before
    assert kg.get_node(session, user.id, n.id)["title"] == "Узел"


def test_disabling_the_writing_module_does_not_break_graph_or_course(session):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)

    modules.set_enabled(session, "languages", False)

    assert "writing" not in {m.id for m in modules.study_methods()}
    assert types_of(session, user)[-1] == "srs"


def test_enabling_back_restores_the_step(session):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)
    modules.set_enabled(session, "srs", False)

    modules.set_enabled(session, "srs", True)

    assert types_of(session, user)[-1] == "srs"


def test_new_method_is_added_without_touching_core_graph_or_other_methods(session, monkeypatch):
    """Новый способ — модуль со своим описанием; ядро, граф и прежние способы не правятся."""

    class Flashcards(modules.BackendModule):
        id = "flash"
        manifest = ModuleManifest(
            "flash", "Флэшкарты", "1.0", provides=frozenset({"study_methods"})
        )

        def study_methods(self):
            return [StudyMethod("flash", "Флэшкарты", REMEMBER, "flash_cards", offline=True)]

    base = modules.load_modules()
    monkeypatch.setattr(modules, "_modules", [*base, Flashcards()])
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)

    # Первым остаётся прежний способ, новый доступен — как выбор, а не как замена.
    assert "flash" in {m.id for m in modules.study_methods()}
    assert modules.activity_type_for(REMEMBER) == "srs"
    assert modules.activity_type_for(REMEMBER, preferred="flash") == "flash_cards"
    # Если прежний выключен, шаг исполняется новым — без правки курса.
    modules.set_enabled(session, "srs", False)
    assert types_of(session, user)[-1] == "flash_cards"


def test_step_without_any_method_is_skipped_not_fatal(session):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)
    for mid in ("srs",):
        modules.set_enabled(session, mid, False)

    assert types_of(session, user)  # курс построился


# ---- свидетельство об освоении ----


def test_repetition_rating_becomes_common_evidence():
    cid = uuid.uuid4()

    e = evidence_from_review(cid, "good")

    assert (e.concept_id, e.bloom, e.score, e.source) == (
        cid,
        "remember",
        RATING_SCORE["good"],
        "srs",
    )
    with pytest.raises(ValueError):
        evidence_from_review(cid, "meh")


def test_evidence_of_any_method_updates_the_same_mastery(session):
    modules.sync_module_state(session)
    n = node(session)
    one, two = make_user(session), make_user(session)

    s1 = kg.record_evidence(session, one.id, "d", evidence_from_review(n.id, "easy"))
    s2 = kg.record_evidence(session, two.id, "d", Evidence(n.id, "remember", 1.0, "any_game"))

    assert s1.estimate == s2.estimate


def test_evidence_endpoint_routes_to_the_module_that_keeps_mastery(session, client):
    modules.sync_module_state(session)
    user = make_user(session)
    n = node(session)

    r = client(user).post(
        "/v1/evidence",
        json={
            "domain": "d",
            "conceptId": str(n.id),
            "bloom": "remember",
            "score": 0.9,
            "source": "x",
        },
    )

    assert r.status_code == 202 and r.json() == {"accepted": 1}
    assert kg.get_mastery(session, user.id, "d")[str(n.id)]["observations"] == 1


@pytest.mark.parametrize("score", [-0.5, 1.5])
def test_evidence_score_out_of_range_is_rejected(session, client, score):
    n = node(session)

    r = client(make_user(session)).post(
        "/v1/evidence",
        json={"domain": "d", "conceptId": str(n.id), "bloom": "remember", "score": score},
    )

    assert r.status_code == 422


def test_evidence_without_accepting_module_is_a_conflict(session, client):
    modules.sync_module_state(session)
    modules.set_enabled(session, "knowledge", False)
    n = node(session)

    r = client(make_user(session)).post(
        "/v1/evidence",
        json={"domain": "d", "conceptId": str(n.id), "bloom": "remember", "score": 0.5},
    )

    assert r.status_code == 409


def test_dispatch_counts_accepting_modules_only(session):
    n = node(session)

    count = dispatch(
        session, make_user(session).id, "d", Evidence(n.id, "remember", 0.5), modules.load_modules()
    )

    assert count == 1  # принимает только модуль, хранящий освоенность


def test_methods_endpoint_lists_what_a_person_can_choose(session, client):
    modules.sync_module_state(session)

    rows = client(make_user(session)).get("/v1/methods").json()

    srs = next(r for r in rows if r["id"] == "srs")
    assert srs == {
        "id": "srs",
        "title": "Повторение карточек",
        "purpose": "remember",
        "activityType": "srs",
        "offline": True,
        "module": "srs",
        "inCourse": True,
    }
    assert {RECALL, READ} <= {r["purpose"] for r in rows}
