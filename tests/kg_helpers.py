"""Общие заготовки для проверок конвейера знаний V-0130…V-0139: документы, фрагменты, утверждения."""

from __future__ import annotations

from datetime import datetime, timezone

from core.objects import MemoryObjectStore
from modules.knowledge import provenance
from modules.knowledge.models import (
    Claim,
    ClaimSource,
    Concept,
    ConceptArea,
    ConceptTerm,
    SourceDocument,
)

THEORY = {
    "summary": "Теория узла. " * 12,
    "sections": [
        {"heading": "Идея", "body": "Подробно.", "examples": ["а"], "counter_examples": []}
    ],
    "references": [],
}


class Gateway:
    """Модель-заглушка: отвечает по очереди, считает вызовы; без ответов падает (значит, вызова быть не должно)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0
        self.prompts: list[str] = []

    def structured(self, tool, desc, schema, prompt, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"неожиданный запрос к модели: {tool}")
        return self.answers[min(self.calls - 1, len(self.answers) - 1)]


def document(
    session,
    title="Book",
    domain="algebra",
    license="cc-by",
    data=None,
    origin_url="https://example.org/book",
    texts=None,
    tier=None,
    lang=None,
):
    """Документ с фрагментами; возвращает (документ, фрагменты)."""
    doc, _ = provenance.add_document(
        session,
        title=title,
        data=data or title.encode(),
        domain=domain,
        license=license,
        origin_url=origin_url,
        store=MemoryObjectStore(),
    )
    doc.trust_tier = tier
    doc.lang = lang
    frags = provenance.add_fragments(
        session, doc, [{"text": t, "page": i} for i, t in enumerate(texts or [title], start=1)]
    )
    return doc, frags


def concept(session, title, domain="algebra", status="draft", summary=None, **kw):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content=kw.pop("content", {"summary": summary or f"{title}: объяснение", "sections": []}),
        bloom_levels=["remember", "understand", "apply"],
        difficulty=1,
        source="doc",
        status=status,
        confidence=0.5,
        **kw,
    )
    session.add(c)
    session.flush()
    return c


def claim(
    session,
    c,
    text,
    frag=None,
    status="accepted",
    quote="quote of the source text",
    volatility="stable",
    as_of=None,
    review_after=None,
    applicability=None,
    text_ru="",
):
    row = Claim(
        concept_id=c.id,
        kind="property",
        text=text,
        text_i18n={"en": text, **({"ru": text_ru} if text_ru else {})},
        status=status,
        volatility=volatility,
        as_of=as_of,
        review_after=review_after,
        applicability=applicability,
    )
    session.add(row)
    session.flush()
    if frag is not None:
        session.add(ClaimSource(claim_id=row.id, fragment_id=frag.id, quote=quote, verified=True))
        session.flush()
    return row


def area(session, c, domain, stage=None, order=None):
    session.add(ConceptArea(concept_id=c.id, domain=domain, stage=stage, stage_order=order))
    session.flush()


def term(session, c, lang, text, primary=False):
    session.add(ConceptTerm(concept_id=c.id, lang=lang, term=text, primary=primary))
    session.flush()


def now():
    return datetime.now(timezone.utc)


def docs_of(session) -> list[SourceDocument]:
    return session.query(SourceDocument).all()
