"""Кэш заданий и его обесценивание по версии узла (KG3-03)."""

from __future__ import annotations

import pytest

from modules.knowledge import assessment_store
from modules.knowledge.assessment_store import find_cached, get_or_generate, purge_stale
from modules.knowledge.models import Assessment, Concept
from tests.conftest import make_user

THEORY = {
    "summary": "Backprop считает градиенты по цепному правилу.",
    "sections": [
        {
            "heading": "Обратный проход",
            "body": "Градиент течёт от потерь к весам.",
            "examples": ["двуслойная сеть"],
            "counter_examples": ["градиент считают прямым проходом"],
        }
    ],
    "references": [],
}


@pytest.fixture
def concept(session):
    c = Concept(
        domain="ml",
        title="Backprop",
        tier="core",
        content=THEORY,
        bloom_levels=["remember"],
        difficulty=2,
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def counting_generator(monkeypatch):
    """Считает, сколько раз реально дошло до генерации."""
    calls = []
    original = assessment_store.generate_assessment

    def spy(title, content, bloom, kind):
        calls.append((title, bloom, kind))
        return original(title, content, bloom, kind)

    monkeypatch.setattr(assessment_store, "generate_assessment", spy)
    return calls


def test_first_call_generates_and_persists(session, concept, counting_generator):
    payload, cached = get_or_generate(session, concept, "remember", "test")

    assert cached is False
    assert payload.items
    assert len(counting_generator) == 1
    assert find_cached(session, concept.id, concept.version, "remember", "test") is not None


def test_second_call_is_served_from_cache(session, concept, counting_generator):
    get_or_generate(session, concept, "remember", "test")
    payload, cached = get_or_generate(session, concept, "remember", "test")

    assert cached is True
    assert payload.items
    assert len(counting_generator) == 1, "повторный запрос не должен звать генерацию"


def test_bloom_and_kind_are_separate_entries(session, concept, counting_generator):
    get_or_generate(session, concept, "remember", "test")
    get_or_generate(session, concept, "understand", "test")
    get_or_generate(session, concept, "apply", "practice")

    assert len(counting_generator) == 3
    assert session.query(Assessment).filter_by(concept_id=concept.id).count() == 3


def test_editing_the_theory_invalidates_the_cache(session, concept, counting_generator):
    """Версия входит в ключ, поэтому правка теории сама обесценивает задания."""
    get_or_generate(session, concept, "remember", "test")

    concept.content = {**THEORY, "summary": "Переписанная теория."}
    concept.version += 1
    session.flush()

    payload, cached = get_or_generate(session, concept, "remember", "test")

    assert cached is False
    assert len(counting_generator) == 2
    assert payload.items[0].expected == "Переписанная теория."


def test_stale_rows_are_removed_on_regeneration(session, concept):
    get_or_generate(session, concept, "remember", "test")
    concept.version += 1
    session.flush()
    get_or_generate(session, concept, "remember", "test")

    rows = session.query(Assessment).filter_by(concept_id=concept.id).all()
    assert len(rows) == 1
    assert rows[0].concept_version == concept.version


def test_purge_leaves_the_current_version_alone(session, concept):
    get_or_generate(session, concept, "remember", "test")

    removed = purge_stale(session, concept)

    assert removed == 0
    assert session.query(Assessment).count() == 1


def test_force_regenerates_without_a_version_bump(session, concept, counting_generator):
    get_or_generate(session, concept, "remember", "test")
    payload, cached = get_or_generate(session, concept, "remember", "test", force=True)

    assert cached is False
    assert len(counting_generator) == 2
    assert payload.items
    assert session.query(Assessment).filter_by(concept_id=concept.id).count() == 1


def test_invalid_bloom_kind_pair_is_rejected_before_the_cache(session, concept):
    with pytest.raises(ValueError):
        get_or_generate(session, concept, "apply", "test")

    assert session.query(Assessment).count() == 0


# ---- эндпоинт ----


def test_endpoint_reports_cache_state(session, client, concept):
    api = client(make_user(session))

    first = api.get(f"/graph/nodes/{concept.id}/assessment?bloom=remember&kind=test")
    second = api.get(f"/graph/nodes/{concept.id}/assessment?bloom=remember&kind=test")

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert second.json()["conceptVersion"] == concept.version


def test_endpoint_rejects_impossible_pair(session, client, concept):
    api = client(make_user(session))

    response = api.get(f"/graph/nodes/{concept.id}/assessment?bloom=apply&kind=test")

    assert response.status_code == 422


def test_endpoint_reports_nodes_without_theory(session, client):
    bare = Concept(
        domain="ml",
        title="Ярлык",
        tier="derived",
        content={"summary": "коротко"},
        bloom_levels=[],
        difficulty=1,
        source="llm",
        status="draft",
    )
    session.add(bare)
    session.flush()

    response = client(make_user(session)).get(
        f"/graph/nodes/{bare.id}/assessment?bloom=remember&kind=test"
    )

    assert response.status_code == 409


def test_regenerate_is_admin_only(session, client, concept):
    path = f"/graph/nodes/{concept.id}/assessment/regenerate?bloom=remember&kind=test"

    assert client(make_user(session)).post(path).status_code == 403
    assert client(make_user(session, superuser=True)).post(path).status_code == 200


def test_missing_concept_is_404(session, client):
    import uuid

    response = client(make_user(session)).get(
        f"/graph/nodes/{uuid.uuid4()}/assessment?bloom=remember&kind=test"
    )

    assert response.status_code == 404
