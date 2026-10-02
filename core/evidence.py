"""Свидетельство об освоении — единый формат для любого способа (T-0062, R-0034).

Способ запоминания не пишет в таблицы графа и не знает, как считается освоенность: он
сообщает, что человек ответил и с каким результатом. Граф (через модуль, который принимает
свидетельства) учитывает результат, а источник нужен только для журнала и разбора.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Evidence:
    """Результат от 0 до 1 по ступени `bloom` для узла `concept_id`; `source` — id способа."""

    concept_id: uuid.UUID
    bloom: str
    score: float
    source: str = "unknown"

    def validate(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("Результат свидетельства — число от 0 до 1")
        if not self.bloom.strip():
            raise ValueError("Не указана ступень освоения")


def dispatch(
    session: Session, user_id: Any, domain: str, evidence: Evidence, modules: list[Any]
) -> int:
    """Передать свидетельство модулям, которые их принимают; вернуть, скольким передано."""
    evidence.validate()
    accepted = 0
    for module in modules:
        if module.accepts_evidence():
            module.accept_evidence(session, user_id, domain, evidence)
            accepted += 1
    return accepted
