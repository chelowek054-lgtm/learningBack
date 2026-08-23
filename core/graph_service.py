"""Центральность и core-детекция (KG2-01). «Фундаментальность» узла = сколько концепций
(транзитивно) зависят от него по prereq/specializes. Гибрид: метрика + курирование.
См. 05-knowledge-model §8."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from core.models import Concept, ConceptEdge

# Порог предложения в ядро (доля узлов, зависящих от данного).
CORE_THRESHOLD = 0.5
# Рёбра, по которым узел считается «более фундаментальным» (идёт раньше).
_DEP_EDGE_TYPES = ("prereq", "specializes")


def recompute_centrality(session: Session, domain: str) -> list[dict[str, Any]]:
    """Пересчитать centrality всех узлов домена (нормированный descendant-count) и
    вернуть список с предложением tier='core'."""
    concepts = session.query(Concept).filter(Concept.domain == domain).all()
    ids = {c.id for c in concepts}

    adj: dict[uuid.UUID, list[uuid.UUID]] = {c.id: [] for c in concepts}
    for e in session.query(ConceptEdge).filter(ConceptEdge.type.in_(_DEP_EDGE_TYPES)).all():
        if e.from_id in ids and e.to_id in ids:
            adj[e.from_id].append(e.to_id)  # from — предпосылка → to зависит от from

    def descendants(start: uuid.UUID) -> int:
        seen: set[uuid.UUID] = set()
        stack = list(adj[start])
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj[n])
        return len(seen)

    denom = max(1, len(concepts) - 1)
    out: list[dict[str, Any]] = []
    for c in concepts:
        d = descendants(c.id)
        c.centrality = round(d / denom, 3)
        out.append(
            {
                "id": str(c.id),
                "title": c.title,
                "tier": c.tier,
                "centrality": c.centrality,
                "dependents": d,
                # гибрид: метрика ИЛИ уже помечен ядром LLM/куратором
                "suggestedCore": c.centrality >= CORE_THRESHOLD or c.tier == "core",
            }
        )
    session.commit()
    out.sort(key=lambda x: x["centrality"], reverse=True)
    return out
