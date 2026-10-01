"""Кэш сгенерированных заданий (KG3-03).

Генерация стоит денег и недетерминирована, поэтому результат кладётся в
`assessment` и переиспользуется. Ключ — `(concept_id, concept_version, bloom,
kind)`: версия узла входит в ключ, так что **правка теории автоматически
обесценивает старые задания** — отдельного механизма инвалидации не нужно
(инвариант №5 слоя: оценки помнят версию контента).

Строки от прошлых версий узла не переиспользуются никогда, поэтому при записи
новой версии они удаляются — иначе таблица растёт на каждую правку.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from modules.knowledge.content import ensure_shape
from modules.knowledge.assessment import (
    AssessmentPayload,
    generate_assessment,
    validate_request,
)
from modules.knowledge.models import Assessment, UserConcept


class Assessable(Protocol):
    """Всё, по чему можно генерировать задания: канон-узел или адаптер личного."""

    id: uuid.UUID
    version: int
    title: str
    content: Any


@dataclass(frozen=True)
class PersonalNodeRef:
    """Личный узел в виде, пригодном для генерации заданий (теория лежит в оверрайде)."""

    id: uuid.UUID
    version: int
    title: str
    content: dict[str, Any]

    @classmethod
    def of(cls, uc: UserConcept) -> "PersonalNodeRef":
        return cls(uc.id, uc.version, uc.title or "", ensure_shape(uc.content_override))


def find_cached(
    session: Session, concept_id: uuid.UUID, version: int, bloom: str, kind: str
) -> Assessment | None:
    return (
        session.query(Assessment)
        .filter(
            Assessment.concept_id == concept_id,
            Assessment.concept_version == version,
            Assessment.bloom == bloom,
            Assessment.kind == kind,
        )
        .first()
    )


def purge_stale(session: Session, concept: Assessable) -> int:
    """Удалить задания, сгенерированные по прежним версиям узла."""
    stale = (
        session.query(Assessment)
        .filter(
            Assessment.concept_id == concept.id,
            Assessment.concept_version != concept.version,
        )
        .all()
    )
    for row in stale:
        session.delete(row)
    return len(stale)


def get_or_generate(
    session: Session, concept: Assessable, bloom: str, kind: str, *, force: bool = False
) -> tuple[AssessmentPayload, bool]:
    """Задания по узлу. Возвращает (payload, cached).

    `force` — перегенерировать, даже если в кэше что-то есть (курирование:
    задания не понравились, теория не менялась).
    """
    validate_request(bloom, kind)

    row = find_cached(session, concept.id, concept.version, bloom, kind)
    if row is not None and not force:
        return AssessmentPayload.model_validate(row.payload), True

    payload = generate_assessment(concept.title, concept.content, bloom, kind)
    if row is None:
        row = Assessment(
            concept_id=concept.id,
            concept_version=concept.version,
            bloom=bloom,
            kind=kind,
            payload=payload.model_dump(),
        )
        session.add(row)
    else:
        row.payload = payload.model_dump()

    purge_stale(session, concept)
    session.commit()
    return payload, False
