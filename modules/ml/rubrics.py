"""Рубрики модуля «Программирование/ML». WS4."""

from core.ai_gateway.base import GRADE_JSON_SCHEMA

CONCEPT_CHECK = {
    "id": "concept_check",
    "version": 1,
    "module": "ml",
    "model": "claude-opus-4-8",
    "prompt": (
        "Ты проверяешь понимание концепции ML/программирования. Оцени открытый ответ по "
        "критериям: Correctness (фактическая верность), Completeness (полнота), Explanation "
        "(качество объяснения) — каждый 0–5. overall — среднее. Выпиши неточности/пробелы как "
        "ошибки с корректировкой и объяснением, чтобы они попали в интервальное повторение."
    ),
    "schema": {
        "criteria": [
            {"name": "Correctness", "max": 5},
            {"name": "Completeness", "max": 5},
            {"name": "Explanation", "max": 5},
        ],
        "grade_schema": GRADE_JSON_SCHEMA,
    },
}

RUBRICS = [CONCEPT_CHECK]
