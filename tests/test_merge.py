"""Слияние понятий: близость по векторам и решение модели (T-0078, V-0095)."""

from __future__ import annotations

import pytest

from core.config import settings
from core.embeddings import HashEmbedder
from core.jobs import process_job
from core.objects import MemoryObjectStore
from modules.knowledge import ingest, merge, provenance
from modules.knowledge.models import (
    Concept,
    ConceptConflict,
    ConceptEdge,
    ConceptEmbedding,
    ConceptSource,
    EdgeSource,
    MergeDecision,
    ReviewLog,
    UserConcept,
)
from tests.conftest import make_user

DOMAIN = "algebra"


class Judge:
    """Модель-заглушка: решение по паре заголовков; считает вопросы."""

    def __init__(self, verdicts=None, default="different"):
        self.verdicts = verdicts or {}
        self.default = default
        self.calls = 0

    def structured(self, tool, desc, schema, prompt):
        self.calls += 1
        for needle, answer in self.verdicts.items():
            if needle in prompt:
                return answer
        return {"verdict": self.default, "reason": "по умолчанию"}


def concept(session, title, summary=None, status="draft", domain=DOMAIN):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content={"summary": summary or f"{title}: объяснение", "sections": []},
        bloom_levels=["remember"],
        difficulty=1,
        source="doc",
        status=status,
        confidence=0.5,
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def frags(session):
    doc, _ = provenance.add_document(
        session, title="Book", data=b"book", store=MemoryObjectStore(), domain=DOMAIN
    )
    return provenance.add_fragments(
        session, doc, [{"text": f"fragment {i}", "page": i} for i in range(1, 7)]
    )


def src(session, c, *fragments):
    provenance.link_concept(session, c, [f.id for f in fragments])


EMB = HashEmbedder()


# ---- эмбеддинги ----


def test_hash_embedder_is_deterministic_and_close_for_similar_titles():
    a, b, far = EMB.embed(
        ["Значения аргументов по умолчанию", "Значения параметров по умолчанию", "Лямбда-функция"]
    )
    again = EMB.embed(["Значения аргументов по умолчанию"])[0]
    assert a == again and len(a) == EMB.dim
    cos = lambda x, y: sum(p * q for p, q in zip(x, y))  # noqa: E731
    assert cos(a, b) > cos(a, far) and cos(a, b) > 0.5


def test_embeddings_are_stored_once_and_recomputed_only_on_change(session):
    c = concept(session, "Group")
    assert merge.embed_concepts(session, [c], EMB) == 1
    assert merge.embed_concepts(session, [c], EMB) == 0
    c.content = {"summary": "новое объяснение", "sections": []}
    assert merge.embed_concepts(session, [c], EMB) == 1
    assert session.query(ConceptEmbedding).count() == 1


def test_candidates_are_nearest_in_the_same_domain_only(session):
    a = concept(session, "Значения аргументов по умолчанию")
    near = concept(session, "Значения параметров по умолчанию")
    other_domain = concept(session, "Значения аргументов по умолчанию", domain="elsewhere")
    rejected = concept(session, "Значения аргументов по умолчанию!", status="rejected")
    merge.embed_concepts(session, [a, near, other_domain, rejected], EMB)

    found = merge.candidates(session, a, min_similarity=0.3)

    assert [c.id for c, _ in found] == [near.id]
    assert 0.3 < found[0][1] <= 1.0


# ---- решения ----


def test_same_merges_sources_edges_and_leaves_one_concept(session, frags):
    a = concept(session, "Значения аргументов по умолчанию")
    b = concept(session, "Значения параметров по умолчанию")
    third = concept(session, "Вызов функции")
    src(session, a, frags[0])
    src(session, b, frags[1], frags[0])
    e = ConceptEdge(from_id=third.id, to_id=b.id, type="related", status="draft")
    session.add(e)
    session.flush()
    provenance.link_edge(session, e, [frags[2].id])
    judge = Judge({"параметров по умолчанию": {"verdict": "same", "reason": "одно и то же"}})

    report = merge.merge_domain(session, DOMAIN, judge, EMB)

    assert report.merged == 1
    left = session.query(Concept).filter(Concept.title.like("Значения%")).all()
    assert len(left) == 1
    keeper = left[0]
    sources = {s.fragment_id for s in session.query(ConceptSource).filter_by(concept_id=keeper.id)}
    assert sources == {frags[0].id, frags[1].id}  # источники сложились без дубля
    edge = session.query(ConceptEdge).one()
    assert edge.to_id == keeper.id  # связь переехала на оставшееся понятие
    assert session.query(EdgeSource).filter_by(edge_id=edge.id).count() == 1
    note = session.query(ReviewLog).filter_by(target_id=keeper.id).one()
    assert note.action == "edit" and "объединено с" in note.note


def test_approved_concept_is_the_one_that_stays(session, frags):
    approved = concept(session, "Значения аргументов по умолчанию", status="approved")
    draft = concept(session, "Значения параметров по умолчанию")
    src(session, draft, frags[0], frags[1], frags[2])  # у черновика источников больше
    src(session, approved, frags[3])
    judge = Judge(default="same")

    merge.merge_domain(session, DOMAIN, judge, EMB)

    survivor = session.query(Concept).filter(Concept.title.like("Значения%")).one()
    assert survivor.id == approved.id and survivor.status == "approved"


def test_people_learning_the_loser_are_moved_to_the_keeper(session, frags):
    keeper = concept(session, "Значения аргументов по умолчанию", status="approved")
    loser = concept(session, "Значения параметров по умолчанию")
    user = make_user(session)
    session.add(UserConcept(user_id=user.id, domain=DOMAIN, base_concept_id=loser.id, title="x"))
    session.flush()

    merge.merge_domain(session, DOMAIN, Judge(default="same"), EMB)

    assert session.query(UserConcept).one().base_concept_id == keeper.id


def test_refines_becomes_a_specialization_link_not_a_merge(session, frags, monkeypatch):
    monkeypatch.setattr(settings, "merge_min_similarity", 0.3)  # заглушка-эмбеддер ближе не даёт
    general = concept(session, "Значения аргументов по умолчанию")
    special = concept(session, "Значения параметров по умолчанию в определении")
    src(session, general, frags[0])
    src(session, special, frags[1])
    # общее — то, что стоит первым по алфавиту в промпте: ответ модели называет его явно
    judge = Judge(default="refines")
    judge.verdicts = {"": {"verdict": "refines", "general": "a", "reason": "частный случай"}}

    report = merge.merge_domain(session, DOMAIN, judge, EMB)

    assert report.refined == 1 and report.merged == 0
    assert session.query(Concept).filter_by(domain=DOMAIN).count() == 2
    edge = session.query(ConceptEdge).one()
    assert edge.type == "specializes" and edge.status == "draft"
    assert session.query(EdgeSource).filter_by(edge_id=edge.id).count() == 2


def test_contradiction_goes_to_the_queue_and_changes_nothing(session):
    a = concept(session, "Значения аргументов по умолчанию")
    b = concept(session, "Значения параметров по умолчанию")

    report = merge.merge_domain(session, DOMAIN, Judge(default="contradicts"), EMB)

    assert report.conflicts == 1 and report.merged == 0
    conflict = session.query(ConceptConflict).one()
    assert conflict.status == "open" and {conflict.a_id, conflict.b_id} == {a.id, b.id}
    assert session.query(Concept).filter_by(domain=DOMAIN).count() == 2


def test_different_concepts_stay_apart(session):
    concept(session, "Значения аргументов по умолчанию")
    concept(session, "Значения параметров по умолчанию")
    report = merge.merge_domain(session, DOMAIN, Judge(default="different"), EMB)
    assert report.merged == report.refined == report.conflicts == 0
    assert session.query(Concept).filter_by(domain=DOMAIN).count() == 2


def test_unclear_answer_means_different_never_a_guess_merge(session):
    concept(session, "Значения аргументов по умолчанию")
    concept(session, "Значения параметров по умолчанию")
    junk = Judge()
    junk.verdicts = {"": {"verdict": "maybe, who knows"}}
    merge.merge_domain(session, DOMAIN, junk, EMB)
    assert session.query(Concept).filter_by(domain=DOMAIN).count() == 2


# ---- бюджет и память решений ----


def test_decision_is_remembered_and_not_asked_again(session):
    concept(session, "Значения аргументов по умолчанию")
    concept(session, "Значения параметров по умолчанию")
    judge = Judge(default="different")

    first = merge.merge_domain(session, DOMAIN, judge, EMB)
    asked = judge.calls
    second = merge.merge_domain(session, DOMAIN, judge, EMB)

    assert first.judged == 1 and asked == 1
    assert second.judged == 0 and second.reused >= 1 and judge.calls == asked
    assert session.query(MergeDecision).count() == 1


def test_judgement_budget_is_respected(session):
    for title in (
        "Значения аргументов по умолчанию",
        "Значения параметров по умолчанию",
        "Значения формальных параметров по умолчанию",
    ):
        concept(session, title)
    judge = Judge()
    report = merge.merge_domain(session, DOMAIN, judge, EMB, max_judgements=1)
    assert report.judged == 1 and judge.calls == 1 and report.skipped_budget >= 1


# ---- циклы предпосылок ----


def test_prerequisite_cycle_is_broken_at_the_weakest_draft_edge(session, frags):
    a, b, c = (concept(session, t) for t in ("A", "B", "C"))
    edges = {}
    for x, y in ((a, b), (b, c), (c, a)):
        edges[(x.title, y.title)] = e = ConceptEdge(
            from_id=x.id, to_id=y.id, type="prereq", status="draft"
        )
        session.add(e)
        session.flush()
    provenance.link_edge(session, edges[("A", "B")], [frags[0].id, frags[1].id])
    provenance.link_edge(session, edges[("B", "C")], [frags[0].id, frags[1].id])
    provenance.link_edge(session, edges[("C", "A")], [frags[0].id])  # самая слабая

    assert merge.find_prereq_cycle(session, DOMAIN) is not None
    removed = merge.break_cycles(session, DOMAIN)

    assert removed == 1 and merge.find_prereq_cycle(session, DOMAIN) is None
    assert session.get(ConceptEdge, edges[("C", "A")].id) is None
    assert session.get(ConceptEdge, edges[("A", "B")].id) is not None


def test_cycle_of_human_approved_edges_is_not_cut_but_queued(session):
    a, b = concept(session, "A"), concept(session, "B")
    for x, y in ((a, b), (b, a)):
        session.add(ConceptEdge(from_id=x.id, to_id=y.id, type="prereq", status="approved"))
    session.flush()

    assert merge.break_cycles(session, DOMAIN) == 0
    assert session.query(ConceptEdge).count() == 2
    assert session.query(ConceptConflict).one().status == "open"


# ---- фоновая задача и связка с разбором ----


def test_job_merges_the_domain(session):
    concept(session, "Значения аргументов по умолчанию")
    concept(session, "Значения параметров по умолчанию")
    user = make_user(session)
    job = merge.enqueue_merge(session, DOMAIN, user.id)

    process_job(session, job, Judge(default="same"))

    assert job.status == "done" and job.result["merged"] == 1
    bad = merge.enqueue_merge(session, "", user.id)
    process_job(session, bad, Judge())
    assert bad.status == "failed"


def test_finished_ingest_queues_a_merge_for_its_domain(session):
    store = MemoryObjectStore()
    doc, _ = provenance.add_document(
        session,
        title="B",
        data=b"# Groups\n\nA group is a set together with an operation that is associative.\n",
        domain=DOMAIN,
        meta={"filename": "b.md"},
        store=store,
    )
    answer = {
        "concepts": [
            {
                "key": "group",
                "title": "Group",
                "summary": "Множество с операцией.",
                "sources": [1],
                "quote": "A group is a set together with an operation",
            }
        ],
        "edges": [],
    }

    class G:
        def structured(self, *a, **k):
            return answer

    user = make_user(session)
    job = ingest.enqueue_ingest(session, doc, user.id)
    from core import objects

    objects._store = store
    try:
        process_job(session, job, G())
    finally:
        objects.reset_store()

    assert job.status == "done"
    from core.models import Job

    queued = session.query(Job).filter_by(type="merge_concepts").one()
    assert queued.input_ref == {"domain": DOMAIN} and queued.status == "pending"
