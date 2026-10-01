"""Backend-модуль «Программирование/ML». Подключается через `core.modules`."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from core.models import Activity
from core.modules import BackendModule
from modules.ml.rubrics import RUBRICS

MODULE_ID = "ml"

# Типы Activity зеркалят клиентский манифест (03-functional §2.1).
ACTIVITY_TYPES = [
    "material_read",
    "concept_recall",
    "concept_srs",
    "code_task",
]

_ML_SUBJECT = re.compile(r"\bml\b|machine|deep learning|машин|нейро|глубок", re.IGNORECASE)


def is_ml_subject(subject: dict[str, Any]) -> bool:
    return bool(_ML_SUBJECT.search(f"{subject.get('id', '')} {subject.get('title', '')}"))


class MlModule(BackendModule):
    id = MODULE_ID

    def rubrics(self) -> list[dict[str, Any]]:
        return RUBRICS

    def grade_jobs(self) -> dict[str, str]:
        return {"grade_concept": MODULE_ID, "grade_code": MODULE_ID}

    def provision(self, session, user_id, subject, now: datetime) -> None:
        """Пробные задания по ML — только тому, кто учит ML."""
        if not is_ml_subject(subject):
            return
        if (
            session.query(Activity)
            .filter_by(user_id=user_id, module=MODULE_ID, type="concept_recall")
            .first()
            is not None
        ):
            return
        session.add(
            Activity(
                user_id=user_id,
                module=MODULE_ID,
                type="concept_recall",
                connectivity="online",
                payload={
                    "prompt": "Почему attention масштабируют на sqrt(d_k)?",
                    "concept": "scaled dot-product attention",
                },
            )
        )
        session.add(
            Activity(
                user_id=user_id,
                module=MODULE_ID,
                type="material_read",
                connectivity="offline",
                payload={
                    "title": "Scaled Dot-Product Attention",
                    "text": "Attention делит скоры на sqrt(d_k) для стабилизации градиентов.",
                },
            )
        )


backend = MlModule()
