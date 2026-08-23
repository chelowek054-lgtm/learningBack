"""COW-чтение графа: наследование канона, оверрайд, свои узлы, изоляция доменов."""

from __future__ import annotations

import importlib

from modules.knowledge.cow import effective_graph, resolve_node
from modules.knowledge.models import Concept, ConceptEdge, UserConcept, UserEdge
from tests.conftest import make_user


def _concept(session, *, domain="ml", title="Backprop", tier="core", summary="теория"):
    c = Concept(
        domain=domain,
        title=title,
        tier=tier,
        content={"summary": summary},
        bloom_levels=["remember"],
        difficulty=2,
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


def test_canon_inherited_without_copying(session):
    """Новый пользователь читает канон сквозняком: персональных строк не создаётся."""
    user = make_user(session)
    _concept(session, title="Линейная алгебра")

    graph = effective_graph(session, user.id, "ml")

    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["kind"] == "canonical"
    assert node["origin"] == "inherited"
    assert node["content"]["summary"] == "теория"
    assert session.query(UserConcept).count() == 0


def test_override_shadows_canon_without_mutating_it(session):
    """Оверрайд подменяет контент только для своего пользователя; канон не меняется."""
    user = make_user(session)
    other = make_user(session)
    c = _concept(session, summary="канонический текст")
    session.add(
        UserConcept(
            user_id=user.id,
            domain="ml",
            base_concept_id=c.id,
            content_override={"summary": "мой текст"},
            origin="edited",
        )
    )
    session.flush()

    mine = effective_graph(session, user.id, "ml")["nodes"][0]
    theirs = effective_graph(session, other.id, "ml")["nodes"][0]

    assert mine["content"]["summary"] == "мой текст"
    assert mine["origin"] == "edited"
    assert theirs["content"]["summary"] == "канонический текст"
    assert theirs["origin"] == "inherited"
    assert session.get(Concept, c.id).content["summary"] == "канонический текст"


def test_canon_improvement_reaches_users_without_override(session):
    """Правка канона видна там, где узел не перекрыт — ради этого и нужен COW."""
    user = make_user(session)
    c = _concept(session, summary="старое")

    c.content = {"summary": "новое"}
    session.flush()

    assert effective_graph(session, user.id, "ml")["nodes"][0]["content"]["summary"] == "новое"


def test_own_node_appears_as_personal(session):
    user = make_user(session)
    session.add(
        UserConcept(
            user_id=user.id,
            domain="ml",
            base_concept_id=None,
            title="Своя ветка",
            content_override={"summary": "выращено"},
            origin="grown_llm",
            status="learning",
        )
    )
    session.flush()

    node = effective_graph(session, user.id, "ml")["nodes"][0]
    assert node["kind"] == "personal"
    assert node["title"] == "Своя ветка"
    assert node["origin"] == "grown_llm"
    assert node["tier"] == "derived"


def test_personal_nodes_do_not_leak_across_domains(session):
    """Регрессия: до миграции 0006 свои узлы подмешивались в граф любого домена."""
    user = make_user(session)
    _concept(session, domain="ml", title="Backprop")
    _concept(session, domain="math", title="Производная")
    session.add(
        UserConcept(
            user_id=user.id,
            domain="ml",
            base_concept_id=None,
            title="Только для ml",
            origin="grown_llm",
        )
    )
    session.flush()

    ml_titles = {n["title"] for n in effective_graph(session, user.id, "ml")["nodes"]}
    math_titles = {n["title"] for n in effective_graph(session, user.id, "math")["nodes"]}

    assert "Только для ml" in ml_titles
    assert "Только для ml" not in math_titles
    assert math_titles == {"Производная"}


def test_personal_edges_do_not_leak_across_domains(session):
    user = make_user(session)
    a = _concept(session, domain="ml", title="A")
    _concept(session, domain="math", title="B")
    session.add(UserEdge(user_id=user.id, domain="ml", from_id=a.id, to_id=a.id, type="related"))
    session.flush()

    assert len(effective_graph(session, user.id, "ml")["edges"]) == 1
    assert effective_graph(session, user.id, "math")["edges"] == []


def test_canon_edges_stay_inside_domain(session):
    """Ребро попадает в граф, только если ОБА конца в этом домене."""
    user = make_user(session)
    a = _concept(session, domain="ml", title="A")
    b = _concept(session, domain="math", title="B")
    session.add(ConceptEdge(from_id=a.id, to_id=b.id, type="prereq"))
    session.flush()

    assert effective_graph(session, user.id, "ml")["edges"] == []
    assert effective_graph(session, user.id, "math")["edges"] == []


def test_resolve_node_uses_canon_when_override_is_absent(session):
    c = _concept(session, summary="канон")
    uc = UserConcept(user_id=make_user(session).id, domain="ml", base_concept_id=c.id)
    session.add(uc)
    session.flush()  # без записи не применятся server_default (mastery, status, origin)

    resolved = resolve_node(c, uc)

    assert resolved["content"]["summary"] == "канон"
    assert resolved["mastery"] == {}
    assert resolved["origin"] == "inherited"


def test_expand_keeps_the_edges_the_model_returned(session, client, monkeypatch):
    """Регрессия: раньше рёбра из ответа LLM выбрасывались, оставалось одно 'related'."""
    # В пакете `modules.knowledge` имя `router` занято объектом APIRouter,
    # поэтому модуль берём явно.
    graph_router = importlib.import_module("modules.knowledge.router")

    c = _concept(session, title="Backprop")
    monkeypatch.setattr(
        graph_router,
        "expand_node",
        lambda title, direction: {
            "nodes": [
                {"key": "a", "title": "Узел A", "content": {"summary": "s"}},
                {"key": "b", "title": "Узел B", "content": {"summary": "s"}},
            ],
            "edges": [
                {"from": "Backprop", "to": "a", "type": "prereq"},
                {"from": "a", "to": "b", "type": "specializes"},
            ],
        },
    )

    user = make_user(session)
    response = client(user).post(
        "/graph/expand", json={"concept_id": str(c.id), "direction": "оптимизаторы"}
    )

    assert response.status_code == 200
    types = sorted(e.type for e in session.query(UserEdge).filter_by(user_id=user.id).all())
    assert types == ["prereq", "specializes"], "связь между новыми узлами должна сохраниться"


def test_expand_attaches_orphan_nodes_to_the_source(session, client, monkeypatch):
    """Узел, который модель не связала, не должен потеряться в графе."""
    # В пакете `modules.knowledge` имя `router` занято объектом APIRouter,
    # поэтому модуль берём явно.
    graph_router = importlib.import_module("modules.knowledge.router")

    c = _concept(session, title="Backprop")
    monkeypatch.setattr(
        graph_router,
        "expand_node",
        lambda title, direction: {
            "nodes": [{"key": "lonely", "title": "Одинокий", "content": {"summary": "s"}}],
            "edges": [],
        },
    )

    user = make_user(session)
    client(user).post("/graph/expand", json={"concept_id": str(c.id), "direction": "x"})

    edges = session.query(UserEdge).filter_by(user_id=user.id).all()
    assert len(edges) == 1
    assert edges[0].from_id == c.id
    assert edges[0].type == "related"
