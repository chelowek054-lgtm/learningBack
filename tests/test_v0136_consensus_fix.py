"""Исправление консенсуса, жалобы, доверие и отзыв источника (V-0136, R-0077, T-0165…T-0167)."""

from __future__ import annotations

from modules.knowledge import freshness, node_compile, reconcile
from modules.knowledge.models import Claim, ClaimConflict, Concept, ReviewLog
from tests.conftest import make_user
from tests.kg_helpers import Gateway, claim, concept, document


def consensus(session, n_sources=5):
    """Утверждение, на котором сошлись n_sources источников, и один новый источник против него."""
    c = concept(session, "Rule", status="approved")
    docs = [
        document(session, title=f"S{i}", data=f"s{i}".encode(), tier="A") for i in range(n_sources)
    ]
    old = claim(session, c, "The rule holds.", docs[0][1][0])
    for d, f in docs[1:]:
        from modules.knowledge.models import ClaimSource

        session.add(ClaimSource(claim_id=old.id, fragment_id=f[0].id, quote="q", verified=True))
    nd, nf = document(session, title="Contrarian", data=b"c", tier="B")
    new = claim(session, c, "The rule does not hold.", nf[0], status="proposed")
    session.flush()
    gw = Gateway(
        {"verdicts": [{"id": "N1", "verdict": "contradicts", "target": "A1", "reason": "обратное"}]}
    )
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    cf = session.query(ClaimConflict).filter_by(b_id=new.id).one()
    return c, old, new, cf, nd


def test_dispute_against_five_sources_creates_a_problem(session):
    c, old, new, cf, _ = consensus(session)
    assert cf.status == "open" and new.status == "disputed" and old.status == "accepted"


def test_outdated_resolution_replaces_and_rebuilds_the_node(session):
    c, old, new, cf, _ = consensus(session)
    c.content = {"summary": "s", "claims_hash": "stale"}
    reconcile.resolve(session, cf.id, "outdated", None, "консенсус устарел")
    assert (
        session.get(Claim, old.id).status == "replaced"
        and session.get(Claim, old.id).replaced_by == new.id
    )
    assert session.get(Claim, new.id).status == "accepted"
    assert "claims_hash" not in c.content  # сборка пересоберёт узел
    live = {x.id for x in node_compile.live_claims(session, c)}
    assert live == {new.id}
    assert (
        session.query(ReviewLog).filter_by(target_type="claim_conflict", action="resolve").count()
        == 1
    )


def test_disputed_resolution_shows_both_positions(session):
    c, old, new, cf, _ = consensus(session)
    reconcile.resolve(session, cf.id, "disputed", None, "")
    resolved = reconcile.pool(session, None, "resolved")
    assert len(resolved) == 1
    assert {resolved[0]["a"]["text"], resolved[0]["b"]["text"]} == {old.text, new.text}
    assert {x.id for x in node_compile.live_claims(session, c)} == {
        old.id
    }  # в теорию — только принятое


def test_learner_report_appears_at_the_right_claim(session, client):
    c, old, *_ = consensus(session)
    learner = make_user(session)
    r = client(learner).post(f"/graph/claims/{old.id}/report", json={"note": "тут неверно"})
    assert r.status_code == 201
    admin = make_user(session, superuser=True)
    reports = client(admin).get("/graph/claims/reports").json()
    assert reports[0]["claimId"] == str(old.id) and reports[0]["reports"] == 1
    assert reports[0]["notes"] == ["тут неверно"]


def test_more_reports_raise_the_claim(session, client):
    c, old, new, *_ = consensus(session)
    for _i in range(3):
        client(make_user(session)).post(f"/graph/claims/{new.id}/report", json={})
    client(make_user(session)).post(f"/graph/claims/{old.id}/report", json={})
    admin = make_user(session, superuser=True)
    ids = [r["claimId"] for r in client(admin).get("/graph/claims/reports").json()]
    assert ids == [str(new.id), str(old.id)]


def test_lost_disputes_lower_the_source_trust(session):
    c, old, new, cf, nd = consensus(session)
    nd.trust_tier = "A"
    freshness.record_lost_dispute(session, new)
    freshness.record_lost_dispute(session, new)
    assert nd.trust_tier == "A"
    freshness.record_lost_dispute(session, new)
    assert nd.trust_tier == "B" and nd.meta["lostDisputes"] == 3
    for _i in range(2):
        freshness.record_lost_dispute(session, new)
    assert nd.trust_tier == "C"
    for _i in range(3):
        freshness.record_lost_dispute(session, new)
    assert nd.quarantined


def test_resolving_for_the_old_claim_hits_the_new_source_but_outdated_does_not(session):
    c, old, new, cf, nd = consensus(session)
    reconcile.resolve(session, cf.id, "outdated", None)
    assert not (nd.meta or {}).get("lostDisputes")
    c2, old2, new2, cf2, nd2 = consensus(session)
    reconcile.resolve(session, cf2.id, "a", None)
    assert nd2.meta["lostDisputes"] == 1


def test_revoking_a_source_removes_what_only_it_supported(session):
    from modules.knowledge import provenance

    d, f = document(session, title="Sole")
    d2, f2 = document(session, title="Other", data=b"o")
    only = concept(session, "OnlyHere", status="draft")
    shared = concept(session, "Shared", status="draft")
    provenance.link_concept(session, only, [f[0].id])
    provenance.link_concept(session, shared, [f[0].id, f2[0].id])
    claim(session, only, "Only fact.", f[0])
    out = freshness.revoke_source(session, d.id, "ошибки")
    session.flush()
    assert out["conceptsDeleted"] == 1
    assert session.get(Concept, only.id) is None and session.get(Concept, shared.id) is not None
