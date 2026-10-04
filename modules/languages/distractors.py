"""Разбор дистракторов: «почему этот вариант неверен» (T-0038, R-0022).

Приходит фоновой задачей при сети и не блокирует дрилл: ответ проверен локально по payload,
а объяснение — приятное дополнение. Нет модели (заглушка) — берём авторское объяснение вопроса.
"""

from __future__ import annotations

from typing import Any

from core.models import Activity, Job

JOB_TYPE = "explain_distractors"

_TOOL = "explain_distractors"
_DESC = "Объясни, почему каждый неверный вариант ответа не подходит, ссылаясь на текст."
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"option": {"type": "string"}, "why": {"type": "string"}},
                "required": ["option", "why"],
            },
        }
    },
    "required": ["items"],
}


def _prompt(passage: str, question: dict[str, Any], wrong: list[str]) -> str:
    return (
        "Text:\n"
        f"{passage}\n\nQuestion: {question.get('prompt', '')}\n"
        f"Correct answer: {question.get('answer')}\n"
        "Wrong options: " + "; ".join(wrong) + "\n"
        "For each wrong option say in one sentence why it is wrong, citing the text. "
        "Answer in Russian."
    )


def explain_distractors(session, job: Job, gateway) -> dict[str, Any]:
    """Обработчик job: input_ref = {activityId, questionId}. Чужой или пустой вход — ValueError."""
    ref = job.input_ref or {}
    activity = session.get(Activity, ref.get("activityId")) if ref.get("activityId") else None
    if activity is None or activity.user_id != job.user_id:
        raise ValueError("activityId не найден")
    payload = activity.payload or {}
    question = next(
        (q for q in payload.get("questions", []) if q.get("id") == ref.get("questionId")), None
    )
    if question is None or question.get("type") != "mcq":
        raise ValueError("вопрос не найден или не с выбором")
    wrong = [o for o in question.get("options", []) if o != question.get("answer")]
    fallback = [{"option": o, "why": question.get("explanation", "")} for o in wrong]
    out = gateway.structured(
        _TOOL, _DESC, _SCHEMA, _prompt(payload.get("passage", ""), question, wrong)
    )
    items = [
        {"option": i["option"], "why": i["why"]}
        for i in (out or {}).get("items", [])
        if i.get("option") in wrong and i.get("why")
    ]
    return {"questionId": question["id"], "items": items or fallback, "fromModel": bool(items)}
