"""Специалисты по областям и очередь проверки (T-0081, R-0046, V-0098)."""

from __future__ import annotations

import pytest

from core.objects import MemoryObjectStore
from modules.knowledge import merge, provenance
from modules.knowledge.models import (
    Concept,
    ConceptConflict,
    ConceptEdge,
    DomainSpecialist,
    ReviewLog,
)
from tests.conftest import make_user

ALGEBRA, FRENCH = "algebra", "french"


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)

    def delete(self, url, **kw):
        return self._c(self._u).delete(url, **kw)


def concept(session, title, domain, status="draft", confidence=0.5):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content={"summary": f"{title}: изложение", "sections": []},
        bloom_levels=["remember"],
        difficulty=1,
        source="doc",
        status=status,
        confidence=confidence,
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def world(session):
    doc, _ = provenance.add_document(
        session, title="Book", data=b"book", domain=ALGEBRA, store=MemoryObjectStore()
    )
    frag = provenance.add_fragments(session, doc, [{"text": "fragment about groups", "page": 3}])[0]
    g = concept(session, "Group", ALGEBRA, confidence=0.4)
    s = concept(session, "Subgroup", ALGEBRA, confidence=0.7)
    fr = concept(session, "Passé composé", FRENCH)
    for c in (g, s, fr):
        provenance.link_concept(session, c, [frag.id])
    edge = ConceptEdge(from_id=g.id, to_id=s.id, type="prereq", status="draft")
    session.add(edge)
    session.flush()
    provenance.link_edge(session, edge, [frag.id])
    return {"group": g, "subgroup": s, "french": fr, "edge": edge}


@pytest.fixture
def people(session, client):
    admin = make_user(session, superuser=True)
    specialist = make_user(session)  # назначаем на алгебру
    session.add(DomainSpecialist(user_id=specialist.id, domain=ALGEBRA, granted_by=admin.id))
    learner = make_user(session)
    session.flush()
    return {
        "admin": As(client, admin),
        "spec": As(client, specialist),
        "learner": As(client, learner),
        "admin_user": admin,
        "spec_user": specialist,
    }


# ---- назначение специалистов ----


def test_only_admin_manages_specialists(session, client, people):
    target = make_user(session)
    session.flush()
    body = {"email": target.email, "domain": FRENCH}
    assert people["learner"].post("/graph/specialists", json=body).status_code == 403
    assert people["spec"].post("/graph/specialists", json=body).status_code == 403
    assert people["spec"].get("/graph/specialists").status_code == 403

    made = people["admin"].post("/graph/specialists", json=body)
    assert made.status_code == 201
    listed = people["admin"].get("/graph/specialists").json()
    assert {(r["email"], r["domain"]) for r in listed} >= {(target.email, FRENCH)}

    again = people["admin"].post("/graph/specialists", json=body)  # повторно — тот же, без дубля
    assert again.json()["id"] == made.json()["id"]
    assert session.query(DomainSpecialist).filter_by(user_id=target.id).count() == 1

    assert people["admin"].delete(f"/graph/specialists/{made.json()['id']}").status_code == 204
    assert session.query(DomainSpecialist).filter_by(user_id=target.id).count() == 0
    assert people["admin"].delete(f"/graph/specialists/{made.json()['id']}").status_code == 404


def test_granting_to_an_unknown_email_or_empty_domain_is_refused(people):
    admin = people["admin"]
    assert (
        admin.post(
            "/graph/specialists", json={"email": "nobody@example.com", "domain": ALGEBRA}
        ).status_code
        == 422
    )
    assert (
        admin.post("/graph/specialists", json={"email": "x@example.com", "domain": " "}).status_code
        == 422
    )


# ---- очередь ----


def test_specialist_sees_only_his_domain_and_admin_sees_all(session, client, world, people):
    mine = people["spec"].get("/graph/review/queue").json()
    assert {c["title"] for c in mine["concepts"]} == {"Group", "Subgroup"}
    assert mine["domains"] == [ALGEBRA] and len(mine["edges"]) == 1
    assert mine["concepts"][0]["title"] == "Group"  # сначала то, в чём модель сомневалась больше
    assert mine["concepts"][0]["sources"][0]["text"] == "fragment about groups"

    everything = people["admin"].get("/graph/review/queue").json()
    assert {c["title"] for c in everything["concepts"]} == {"Group", "Subgroup", "Passé composé"}
    assert everything["domains"] is None


def test_specialist_cannot_ask_for_a_foreign_domain_and_learner_gets_nothing(people, world):
    assert people["spec"].get("/graph/review/queue", params={"domain": FRENCH}).status_code == 403
    assert people["spec"].get("/graph/review/queue", params={"domain": ALGEBRA}).status_code == 200
    assert people["learner"].get("/graph/review/queue").status_code == 403


def test_verified_items_leave_the_queue(session, people, world):
    provenance.review_concept(session, world["group"], people["admin_user"].id, "approve")
    session.flush()
    titles = {c["title"] for c in people["spec"].get("/graph/review/queue").json()["concepts"]}
    assert titles == {"Subgroup"}


# ---- решения ----


def test_specialist_approves_in_his_domain_and_is_logged_not_elsewhere(session, people, world):
    ok = people["spec"].post(
        f"/graph/canon/nodes/{world['group'].id}/review", json={"action": "approve"}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    log = session.query(ReviewLog).filter_by(target_id=world["group"].id).one()
    assert log.reviewer_id == people["spec_user"].id and log.action == "approve"

    foreign = people["spec"].post(
        f"/graph/canon/nodes/{world['french'].id}/review", json={"action": "approve"}
    )
    assert foreign.status_code == 403
    assert session.get(Concept, world["french"].id).status == "draft"
    assert people["spec"].get(f"/graph/canon/nodes/{world['french'].id}/sources").status_code == 403
    assert (
        people["learner"]
        .post(f"/graph/canon/nodes/{world['group'].id}/review", json={"action": "approve"})
        .status_code
        == 403
    )


def test_edge_review_follows_the_domain_of_its_concepts(session, people, world):
    assert (
        people["spec"]
        .post(f"/graph/canon/edges/{world['edge'].id}/review", json={"action": "approve"})
        .status_code
        == 200
    )
    assert session.get(ConceptEdge, world["edge"].id).status == "approved"
    assert (
        people["learner"]
        .post(
            f"/graph/canon/edges/{world['edge'].id}/review", json={"action": "reject", "note": "x"}
        )
        .status_code
        == 403
    )


def test_rejection_needs_a_reason(people, world):
    r = people["spec"].post(
        f"/graph/canon/nodes/{world['group'].id}/review", json={"action": "reject"}
    )
    assert r.status_code == 422


def test_edit_changes_text_keeps_status_and_writes_the_journal(session, people, world):
    r = people["spec"].post(
        f"/graph/canon/nodes/{world['group'].id}/edit",
        json={
            "title": "Группа",
            "summary": "Исправленное изложение.",
            "note": "опечатка в определении",
        },
    )
    assert r.status_code == 200 and r.json()["title"] == "Группа" and r.json()["status"] == "draft"
    c = session.get(Concept, world["group"].id)
    assert c.content["summary"] == "Исправленное изложение."
    note = session.query(ReviewLog).filter_by(target_id=c.id, action="edit").one().note
    assert "название" in note and "опечатка" in note

    nothing = people["spec"].post(
        f"/graph/canon/nodes/{world['group'].id}/edit", json={"title": "Группа"}
    )
    assert nothing.status_code == 422  # ничего не изменилось
    foreign = people["spec"].post(
        f"/graph/canon/nodes/{world['french'].id}/edit", json={"title": "x"}
    )
    assert foreign.status_code == 403


# ---- противоречия ----


@pytest.fixture
def conflict(session, world):
    c = merge.record_conflict(session, world["group"], world["subgroup"], "определения расходятся")
    session.flush()
    return c


def test_conflict_is_in_the_queue_with_both_sides(people, world, conflict):
    q = people["spec"].get("/graph/review/queue").json()
    assert len(q["conflicts"]) == 1
    sides = {q["conflicts"][0]["a"]["title"], q["conflicts"][0]["b"]["title"]}
    assert (
        sides == {"Group", "Subgroup"} and q["conflicts"][0]["reason"] == "определения расходятся"
    )


def test_keep_both_closes_the_conflict_and_changes_no_concepts(session, people, world, conflict):
    r = people["spec"].post(
        f"/graph/review/conflicts/{conflict.id}/resolve", json={"resolution": "keep_both"}
    )
    assert r.status_code == 200
    assert session.query(ConceptConflict).one().status == "resolved"
    assert session.query(Concept).filter(Concept.domain == ALGEBRA).count() == 2
    again = people["spec"].post(
        f"/graph/review/conflicts/{conflict.id}/resolve", json={"resolution": "keep_both"}
    )
    assert again.status_code == 422


def test_reject_one_side_requires_a_reason_and_rejects_it(session, people, world, conflict):
    url = f"/graph/review/conflicts/{conflict.id}/resolve"
    assert people["spec"].post(url, json={"resolution": "reject_b"}).status_code == 422
    ok = people["spec"].post(url, json={"resolution": "reject_b", "note": "неверно по учебнику"})
    assert ok.status_code == 200
    a_id, b_id = conflict.a_id, conflict.b_id
    rejected = [
        c
        for c in (session.get(Concept, a_id), session.get(Concept, b_id))
        if c.status == "rejected"
    ]
    assert len(rejected) == 1


def test_merge_resolution_leaves_one_concept(session, people, world, conflict):
    ok = people["spec"].post(
        f"/graph/review/conflicts/{conflict.id}/resolve",
        json={"resolution": "merge", "note": "одно и то же"},
    )
    assert ok.status_code == 200
    assert session.query(Concept).filter(Concept.domain == ALGEBRA).count() == 1


def test_unknown_resolution_and_foreign_domain_conflicts_are_refused(
    session, people, world, conflict
):
    url = f"/graph/review/conflicts/{conflict.id}/resolve"
    assert people["spec"].post(url, json={"resolution": "burn"}).status_code == 422
    other = merge.record_conflict(
        session, world["french"], concept(session, "Imparfait", FRENCH), "противоречие"
    )
    session.flush()
    assert (
        people["spec"]
        .post(f"/graph/review/conflicts/{other.id}/resolve", json={"resolution": "keep_both"})
        .status_code
        == 403
    )
    assert people["learner"].post(url, json={"resolution": "keep_both"}).status_code == 403
