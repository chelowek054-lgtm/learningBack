"""Схема графа канона в админке: чтение и правки без циклов и дублей (T-0107…T-0110, R-0058)."""

from __future__ import annotations

import pytest

from modules.knowledge import graph_admin_data as data
from modules.knowledge.admin import VIEWS
from modules.knowledge.graph_admin import GraphAdmin
from modules.knowledge.models import Concept, ConceptEdge


def concept(session, title, domain="ml", stage="Основы", order=1, **kw):
    c = Concept(
        domain=domain,
        title=title,
        stage=stage,
        stage_order=order,
        content={"summary": f"Описание {title}", "sections": []},
        status=kw.pop("status", "draft"),
        **kw,
    )
    session.add(c)
    session.flush()
    return c


def edge(session, a, b, type_="prereq"):
    e = ConceptEdge(from_id=a.id, to_id=b.id, type=type_, status="draft")
    session.add(e)
    session.flush()
    return e


def test_view_is_registered_and_has_a_template():
    assert GraphAdmin in VIEWS
    from modules.knowledge.graph_admin import TEMPLATES_DIR
    from pathlib import Path

    assert (Path(TEMPLATES_DIR) / "graph.html").is_file()


def test_overview_and_area_graph(session):
    a, b = concept(session, "Азы"), concept(session, "Приёмы", stage="Методы", order=2)
    concept(session, "Чужое", domain="other")
    edge(session, a, b)

    assert {o["domain"]: o["concepts"] for o in data.overview(session)} == {"ml": 2, "other": 1}
    graph = data.area_graph(session, "ml")
    assert {n["title"] for n in graph["nodes"]} == {"Азы", "Приёмы"}
    assert len(graph["edges"]) == 1 and graph["edges"][0]["type"] == "prereq"
    assert [s["title"] for s in graph["stages"]] == ["Основы", "Методы"]
    assert all(n["sources"] == 0 for n in graph["nodes"])


def test_node_card_shows_neighbors_both_ways(session):
    a, b, c = concept(session, "A"), concept(session, "B"), concept(session, "C")
    edge(session, a, b)
    edge(session, b, c)

    card = data.node_card(session, str(b.id))

    assert {(n["direction"], n["title"]) for n in card["neighbors"]} == {("in", "A"), ("out", "C")}


def test_edit_bumps_version_only_when_content_changes(session):
    c = concept(session, "A")
    before = c.version

    data.update_node(session, str(c.id), {"tier": "core", "optional": True}, None)
    assert c.version == before and c.tier == "core" and c.optional is True

    data.update_node(session, str(c.id), {"summary": "Новое пояснение"}, None)
    assert c.version == before + 1 and c.content["summary"] == "Новое пояснение"


def test_bad_values_are_refused(session):
    c = concept(session, "A")
    for fields, code in (
        ({"title": "  "}, "empty_title"),
        ({"tier": "legend"}, "bad_tier"),
        ({"status": "weird"}, "bad_status"),
    ):
        with pytest.raises(data.GraphEditError) as e:
            data.update_node(session, str(c.id), fields, None)
        assert e.value.code == code


def test_rejecting_needs_a_reason_and_approving_does_not(session):
    c = concept(session, "A")

    with pytest.raises(data.GraphEditError) as e:
        data.update_node(session, str(c.id), {"status": "rejected"}, None)
    assert e.value.code == "note_required"

    data.update_node(session, str(c.id), {"status": "rejected", "note": "неверно"}, None)
    assert c.status == "rejected"
    data.update_node(session, str(c.id), {"status": "approved"}, None)
    assert c.status == "approved"


def test_add_edge_refuses_loops_duplicates_and_cycles(session):
    a, b, c = concept(session, "A"), concept(session, "B"), concept(session, "C")
    data.add_edge(session, str(a.id), str(b.id), "prereq")
    data.add_edge(session, str(b.id), str(c.id), "prereq")

    for args, code in (
        ((a.id, a.id, "prereq"), "self_loop"),
        ((a.id, b.id, "prereq"), "duplicate"),
        ((c.id, a.id, "prereq"), "cycle"),
        ((a.id, c.id, "weird"), "bad_type"),
    ):
        with pytest.raises(data.GraphEditError) as e:
            data.add_edge(session, str(args[0]), str(args[1]), args[2])
        assert e.value.code == code

    # цикл запрещён только для предпосылок: «рядом» в обратную сторону допустимо
    data.add_edge(session, str(c.id), str(a.id), "related")


def test_change_and_delete_edge(session):
    a, b = concept(session, "A"), concept(session, "B")
    e = edge(session, a, b, "related")

    data.change_edge(session, str(e.id), {"type": "prereq"}, None)
    assert e.type == "prereq"
    with pytest.raises(data.GraphEditError) as err:
        data.change_edge(session, str(e.id), {"status": "rejected"}, None)
    assert err.value.code == "note_required"
    data.change_edge(session, str(e.id), {"status": "rejected", "note": "лишняя"}, None)
    assert e.status == "rejected"

    data.delete_edge(session, str(e.id))
    assert session.query(ConceptEdge).count() == 0


def test_changing_type_into_a_cycle_is_refused(session):
    a, b = concept(session, "A"), concept(session, "B")
    edge(session, a, b, "prereq")
    back = edge(session, b, a, "related")

    with pytest.raises(data.GraphEditError) as e:
        data.change_edge(session, str(back.id), {"type": "prereq"}, None)
    assert e.value.code == "cycle"


def test_domains_graph_counts_concepts(session):
    from modules.knowledge import domains

    domains.register(session, "ml", foundation=True)
    concept(session, "A")
    concept(session, "B", status="approved")

    nodes = {n["title"]: n for n in data.domains_graph(session)["nodes"]}
    assert nodes["ml"]["concepts"] == 2 and nodes["ml"]["drafts"] == 1
