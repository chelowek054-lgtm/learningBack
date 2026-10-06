"""Источники видят только администраторы: ни одного пути утечки к учащемуся (T-0080, R-0045, V-0097).

Не список проверенных ответов, а обход всех GET-маршрутов приложения: если завтра в чей-то ответ
попадёт документ или текст фрагмента, тест упадёт, даже если про этот маршрут никто не думал.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from api.app import app
from core import objects
from core.objects import MemoryObjectStore
from modules.knowledge import provenance
from modules.knowledge.course import generate_course
from modules.knowledge.models import Concept, ConceptEdge
from tests.conftest import make_user

SECRET_TITLE = "SECRET-BOOK-TITLE-9f2c"
SECRET_TEXT = "SECRET-FRAGMENT-TEXT-77ab about groups"
DOMAIN = "algebra"


@pytest.fixture
def world(session, monkeypatch):
    store = MemoryObjectStore()
    monkeypatch.setattr(objects, "_store", store)
    doc, _ = provenance.add_document(
        session,
        title=SECRET_TITLE,
        data=b"secret book",
        domain=DOMAIN,
        license="SECRET-LICENSE",
        store=store,
    )
    frag = provenance.add_fragments(
        session, doc, [{"text": SECRET_TEXT, "page": 7, "heading": "SECRET-HEADING"}]
    )[0]
    concepts = []
    for title in ("Group", "Subgroup"):
        c = Concept(
            domain=DOMAIN,
            title=title,
            tier="core",
            content={"summary": "Своими словами про группы, достаточно длинно.", "sections": []},
            bloom_levels=["remember", "understand"],
            difficulty=1,
            source="doc",
            status="draft",
        )
        session.add(c)
        session.flush()
        provenance.link_concept(session, c, [frag.id])
        concepts.append(c)
    edge = ConceptEdge(from_id=concepts[0].id, to_id=concepts[1].id, type="prereq", status="draft")
    session.add(edge)
    session.flush()
    provenance.link_edge(session, edge, [frag.id])
    learner = make_user(session)
    course = generate_course(session, learner.id, DOMAIN, "understand", [])
    session.flush()
    yield {
        "doc": doc,
        "frag": frag,
        "concepts": concepts,
        "edge": edge,
        "learner": learner,
        "course": course,
    }
    objects.reset_store()


def fill(path: str, w) -> str | None:
    """Подставить значения в параметры пути; None — для параметра нет разумного значения."""
    values = {
        "domain": DOMAIN,
        "concept_id": str(w["concepts"][0].id),
        "node_id": str(w["concepts"][0].id),
        "edge_id": str(w["edge"].id),
        "document_id": str(w["doc"].id),
        "material_id": "00000000-0000-0000-0000-000000000000",
        "id": str(w["concepts"][0].id),
    }
    names = re.findall(r"{(\w+)(?::[^}]*)?}", path)
    if any(n not in values for n in names):
        return None
    return re.sub(r"{(\w+)(?::[^}]*)?}", lambda m: values[m.group(1)], path)


def _flatten(routes, prefix=""):
    """Все маршруты приложения с полными путями: вложенные роутеры FastAPI лежат обёртками."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif type(r).__name__ == "_IncludedRouter":
            yield from _flatten(r.original_router.routes, prefix + (r.include_context.prefix or ""))


def api_routes():
    """(полный путь, маршрут) для всего, что приложение отдаёт под /v1."""
    return [(path, r) for path, r in _flatten(app.routes) if path.startswith("/v1/")]


def test_no_get_route_hands_a_source_to_a_learner(session, client, world):
    c = client(world["learner"])
    checked, leaked = 0, []
    for path, route in api_routes():
        if "GET" not in route.methods or "/sources" in path:
            continue
        url = fill(path, world)
        if url is None:
            continue
        r = c.get(url.removeprefix("/v1"))
        checked += 1
        body = r.text
        for marker in (
            SECRET_TITLE,
            SECRET_TEXT,
            "SECRET-HEADING",
            "SECRET-LICENSE",
            world["doc"].object_key,
        ):
            if marker in body:
                leaked.append(f"{path}: {marker}")
    assert checked >= 10, "обход почти ничего не проверил: поправьте подстановку параметров"
    assert leaked == []


def test_course_and_nodes_show_status_but_no_trace_of_the_source(session, client, world):
    c = client(world["learner"])
    course = c.get(f"/graph/course/{DOMAIN}")
    assert course.status_code == 200
    blob = course.text
    assert "draft" in blob and SECRET_TITLE not in blob and "fragment" not in blob.lower()


def test_every_source_route_refuses_a_learner(session, client, world):
    c = client(world["learner"])
    seen = 0
    for path, route in api_routes():
        if "/sources" not in path and not path.endswith("/review"):
            continue
        url = fill(path, world)
        if url is None:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = c.request(method, url.removeprefix("/v1"))
            seen += 1
            assert r.status_code in (401, 403, 405, 422), f"{method} {path} → {r.status_code}"
            assert SECRET_TITLE not in r.text and SECRET_TEXT not in r.text
    assert seen >= 8


def test_admin_routes_for_sources_are_admin_only_by_declaration(world):
    """Каждый маршрут источников объявляет зависимость «суперпользователь», а не полагается на память."""
    names = set()
    for path, route in api_routes():
        if "/sources" in path or path.endswith("/review"):
            deps = {d.call.__name__ for d in route.dependant.dependencies}
            names.add(path)
            assert "get_current_superuser" in deps, path
    assert len(names) >= 6


def test_admin_still_gets_everything_the_learner_must_not(session, client, world):
    admin = client(make_user(session, superuser=True))
    sources = admin.get(f"/graph/canon/nodes/{world['concepts'][0].id}/sources").json()
    assert (
        sources["sources"][0]["text"] == SECRET_TEXT
        and sources["sources"][0]["document"] == SECRET_TITLE
    )
    link = admin.get(f"/graph/sources/{world['doc'].id}/link").json()
    assert link["expiresInSec"] == 300 and link["url"]
