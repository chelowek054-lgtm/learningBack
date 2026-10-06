"""Постановка цели как диалог (T-0061, R-0033, T-0074, R-0041): уточнение, пересказ, подтверждение.

Голая строка «название предмета» не даёт ни уточнить, ни проверить, что система поняла цель.
Поэтому перед построением графа идёт диалог: свободный ввод → 2–4 уточняющих вопроса (на них
можно не отвечать) → пересказ → явное подтверждение человеком.

Пять полей цели (R-0041): область; уровень, до которого учить; зачем (цель применения); что
человек уже знает; ограничения (срок, часов в неделю, формат). Пропущенное поле не блокирует
построение: оно берётся по умолчанию и перечислено в `assumed` («предположили»), чтобы человек
видел, чего система не знает, и мог поправить.

Пока цель не подтверждена, граф не строится и модель не тратится на построение. Хранится
только итог (пересказ), а не переписка: она нужна лишь затем, чтобы получить итог.

Здесь логика над словарями и одна таблица; запросы к модели идут через общий шлюз.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.ai_gateway import get_ai_gateway, has_llm
from modules.knowledge.models import GoalIntake

MIN_QUESTIONS = 2
MAX_QUESTIONS = 4
MAX_WISHES = 6
MAX_HOURS_PER_WEEK = 100
_MAX_TEXT = 300

# Уровни, которые человек может назвать целью (те же, что в выборе уровня на клиенте).
LEVELS = ("remember", "understand", "apply", "create")
DEFAULT_LEVEL = "apply"

QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "description": "2–4 коротких уточняющих вопроса",
            "items": {"type": "string"},
        }
    },
    "required": ["questions"],
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "area": {"type": "string", "description": "область, как её понял собеседник"},
        "goal": {"type": "string", "description": "для чего человеку это нужно"},
        "level": {"type": "string", "enum": list(LEVELS)},
        "wishes": {
            "type": "array",
            "description": "важные подтемы и пожелания",
            "items": {"type": "string"},
        },
        "knows": {
            "type": "string",
            "description": "что человек уже знает по теме, его словами; пусто, если не сказал",
        },
        "constraints": {
            "type": "object",
            "description": "ограничения, которые человек назвал; пустое поле — не назвал",
            "properties": {
                "deadline": {"type": "string", "description": "срок, как сказал человек"},
                "hoursPerWeek": {"type": "number", "description": "часов в неделю"},
                "format": {"type": "string", "description": "предпочитаемый формат занятий"},
            },
        },
    },
    "required": ["area", "goal", "level"],
}


class GoalIntakeError(ValueError):
    """Итог диалога некорректен; `code` — для тестов и клиента."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


# ---- уточняющие вопросы ----


def _fixture_questions() -> list[str]:
    """Без ключа модели — общие вопросы: цель, исходный уровень, важные подтемы."""
    return [
        "Для чего вам это — работа, экзамен, интерес?",
        "С какого уровня начинаете: что уже знаете?",
        "Есть ли срок и сколько часов в неделю можете заниматься?",
        "Какие подтемы особенно важны?",
    ]


def clean_questions(raw: Any) -> list[dict[str, str]]:
    """Привести ответ модели к 2–4 уникальным вопросам; нехватку добивают общими."""
    seen: set[str] = set()
    questions: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        text = _text(item)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            questions.append(text)
    for fallback in _fixture_questions():
        if len(questions) >= MIN_QUESTIONS:
            break
        if fallback.lower() not in seen:
            seen.add(fallback.lower())
            questions.append(fallback)
    return [{"id": f"q{i + 1}", "text": q} for i, q in enumerate(questions[:MAX_QUESTIONS])]


def propose_questions(text: str) -> list[dict[str, str]]:
    """Уточняющие вопросы к свободному вводу. Ничего не пишет в базу."""
    goal = _text(text)
    if not goal:
        raise GoalIntakeError("empty_goal", "Сначала опишите, что хотите изучить")
    if has_llm():
        raw = get_ai_gateway().structured(
            "submit_questions",
            "Вернуть уточняющие вопросы.",
            QUESTIONS_SCHEMA,
            (
                f"Человек хочет изучить: «{goal}». Задай от {MIN_QUESTIONS} до {MAX_QUESTIONS} "
                "коротких уточняющих вопросов. Нужно выяснить, если человек ещё не сказал: до какого "
                "уровня учить, зачем это ему (цель применения), что он уже знает по теме, какие у "
                "него ограничения (срок, часов в неделю, формат занятий). Спрашивай только о том, "
                "чего в запросе нет. Если область двусмысленна, первым вопросом разреши "
                "двусмысленность."
            ),
        )
        return clean_questions((raw or {}).get("questions"))
    return clean_questions(_fixture_questions())


# ---- пересказ ----


def _answered(answers: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Только вопросы, на которые ответили: пропущенные в пересказ не попадают."""
    out = []
    for a in answers:
        answer = _text(a.get("answer"))
        if answer:
            out.append({"question": _text(a.get("question")), "answer": answer})
    return out


def clean_summary(raw: Any, text: str) -> dict[str, Any]:
    """Привести пересказ к безопасному виду: непустая область, известный уровень, лимиты."""
    raw = raw if isinstance(raw, dict) else {}
    area = _text(raw.get("area")) or _text(text)
    if not area:
        raise GoalIntakeError("empty_area", "Не удалось понять область")
    level = raw.get("level") if raw.get("level") in LEVELS else DEFAULT_LEVEL
    wishes: list[str] = []
    for w in raw.get("wishes") or []:
        item = _text(w)
        if item and item not in wishes:
            wishes.append(item)
    goal = _text(raw.get("goal"))
    knows = _text(raw.get("knows"))
    constraints = clean_constraints(raw.get("constraints"))
    # Что человек не назвал, а система подставила сама: ему это показывают словами «предположили».
    assumed = [
        field
        for field, stated in (
            ("goal", bool(goal) and goal != area),
            ("level", raw.get("level") in LEVELS),
            ("knows", bool(knows)),
            ("constraints", bool(constraints)),
        )
        if not stated
    ]
    return {
        "area": area,
        "goal": goal or area,
        "level": level,
        "wishes": wishes[:MAX_WISHES],
        "knows": knows,
        "constraints": constraints,
        "assumed": assumed,
    }


def clean_constraints(raw: Any) -> dict[str, Any]:
    """Ограничения: срок и формат — текст, часы в неделю — число от 0 до 100; пустое не хранится."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    if deadline := _text(raw.get("deadline"), 100):
        out["deadline"] = deadline
    if fmt := _text(raw.get("format"), 100):
        out["format"] = fmt
    hours = raw.get("hoursPerWeek")
    if (
        isinstance(hours, int | float)
        and not isinstance(hours, bool)
        and 0 < hours <= MAX_HOURS_PER_WEEK
    ):
        out["hoursPerWeek"] = int(hours) if hours == int(hours) else float(hours)
    return out


def summarize(text: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Пересказ итога диалога для подтверждения. Ничего не пишет в базу."""
    goal = _text(text)
    if not goal:
        raise GoalIntakeError("empty_goal", "Сначала опишите, что хотите изучить")
    given = _answered(answers)
    if has_llm():
        dialog = "\n".join(f"- {a['question']} → {a['answer']}" for a in given) or "(без ответов)"
        raw = get_ai_gateway().structured(
            "submit_summary",
            "Вернуть пересказ цели обучения.",
            SUMMARY_SCHEMA,
            (
                f"Человек хочет изучить: «{goal}».\nУточнения:\n{dialog}\n"
                "Сформулируй пять полей: область; цель (зачем это человеку); уровень (remember — узнавать "
                "термины, understand — объяснять своими словами, apply — решать задачи, create — "
                "создавать своё); что он уже знает; ограничения (срок, часов в неделю, формат). "
                "Плюс важные подтемы. Опирайся ТОЛЬКО на сказанное человеком: чего он не сказал, "
                "оставляй пустым, не придумывай."
            ),
        )
    else:
        # Без модели пересказ собирается из слов человека без домыслов.
        raw = {
            "area": goal,
            "goal": given[0]["answer"] if given else goal,
            # Без модели ответ нельзя отнести к «знает» или «ограничениям»: порядок вопросов
            # после пропусков неизвестен, а домысел хуже пустого поля.
            "knows": "",
            "constraints": {},
            "wishes": [a["answer"] for a in given[1:]],
        }
    return clean_summary(raw, goal)


def as_goal_text(summary: dict[str, Any]) -> str:
    """Подтверждённая цель одной строкой — вход построения графа вместо голого названия."""
    parts = [str(summary.get("area") or "")]
    if summary.get("goal") and summary["goal"] != summary.get("area"):
        parts.append(f"цель: {summary['goal']}")
    if summary.get("knows"):
        parts.append(f"уже знает: {summary['knows']}")
    constraints = summary.get("constraints") or {}
    limits = [
        f"срок {constraints['deadline']}" if constraints.get("deadline") else "",
        f"{constraints['hoursPerWeek']} ч в неделю" if constraints.get("hoursPerWeek") else "",
        f"формат: {constraints['format']}" if constraints.get("format") else "",
    ]
    if any(limits):
        parts.append("ограничения: " + ", ".join(x for x in limits if x))
    if summary.get("wishes"):
        parts.append("важно: " + "; ".join(summary["wishes"]))
    return ". ".join(p for p in parts if p)


# ---- подтверждение ----


def confirm(
    session: Session, user_id: uuid.UUID, domain: str, summary: dict[str, Any]
) -> GoalIntake:
    """Сохранить подтверждённый итог; повторное подтверждение области заменяет прежний."""
    domain = domain.strip()
    if not domain:
        raise GoalIntakeError("no_domain", "Не указана область")
    clean = clean_summary(summary, summary.get("area") or "")
    row = session.query(GoalIntake).filter_by(user_id=user_id, domain=domain).one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = GoalIntake(user_id=user_id, domain=domain, summary=clean, confirmed_at=now)
        session.add(row)
    else:
        row.summary = clean
        row.confirmed_at = now
    session.flush()
    return row


def get_confirmed(session: Session, user_id: uuid.UUID, domain: str) -> GoalIntake | None:
    return session.query(GoalIntake).filter_by(user_id=user_id, domain=domain).one_or_none()


def view(row: GoalIntake | None) -> dict[str, Any]:
    if row is None:
        return {"confirmed": False, "summary": None}
    return {"confirmed": True, "summary": row.summary, "confirmedAt": row.confirmed_at.isoformat()}


__all__ = [
    "GoalIntakeError",
    "as_goal_text",
    "clean_constraints",
    "clean_questions",
    "clean_summary",
    "confirm",
    "get_confirmed",
    "propose_questions",
    "summarize",
    "view",
]
