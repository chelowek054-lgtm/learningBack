"""Оценка ответа на зонд (KG4-03).

Закрытый вопрос проверяется локально и бесплатно — по вариантам самого задания.
Открытый уходит в AI-роль `estimate_mastery`, но и она заземлена: модель видит
эталон из узла, а не судит «вообще».
"""

from __future__ import annotations

import re
from typing import Any

from core.ai_gateway import get_ai_gateway
from core.config import settings
from modules.knowledge.assessment import AssessmentItem

ESTIMATE_IO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "description": "0 — неверно, 1 — верно, между — частично верно",
        },
        "explanation": {"type": "string", "description": "коротко, что не так или что верно"},
    },
    "required": ["score", "explanation"],
}

_TOOL = "submit_estimate"
_TOOL_DESC = "Оценить ответ ученика по эталону."

_WORD = re.compile(r"\w+", re.UNICODE)
# Ниже этой доли совпадения открытый ответ считаем незачётом (эвристика без ключа).
_OVERLAP_PASS = 0.5


def score_answer(item: AssessmentItem, answer: Any) -> tuple[float, str]:
    """Вернуть (score 0..1, объяснение)."""
    if item.options:
        return _score_choice(item, answer)
    return _score_open(item, str(answer or ""))


def _score_choice(item: AssessmentItem, answer: Any) -> tuple[float, str]:
    """Выбор варианта: по индексу или по тексту. LLM не нужен."""
    chosen = None
    if isinstance(answer, int) and 0 <= answer < len(item.options):
        chosen = item.options[answer]
    else:
        needle = str(answer or "").strip().casefold()
        for option in item.options:
            if option.text.strip().casefold() == needle:
                chosen = option
                break
    if chosen is None:
        return 0.0, "Вариант не распознан."
    if chosen.correct:
        return 1.0, chosen.why or "Верно."
    return 0.0, chosen.why or "Неверно."


def _score_open(item: AssessmentItem, answer: str) -> tuple[float, str]:
    if not answer.strip():
        return 0.0, "Ответ пуст."
    if not settings.claude_api_key:
        return _overlap_score(item.expected, answer)

    prompt = (
        "Оцени ответ ученика по эталону. Опирайся только на эталон, "
        "не добавляй своих требований.\n\n"
        f"ВОПРОС: {item.prompt}\n"
        f"ЭТАЛОН: {item.expected}\n"
        f"ОТВЕТ УЧЕНИКА: {answer}"
    )
    raw = get_ai_gateway().structured(_TOOL, _TOOL_DESC, ESTIMATE_IO_SCHEMA, prompt)
    if not isinstance(raw, dict) or not isinstance(raw.get("score"), int | float):
        return _overlap_score(item.expected, answer)
    score = max(0.0, min(1.0, float(raw["score"])))
    return score, str(raw.get("explanation", ""))


def _overlap_score(expected: str, answer: str) -> tuple[float, str]:
    """Заглушка без ключа: доля значимых слов эталона, встреченных в ответе.

    Грубо, но детерминированно и заземлено на эталон — этого хватает, чтобы
    разрабатывать и тестировать плейсмент целиком без обращения к LLM.
    """
    expected_words = {w.casefold() for w in _WORD.findall(expected) if len(w) > 3}
    if not expected_words:
        return 0.5, "Эталон слишком короткий для автоматической сверки."
    answer_words = {w.casefold() for w in _WORD.findall(answer)}
    hit = len(expected_words & answer_words) / len(expected_words)
    score = round(min(1.0, hit / _OVERLAP_PASS), 3) if hit < _OVERLAP_PASS else 1.0
    verdict = "Ответ покрывает эталон." if score >= 1.0 else "Ответ покрывает эталон частично."
    return score, f"{verdict} Совпадение по ключевым словам: {hit:.0%}."
