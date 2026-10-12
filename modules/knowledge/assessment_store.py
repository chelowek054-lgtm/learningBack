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

import random
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from modules.knowledge.content import ensure_shape
from modules.knowledge import langs
from modules.knowledge.assessment import (
    ITEMS_PER_REQUEST,
    AssessmentItem,
    AssessmentPayload,
    Option,
    generate_assessment,
    validate_request,
)
from modules.knowledge.models import Assessment, LearningItem, UserConcept


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


def ready_payload(
    session: Session, concept_id: uuid.UUID, version: int, bloom: str, kind: str, lang: str | None
) -> AssessmentPayload | None:
    """Задания из готового банка узла (R-0075): тест — из вариантов выбора, практика — из пропусков.

    Банк собран при сборке узла, поэтому здесь нет ни запросов к модели, ни ожидания; нет заданий
    нужной формы на нужной версии узла — None, и вызывающий строит их по-старому.
    """
    wanted = {"test": "mcq", "practice": "cloze", "probe": "mcq"}.get(kind)
    if wanted is None:
        return None
    for code in dict.fromkeys([x for x in (lang, "ru", *langs.supported()) if x]):
        rows = (
            session.query(LearningItem)
            .filter_by(concept_id=concept_id, concept_version=version, lang=code, kind=wanted)
            .all()
        )
        if not rows:
            continue
        random.shuffle(rows)
        items: list[AssessmentItem] = []
        for r in rows[:ITEMS_PER_REQUEST]:
            ref = ",".join(r.claim_ids or [])
            if wanted == "mcq":
                chosen = r.answer_key.get("index")
                opts = [
                    Option(text=str(o), correct=(i == chosen))
                    for i, o in enumerate(r.payload.get("options") or [])
                ]
                items.append(
                    AssessmentItem(
                        prompt=r.payload.get("question", ""), options=opts, grounded_in=ref
                    )
                )
            else:
                items.append(
                    AssessmentItem(
                        prompt=r.payload.get("text", ""),
                        expected=str(r.answer_key.get("answer", "")),
                        exact=True,
                        grounded_in=ref,
                    )
                )
        return AssessmentPayload(bloom=bloom, kind=kind, items=items)
    return None


def get_or_generate(
    session: Session,
    concept: Assessable,
    bloom: str,
    kind: str,
    *,
    force: bool = False,
    lang: str | None = None,
) -> tuple[AssessmentPayload, bool]:
    """Задания по узлу. Возвращает (payload, cached).

    `force` — перегенерировать, даже если в кэше что-то есть (курирование:
    задания не понравились, теория не менялась).
    """
    validate_request(bloom, kind)

    if not force:
        ready = ready_payload(session, concept.id, concept.version, bloom, kind, lang)
        if ready is not None:
            return ready, True

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
