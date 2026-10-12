"""Многоязычный граф и разбор источников на двух языках (V-0138, V-0139; R-0079, R-0080)."""

from __future__ import annotations

import pytest

from core.config import settings
from modules.knowledge import funnel, langs, merge, node_compile, profile_sources, provenance
from modules.knowledge.models import Concept, ConceptSource, ConceptTerm
from tests.conftest import make_user
from tests.kg_helpers import Gateway, claim, concept, document, term


class SameVector:
    """Эмбеддер-заглушка: смысл один — вектор один (так ведёт себя bge-m3 на переводе)."""

    model = "fake-multilingual"
    dim = 1024

    def embed(self, texts):
        v = [1.0] + [0.0] * 1023
        return [v for _ in texts]


@pytest.fixture
def ru_en(session):
    d_en, f_en = document(session, title="Calculus", lang="en", domain="calculus")
    d_ru, f_ru = document(session, title="Матанализ", lang="ru", domain="calculus", data=b"ru")
    en = concept(session, "Derivative", domain="calculus")
    ru = concept(session, "Производная", domain="calculus")
    term(
        session, en, "ru", "Производная"
    )  # перевод, который дала модель при разборе английского источника
    provenance.link_concept(session, en, [f_en[0].id])
    provenance.link_concept(session, ru, [f_ru[0].id])
    claim(
        session,
        en,
        "A derivative is a rate of change.",
        f_en[0],
        status="proposed",
        text_ru="Производная — скорость изменения.",
    )
    claim(session, ru, "Производная — скорость изменения.", f_ru[0], status="proposed")
    return en, ru


def test_russian_and_english_fragments_become_one_node_with_two_links(session, ru_en):
    merge.merge_domain(session, "calculus", Gateway(), SameVector())
    nodes = session.query(Concept).filter(Concept.domain == "calculus").all()
    assert len(nodes) == 1
    assert session.query(ConceptSource).filter_by(concept_id=nodes[0].id).count() == 2
    terms = {(t.lang, t.term) for t in session.query(ConceptTerm).filter_by(concept_id=nodes[0].id)}
    assert ("ru", "Производная") in terms


def compile_answer():
    return {
        "summary": {"en": {"text": "Rate of change."}, "ru": {"text": "Скорость изменения."}},
        "sections": [
            {
                "claims": [1],
                "i18n": {
                    "en": {"heading": "Definition", "body": "Rate."},
                    "ru": {"heading": "Определение", "body": "Скорость."},
                },
            }
        ],
        "cards": [],
        "tests": [],
        "clozes": [],
    }


def test_switching_language_returns_ready_text_without_a_model(session, client):
    d, f = document(session, domain="calculus")
    c = concept(session, "Derivative", domain="calculus", status="approved")
    term(session, c, "ru", "Производная")
    claim(session, c, "A derivative is a rate of change.", f[0])
    node_compile.compile_concept(
        session, c, Gateway(compile_answer()), node_compile.CompileReport()
    )
    user = make_user(session)
    api = client(user)
    en = api.get(f"/graph/nodes/{c.id}", params={"lang": "en"}).json()
    ru = api.get(f"/graph/nodes/{c.id}", params={"lang": "ru"}).json()
    assert en["content"]["sections"][0]["heading"] == "Definition"
    assert ru["content"]["sections"][0]["heading"] == "Определение"
    assert ru["title"] == "Производная" and en["title"] == "Derivative"


def test_profile_language_is_used_when_the_request_has_none(session, client):
    d, f = document(session, domain="calculus")
    c = concept(session, "Derivative", domain="calculus", status="approved")
    claim(session, c, "A derivative is a rate of change.", f[0])
    node_compile.compile_concept(
        session, c, Gateway(compile_answer()), node_compile.CompileReport()
    )
    user = make_user(session)
    api = client(user)
    assert api.put("/auth/me/lang", json={"studyLang": "en"}).status_code == 200
    assert (
        api.get(f"/graph/nodes/{c.id}").json()["content"]["sections"][0]["heading"] == "Definition"
    )
    assert api.put("/auth/me/lang", json={"studyLang": "ru"}).status_code == 200
    assert (
        api.get(f"/graph/nodes/{c.id}").json()["content"]["sections"][0]["heading"] == "Определение"
    )
    bad = api.put("/auth/me/lang", json={"studyLang": "de"})
    assert bad.status_code == 422 and bad.json()["code"] == "lang_unsupported"


def test_language_of_the_interface_does_not_erase_the_study_language(session, client):
    api = client(make_user(session))
    api.put("/auth/me/lang", json={"studyLang": "en"})
    me = api.put("/auth/me/lang", json={"uiLang": "ru"}).json()
    assert me["profile"]["studyLang"] == "en" and me["profile"]["uiLang"] == "ru"


# ---- V-0139 ----


def test_queries_for_an_area_are_built_in_both_languages():
    qs = profile_sources.queries_for(
        "Calculus", ["Limits"], ["Derivative"], ["Математический анализ"]
    )
    assert "Calculus" in qs and "Математический анализ" in qs


def test_area_title_in_the_other_language_comes_from_one_short_request():
    gw = Gateway({"titles": {"en": "Calculus", "ru": "Математический анализ"}})
    titles = profile_sources.titles_in_languages(gw, "Calculus")
    assert gw.calls == 1 and "Математический анализ" in titles


def test_unsupported_language_is_refused_without_the_model(session, monkeypatch):
    monkeypatch.setattr(settings, "funnel_enabled", True)
    german = (
        "Die Gruppentheorie untersucht Mengen mit einer Verknüpfung und ist die Grundlage der Algebra. "
        * 8
    )
    doc, frags = document(session, texts=[german] * 30)
    gw = Gateway()
    r = funnel.screen(session, doc, frags, gw)
    assert not r.ok and r.step == "language" and gw.calls == 0 and doc.quarantined


def test_language_is_detected_without_the_model():
    assert (
        langs.detect_lang(
            "The theory of groups studies sets with an operation and it is the basis. " * 6
        )
        == "en"
    )
    assert (
        langs.detect_lang(
            "Теория групп изучает множества с операцией и является основой алгебры. " * 6
        )
        == "ru"
    )
    assert langs.detect_lang("short") is None


def test_admin_sees_the_split_by_language(session, client):
    document(session, title="En book", lang="en", tier="A")
    document(session, title="Ru book", lang="ru", tier="B", data=b"r")
    admin = make_user(session, superuser=True)
    items = client(admin).get("/graph/sources").json()
    assert {i["title"]: i["lang"] for i in items} == {"En book": "en", "Ru book": "ru"}
    assert {i["trustTier"] for i in items} == {"A", "B"}
