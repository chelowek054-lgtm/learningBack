"""Разбор документа в понятия со ссылками на фрагмент (T-0077, V-0094)."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from core.config import settings
from core.jobs import process_job
from core.models import Job
from core.objects import MemoryObjectStore
from modules.knowledge import ingest, provenance
from modules.knowledge.models import Concept, ConceptEdge, ConceptSource, SourceDocument
from tests.conftest import make_user

BOOK = """# Groups

A group is a set together with an operation that is associative and has an identity element.

# Examples

An example of a group is the set of integers under addition.

# Subgroups

A subgroup is a subset of a group that is itself a group under the same operation.
"""


class FakeGateway:
    """Отвечает по номеру вызова; считает вызовы, чтобы проверять «не тратит токены»."""

    def __init__(self, answers, fail_on=None):
        self.answers = list(answers)
        self.calls = 0
        self.fail_on = fail_on

    def structured(self, *args, **kwargs):
        self.calls += 1
        if self.fail_on == self.calls:
            self.fail_on = None
            raise RuntimeError("провайдер недоступен")
        return self.answers[min(self.calls - 1, len(self.answers) - 1)]


def group_answer():
    return {
        "concepts": [
            {
                "key": "group",
                "title": "Group",
                "tier": "core",
                "summary": "Множество с ассоциативной операцией и нейтральным элементом.",
                "examples": ["целые числа со сложением"],
                "sources": [1, 2],
                "quote": "A group is a set together with an operation that is associative",
                "confidence": 0.99,
            }
        ],
        "edges": [],
    }


@pytest.fixture(autouse=True)
def _small_windows(monkeypatch):
    monkeypatch.setattr(settings, "ingest_window_fragments", 2)


@pytest.fixture
def store():
    return MemoryObjectStore()


def book(session, store, domain="algebra", data=BOOK.encode()):
    doc, _ = provenance.add_document(
        session,
        title="Algebra",
        data=data,
        domain=domain,
        meta={"filename": "book.md"},
        store=store,
    )
    return doc


# ---- цитата ----


def test_quote_must_really_be_in_the_text():
    texts = ["A group is a set\ntogether with  an operation."]
    assert ingest.quote_found("a group is a set together with an operation", texts)
    assert ingest.quote_found("«A group is a set together»", texts)
    assert not ingest.quote_found("a ring is a set with two operations", texts)
    assert not ingest.quote_found("group", texts)  # слишком короткая, чтобы что-то доказывать


# ---- очистка ответа модели ----


def window_of(session, store):
    doc = book(session, store)
    frags = ingest.parse_document(session, doc, "book.md", BOOK.encode())
    return doc, frags


def test_unfounded_claims_are_dropped_and_reasons_named(session, store):
    _, frags = window_of(session, store)
    ok = group_answer()["concepts"][0]
    raw = {
        "concepts": [
            ok,
            {
                **ok,
                "key": "ghost",
                "title": "Ghost",
                "quote": "this sentence is not in the book anywhere",
            },
            {**ok, "key": "nosrc", "title": "NoSource", "sources": []},
            {**ok, "key": "badidx", "title": "BadIdx", "sources": [99]},
            {**ok, "key": "nosum", "title": "NoSummary", "summary": " "},
            {"key": "x", "title": " ", "sources": [1], "quote": "A group is a set together with"},
        ],
        "edges": [
            {"from": "group", "to": "ghost", "type": "prereq", "sources": [1]},
            {"from": "group", "to": "group", "type": "prereq", "sources": [1]},
            {"from": "group", "to": "nosrc", "type": "nonsense", "sources": [1]},
        ],
    }
    cleaned = ingest.clean_extraction(raw, frags[:2])
    assert [c["key"] for c in cleaned.concepts] == ["group"]
    assert cleaned.edges == []
    assert len(cleaned.dropped) == 8
    assert any("цитата не найдена" in d for d in cleaned.dropped)
    assert (
        cleaned.concepts[0]["confidence"] == ingest.MAX_CONFIDENCE
    )  # модель не вправе быть уверенной


def test_edge_needs_both_ends_and_a_fragment(session, store):
    _, frags = window_of(session, store)
    a = group_answer()["concepts"][0]
    b = {
        **a,
        "key": "subgroup",
        "title": "Subgroup",
        "sources": [1],
        "quote": "A group is a set together with an operation",
    }
    raw = {
        "concepts": [a, b],
        "edges": [
            {"from": "group", "to": "subgroup", "type": "prereq", "sources": [1]},
            {"from": "subgroup", "to": "group", "type": "specializes", "sources": []},
        ],
    }
    cleaned = ingest.clean_extraction(raw, frags[:2])
    assert [(e["from"], e["to"]) for e in cleaned.edges] == [("group", "subgroup")]


def test_duplicate_concept_in_one_window_merges_sources(session, store):
    _, frags = window_of(session, store)
    a = group_answer()["concepts"][0]
    twin = {**a, "sources": [2], "quote": "An example of a group is the set of integers"}
    cleaned = ingest.clean_extraction(
        {"concepts": [{**a, "sources": [1]}, twin], "edges": []}, frags[:2]
    )
    assert len(cleaned.concepts) == 1 and len(cleaned.concepts[0]["fragments"]) == 2


# ---- оглавление и окна ----


def test_pdf_outline_gives_chapter_headings():
    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(100, 100)
    ch1 = writer.add_outline_item("Groups", 0)
    writer.add_outline_item("Subgroups", 1, parent=ch1)
    writer.add_outline_item("Rings", 3)
    buf = io.BytesIO()
    writer.write(buf)
    headings = ingest.pdf_headings(buf.getvalue())
    assert headings == {1: "Groups", 2: "Subgroups", 4: "Rings"}
    assert ingest.heading_for(3, headings) == "Subgroups"  # глава действует до следующей закладки
    assert ingest.heading_for(5, headings) == "Rings"
    assert ingest.heading_for(None, headings) is None and ingest.heading_for(1, {}) is None


def test_windows_do_not_split_a_chapter_when_it_can_be_avoided(session, store):
    _, frags = window_of(session, store)
    big = ingest.windows(frags, 10)
    assert len(big) == 1 and len(big[0]) == len(frags)
    small = ingest.windows(frags, 2)
    assert all(len(w) <= 2 for w in small) and sum(len(w) for w in small) == len(frags)


# ---- разбор целого документа ----


def test_ingest_saves_drafts_with_sources_and_pages(session, store, monkeypatch):
    monkeypatch.setattr(settings, "ingest_window_fragments", 10)  # вся книга одним окном
    doc = book(session, store)
    gateway = FakeGateway([group_answer()])

    state = ingest.ingest(session, doc, gateway, store=store)

    assert state["status"] == "done" and state["concepts"] == 1 and state["edges"] == 0
    concept = session.query(Concept).filter_by(domain="algebra", key="group").one()
    assert concept.status == "draft" and concept.source == "doc"
    assert concept.confidence == ingest.MAX_CONFIDENCE
    view = provenance.concept_sources(session, concept.id)
    assert {v["document"] for v in view} == {"Algebra"} and len(view) == 2
    assert all("group" in v["text"].lower() for v in view)
    assert concept.content["sections"][0]["examples"] == ["целые числа со сложением"]


def test_second_ingest_of_the_same_document_spends_nothing(session, store):
    doc = book(session, store)
    gateway = FakeGateway([group_answer()])
    ingest.ingest(session, doc, gateway, store=store)
    calls = gateway.calls

    again = ingest.ingest(session, doc, gateway, store=store)

    assert again["skipped"] is True and gateway.calls == calls
    assert session.query(Concept).filter_by(domain="algebra").count() == 1


def test_failure_in_a_later_window_resumes_without_repeating_earlier_ones(session, store):
    doc = book(session, store)
    gateway = FakeGateway([group_answer(), {"concepts": [], "edges": []}], fail_on=2)

    with pytest.raises(RuntimeError):
        ingest.ingest(session, doc, gateway, store=store)
    assert doc.meta["ingest"]["done"] == 1 and doc.meta["ingest"]["status"] == "running"
    first_calls = gateway.calls

    state = ingest.ingest(session, doc, gateway, store=store)

    assert state["status"] == "done" and state["done"] == state["windows"]
    assert gateway.calls - first_calls == state["windows"] - 1  # первое окно заново не просили
    assert session.query(Concept).filter_by(domain="algebra").count() == 1


def test_known_concept_gets_another_source_instead_of_a_duplicate(session, store, monkeypatch):
    monkeypatch.setattr(settings, "ingest_window_fragments", 10)
    existing = Concept(
        domain="algebra",
        title="Group",
        key="group",
        tier="core",
        content={"summary": "x", "sections": []},
        source="curated",
        status="approved",
    )
    session.add(existing)
    session.flush()
    doc = book(session, store)

    ingest.ingest(session, doc, FakeGateway([group_answer()]), store=store)

    assert session.query(Concept).filter_by(domain="algebra", key="group").count() == 1
    assert session.get(Concept, existing.id).status == "approved"  # подтверждённое не сбрасывается
    assert session.query(ConceptSource).filter_by(concept_id=existing.id).count() == 2


def test_edges_between_concepts_become_draft_links_with_sources(session, store):
    a = group_answer()["concepts"][0]
    b = {
        **a,
        "key": "subgroup",
        "title": "Subgroup",
        "sources": [1],
        "quote": "A group is a set together with an operation",
    }
    answer = {
        "concepts": [a, b],
        "edges": [{"from": "group", "to": "subgroup", "type": "prereq", "sources": [1]}],
    }
    doc = book(session, store)

    state = ingest.ingest(session, doc, FakeGateway([answer]), store=store)

    assert state["edges"] == 1
    edge = session.query(ConceptEdge).one()
    assert edge.status == "draft" and edge.type == "prereq"
    assert provenance.edge_sources(session, edge.id)


def test_hallucinated_concept_never_reaches_the_graph(session, store):
    ghost = {
        **group_answer()["concepts"][0],
        "quote": "an invented sentence about some topic nobody wrote",
    }
    doc = book(session, store)

    state = ingest.ingest(
        session, doc, FakeGateway([{"concepts": [ghost], "edges": []}]), store=store
    )

    assert state["concepts"] == 0 and state["dropped"] >= 1
    assert session.query(Concept).filter_by(domain="algebra").count() == 0


def test_document_without_domain_or_file_is_refused(session, store):
    no_domain, _ = provenance.add_document(session, title="A", data=b"x", store=store)
    with pytest.raises(ValueError, match="область"):
        ingest.ingest(session, no_domain, FakeGateway([]), store=store)
    no_file = SourceDocument(title="B", content_hash="h-no-file", domain="algebra")
    session.add(no_file)
    session.flush()
    with pytest.raises(ValueError, match="не сохранён"):
        ingest.ingest(session, no_file, FakeGateway([]), store=store)


def test_window_limit_is_reported_as_truncation(session, store):
    doc = book(session, store)
    state = ingest.ingest(
        session, doc, FakeGateway([{"concepts": [], "edges": []}]), store=store, max_windows=1
    )
    assert state["windows"] == 1 and state["truncated"] is True


# ---- фоновая задача ----


def job_for(session, doc, user):
    return ingest.enqueue_ingest(session, doc, user.id)


def test_job_runs_through_the_worker_path_and_retries_a_transient_failure(
    session, store, monkeypatch
):
    from core import objects

    monkeypatch.setattr(objects, "_store", store)
    user = make_user(session)
    doc = book(session, store)
    job = job_for(session, doc, user)
    gateway = FakeGateway([group_answer(), {"concepts": [], "edges": []}], fail_on=2)

    process_job(session, job, gateway)
    assert job.status == "pending" and job.result["retrying"] is True  # сбой временный
    assert doc.meta["ingest"]["done"] == 1

    process_job(session, job, gateway)
    assert job.status == "done" and job.result["status"] == "done"
    assert session.query(Concept).filter_by(domain="algebra").count() == 1


def test_job_with_missing_document_fails_for_good(session):
    user = make_user(session)
    job = Job(
        user_id=user.id,
        type="ingest_document",
        status="pending",
        input_ref={"documentId": "00000000-0000-0000-0000-000000000000"},
    )
    session.add(job)
    session.flush()
    process_job(session, job, FakeGateway([]))
    assert job.status == "failed" and "не найден" in job.result["error"]
    bad = Job(
        user_id=user.id, type="ingest_document", status="pending", input_ref={"documentId": "oops"}
    )
    session.add(bad)
    session.flush()
    process_job(session, bad, FakeGateway([]))
    assert bad.status == "failed"
