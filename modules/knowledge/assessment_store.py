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

from sqlalchemy.orm import Session

from modules.knowledge.assessment import (
    AssessmentPayload,
    generate_assessment,
    validate_request,
)
from modules.knowledge.models import Assessment, Concept


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


def purge_stale(session: Session, concept: Concept) -> int:
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
    session: Session, concept: Concept, bloom: str, kind: str, *, force: bool = False
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
