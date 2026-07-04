"""Генераторы контента модуля «Программирование/ML». WS4.

Для MVP concept_recall-активности сидируются напрямую (см. scripts/seed.py).
error→card (concept_srs) — через общий core.srs.errors_to_card_partials.
LLM-генерация вопросов из материала — Фаза 2.
"""

from typing import Any


def concept_recall_from_material(material: dict[str, Any]) -> list[dict[str, Any]]:
    """Минимально: превратить перечисленные концепты материала в payload'ы concept_recall."""
    concepts = material.get("concepts", [])
    return [{"prompt": f"Объясни: {c}", "concept": c} for c in concepts]
