"""Права на граф: канон общий, поэтому его правит только администратор.

Персональный слой (COW) — данные пользователя, он остаётся открытым всем.
"""

from __future__ import annotations

import pytest

from modules.knowledge.models import Concept
from tests.conftest import make_user

# build здесь НЕ перечислен: пустую область заводит любой пользователь,
# правило для него отдельное — см. тесты ниже.
CANON_WRITES = [
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


def test_regular_user_builds_an_empty_domain(session, client):
    """Предмет выбирает пользователь — значит, и завести его он должен сам.

    Пока это было закрыто админом, выбор нового предмета упирался в тупик:
    карты нет, построить некому, и «появится, как только будет готова» не
    сбывалось никогда.
    """
    api = client(make_user(session))

    response = api.post("/graph/canon/build", json={"domain": "рыбалка", "topic": "Рыбалка"})

    assert response.status_code == 200


def test_built_nodes_are_unmoderated(session, client):
    """Построенное пользователем — черновик: куратор ещё не смотрел."""
    api = client(make_user(session))
    api.post("/graph/canon/build", json={"domain": "рыбалка", "topic": "Рыбалка"})

    built = session.query(Concept).filter_by(domain="рыбалка").all()
    assert built, "граф должен был появиться"
    assert {c.status for c in built} == {"draft"}


def test_regular_user_cannot_rebuild_an_existing_domain(session, client):
    """Канон общий: переписывать уже построенное вправе только куратор."""
    _concept(session, domain="ml")
    api = client(make_user(session))

    response = api.post("/graph/canon/build", json={"domain": "ml", "topic": "t"})

    assert response.status_code == 403


def test_regular_user_cannot_refresh(session, client):
    """refresh перегенерирует теорию — это правка курированного контента."""
    api = client(make_user(session))

    response = api.post(
        "/graph/canon/build", json={"domain": "новая", "topic": "t", "refresh": True}
    )

    assert response.status_code == 403


def test_review_status_reaches_the_client(session, client):
    """Без этого учащийся не отличит непроверенный черновик от вычитанного канона."""
    _concept(session, title="Проверенный")
    api = client(make_user(session))

    nodes = api.get("/graph/ml").json()["nodes"]

    assert nodes[0]["reviewStatus"] == "approved"


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
