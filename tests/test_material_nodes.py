"""Личные узлы из материала со ссылками на фрагменты (T-0015, R-0011)."""

from __future__ import annotations

import uuid

import pytest

from core.models import Material
from modules.knowledge.material_graph import MAX_NODES, ground, propose
from modules.knowledge.models import UserConcept, UserEdge
from tests.conftest import make_user

FRAGMENTS = [
    {"id": "f1", "heading": "Градиентный спуск", "text": "Идём против градиента функции потерь."},
    {"id": "f2", "heading": "Шаг обучения", "text": "Большой шаг расходится, малый — медленный."},
    {"id": "f3", "heading": "Итоги", "text": "Шаг подбирают по валидации."},
]


def _material(session, user, fragments=FRAGMENTS, title="Конспект"):
    m = Material(
        user_id=user.id,
        module="knowledge",
        source="markdown",
        title=title,
        content={"fragments": fragments},
    )
    session.add(m)
    session.flush()
    return m


# ---- заземление ----


def test_ground_keeps_only_nodes_that_stand_on_existing_fragments():
    nodes, edges = ground(
        {
            "nodes": [
                {"key": "a", "title": "A", "fragments": ["f1", "ghost"]},
                {"key": "b", "title": "B", "fragments": ["ghost"]},  # выдумка
                {"key": "c", "title": "C", "fragments": []},  # без ссылки
                {"key": "a", "title": "A дубль", "fragments": ["f2"]},  # тот же ключ
                {"key": "d", "title": "D", "fragments": ["f2"]},
            ],
            "edges": [
                {"from": "a", "to": "d", "type": "prereq"},
                {"from": "a", "to": "b", "type": "prereq"},  # b отброшен
                {"from": "a", "to": "a", "type": "related"},  # петля
            ],
        },
        FRAGMENTS,
    )

    assert [n["key"] for n in nodes] == ["a", "d"]
    assert nodes[0]["fragments"] == ["f1"]  # несуществующая ссылка вычищена
    assert edges == [{"from": "a", "to": "d", "type": "prereq"}]


def test_ground_limits_number_of_nodes():
    many = {
        "nodes": [{"key": f"k{i}", "title": f"T{i}", "fragments": ["f1"]} for i in range(30)],
        "edges": [],
    }

    nodes, _ = ground(many, FRAGMENTS)

    assert len(nodes) == MAX_NODES


def test_proposal_without_llm_is_grounded_on_the_material_itself(session):
    m = _material(session, make_user(session))

    p = propose(m)

    assert [n["title"] for n in p["nodes"]] == ["Градиентный спуск", "Шаг обучения", "Итоги"]
    assert all(n["fragments"] for n in p["nodes"])
    assert len(p["edges"]) == 2 and p["truncated"] is False


def test_proposal_uses_model_answer_and_drops_ungrounded_part(session, monkeypatch):
    class Gateway:
        def structured(self, *a, **k):
            return {
                "nodes": [
                    {
                        "key": "gd",
                        "title": "Градиентный спуск",
                        "summary": "s",
                        "fragments": ["f1"],
                    },
                    {"key": "x", "title": "Выдумка", "summary": "s", "fragments": ["nope"]},
                ],
                "edges": [{"from": "gd", "to": "x", "type": "prereq"}],
            }

    monkeypatch.setattr("modules.knowledge.material_graph.has_llm", lambda: True)
    monkeypatch.setattr("modules.knowledge.material_graph.get_ai_gateway", lambda: Gateway())

    p = propose(_material(session, make_user(session)))

    assert [n["key"] for n in p["nodes"]] == ["gd"] and p["edges"] == []


def test_long_material_is_truncated_and_says_so(session):
    many = [{"id": f"f{i}", "text": f"текст {i}"} for i in range(60)]

    p = propose(_material(session, make_user(session), many))

    assert p["truncated"] is True


# ---- API ----


def test_propose_does_not_write_to_the_graph(session, client):
    user = make_user(session)
    m = _material(session, user)

    r = client(user).post(f"/graph/materials/{m.id}/propose")

    assert r.status_code == 200 and len(r.json()["nodes"]) == 3
    assert session.query(UserConcept).filter_by(user_id=user.id).count() == 0


def test_accept_creates_personal_nodes_with_fragment_references(session, client):
    user = make_user(session)
    m = _material(session, user)
    api = client(user)
    proposal = api.post(f"/graph/materials/{m.id}/propose").json()

    r = api.post(
        f"/graph/materials/{m.id}/accept",
        json={"domain": "d", "nodes": proposal["nodes"], "edges": proposal["edges"]},
    )

    assert r.status_code == 201, r.text
    assert r.json()["created"] == 3
    uc = session.query(UserConcept).filter_by(user_id=user.id, title="Шаг обучения").one()
    assert uc.origin == "material" and uc.base_concept_id is None and uc.domain == "d"
    [ref] = uc.content_override["references"]
    assert ref["material_id"] == str(m.id) and ref["fragment_ids"] == ["f2"]
    assert session.query(UserEdge).filter_by(user_id=user.id).count() == 2


def test_accepted_nodes_appear_in_the_graph_with_references(session, client):
    user = make_user(session)
    m = _material(session, user)
    api = client(user)
    nodes = api.post(f"/graph/materials/{m.id}/propose").json()["nodes"]

    graph = api.post(
        f"/graph/materials/{m.id}/accept", json={"domain": "d", "nodes": nodes}
    ).json()["graph"]

    assert {n["title"] for n in graph["nodes"]} == {n["title"] for n in nodes}
    node_id = graph["nodes"][0]["id"]
    full = api.get(f"/graph/nodes/{node_id}").json()
    assert full["content"]["references"][0]["fragment_ids"]


def test_accept_rechecks_grounding_instead_of_trusting_the_client(session, client):
    user = make_user(session)
    m = _material(session, user)
    forged = {
        "domain": "d",
        "nodes": [{"key": "x", "title": "Подделка", "summary": "s", "fragments": ["f999"]}],
    }

    r = client(user).post(f"/graph/materials/{m.id}/accept", json=forged)

    assert r.status_code == 422
    assert session.query(UserConcept).filter_by(user_id=user.id).count() == 0


def test_accepting_twice_does_not_duplicate_nodes(session, client):
    user = make_user(session)
    m = _material(session, user)
    api = client(user)
    nodes = api.post(f"/graph/materials/{m.id}/propose").json()["nodes"]
    body = {"domain": "d", "nodes": nodes}

    api.post(f"/graph/materials/{m.id}/accept", json=body)
    second = api.post(f"/graph/materials/{m.id}/accept", json=body)

    assert second.json()["created"] == 0
    assert session.query(UserConcept).filter_by(user_id=user.id).count() == 3


def test_foreign_material_is_not_found(session, client):
    owner, stranger = make_user(session), make_user(session)
    m = _material(session, owner)

    r = client(stranger).post(f"/graph/materials/{m.id}/propose")

    assert r.status_code == 404


@pytest.mark.parametrize("material_id", ["not-a-uuid", str(uuid.uuid4())])
def test_unknown_material_is_not_found(session, client, material_id):
    assert (
        client(make_user(session)).post(f"/graph/materials/{material_id}/propose").status_code
        == 404
    )


def test_material_without_fragments_is_conflict(session, client):
    user = make_user(session)
    m = _material(session, user, fragments=[])

    assert client(user).post(f"/graph/materials/{m.id}/propose").status_code == 409
