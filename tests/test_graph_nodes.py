"""Граф: устойчивый ключ узла при перегенерации (T-0007), облегчённый список (T-0008)."""

from __future__ import annotations

import uuid

import pytest

from modules.knowledge.models import Concept, UserConcept
from tests.conftest import make_user

THEORY = {
    "summary": "Краткое пояснение узла.",
    "sections": [{"heading": "Определение", "body": "Подробная теория узла. " * 20}],
    "references": [],
}


def _draft(*nodes):
    """Ответ модели: узлы (key, title) и одно ребро между первыми двумя."""
    out = {
        "nodes": [
            {"key": k, "title": t, "tier": "derived", "content": THEORY, "bloomLevels": []}
            for k, t in nodes
        ],
        "edges": [],
    }
    if len(nodes) >= 2:
        out["edges"].append({"from": nodes[0][0], "to": nodes[1][0], "type": "prereq"})
    return out


@pytest.fixture
def build(monkeypatch, session, client):
    """Вызов /canon/build с подставленным ответом модели; от имени администратора."""
    api = client(make_user(session, superuser=True))
    state = {}

    def run(draft, refresh=False):
        state["draft"] = draft
        monkeypatch.setattr("modules.knowledge.router.build_graph", lambda *a, **k: state["draft"])
        return api.post("/graph/canon/build", json={"domain": "d", "topic": "t", "refresh": refresh})

    return run


# ---- T-0007: ключ узла ----


def test_rename_in_regeneration_does_not_duplicate_node(build, session):
    build(_draft(("alpha", "Альфа"), ("beta", "Бета")))
    build(_draft(("alpha", "Альфа (переименовано)"), ("beta", "Бета")), refresh=True)

    titles = sorted(c.title for c in session.query(Concept).filter_by(domain="d"))
    assert titles == ["Альфа", "Бета"]  # заголовок куратора не затирается, дубля нет


def test_new_key_creates_new_node(build, session):
    build(_draft(("alpha", "Альфа")))
    build(_draft(("alpha", "Альфа"), ("gamma", "Гамма")))

    keys = sorted(c.key for c in session.query(Concept).filter_by(domain="d"))
    assert keys == ["alpha", "gamma"]


def test_node_without_key_is_matched_by_title_and_gets_key(build, session):
    legacy = Concept(
        domain="d", title="Альфа", tier="derived", content=THEORY, source="llm", status="draft"
    )
    session.add(legacy)
    session.flush()

    build(_draft(("alpha", "Альфа")))

    assert session.query(Concept).filter_by(domain="d").count() == 1
    session.refresh(legacy)
    assert legacy.key == "alpha"


def test_edges_follow_matched_node_after_rename(build, session):
    build(_draft(("alpha", "Альфа"), ("beta", "Бета")))
    build(_draft(("alpha", "Альфа 2"), ("beta", "Бета")), refresh=True)

    from modules.knowledge.models import ConceptEdge

    assert session.query(ConceptEdge).count() == 1  # ребро не задвоилось


def test_same_key_in_other_domain_is_independent(build, session, monkeypatch, client):
    build(_draft(("alpha", "Альфа")))
    api = client(make_user(session, superuser=True))
    monkeypatch.setattr(
        "modules.knowledge.router.build_graph", lambda *a, **k: _draft(("alpha", "Альфа"))
    )
    api.post("/graph/canon/build", json={"domain": "other", "topic": "t"})

    assert session.query(Concept).filter_by(key="alpha").count() == 2


# ---- T-0008: облегчённый список ----


def test_graph_list_has_no_full_theory(build, session, client):
    build(_draft(("alpha", "Альфа")))

    nodes = client(make_user(session)).get("/graph/d").json()["nodes"]

    assert nodes[0]["light"] is True
    assert nodes[0]["content"] == {"summary": THEORY["summary"]}


def test_node_endpoint_returns_full_theory(build, session, client):
    build(_draft(("alpha", "Альфа")))
    node_id = session.query(Concept).filter_by(domain="d").one().id

    node = client(make_user(session)).get(f"/graph/nodes/{node_id}").json()

    assert node["light"] is False
    assert node["content"]["sections"][0]["body"] == THEORY["sections"][0]["body"]
    assert node["key"] == "alpha"


def test_node_endpoint_applies_users_override(build, session, client):
    build(_draft(("alpha", "Альфа")))
    node_id = session.query(Concept).filter_by(domain="d").one().id
    user = make_user(session)
    api = client(user)
    api.post(f"/graph/nodes/{node_id}/override", json={"content": {"summary": "моё"}})

    node = api.get(f"/graph/nodes/{node_id}").json()

    assert node["content"]["summary"] == "моё"


def test_node_endpoint_returns_own_node_only_to_owner(session, client):
    owner, stranger = make_user(session), make_user(session)
    uc = UserConcept(
        user_id=owner.id, domain="d", title="Свой", content_override=THEORY, origin="grown_llm"
    )
    session.add(uc)
    session.flush()

    assert client(owner).get(f"/graph/nodes/{uc.id}").status_code == 200
    assert client(stranger).get(f"/graph/nodes/{uc.id}").status_code == 404


def test_node_endpoint_unknown_and_malformed_ids(session, client):
    api = client(make_user(session))
    assert api.get(f"/graph/nodes/{uuid.uuid4()}").status_code == 404
    assert api.get("/graph/nodes/not-a-uuid").status_code == 404
