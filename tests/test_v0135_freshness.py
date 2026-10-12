"""Срок актуальности и замена утверждений (V-0135, R-0076, T-0162, T-0163, T-0164)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from core.config import settings
from core.models import Job, SrsCard
from core.srs import initial_fsrs_state
from modules.knowledge import freshness, node_compile, reconcile
from modules.knowledge.models import Claim, ClaimConflict
from tests.conftest import make_user
from tests.kg_helpers import Gateway, claim, concept, document

NOW = datetime.now(timezone.utc)


def test_expired_fast_claim_puts_the_area_on_refresh(session):
    d, f = document(session)
    c = concept(session, "Release", domain="python")
    claim(
        session,
        c,
        "Latest is 3.11.",
        f[0],
        volatility="fast",
        as_of=NOW - timedelta(days=400),
        review_after=NOW - timedelta(days=130),
    )
    fresh = concept(session, "Syntax", domain="python")
    claim(
        session,
        fresh,
        "Fine.",
        f[0],
        volatility="fast",
        as_of=NOW,
        review_after=NOW + timedelta(days=270),
    )
    assert freshness.stale_by_domain(session) == {"python": ["Release"]}


def test_refresh_job_starts_a_source_search_for_the_stale_area(session, monkeypatch):
    monkeypatch.setattr(settings, "profile_auto_sources", True)
    user = make_user(session, superuser=True)
    d, f = document(session)
    c = concept(session, "Release", domain="python")
    claim(
        session, c, "Latest is 3.11.", f[0], volatility="fast", review_after=NOW - timedelta(days=1)
    )
    job = freshness.enqueue_refresh(session, user.id)
    out = freshness.refresh_job(session, job, None)
    assert out["queued"] == ["python"]
    search = session.query(Job).filter_by(type="profile_sources").one()
    assert search.input_ref["domain"] == "python" and "Release" in search.input_ref["queries"][0]


def fast_pair(session):
    d1, f1 = document(session, title="Old")
    d2, f2 = document(session, title="New", data=b"n")
    c = concept(session, "Release", domain="python", status="approved")
    old = claim(
        session, c, "Latest is 3.11.", f1[0], volatility="fast", as_of=NOW - timedelta(days=400)
    )
    new = claim(
        session, c, "Latest is 3.13.", f2[0], status="proposed", volatility="fast", as_of=NOW
    )
    return c, old, new


def test_newer_fast_claim_replaces_the_old_one_and_history_stays(session):
    c, old, new = fast_pair(session)
    gw = Gateway({"verdicts": [{"id": "N1", "verdict": "contradicts", "target": "A1"}]})
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    assert session.query(ClaimConflict).count() == 0
    assert session.get(Claim, old.id).status == "replaced"
    assert session.get(Claim, old.id).replaced_by == new.id
    assert session.get(Claim, new.id).status == "accepted"


def test_equal_dates_still_go_to_the_pool(session):
    c, old, new = fast_pair(session)
    new.as_of = old.as_of
    gw = Gateway({"verdicts": [{"id": "N1", "verdict": "contradicts", "target": "A1"}]})
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    assert session.query(ClaimConflict).count() == 1


def test_changed_node_returns_to_review_for_those_who_studied_it(session):
    c, old, new = fast_pair(session)
    new.status = "accepted"
    old.status = "replaced"
    c.content = {
        "summary": "s",
        "sections": [],
        "i18n": {"en": {"summary": "x", "sections": []}},
        "claims_hash": "old",
    }
    user = make_user(session)
    due = NOW + timedelta(days=20)
    card = SrsCard(
        user_id=user.id,
        module="knowledge",
        front={},
        back={},
        source="generated",
        concept_id=c.id,
        fsrs_state=initial_fsrs_state(NOW),
        due_at=due,
    )
    session.add(card)
    session.flush()
    answer = {
        "summary": {"en": {"text": "S"}, "ru": {"text": "С"}},
        "sections": [
            {
                "claims": [1],
                "i18n": {"en": {"heading": "H", "body": "B"}, "ru": {"heading": "З", "body": "Т"}},
            }
        ],
        "cards": [],
        "tests": [],
        "clozes": [],
    }
    assert node_compile.compile_concept(session, c, Gateway(answer), node_compile.CompileReport())
    session.refresh(card)
    assert card.due_at < due and card.due_at <= datetime.now(timezone.utc) + timedelta(minutes=5)


def test_different_applicability_gives_no_problem(session):
    d, f = document(session)
    c = concept(session, "Syntax")
    claim(session, c, "Old rule.", f[0], applicability="until 3.8")
    claim(session, c, "New rule.", f[0], status="proposed", applicability="since 3.9")
    gw = Gateway({"verdicts": [{"id": "N1", "verdict": "contradicts", "target": "A1"}]})
    reconcile.reconcile_concept(session, c, gw, reconcile.ReconcileReport())
    assert session.query(ClaimConflict).count() == 0


# ---- проверка ссылок (T-0164) ----


def test_dead_link_is_marked_and_the_fragment_stays(session):
    d1, f1 = document(session, origin_url="https://alive.example/a")
    d2, f2 = document(session, title="Dead", data=b"x", origin_url="https://dead.example/b")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.host == "alive.example" else 404)

    rep = freshness.check_links(session, transport=httpx.MockTransport(handler))
    assert rep == {"checked": 2, "dead": 1, "alive": 1}
    assert d2.meta["linkDead"] is True and d1.meta["linkDead"] is False
    assert f2[0].text  # сохранённый фрагмент на месте


def test_recent_check_is_not_repeated(session):
    d, _ = document(session, origin_url="https://alive.example/a")
    t = httpx.MockTransport(lambda r: httpx.Response(200))
    freshness.check_links(session, transport=t)
    assert freshness.check_links(session, transport=t)["checked"] == 0


def test_dead_link_is_flagged_in_the_learner_api(session, client):
    d, f = document(session, origin_url="https://dead.example/b")
    c = concept(session, "Group")
    claim(session, c, "Fact.", f[0])
    freshness.check_links(session, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    src = client(make_user(session)).get(f"/graph/nodes/{c.id}/claims").json()[0]["sources"][0]
    assert src["linkDead"] is True
