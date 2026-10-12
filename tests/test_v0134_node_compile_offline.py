"""Сборка узла и обучение без модели (V-0134, R-0075, T-0159, T-0160, T-0161)."""

from __future__ import annotations

import pytest

from core.config import settings
from modules.knowledge import assessment_store, node_compile
from modules.knowledge.answer import score_answer
from modules.knowledge.assessment import AssessmentItem
from modules.knowledge.course import generate_course
from modules.knowledge.models import LearningItem
from modules.knowledge.study import start_step
from tests.conftest import make_user
from tests.kg_helpers import Gateway, claim, concept, document


def lang(en, ru):
    return {"en": en, "ru": ru}


def node_answer():
    return {
        "summary": {"en": {"text": "A group."}, "ru": {"text": "Группа."}},
        "sections": [
            {
                "claims": [1, 2],
                "i18n": {
                    "en": {
                        "heading": "Definition",
                        "body": "A set with an operation.",
                        "examples": ["Z"],
                    },
                    "ru": {
                        "heading": "Определение",
                        "body": "Множество с операцией.",
                        "examples": ["Z"],
                    },
                },
            },
            {  # без ссылки на утверждение — отбрасывается
                "claims": [],
                "i18n": {
                    "en": {"heading": "Invented", "body": "Not from the claims."},
                    "ru": {"heading": "Выдумка", "body": "Не из утверждений."},
                },
            },
        ],
        "cards": [
            {
                "claims": [1],
                "i18n": lang(
                    {"front": "Group?", "back": "A set"}, {"front": "Группа?", "back": "Множество"}
                ),
            }
        ],
        "tests": [
            {
                "claims": [1],
                "answer": 1,
                "i18n": lang(
                    {
                        "question": "What is a group?",
                        "options": ["A number", "A set with an operation", "A line", "A point"],
                    },
                    {
                        "question": "Что такое группа?",
                        "options": ["Число", "Множество с операцией", "Линия", "Точка"],
                    },
                ),
            }
        ],
        "clozes": [
            {
                "claims": [2],
                "i18n": lang(
                    {"text": "A group has an ____ element.", "answer": "identity"},
                    {"text": "В группе есть ____ элемент.", "answer": "нейтральный"},
                ),
            }
        ],
    }


@pytest.fixture
def node(session):
    d, f = document(session)
    c = concept(session, "Group", domain="algebra", status="approved")
    claim(session, c, "A group is a set with an operation.", f[0])
    claim(session, c, "A group has an identity element.", f[0])
    return c


def test_section_without_claim_reference_is_dropped(session, node):
    rep = node_compile.CompileReport()
    assert node_compile.compile_concept(session, node, Gateway(node_answer()), rep)
    assert rep.sections == 1 and rep.dropped == 1
    headings = [s["heading"] for s in node.content["i18n"]["en"]["sections"]]
    assert headings == ["Definition"]
    assert [s["heading"] for s in node.content["i18n"]["ru"]["sections"]] == ["Определение"]
    assert node.content["i18n"]["en"]["sections"][0]["claims"]


def test_unchanged_claims_do_not_trigger_a_second_compile(session, node):
    gw = Gateway(node_answer())
    rep = node_compile.CompileReport()
    node_compile.compile_concept(session, node, gw, rep)
    rep2 = node_compile.CompileReport()
    assert not node_compile.compile_concept(session, node, gw, rep2)
    assert gw.calls == 1 and rep2.skipped == 1


def test_bank_has_items_on_both_languages_with_keys(session, node):
    node_compile.compile_concept(
        session, node, Gateway(node_answer()), node_compile.CompileReport()
    )
    rows = session.query(LearningItem).filter_by(concept_id=node.id).all()
    assert {(r.lang, r.kind) for r in rows} == {
        (lg, k) for lg in ("en", "ru") for k in ("card", "mcq", "cloze")
    }
    mcq = next(r for r in rows if r.kind == "mcq" and r.lang == "en")
    assert mcq.answer_key == {"index": 1} and mcq.claim_ids


def test_learning_step_works_with_the_model_switched_off(session, node, monkeypatch):
    node_compile.compile_concept(
        session, node, Gateway(node_answer()), node_compile.CompileReport()
    )

    def forbidden(*a, **k):
        raise AssertionError("в обучении обратились к модели")

    monkeypatch.setattr(assessment_store, "generate_assessment", forbidden)
    monkeypatch.setattr(settings, "llm_api_key", "")
    user = make_user(session)
    course = generate_course(session, user.id, "algebra", "apply")
    acts = start_step(session, user.id, course, str(node.id), "en")
    by_type = {a.type: a for a in acts}
    assert "concept_study" in by_type
    study = by_type["concept_study"].payload
    assert study["content"]["sections"][0]["heading"] == "Definition"
    recall = by_type["concept_recall"].payload["item"]
    assert recall["prompt"] == "What is a group?"
    assert [o["correct"] for o in recall["options"]] == [False, True, False, False]
    apply_item = by_type["concept_apply"].payload["item"]
    assert apply_item["exact"] and apply_item["expected"] == "identity"


def test_choice_and_cloze_are_checked_without_model(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    cloze = AssessmentItem(prompt="x ____", expected="identity", exact=True)
    assert score_answer(cloze, " Identity. ")[0] == 1.0
    assert score_answer(cloze, "inverse")[0] == 0.0


def test_other_language_gives_ready_items_without_model(session, node, monkeypatch):
    node_compile.compile_concept(
        session, node, Gateway(node_answer()), node_compile.CompileReport()
    )
    monkeypatch.setattr(assessment_store, "generate_assessment", lambda *a, **k: 1 / 0)
    ru, _ = assessment_store.get_or_generate(session, node, "remember", "test", lang="ru")
    en, _ = assessment_store.get_or_generate(session, node, "remember", "test", lang="en")
    assert ru.items[0].prompt == "Что такое группа?" and en.items[0].prompt == "What is a group?"
