"""Backend-модуль «Языки» (IELTS/TOEFL). Подключается через `core.modules`."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from core.models import Activity, SrsCard
from core.manifest import ModuleManifest
from core.methods import APPLY, StudyMethod
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


# Reading-дрилл: текст, вопросы трёх форматов и время. Верные ответы лежат в payload —
# проверка локальная и мгновенная, без сети (R-0022); это тренировка, а не экзамен.
DEMO_READING_PAYLOAD: dict[str, Any] = {
    "title": "The Rise of Urban Gardens",
    "timeLimitSec": 600,
    "passage": (
        "Urban gardens have spread rapidly across large cities over the past two decades. "
        "Residents turn unused rooftops, vacant lots and balconies into small farms, "
        "growing vegetables, herbs and fruit. Supporters argue that these gardens reduce "
        "food transport costs, improve air quality and give neighbours a place to meet.\n\n"
        "Critics, however, point out that urban gardens produce only a small share of a "
        "city's food. Soil in some districts contains lead and other pollutants, so "
        "produce must be tested before it is eaten. Several city councils have therefore "
        "introduced guidelines requiring regular soil tests before a garden may open."
    ),
    "questions": [
        {
            "id": "q1",
            "type": "mcq",
            "prompt": "According to the passage, supporters say urban gardens...",
            "options": [
                "replace supermarkets completely",
                "reduce food transport costs",
                "make soil cleaner",
                "need no regulation",
            ],
            "answer": "reduce food transport costs",
            "explanation": "Supporters name transport costs, air quality and meeting places.",
        },
        {
            "id": "q2",
            "type": "tfng",
            "prompt": "Urban gardens produce most of the food that a city consumes.",
            "answer": "false",
            "explanation": "Critics say they produce only a small share.",
        },
        {
            "id": "q3",
            "type": "tfng",
            "prompt": "Every city in the world has introduced soil testing rules.",
            "answer": "not given",
            "explanation": "Only 'several city councils' are mentioned.",
        },
        {
            "id": "q4",
            "type": "gap",
            "prompt": "Produce must be ____ before it is eaten.",
            "answer": ["tested"],
            "explanation": "The passage says produce must be tested before it is eaten.",
        },
    ],
}


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


_GAP_MIN_LEN = 5
_MAX_GAPS = 3


def reading_payload(node: dict[str, Any]) -> dict[str, Any] | None:
    """Дрилл чтения из теории узла: текст — его формулировка и разделы, вопросы — пропуски.

    Без сети и модели: пропуск делается по самому длинному слову предложения, ответ берётся
    из текста, поэтому проверка локальная. Нет формулировки — дрилла нет (шаг пропускается).
    """
    content = node.get("content") or {}
    summary = (content.get("summary") or "").strip()
    if not summary:
        return None
    sections = [s.get("body", "").strip() for s in content.get("sections") or []]
    passage = "\n\n".join(p for p in [summary, *sections] if p)
    questions: list[dict[str, Any]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", summary):
        words = [w for w in re.findall(r"[^\W\d_]+", sentence) if len(w) >= _GAP_MIN_LEN]
        if not words:
            continue
        target = max(words, key=len)
        questions.append(
            {
                "id": f"q{len(questions) + 1}",
                "type": "gap",
                "prompt": sentence.replace(target, "____", 1),
                "answer": [target],
                "explanation": f"В тексте: «{sentence}»",
            }
        )
        if len(questions) == _MAX_GAPS:
            break
    if not questions:
        return None
    base = {k: v for k, v in node.items() if k != "content"}
    return {
        **base,
        "title": node.get("title") or "Reading",
        "timeLimitSec": 300,
        "passage": passage,
        "questions": questions,
    }


class LanguagesModule(BackendModule):
    id = MODULE_ID
    first_party = True
    manifest = ModuleManifest(
        id=MODULE_ID,
        title="Языки: письмо, чтение, словарь",
        version="1.0",
        provides=frozenset(
            {
                "rubrics",
                "grade_jobs",
                "provision",
                "study_methods",
                "apply_activity",
                "activity_payload",
            }
        ),
        requires=frozenset({"data.activity", "data.srs_card"}),
    )

    def study_methods(self) -> list[StudyMethod]:
        """Письмо с оценкой по рубрике: рубрики и оценщики лежат в модуле, не в ядре."""
        return [
            StudyMethod(
                "writing",
                "Письмо с оценкой по рубрике",
                APPLY,
                "ielts_writing_task2",
                in_course=False,
            ),
            StudyMethod(
                "reading_drill",
                "Чтение с вопросами",
                APPLY,
                "reading_drill",
                offline=True,
                in_course=False,
            ),
        ]

    def apply_activity(self, domain: str) -> str | None:
        """Практика языкового предмета — дрилл чтения по теории узла (T-0020, R-0013)."""
        return "reading_drill" if is_language_subject({"id": domain, "title": domain}) else None

    def activity_payload(self, activity_type: str, node: dict[str, Any]) -> dict[str, Any] | None:
        if activity_type != "reading_drill":
            return None
        return reading_payload(node)

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
        # Чтение нужно и IELTS, и TOEFL.
        if (
            session.query(Activity)
            .filter_by(user_id=user_id, module=MODULE_ID, type="reading_drill")
            .first()
            is None
        ):
            session.add(
                Activity(
                    user_id=user_id,
                    module=MODULE_ID,
                    type="reading_drill",
                    connectivity="offline",
                    payload=DEMO_READING_PAYLOAD,
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
