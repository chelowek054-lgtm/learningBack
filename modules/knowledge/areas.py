"""Принадлежность понятия областям (R-0073): одно понятие может входить в несколько областей.

`Concept.domain` — «домашняя» область, где понятие появилось; остальные членства — в `concept_area`
вместе с этапом, уровнем и необязательностью для этой области. Курс, схема, плейсмент и освоенность
берут понятия области через `in_area`, а не сравнивая `Concept.domain`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from modules.knowledge.models import Concept, ConceptArea


def in_area(domain: str):
    """Условие «понятие входит в область»: домашняя область или запись в concept_area."""
    return or_(
        Concept.domain == domain,
        Concept.id.in_(select(ConceptArea.concept_id).where(ConceptArea.domain == domain)),
    )


def area_concepts(session: Session, domain: str, *, include_rejected: bool = True) -> list[Concept]:
    q = session.query(Concept).filter(in_area(domain))
    if not include_rejected:
        q = q.filter(Concept.status != "rejected")
    return q.all()


def area_meta(session: Session, domain: str) -> dict[uuid.UUID, dict[str, Any]]:
    """Этап, уровень и необязательность понятий именно в этой области (иначе — собственные поля понятия)."""
    rows = session.query(ConceptArea).filter(ConceptArea.domain == domain).all()
    return {
        r.concept_id: {
            "stage": r.stage,
            "stageOrder": r.stage_order,
            "level": r.level,
            "optional": bool(r.optional),
        }
        for r in rows
    }
