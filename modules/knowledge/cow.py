"""COW-чтение графа знаний (KG1-02). Эффективный граф пользователя = канон,
перекрытый персональным слоем (user_concept/user_edge). См. 05-knowledge-model §2."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge.content import ensure_shape
from modules.knowledge.models import Concept, ConceptEdge, UserConcept, UserEdge


def resolve_node(c: Concept | None, uc: UserConcept | None) -> dict[str, Any]:
    """Эффективный узел: канон + персональный оверрайд, либо свой персональный узел."""
    if c is not None:
        content = uc.content_override if (uc and uc.content_override is not None) else c.content
        return {
            "id": str(c.id),
            "kind": "canonical",
            "userConceptId": str(uc.id) if uc else None,
            "title": c.title,
            "tier": c.tier,
            "centrality": c.centrality,
            "content": ensure_shape(content),
            "bloomLevels": c.bloom_levels,
            "difficulty": c.difficulty,
            "version": c.version,
            "mastery": uc.mastery if uc else {},
            "status": uc.status if uc else "locked",
            "origin": uc.origin if uc else "inherited",
        }
    # собственный персональный узел (base_concept_id = null)
    assert uc is not None
    return {
        "id": str(uc.id),
        "kind": "personal",
        "userConceptId": str(uc.id),
        "title": uc.title,
        "tier": "derived",
        "centrality": 0.0,
        "content": ensure_shape(uc.content_override),
        "bloomLevels": [],
        "difficulty": 1,
        "version": 1,
        "mastery": uc.mastery,
        "status": uc.status,
        "origin": uc.origin,
    }


def effective_graph(session: Session, user_id: uuid.UUID, domain: str) -> dict[str, Any]:
    """Полный граф пользователя в домене: наследованный канон + персональные правки/ветки."""
    concepts = session.query(Concept).filter(Concept.domain == domain).all()
    concept_ids = {c.id for c in concepts}

    # Фильтр по домену обязателен: иначе свои узлы пользователя из других
    # областей подмешиваются в этот граф (баг до миграции 0006).
    ucs = (
        session.query(UserConcept)
        .filter(UserConcept.user_id == user_id, UserConcept.domain == domain)
        .all()
    )
    by_base = {uc.base_concept_id: uc for uc in ucs if uc.base_concept_id is not None}
    own = [uc for uc in ucs if uc.base_concept_id is None]

    nodes = [resolve_node(c, by_base.get(c.id)) for c in concepts]
    nodes += [resolve_node(None, uc) for uc in own]

    edges: list[dict[str, Any]] = []
    canon_edges = (
        session.query(ConceptEdge)
        .filter(ConceptEdge.from_id.in_(concept_ids), ConceptEdge.to_id.in_(concept_ids))
        .all()
        if concept_ids
        else []
    )
    for e in canon_edges:
        edges.append(
            {"id": str(e.id), "from": str(e.from_id), "to": str(e.to_id),
             "type": e.type, "kind": "canonical"}
        )
    user_edges = (
        session.query(UserEdge)
        .filter(UserEdge.user_id == user_id, UserEdge.domain == domain)
        .all()
    )
    for ue in user_edges:
        edges.append(
            {"id": str(ue.id), "from": str(ue.from_id), "to": str(ue.to_id),
             "type": ue.type, "kind": "personal"}
        )

    return {"domain": domain, "nodes": nodes, "edges": edges}
