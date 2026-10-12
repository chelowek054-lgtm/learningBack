"""Сверка утверждений и пул проблем (V-0133, R-0074, T-0157, T-0158)."""

from __future__ import annotations

from modules.knowledge import node_compile, reconcile
from modules.knowledge.models import Claim, ClaimConflict, ClaimSource
from tests.kg_helpers import Gateway, claim, concept, document


def setup_node(session):
    d1, f1 = document(session, title="Book A", tier="A")
    d2, f2 = document(session, title="Book B", tier="A", data=b"other")
    c = concept(session, "Speed", status="draft")
    a1 = claim(session, c, "Light travels at 299792 km/s.", f1[0])
    n_same = claim(session, c, "The speed of light is 299792 km/s.", f2[0], status="proposed")
    n_new = claim(session, c, "Light is an electromagnetic wave.", f2[0], status="proposed")
    n_bad = claim(session, c, "Light travels at 150000 km/s.", f2[0], status="proposed")
    return c, a1, n_same, n_new, n_bad


def verdicts():
    return {
        "verdicts": [
            {"id": "N1", "verdict": "same", "target": "A1"},
            {"id": "N2", "verdict": "neutral"},
            {"id": "N3", "verdict": "contradicts", "target": "A1", "reason": "другая скорость"},
        ]
    }


def test_three_claims_cost_one_request_and_give_expected_rows(session):
    c, a1, n_same, n_new, n_bad = setup_node(session)
    gw = Gateway(verdicts())
    rep = reconcile.ReconcileReport()
    reconcile.reconcile_concept(session, c, gw, rep)
    session.flush()
    assert gw.calls == 1 and rep.calls == 1
    # совпавшее влито в принятое: одна новая ссылка, самого утверждения больше нет
    assert session.query(ClaimSource).filter_by(claim_id=a1.id).count() == 2
    assert session.get(Claim, n_same.id) is None
    # нейтральное принято, противоречащее ждёт решения
    assert session.get(Claim, n_new.id).status == "accepted"
    assert session.get(Claim, n_bad.id).status == "disputed"
    conflicts = session.query(ClaimConflict).all()
    assert len(conflicts) == 1 and conflicts[0].status == "open"
    assert (conflicts[0].a_id, conflicts[0].b_id) == (a1.id, n_bad.id)


def test_disputed_claim_is_not_used_by_node_compile(session):
    c, a1, n_same, n_new, n_bad = setup_node(session)
    reconcile.reconcile_concept(session, c, Gateway(verdicts()), reconcile.ReconcileReport())
    live = {x.id for x in node_compile.live_claims(session, c)}
    assert live == {a1.id, n_new.id} and n_bad.id not in live


def test_open_problem_is_in_the_pool_with_both_sources(session):
    c, a1, *_ = setup_node(session)
    reconcile.reconcile_concept(session, c, Gateway(verdicts()), reconcile.ReconcileReport())
    pool = reconcile.pool(session)
    assert len(pool) == 1
    p = pool[0]
    assert p["concept"] == "Speed" and p["a"]["id"] == str(a1.id)
    assert p["a"]["sources"] and p["b"]["sources"]


def test_different_applicability_is_not_a_contradiction(session):
    d, f = document(session)
    c = concept(session, "Syntax")
    claim(session, c, "Feature X is absent.", f[0], applicability="before v3.12")
    claim(session, c, "Feature X is present.", f[0], status="proposed", applicability="since v3.12")
    gw = Gateway({"verdicts": [{"id": "N1", "verdict": "contradicts", "target": "A1"}]})
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    assert session.query(ClaimConflict).count() == 0
    assert session.query(Claim).filter_by(concept_id=c.id, status="accepted").count() == 2


def test_first_source_needs_no_request(session):
    d, f = document(session)
    c = concept(session, "Fresh")
    claim(session, c, "One.", f[0], status="proposed")
    claim(session, c, "Two.", f[0], status="proposed")
    gw = Gateway()
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    assert gw.calls == 0
    assert session.query(Claim).filter_by(concept_id=c.id, status="accepted").count() == 2


def test_verdict_without_target_is_not_applied(session):
    c, *_ = setup_node(session)
    gw = Gateway({"verdicts": [{"id": "N3", "verdict": "contradicts"}]})
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    assert session.query(ClaimConflict).count() == 0
