"""Происхождение знаний: источники, статус черновика, проверка, удаление (T-0076)."""

from __future__ import annotations

import json
import uuid

import pytest

from core.objects import MemoryObjectStore, ObjectNotFound
from modules.knowledge import provenance
from modules.knowledge.course import generate_course
from modules.knowledge.models import (
    Concept,
    ConceptEdge,
    ConceptSource,
    EdgeSource,
    ReviewLog,
    SourceDocument,
    SourceFragment,
    UserConcept,
)
from tests.conftest import make_user

THEORY = {
    "summary": "Достаточно теории, чтобы узел считался пригодным для заданий.",
    "sections": [{"heading": "Раздел", "body": "Разбор", "examples": ["пример"]}],
    "references": [],
}


def concept(session, title, status="draft", domain="algebra"):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content=THEORY,
        bloom_levels=["remember", "understand"],
        difficulty=1,
        source="doc",
        status=status,
    )
    session.add(c)
    session.flush()
    return c


def edge(session, a, b, status="draft"):
    e = ConceptEdge(from_id=a.id, to_id=b.id, type="prereq", status=status)
    session.add(e)
    session.flush()
    return e


@pytest.fixture
def store():
    return MemoryObjectStore()


@pytest.fixture
def book(session, store):
    doc, created = provenance.add_document(
        session, title="Algebra 1", data=b"%PDF fake book", domain="algebra", store=store
    )
    assert created
    frags = provenance.add_fragments(
        session,
        doc,
        [
            {"text": "A group is a set with an operation.", "page": 12, "heading": "Groups"},
            {"text": "Examples: integers under addition.", "page": 13, "heading": "Groups"},
            {"text": "   ", "page": 14},
        ],
    )
    return doc, frags


# ---- документы и фрагменты ----


def test_same_file_is_not_added_twice_and_lands_in_the_object_store(session, store):
    first, created1 = provenance.add_document(session, title="A", data=b"same", store=store)
    second, created2 = provenance.add_document(session, title="B", data=b"same", store=store)
    assert created1 and not created2 and first.id == second.id
    assert store.get(first.object_key) == b"same"
    with pytest.raises(provenance.ProvenanceError) as e:
        provenance.add_document(session, title="empty", data=b"", store=store)
    assert e.value.code == "empty_file"


def test_blank_fragments_are_skipped_and_order_continues(session, book):
    doc, frags = book
    assert [f.ordinal for f in frags] == [0, 1] and frags[0].page == 12
    more = provenance.add_fragments(session, doc, [{"text": "next"}])
    assert more[0].ordinal == 2


def test_claim_without_source_or_with_unknown_fragment_is_refused(session, book):
    c = concept(session, "Group")
    with pytest.raises(provenance.ProvenanceError) as e:
        provenance.link_concept(session, c, [])
    assert e.value.code == "no_source"
    with pytest.raises(provenance.ProvenanceError) as e:
        provenance.link_concept(session, c, [uuid.uuid4()])
    assert e.value.code == "unknown_fragment"
    with pytest.raises(provenance.ProvenanceError):
        provenance.link_concept(session, c, [book[1][0].id], role="nonsense")


def test_linking_twice_does_not_duplicate(session, book):
    c = concept(session, "Group")
    provenance.link_concept(session, c, [book[1][0].id, book[1][0].id])
    provenance.link_concept(session, c, [book[1][0].id])
    assert session.query(ConceptSource).filter_by(concept_id=c.id).count() == 1


# ---- что видит администратор ----


def test_admin_view_has_document_page_and_text(session, book):
    c = concept(session, "Group")
    provenance.link_concept(session, c, [book[1][0].id])
    provenance.link_concept(session, c, [book[1][1].id], role="example")
    view = provenance.concept_sources(session, c.id)
    assert [(v["document"], v["page"], v["role"]) for v in view] == [
        ("Algebra 1", 12, "definition"),
        ("Algebra 1", 13, "example"),
    ]
    assert view[0]["text"].startswith("A group")


# ---- проверка человеком ----


def test_new_edges_are_drafts_and_review_is_logged(session):
    a, b = concept(session, "A"), concept(session, "B")
    e = edge(session, a, b)
    reviewer = make_user(session, superuser=True)
    assert e.status == "draft"
    provenance.review_edge(session, e, reviewer.id, "approve")
    assert e.status == "approved"
    log = provenance.history(session, "edge", e.id)
    assert [x["action"] for x in log] == ["approve"] and log[0]["reviewer"] == str(reviewer.id)


def test_rejection_needs_a_reason_and_unknown_action_is_refused(session):
    c = concept(session, "A")
    reviewer = make_user(session, superuser=True)
    with pytest.raises(provenance.ProvenanceError) as e:
        provenance.review_concept(session, c, reviewer.id, "reject")
    assert e.value.code == "note_required" and c.status == "draft"
    provenance.review_concept(session, c, reviewer.id, "reject", "определение неверно")
    assert c.status == "rejected"
    with pytest.raises(provenance.ProvenanceError):
        provenance.review_concept(session, c, reviewer.id, "maybe")


# ---- учащийся видит статус, но не источник ----


def test_course_shows_status_but_never_sources(session, book):
    user = make_user(session)
    verified, drafted = concept(session, "Verified", "approved"), concept(session, "Drafted")
    provenance.link_concept(session, verified, [book[1][0].id])
    provenance.link_concept(session, drafted, [book[1][1].id])
    edge(session, verified, drafted, "approved")
    course = generate_course(session, user.id, "algebra", "understand", [])
    from modules.knowledge.course import course_view

    view = course_view(course, session)
    by_title = {s["title"]: s["status"] for s in view["steps"]}
    assert by_title == {"Verified": "verified", "Drafted": "draft"}
    assert view["draftSteps"] == 1
    blob = json.dumps(view, ensure_ascii=False)
    assert (
        "Algebra 1" not in blob
        and "A group is a set" not in blob
        and "fragment" not in blob.lower()
    )


def test_status_in_course_follows_the_review_without_rebuilding(session, book):
    user = make_user(session)
    c = concept(session, "Only")
    course = generate_course(session, user.id, "algebra", "understand", [])
    from modules.knowledge.course import course_view

    assert course_view(course, session)["steps"][0]["status"] == "draft"
    provenance.review_concept(session, c, make_user(session, superuser=True).id, "approve")
    assert course_view(course, session)["steps"][0]["status"] == "verified"


# ---- удаление источника ----


def test_removing_a_source_deletes_only_what_it_alone_supports(session, book, store):
    doc, frags = book
    other, _ = provenance.add_document(session, title="Algebra 2", data=b"other", store=store)
    other_frag = provenance.add_fragments(session, other, [{"text": "Groups again", "page": 1}])[0]

    only_here = concept(session, "OnlyHere")
    shared = concept(session, "Shared")
    survivor = concept(session, "Survivor")
    provenance.link_concept(session, only_here, [frags[0].id])
    provenance.link_concept(session, shared, [frags[0].id])
    provenance.link_concept(session, shared, [other_frag.id])
    provenance.link_concept(session, survivor, [other_frag.id])
    e_gone = edge(session, only_here, shared)
    e_kept = edge(session, shared, survivor)
    provenance.link_edge(session, e_gone, [frags[0].id])
    provenance.link_edge(session, e_kept, [frags[0].id, other_frag.id])
    key = doc.object_key

    report = provenance.remove_document(session, doc, store=store)
    session.flush()

    assert report.concepts_deleted == [str(only_here.id)] and report.edges_deleted == 1
    assert session.get(Concept, only_here.id) is None
    assert session.get(Concept, shared.id) is not None and session.get(Concept, survivor.id)
    assert session.get(ConceptEdge, e_gone.id) is None and session.get(ConceptEdge, e_kept.id)
    assert session.query(SourceFragment).filter_by(document_id=doc.id).count() == 0
    assert session.get(SourceDocument, doc.id) is None
    assert report.file_deleted and not store.exists(key)
    with pytest.raises(ObjectNotFound):
        store.get(key)
    # Остальные источники и их ссылки целы.
    assert session.query(ConceptSource).filter_by(concept_id=shared.id).count() == 1


def test_concept_people_already_learn_is_rejected_not_deleted(session, book, store):
    doc, frags = book
    c = concept(session, "InUse")
    provenance.link_concept(session, c, [frags[0].id])
    session.add(
        UserConcept(
            user_id=make_user(session).id, domain="algebra", base_concept_id=c.id, title="InUse"
        )
    )
    session.flush()

    report = provenance.remove_document(session, doc, store=store)

    assert report.concepts_rejected == [str(c.id)] and report.concepts_deleted == []
    assert session.get(Concept, c.id).status == "rejected"
    assert "уже учатся" in provenance.history(session, "concept", c.id)[-1]["note"]


def test_keep_verified_keeps_human_checked_items(session, book, store):
    doc, frags = book
    checked = concept(session, "Checked", "approved")
    draft = concept(session, "Draft")
    provenance.link_concept(session, checked, [frags[0].id])
    provenance.link_concept(session, draft, [frags[1].id])
    e = edge(session, checked, draft, "approved")
    provenance.link_edge(session, e, [frags[0].id])

    report = provenance.remove_document(session, doc, keep_verified=True, store=store)

    assert report.concepts_kept == [str(checked.id)] and report.concepts_deleted == [str(draft.id)]
    assert session.get(ConceptEdge, e.id) is None or session.get(Concept, draft.id) is None
    assert session.query(ReviewLog).filter_by(target_id=checked.id).count() == 1


# ---- API ----


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)

    def delete(self, url, **kw):
        return self._c(self._u).delete(url, **kw)


def test_only_admin_sees_sources_and_reviews(session, client, book):
    c = concept(session, "Group")
    provenance.link_concept(session, c, [book[1][0].id])
    admin = As(client, make_user(session, superuser=True))
    learner = As(client, make_user(session))

    assert learner.get(f"/graph/canon/nodes/{c.id}/sources").status_code == 403
    assert (
        learner.post(f"/graph/canon/nodes/{c.id}/review", json={"action": "approve"}).status_code
        == 403
    )
    assert learner.get("/graph/sources").status_code == 403
    assert learner.delete(f"/graph/sources/{book[0].id}").status_code == 403

    got = admin.get(f"/graph/canon/nodes/{c.id}/sources").json()
    assert got["status"] == "draft" and got["sources"][0]["page"] == 12

    assert (
        admin.post(f"/graph/canon/nodes/{c.id}/review", json={"action": "reject"}).status_code
        == 422
    )
    ok = admin.post(
        f"/graph/canon/nodes/{c.id}/review", json={"action": "approve", "note": "сверено"}
    )
    assert ok.json()["status"] == "approved"
    hist = admin.get(f"/graph/canon/nodes/{c.id}/sources").json()["history"]
    assert [h["action"] for h in hist] == ["approve"]


def test_admin_gets_a_signed_link_and_can_delete_the_source(session, client, book, monkeypatch):
    from core import objects

    monkeypatch.setattr(objects, "_store", MemoryObjectStore())
    doc, _ = provenance.add_document(session, title="Fresh", data=b"fresh file")
    admin = As(client, make_user(session, superuser=True))
    link = admin.get(f"/graph/sources/{doc.id}/link").json()
    assert link["url"].startswith("memory://") and link["expiresInSec"] == 300
    deleted = admin.delete(f"/graph/sources/{doc.id}").json()
    assert deleted["fileDeleted"] is True
    assert admin.get(f"/graph/sources/{doc.id}/link").status_code == 404
    assert admin.get("/graph/sources/not-a-uuid/link").status_code == 404


def test_old_approve_endpoint_now_leaves_a_trace(session, client):
    c = concept(session, "Old")
    admin = As(client, make_user(session, superuser=True))
    r = admin.post(f"/graph/canon/nodes/{c.id}/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert session.query(ReviewLog).filter_by(target_id=c.id, action="approve").count() == 1
    assert session.query(EdgeSource).count() == 0
