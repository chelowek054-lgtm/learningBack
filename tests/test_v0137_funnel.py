"""Воронка отсева источников (V-0137, R-0078, T-0168…T-0170)."""

from __future__ import annotations

import pytest

from core.config import settings
from modules.knowledge import funnel, node_compile, reconcile
from modules.knowledge.models import ClaimConflict
from tests.kg_helpers import Gateway, claim, concept, document

SENT = (
    "The theory of groups studies sets with an operation, and it is the basis of the whole algebra "
    "that we use in many of the other parts of the mathematics and the physics. "
)


def para(i: int, tweak: str = "") -> str:
    return (f"Section {i}. " + SENT * 3 + tweak)[:700]


def good_texts(n=30, tweak=""):
    return [para(i, tweak) for i in range(n)]


PROBE_OK = {"is_learning_text": True, "tier": "A", "has_errors": False, "reasons": ["textbook"]}


@pytest.fixture(autouse=True)
def funnel_on(monkeypatch):
    monkeypatch.setattr(settings, "funnel_enabled", True)


def test_good_source_passes_with_one_probe(session):
    doc, frags = document(session, texts=good_texts())
    gw = Gateway(PROBE_OK)
    r = funnel.screen(session, doc, frags, gw)
    assert r.ok and r.tier == "A" and gw.calls == 1 and doc.lang == "en" and not doc.quarantined


def test_exact_duplicate_is_rejected_without_the_model(session):
    first, f1 = document(session, title="First", texts=good_texts(), tier="A")
    second, f2 = document(session, title="Copy", data=b"copy", texts=good_texts())
    gw = Gateway()
    r = funnel.screen(session, second, f2, gw)
    assert not r.ok and r.step == "duplicate" and gw.calls == 0 and "First" in r.reasons[-1]


def test_near_duplicate_is_rejected_without_the_model(session):
    first, f1 = document(session, title="First", texts=good_texts(), tier="A")
    second, f2 = document(
        session,
        title="Second edition",
        data=b"2nd",
        texts=[t.replace("studies", "explores") for t in good_texts()],
    )
    gw = Gateway()
    r = funnel.screen(session, second, f2, gw)
    assert not r.ok and r.step == "duplicate" and gw.calls == 0


def test_scan_with_garbage_is_rejected_without_the_model(session):
    junk = ["§¶¤¢£¥ƒ©®™ ░▒▓█ ♪♫☼ ¿¡ " * 12 for _ in range(30)]
    doc, frags = document(session, texts=junk)
    gw = Gateway()
    r = funnel.screen(session, doc, frags, gw)
    assert not r.ok and gw.calls == 0 and r.step in ("language", "quality")


def test_too_little_text_is_rejected(session):
    doc, frags = document(session, texts=["Short text about groups and the sets."])
    r = funnel.screen(session, doc, frags, Gateway())
    assert not r.ok and r.step in ("language", "quality")


def test_unsupported_language_is_rejected_without_the_model(session):
    german = (
        "Die Gruppentheorie untersucht Mengen mit einer Verknüpfung und ist die Grundlage der Algebra. "
        * 8
    )
    doc, frags = document(session, texts=[german] * 30)
    gw = Gateway()
    r = funnel.screen(session, doc, frags, gw)
    assert not r.ok and r.step == "language" and gw.calls == 0


def test_off_topic_text_is_rejected_without_the_model(session, monkeypatch):
    class Fake:
        model = "fake"
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr("core.embeddings.get_embedder", lambda: Fake())
    doc, frags = document(session, domain="cooking", texts=good_texts())
    gw = Gateway()
    r = funnel.screen(session, doc, frags, gw)
    assert not r.ok and r.step == "topic" and gw.calls == 0


def test_model_probe_refuses_not_a_textbook(session):
    doc, frags = document(session, texts=good_texts())
    r = funnel.screen(
        session, doc, frags, Gateway({**PROBE_OK, "is_learning_text": False, "tier": "C"})
    )
    assert not r.ok and r.step == "probe" and doc.quarantined


def test_claim_backed_only_by_tier_c_is_not_confirmed_alone(session):
    dc, fc = document(session, title="Weak", tier="C")
    da, fa = document(session, title="Strong", data=b"s", tier="A")
    c = concept(session, "Group", status="approved")
    weak = claim(session, c, "Weak only.", fc[0])
    both = claim(session, c, "Backed twice.", fc[0])
    from modules.knowledge.models import ClaimSource

    session.add(ClaimSource(claim_id=both.id, fragment_id=fa[0].id, quote="q", verified=True))
    session.flush()
    live = {x.id for x in node_compile.live_claims(session, c)}
    assert live == {both.id} and weak.id not in live


def test_high_share_of_dropped_extraction_demotes_then_quarantines(session):
    doc, _ = document(session, tier="A")
    funnel.after_extraction(doc, concepts=6, dropped=6)
    assert doc.trust_tier == "C" and not doc.quarantined
    doc2, _ = document(session, title="Worse", data=b"w", tier="A")
    funnel.after_extraction(doc2, concepts=1, dropped=19)
    assert doc2.quarantined


def test_source_with_many_contradictions_goes_to_quarantine(session):
    base, fb = document(session, title="Base", tier="A")
    bad, fx = document(session, title="Contrarian", data=b"c", tier="B")
    gw_answers = []
    for i in range(10):
        c = concept(session, f"Topic {i}", status="draft")
        claim(session, c, f"Fact {i}.", fb[0])
        claim(session, c, f"Not fact {i}.", fx[0], status="proposed")
        gw_answers.append({"verdicts": [{"id": "N1", "verdict": "contradicts", "target": "A1"}]})
    reconcile.reconcile_domain(session, "algebra", Gateway(*gw_answers))
    assert session.query(ClaimConflict).count() == 10
    assert bad.quarantined and "противореч" in " ".join(bad.trust_reasons)
    assert not base.quarantined
