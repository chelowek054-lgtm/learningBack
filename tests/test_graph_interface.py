"""Публичный интерфейс графа: узел, связи, граница, освоенность, подписка (T-0052)."""

from __future__ import annotations

import uuid

import pytest

from modules.knowledge import api as kg
from modules.knowledge import events
from modules.knowledge.models import Concept, ConceptEdge, UserConcept
from tests.conftest import make_user

THEORY = {"summary": "Теория. " * 20, "sections": [], "references": []}


def concept(session, title, domain="d", tier="core"):
    c = Concept(
        domain=domain,
        title=title,
        tier=tier,
        content=THEORY,
        bloom_levels=["remember", "understand"],
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def chain(session):
    """A → B → C: предпосылки идут слева направо."""
    a, b, c = (concept(session, t) for t in ("A", "B", "C"))
    session.add_all(
        [
            ConceptEdge(from_id=a.id, to_id=b.id, type="prereq"),
            ConceptEdge(from_id=b.id, to_id=c.id, type="prereq"),
        ]
    )
    session.flush()
    return a, b, c


@pytest.fixture(autouse=True)
def _no_leaked_subscribers():
    before = list(events._subscribers)
    yield
    events._subscribers[:] = before


# ---- чтение ----


def test_get_node_returns_canon_with_users_override(session):
    user, base = make_user(session), None
    base = concept(session, "База")
    session.add(
        UserConcept(
            user_id=user.id,
            domain="d",
            base_concept_id=base.id,
            content_override={"summary": "моя правка"},
            origin="edited",
        )
    )
    session.flush()

    node = kg.get_node(session, user.id, base.id)

    assert node["title"] == "База" and node["content"]["summary"] == "моя правка"


def test_get_node_hides_foreign_personal_nodes_and_unknown_ids(session):
    owner, stranger = make_user(session), make_user(session)
    own = UserConcept(user_id=owner.id, domain="d", title="Свой", origin="grown_llm")
    session.add(own)
    session.flush()

    assert kg.get_node(session, owner.id, own.id)["title"] == "Свой"
    assert kg.get_node(session, stranger.id, own.id) is None
    assert kg.get_node(session, owner.id, uuid.uuid4()) is None


def test_get_edges_returns_both_directions(session, chain):
    a, b, c = chain
    user = make_user(session)

    edges = kg.get_edges(session, user.id, "d", b.id)

    assert {(e["from"], e["to"]) for e in edges} == {
        (str(a.id), str(b.id)),
        (str(b.id), str(c.id)),
    }


# ---- граница и освоенность ----


def test_frontier_starts_at_roots_and_moves_as_evidence_arrives(session, chain):
    a, b, c = chain
    user = make_user(session)
    assert [n["conceptId"] for n in kg.frontier(session, user.id, "d")] == [str(a.id)]

    for _ in range(12):
        kg.record_evidence(session, user.id, "d", kg.Evidence(a.id, "understand", 1.0, "test"))

    assert [n["conceptId"] for n in kg.frontier(session, user.id, "d")] == [str(b.id)]


def test_get_mastery_covers_the_area_and_counts_observations(session, chain):
    a, b, c = chain
    user = make_user(session)
    before = kg.get_mastery(session, user.id, "d")
    assert set(before) == {str(a.id), str(b.id), str(c.id)}  # по узлу — состояние-приор
    assert all(m["observations"] == 0 for m in before.values())

    state = kg.record_evidence(session, user.id, "d", kg.Evidence(a.id, "remember", 0.9, "x"))

    mastery = kg.get_mastery(session, user.id, "d")
    assert mastery[str(a.id)]["observations"] == state.observations == 1
    assert mastery[str(b.id)]["observations"] == 0


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_evidence_score_must_be_between_zero_and_one(session, chain, score):
    user = make_user(session)

    with pytest.raises(ValueError):
        kg.record_evidence(session, user.id, "d", kg.Evidence(chain[0].id, "remember", score))


def test_evidence_does_not_depend_on_who_reported_it(session, chain):
    a, _, _ = chain
    one, two = make_user(session), make_user(session)

    s1 = kg.record_evidence(session, one.id, "d", kg.Evidence(a.id, "remember", 0.8, "cards"))
    s2 = kg.record_evidence(session, two.id, "d", kg.Evidence(a.id, "remember", 0.8, "game"))

    assert s1.estimate == s2.estimate  # граф учитывает результат, а не способ


# ---- подписка ----


def test_subscriber_is_told_when_canon_node_changes(session, client):
    admin = make_user(session, superuser=True)
    node = concept(session, "Узел")
    seen = []
    kg.subscribe(seen.append)

    client(admin).put(
        f"/graph/canon/nodes/{node.id}", json={"content": {"summary": "новое", "sections": []}}
    )

    assert len(seen) == 1
    assert (seen[0].node_id, seen[0].domain, seen[0].version, seen[0].user_id) == (
        node.id,
        "d",
        2,
        None,
    )


def test_personal_edit_is_reported_with_the_author(session, client):
    user = make_user(session)
    uc = UserConcept(
        user_id=user.id, domain="d", title="Свой", content_override=THEORY, origin="grown_llm"
    )
    session.add(uc)
    session.flush()
    seen = []
    kg.subscribe(seen.append)

    client(user).put(f"/graph/user-nodes/{uc.id}", json={"title": "Новое имя"})

    assert [(e.node_id, e.user_id, e.version) for e in seen] == [(uc.id, user.id, 2)]


def test_no_event_when_nothing_changed(session, client):
    user = make_user(session)
    uc = UserConcept(user_id=user.id, domain="d", title="Свой", origin="grown_llm")
    session.add(uc)
    session.flush()
    seen = []
    kg.subscribe(seen.append)

    client(user).put(f"/graph/user-nodes/{uc.id}", json={"mastery": {"p": 1}})

    assert seen == []


def test_unsubscribe_stops_notifications_and_failing_subscriber_does_not_break_others(
    session, client
):
    admin = make_user(session, superuser=True)
    node = concept(session, "Узел")
    good, calls = [], []

    def broken(_):
        calls.append("broken")
        raise RuntimeError("упал")

    kg.subscribe(broken)
    off = kg.subscribe(good.append)
    api = client(admin)

    api.put(f"/graph/canon/nodes/{node.id}", json={"title": "Раз"})
    assert calls == ["broken"] and len(good) == 1  # упавший подписчик не помешал соседу

    off()
    api.put(f"/graph/canon/nodes/{node.id}", json={"title": "Два"})
    assert len(good) == 1


def test_rebuild_with_refresh_reports_changed_nodes(session, client, monkeypatch):
    admin = make_user(session, superuser=True)
    api = client(admin)
    api.post("/graph/canon/build", json={"domain": "d", "topic": "t"})
    stale = session.query(Concept).filter_by(domain="d").first()
    stale.content = {"summary": "коротко"}  # теория непригодна: refresh её заменит
    session.flush()
    seen = []
    kg.subscribe(seen.append)

    api.post("/graph/canon/build", json={"domain": "d", "topic": "t", "refresh": True})

    assert any(e.node_id == stale.id for e in seen)
