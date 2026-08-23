"""Centrality и детекция фундаментального ядра.

«Фундаментальность» = доля узлов домена, которые транзитивно зависят от данного
по рёбрам prereq/specializes.
"""

from __future__ import annotations

from modules.knowledge.centrality import CORE_THRESHOLD, recompute_centrality
from modules.knowledge.models import Concept, ConceptEdge


def _c(session, title, *, domain="ml", tier="derived"):
    c = Concept(
        domain=domain,
        title=title,
        tier=tier,
        content={},
        bloom_levels=[],
        difficulty=1,
        source="llm",
        status="draft",
    )
    session.add(c)
    session.flush()
    return c


def _edge(session, a, b, kind="prereq"):
    session.add(ConceptEdge(from_id=a.id, to_id=b.id, type=kind))
    session.flush()


def test_root_of_chain_is_most_central(session):
    """Цепочка A→B→C: от A зависят двое из двух остальных → centrality 1.0."""
    a, b, c = _c(session, "A"), _c(session, "B"), _c(session, "C")
    _edge(session, a, b)
    _edge(session, b, c)

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert rows["A"]["centrality"] == 1.0
    assert rows["A"]["dependents"] == 2
    assert rows["B"]["centrality"] == 0.5
    assert rows["C"]["centrality"] == 0.0


def test_result_is_sorted_by_centrality(session):
    a, b, c = _c(session, "A"), _c(session, "B"), _c(session, "C")
    _edge(session, a, b)
    _edge(session, b, c)

    values = [r["centrality"] for r in recompute_centrality(session, "ml")]

    assert values == sorted(values, reverse=True)


def test_centrality_is_persisted_on_the_node(session):
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)

    recompute_centrality(session, "ml")

    assert session.get(Concept, a.id).centrality == 1.0


def test_specializes_counts_as_dependency(session):
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b, kind="specializes")

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert rows["A"]["dependents"] == 1


def test_related_edges_do_not_count(session):
    """Контекстные связи не делают узел фундаментальнее — порядок задают только prereq/specializes."""
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b, kind="related")

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert rows["A"]["dependents"] == 0


def test_cycle_does_not_hang(session):
    """Граф от LLM может прийти с циклом — обход обязан завершиться."""
    a, b = _c(session, "A"), _c(session, "B")
    _edge(session, a, b)
    _edge(session, b, a)

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert rows["A"]["dependents"] == 2  # включая себя через цикл
    assert rows["B"]["dependents"] == 2


def test_core_suggested_by_metric(session):
    a, b, c = _c(session, "A"), _c(session, "B"), _c(session, "C")
    _edge(session, a, b)
    _edge(session, b, c)

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert rows["A"]["centrality"] >= CORE_THRESHOLD
    assert rows["A"]["suggestedCore"] is True
    assert rows["C"]["suggestedCore"] is False


def test_core_suggested_by_llm_mark_even_with_low_metric(session):
    """Гибрид: помеченный ядром узел остаётся кандидатом, даже если метрика низкая."""
    _c(session, "Одинокий", tier="core")
    _c(session, "Прочий")

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert rows["Одинокий"]["centrality"] == 0.0
    assert rows["Одинокий"]["suggestedCore"] is True
    assert rows["Прочий"]["suggestedCore"] is False


def test_other_domains_are_not_counted(session):
    a = _c(session, "A", domain="ml")
    b = _c(session, "B", domain="math")
    _edge(session, a, b)

    rows = {r["title"]: r for r in recompute_centrality(session, "ml")}

    assert set(rows) == {"A"}
    assert rows["A"]["dependents"] == 0
