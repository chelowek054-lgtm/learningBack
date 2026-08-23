"""Права на граф: канон общий, поэтому его правит только администратор.

Персональный слой (COW) — данные пользователя, он остаётся открытым всем.
"""

from __future__ import annotations

import pytest

from modules.knowledge.models import Concept
from tests.conftest import make_user

CANON_WRITES = [
    ("post", "/graph/canon/build", {"domain": "ml", "topic": "t"}),
    (
        "post",
        "/graph/canon/nodes",
        {"domain": "ml", "title": "Новый", "tier": "derived", "content": {}},
    ),
    ("post", "/graph/canon/recompute-centrality", {"domain": "ml"}),
]


def _concept(session, title="Backprop", domain="ml"):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content={"summary": "s"},
        bloom_levels=[],
        difficulty=1,
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


@pytest.mark.parametrize("method,path,body", CANON_WRITES)
def test_canon_write_forbidden_for_regular_user(session, client, method, path, body):
    api = client(make_user(session))

    response = getattr(api, method)(path, json=body)

    assert response.status_code == 403


@pytest.mark.parametrize("method,path,body", CANON_WRITES)
def test_canon_write_allowed_for_superuser(session, client, method, path, body):
    api = client(make_user(session, superuser=True))

    response = getattr(api, method)(path, json=body)

    assert response.status_code < 400


def test_approve_is_admin_only(session, client):
    c = _concept(session)

    forbidden = client(make_user(session)).post(
        f"/graph/canon/nodes/{c.id}/approve", json={"tier": "core"}
    )
    assert forbidden.status_code == 403

    allowed = client(make_user(session, superuser=True)).post(
        f"/graph/canon/nodes/{c.id}/approve", json={"tier": "core"}
    )
    assert allowed.status_code == 200


def test_regular_user_can_read_the_graph(session, client):
    _concept(session)

    response = client(make_user(session)).get("/graph/ml")

    assert response.status_code == 200
    assert len(response.json()["nodes"]) == 1


def test_regular_user_can_override_a_canon_node(session, client):
    """Правка своего слоя — не курирование: она никому, кроме автора, не видна."""
    c = _concept(session)
    api = client(make_user(session))

    response = api.post(f"/graph/nodes/{c.id}/override", json={"content": {"summary": "моё"}})

    assert response.status_code == 200
    assert session.get(Concept, c.id).content["summary"] == "s"


def test_canon_edit_does_not_touch_version_of_other_fields(session, client):
    """Версия узла растёт только при смене контента — на неё завязан кэш заданий."""
    c = _concept(session)
    before = c.version
    api = client(make_user(session, superuser=True))

    api.put(f"/graph/canon/nodes/{c.id}", json={"title": "Другое имя"})
    assert session.get(Concept, c.id).version == before

    api.put(f"/graph/canon/nodes/{c.id}", json={"content": {"summary": "новое"}})
    assert session.get(Concept, c.id).version == before + 1
