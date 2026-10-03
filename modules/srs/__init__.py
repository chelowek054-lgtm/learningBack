"""Модуль интервального повторения (T-0053, R-0029).

Карточки и расписание повторений — способ «удержать» для любого узла любого предмета.
Сами карточки заводят другие модули (стартовые колоды, карточки ошибок); здесь — описание
способа и перевод результата повторения в свидетельство об освоении. Поведение для
учащегося то же: повторение идёт очередью карточек (A-0016), а не отдельной активностью.
"""

from __future__ import annotations

import uuid

from core.evidence import Evidence
from core.manifest import ModuleManifest
from core.methods import REMEMBER, StudyMethod
from core.modules import BackendModule

MODULE_ID = "srs"

# Оценка карточки (FSRS) → результат от 0 до 1: «снова» — провал, «легко» — полное владение.
RATING_SCORE = {"again": 0.0, "hard": 0.4, "good": 0.8, "easy": 1.0}


def evidence_from_review(concept_id: uuid.UUID, rating: str, bloom: str = "remember") -> Evidence:
    """Повторение карточки узла как свидетельство об освоении в общем формате."""
    if rating not in RATING_SCORE:
        raise ValueError(f"Неизвестная оценка повторения: {rating!r}")
    return Evidence(concept_id, bloom, RATING_SCORE[rating], source=MODULE_ID)


class SrsModule(BackendModule):
    id = MODULE_ID
    first_party = True
    manifest = ModuleManifest(
        id=MODULE_ID,
        title="Интервальное повторение",
        version="1.0",
        provides=frozenset({"study_methods"}),
        requires=frozenset({"data.srs_card"}),
    )

    def study_methods(self) -> list[StudyMethod]:
        # Тип «srs» — маркер шага курса: исполняется очередью карточек, не отдельной активностью.
        return [StudyMethod("srs", "Повторение карточек", REMEMBER, "srs", offline=True)]


backend = SrsModule()

__all__ = ["MODULE_ID", "RATING_SCORE", "backend", "evidence_from_review"]
