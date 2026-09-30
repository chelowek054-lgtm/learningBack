"""Backend-модуль «Языки» (IELTS/TOEFL). Подключается через `core.modules`."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from core.models import Activity, SrsCard
from core.modules import BackendModule
from core.srs import insert_cards
from modules.languages.generators import awl_card_partials
from modules.languages.rubrics import RUBRICS

MODULE_ID = "languages"

# Типы Activity зеркалят клиентский манифест (03-functional §1.1).
ACTIVITY_TYPES = [
    "ielts_writing_task2",
    "ielts_writing_task1",
    "reading_drill",
    "listening_drill",
    "speaking_response",
    "vocab_srs",
]

# Предмет считается языковым, если в названии есть экзамен или английский.
_LANGUAGE_SUBJECT = re.compile(r"ielts|toefl|english|англ", re.IGNORECASE)

DEMO_ESSAY_PROMPT = (
    "Some people believe technology makes life more complex. "
    "To what extent do you agree or disagree?"
)


def is_language_subject(subject: dict[str, Any]) -> bool:
    return bool(_LANGUAGE_SUBJECT.search(f"{subject.get('id', '')} {subject.get('title', '')}"))


class LanguagesModule(BackendModule):
    id = MODULE_ID

    def rubrics(self) -> list[dict[str, Any]]:
        return RUBRICS

    def grade_jobs(self) -> dict[str, str]:
        return {"grade_writing": MODULE_ID}

    def provision(self, session, user_id, subject, now: datetime) -> None:
        """Колода AWL и пробное эссе — только тому, кто учит язык (FR-SRS-05)."""
        if not is_language_subject(subject):
            return
        if session.query(SrsCard).filter_by(user_id=user_id, source="awl").first() is None:
            insert_cards(session, user_id, MODULE_ID, awl_card_partials(), now)
        if (
            session.query(Activity)
            .filter_by(user_id=user_id, module=MODULE_ID, type="ielts_writing_task2")
            .first()
            is None
        ):
            session.add(
                Activity(
                    user_id=user_id,
                    module=MODULE_ID,
                    type="ielts_writing_task2",
                    connectivity="online",
                    payload={"prompt": DEMO_ESSAY_PROMPT},
                )
            )


backend = LanguagesModule()
