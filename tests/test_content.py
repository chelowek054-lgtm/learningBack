"""Структура теории узла (KG3-01).

Форма контента — предусловие генерации заданий: если она «как повезёт», то и
заземлять генерацию не на что.
"""

from __future__ import annotations

import pytest

from modules.knowledge.content import NodeContent, coerce_content, ensure_shape
from modules.knowledge.models import Concept
from tests.conftest import make_user

FULL = {
    "summary": "Краткое описание",
    "sections": [
        {
            "heading": "Механика",
            "body": "Как оно работает",
            "examples": ["пример"],
            "counter_examples": ["заблуждение"],
        }
    ],
    "references": [{"title": "Книга", "url": "https://example.com"}],
}


def test_full_content_survives_a_round_trip():
    assert coerce_content(FULL) == FULL


def test_missing_keys_are_filled_in():
    """Узлы, созданные до KG3-01, читаются как полная форма."""
    assert ensure_shape({"summary": "только это"}) == {
        "summary": "только это",
        "sections": [],
        "references": [],
    }


def test_sections_get_empty_example_lists():
    result = coerce_content({"sections": [{"heading": "h", "body": "b"}]})

    assert result["sections"][0]["examples"] == []
    assert result["sections"][0]["counter_examples"] == []


@pytest.mark.parametrize("junk", [None, "строка", 42, [], {"sections": "не список"}])
def test_garbage_never_raises(junk):
    """Вывод LLM бывает любым: узел не должен теряться целиком из-за формы."""
    result = coerce_content(junk)

    assert set(result) == {"summary", "sections", "references"}


def test_partial_garbage_keeps_the_summary():
    result = coerce_content({"summary": "спасённое", "sections": "мусор"})

    assert result["summary"] == "спасённое"
    assert result["sections"] == []


def test_unknown_fields_are_dropped():
    result = coerce_content({"summary": "s", "выдумка": 1})

    assert "выдумка" not in result


def test_groundable_requires_sections_or_a_real_summary():
    """Один короткий summary — ярлык, а не контейнер знания."""
    assert NodeContent(summary="коротко").is_groundable() is False
    assert NodeContent(summary="x" * 120).is_groundable() is True
    assert (
        NodeContent(sections=[{"heading": "h", "body": "b"}]).is_groundable() is True
    )


def test_api_rejects_malformed_content(session, client):
    """Клиенту прощать форму не надо — в отличие от LLM."""
    api = client(make_user(session, superuser=True))

    response = api.post(
        "/graph/canon/nodes",
        json={"domain": "ml", "title": "T", "content": {"sections": "не список"}},
    )

    assert response.status_code == 422


def test_api_stores_normalized_content(session, client):
    api = client(make_user(session, superuser=True))

    created = api.post(
        "/graph/canon/nodes",
        json={"domain": "ml", "title": "T", "content": {"summary": "s"}},
    )

    stored = session.get(Concept, created.json()["id"]).content
    assert stored == {"summary": "s", "sections": [], "references": []}


def test_reading_a_node_returns_the_full_shape(session, client):
    """Даже если в БД лежит укороченный контент (создан до KG3-01)."""
    session.add(
        Concept(
            domain="ml",
            title="Старый",
            tier="derived",
            content={"summary": "только summary"},
            bloom_levels=[],
            difficulty=1,
            source="llm",
            status="draft",
        )
    )
    session.flush()

    node = client(make_user(session)).get("/graph/ml").json()["nodes"][0]

    assert set(node["content"]) == {"summary", "sections", "references"}


def test_build_fills_in_theory_for_nodes_that_lack_it(session, client):
    """Граф, построенный до KG3-01, должен дополняться, а не оставаться мёртвым."""
    stale = Concept(
        domain="ml",
        title="Линейная алгебра",
        tier="core",
        content={"summary": "коротко"},
        bloom_levels=[],
        difficulty=1,
        source="llm",
        status="draft",
    )
    session.add(stale)
    session.flush()
    before = stale.version

    client(make_user(session, superuser=True)).post(
        "/graph/canon/build", json={"domain": "ml", "topic": "Demo"}
    )

    refreshed = session.get(Concept, stale.id)
    assert refreshed.content["sections"], "теория должна появиться"
    assert refreshed.version == before + 1, "версия растёт → кэш заданий обесценивается"


def test_build_does_not_overwrite_usable_theory(session, client):
    """Курированный контент трогать нельзя — обновляем только непригодные узлы."""
    curated = Concept(
        domain="ml",
        title="Линейная алгебра",
        tier="core",
        content={
            "summary": "Моя формулировка",
            "sections": [{"heading": "Раздел", "body": "Текст", "examples": [], "counter_examples": []}],
            "references": [],
        },
        bloom_levels=[],
        difficulty=1,
        source="curated",
        status="approved",
    )
    session.add(curated)
    session.flush()
    before = curated.version

    client(make_user(session, superuser=True)).post(
        "/graph/canon/build", json={"domain": "ml", "topic": "Demo"}
    )

    kept = session.get(Concept, curated.id)
    assert kept.content["summary"] == "Моя формулировка"
    assert kept.version == before
