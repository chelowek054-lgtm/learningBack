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
    "toefl_writing_independent",
    "toefl_writing_integrated",
    "reading_drill",
    "listening_drill",
    "speaking_response",
    "vocab_srs",
]

# Предмет считается языковым, если в названии есть экзамен или английский.
_LANGUAGE_SUBJECT = re.compile(r"ielts|toefl|english|англ", re.IGNORECASE)

_TOEFL_SUBJECT = re.compile(r"toefl", re.IGNORECASE)

DEMO_TOEFL_PROMPT = (
    "Do you agree or disagree: universities should require every student to take "
    "a course in a foreign language? Use specific reasons and examples to support your answer."
)

DEMO_ESSAY_PROMPT = (
    "Some people believe technology makes life more complex. "
    "To what extent do you agree or disagree?"
)


# Задание Task 1: данные лежат в payload, клиент рисует их сам (картинка не обязательна).
DEMO_TASK1_PAYLOAD: dict[str, Any] = {
    "prompt": (
        "The chart shows the percentage of households with internet access in three "
        "countries. Summarise the information by selecting and reporting the main features, "
        "and make comparisons where relevant."
    ),
    "minWords": 150,
    "rubricId": "ielts_writing_task1",
    "data": {
        "kind": "bar",
        "title": "Households with internet access (%)",
        "unit": "%",
        "categories": ["2010", "2015", "2020"],
        "series": [
            {"name": "Country A", "values": [52, 68, 85]},
            {"name": "Country B", "values": [34, 51, 70]},
            {"name": "Country C", "values": [71, 78, 83]},
        ],
    },
}


def is_language_subject(subject: dict[str, Any]) -> bool:
    return bool(_LANGUAGE_SUBJECT.search(f"{subject.get('id', '')} {subject.get('title', '')}"))


def is_toefl_subject(subject: dict[str, Any]) -> bool:
    return bool(_TOEFL_SUBJECT.search(f"{subject.get('id', '')} {subject.get('title', '')}"))


def demo_writing(subject: dict[str, Any]) -> tuple[str, str, str]:
    """(тип Activity, рубрика, текст задания) для пробного письма по предмету.

    Тип выбирается по предмету, который назвал человек: TOEFL → задание TOEFL
    со своей рубрикой, иначе IELTS Task 2.
    """
    if is_toefl_subject(subject):
        return "toefl_writing_independent", "toefl_writing_independent", DEMO_TOEFL_PROMPT
    return "ielts_writing_task2", "ielts_writing_task2", DEMO_ESSAY_PROMPT


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
        activity_type, rubric_id, prompt = demo_writing(subject)
        if (
            session.query(Activity)
            .filter_by(user_id=user_id, module=MODULE_ID, type=activity_type)
            .first()
            is None
        ):
            session.add(
                Activity(
                    user_id=user_id,
                    module=MODULE_ID,
                    type=activity_type,
                    connectivity="online",
                    payload={"prompt": prompt, "rubricId": rubric_id},
                )
            )
        # IELTS — ещё и описание данных (Task 1); TOEFL такого задания не имеет.
        if not is_toefl_subject(subject) and (
            session.query(Activity)
            .filter_by(user_id=user_id, module=MODULE_ID, type="ielts_writing_task1")
            .first()
            is None
        ):
            session.add(
                Activity(
                    user_id=user_id,
                    module=MODULE_ID,
                    type="ielts_writing_task1",
                    connectivity="online",
                    payload=DEMO_TASK1_PAYLOAD,
                )
            )


backend = LanguagesModule()
