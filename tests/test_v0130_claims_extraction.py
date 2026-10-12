"""Утверждения и ссылки на место: извлечение (V-0130, R-0071, T-0150).

Живая часть проверки (20 случайных утверждений на пересобранном графе) остаётся за человеком.
"""

from __future__ import annotations

import pytest

from core.config import settings
from core.objects import MemoryObjectStore
from modules.knowledge import ingest, provenance
from modules.knowledge.models import Claim, ClaimSource, ConceptArea, ConceptTerm, SourceFragment
from tests.kg_helpers import Gateway

BOOK = """# Groups

A group is a set together with an operation that is associative and has an identity element.

# Subgroups

A subgroup is a subset of a group that is itself a group under the same operation.
"""
Q1 = "A group is a set together with an operation that is associative"
Q2 = "A subgroup is a subset of a group that is itself a group"


def answer(claims):
    return {
        "concepts": [
            {
                "key": "group",
                "title": "Group",
                "title_ru": "Группа",
                "tier": "core",
                "summary": "A set with an associative operation.",
                "summary_ru": "Множество с ассоциативной операцией.",
                "sources": [1, 2],
                "quote": Q1,
                "confidence": 0.9,
                "claims": claims,
            }
        ],
        "edges": [],
    }


def claim(text, quote, **kw):
    return {
        "kind": "definition",
        "text": text,
        "text_ru": "",
        "quote": quote,
        "sources": [1, 2],
        **kw,
    }


@pytest.fixture(autouse=True)
def _small_windows(monkeypatch):
    monkeypatch.setattr(settings, "ingest_window_fragments", 4)


@pytest.fixture
def doc(session):
    d, _ = provenance.add_document(
        session,
        title="Algebra",
        data=BOOK.encode(),
        domain="algebra",
        license="cc-by",
        meta={"filename": "book.md"},
        store=(store := MemoryObjectStore()),
    )
    d._store = store  # noqa: SLF001
    return d


def fragments(session, doc):
    return ingest.parse_document(session, doc, "book.md", BOOK.encode())


def test_invented_quote_drops_only_that_claim(session, doc):
    frags = fragments(session, doc)
    raw = answer(
        [
            claim("A group has an operation.", Q1),
            claim("A group is always commutative.", "Every group is commutative by definition."),
        ]
    )
    cleaned = ingest.clean_extraction(raw, frags)
    assert len(cleaned.concepts) == 1
    assert [c["text"] for c in cleaned.concepts[0]["claims"]] == ["A group has an operation."]


def test_concept_without_any_real_quote_is_dropped(session, doc):
    frags = fragments(session, doc)
    raw = answer([claim("Выдумка.", "Totally invented sentence that is nowhere in the book")])
    raw["concepts"][0]["quote"] = "Totally invented sentence that is nowhere in the book"
    cleaned = ingest.clean_extraction(raw, frags)
    assert cleaned.concepts == []
    assert cleaned.dropped


def test_offsets_point_at_the_quote_in_fragment_text(session, doc):
    frags = fragments(session, doc)
    cleaned = ingest.clean_extraction(answer([claim("A subgroup is a subset.", Q2)]), frags)
    c = cleaned.concepts[0]["claims"][0]
    fragment = c["fragment"]
    assert c["start"] is not None
    assert fragment.text[c["start"] : c["end"]].lower() == Q2.lower()


def test_full_ingest_stores_claims_sources_terms_and_area(session, doc):
    gw = Gateway(answer([claim("A group has an associative operation.", Q1)]))
    state = ingest.ingest(session, doc, gw, store=doc._store)  # noqa: SLF001
    assert state["status"] == "done"
    rows = session.query(Claim).all()
    assert len(rows) == 1 and rows[0].text_i18n["en"] == rows[0].text
    cs = session.query(ClaimSource).one()
    frag = session.get(SourceFragment, cs.fragment_id)
    assert frag.text[cs.char_start : cs.char_end].lower() == Q1.lower()
    assert cs.verified
    terms = {(t.lang, t.term) for t in session.query(ConceptTerm).all()}
    assert terms == {("en", "Group"), ("ru", "Группа")}
    assert session.query(ConceptArea).one().domain == "algebra"


def test_old_format_answer_without_claims_still_gives_one_claim(session, doc):
    raw = answer([])
    del raw["concepts"][0]["claims"]
    frags = fragments(session, doc)
    cleaned = ingest.clean_extraction(raw, frags)
    assert len(cleaned.concepts[0]["claims"]) == 1
