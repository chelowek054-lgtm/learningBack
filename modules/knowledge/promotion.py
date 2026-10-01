"""Промоция персональных узлов в канон (T-0001, AC-13.5).

Кандидат — то, что пользователи уже делают с графом сами: свой узел с тем же
названием у нескольких людей либо правка одного и того же канон-узла. Решает
администратор: промоция меняет граф для всех.

Два случая:
- свой узел (`base_concept_id is null`) → создаётся канонический узел версии 1,
  а узел автора становится обычной записью поверх него: оверрайда нет,
  освоенность и статус сохранены;
- правка канон-узла (оверрайд) → её содержимое становится содержимым канона
  с новой версией (кэш заданий обесценивается), у автора оверрайд снимается.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String, cast, func
from sqlalchemy.orm import Session

from modules.knowledge.content import ensure_shape
from modules.knowledge.models import Concept, ConceptEdge, UserConcept, UserEdge


class NotPromotable(Exception):
    """Узел нельзя продвинуть (пустая теория, имя занято каноном)."""


def candidates(session: Session, domain: str, min_users: int = 1) -> list[dict[str, Any]]:
    """Кандидаты в домене, самые востребованные первыми."""
    out: list[dict[str, Any]] = []

    overrides = (
        session.query(
            UserConcept.base_concept_id,
            func.count(func.distinct(UserConcept.user_id)),
            func.min(cast(UserConcept.id, String)),
        )
        .filter(
            UserConcept.domain == domain,
            UserConcept.base_concept_id.isnot(None),
            UserConcept.content_override.isnot(None),
        )
        .group_by(UserConcept.base_concept_id)
        .all()
    )
    for base_id, users, sample in overrides:
        base = session.get(Concept, base_id)
        if base is not None and users >= min_users:
            out.append(
                {
                    "kind": "override",
                    "title": base.title,
                    "baseConceptId": str(base_id),
                    "users": users,
                    "sampleUserConceptId": str(sample),
                }
            )

    own = (
        session.query(
            func.lower(UserConcept.title),
            func.min(UserConcept.title),
            func.count(func.distinct(UserConcept.user_id)),
            func.min(cast(UserConcept.id, String)),
        )
        .filter(
            UserConcept.domain == domain,
            UserConcept.base_concept_id.is_(None),
            UserConcept.title.isnot(None),
        )
        .group_by(func.lower(UserConcept.title))
        .all()
    )
    for _key, title, users, sample in own:
        if users >= min_users:
            out.append(
                {
                    "kind": "own",
                    "title": title,
                    "baseConceptId": None,
                    "users": users,
                    "sampleUserConceptId": str(sample),
                }
            )

    out.sort(key=lambda c: (-c["users"], c["title"]))
    return out


def promote(session: Session, user_concept_id: uuid.UUID, tier: str = "derived") -> dict[str, Any]:
    uc = session.get(UserConcept, user_concept_id)
    if uc is None:
        raise LookupError("персональный узел не найден")
    if uc.content_override is None:
        raise NotPromotable("у узла нет собственной теории — продвигать нечего")
    content = ensure_shape(uc.content_override)

    if uc.base_concept_id is not None:
        concept = session.get(Concept, uc.base_concept_id)
        assert concept is not None
        concept.content = content
        concept.version += 1
        concept.source = "promoted"
        uc.content_override = None
        uc.origin = "inherited"
        uc.version += 1
        session.flush()
        return {"id": str(concept.id), "version": concept.version, "kind": "override"}

    if not uc.title:
        raise NotPromotable("у узла нет названия")
    clash = session.query(Concept).filter_by(domain=uc.domain, title=uc.title).first()
    if clash is not None:
        raise NotPromotable("в каноне уже есть узел с таким названием")

    concept = Concept(
        domain=uc.domain,
        title=uc.title,
        tier=tier,
        content=content,
        source="promoted",
        status="approved",
    )
    session.add(concept)
    session.flush()

    # Узел автора теперь стоит поверх канона: без оверрайда, с прежним прогрессом.
    old_id = uc.id
    uc.base_concept_id = concept.id
    uc.title = None
    uc.content_override = None
    uc.origin = "inherited"
    _rewire_edges(session, uc.user_id, old_id=old_id, new_id=concept.id)
    session.flush()
    return {"id": str(concept.id), "version": concept.version, "kind": "own"}


def _rewire_edges(
    session: Session, user_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID
) -> None:
    """Связи автора со старым id переносятся на канон-узел.

    Связь между двумя канон-узлами становится канонической; связь с другим
    личным узлом остаётся личной, но смотрит на новый id.
    """
    edges = (
        session.query(UserEdge)
        .filter(UserEdge.user_id == user_id)
        .filter((UserEdge.from_id == old_id) | (UserEdge.to_id == old_id))
        .all()
    )
    for e in edges:
        if e.from_id == old_id:
            e.from_id = new_id
        if e.to_id == old_id:
            e.to_id = new_id
        both_canon = (
            session.get(Concept, e.from_id) is not None
            and session.get(Concept, e.to_id) is not None
        )
        if both_canon:
            exists = (
                session.query(ConceptEdge)
                .filter_by(from_id=e.from_id, to_id=e.to_id, type=e.type)
                .first()
            )
            if exists is None:
                session.add(ConceptEdge(from_id=e.from_id, to_id=e.to_id, type=e.type))
            session.delete(e)
