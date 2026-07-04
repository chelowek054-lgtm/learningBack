"""Рубрики модуля «Языки». WS4."""

from core.ai_gateway.base import GRADE_JSON_SCHEMA

IELTS_WRITING_TASK2 = {
    "id": "ielts_writing_task2",
    "version": 1,
    "module": "languages",
    "model": "claude-opus-4-8",
    "prompt": (
        "Ты — экзаменатор IELTS Academic Writing Task 2. Оцени эссе по официальным "
        "band descriptors (0–9) по четырём критериям: Task Response, Coherence and "
        "Cohesion, Lexical Resource, Grammatical Range and Accuracy. Для каждого критерия "
        "дай балл (0–9) и краткий комментарий. Итоговый overall — среднее, округлённое до "
        "0.5. Выпиши ключевые ошибки (грамматика, коллокации, связность) с корректировкой и "
        "объяснением. Приведи улучшенный образец 1–2 проблемных предложений."
    ),
    "schema": {
        "criteria": [
            {"name": "Task Response", "max": 9},
            {"name": "Coherence and Cohesion", "max": 9},
            {"name": "Lexical Resource", "max": 9},
            {"name": "Grammatical Range and Accuracy", "max": 9},
        ],
        "grade_schema": GRADE_JSON_SCHEMA,
    },
}

RUBRICS = [IELTS_WRITING_TASK2]
