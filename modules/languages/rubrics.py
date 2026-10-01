"""Рубрики модуля «Языки». WS4."""

from core.ai_gateway.base import GRADE_JSON_SCHEMA

IELTS_WRITING_TASK2 = {
    "id": "ielts_writing_task2",
    "version": 1,
    "module": "languages",
    # Пусто → модель берётся из конфига (LLM_MODEL_SCORING); слаг здесь
    # означал бы привязку рубрики к конкретному провайдеру.
    "model": "",
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

TOEFL_WRITING_INDEPENDENT = {
    "id": "toefl_writing_independent",
    "version": 1,
    "module": "languages",
    "model": "",
    "prompt": (
        "Ты — оценщик TOEFL iBT Writing, задание Writing for an Academic Discussion / "
        "Independent Essay. Оцени текст по шкале 0–5 по трём критериям: Development "
        "(раскрытие и обоснование позиции, примеры), Organization (структура, связность, "
        "переходы), Language Use (грамматика, лексика, разнообразие конструкций). "
        "overall — среднее, округлённое до 0.5. Выпиши ключевые ошибки (грамматика, "
        "коллокации, связность) с корректировкой и объяснением и приведи улучшенный образец "
        "1–2 проблемных предложений."
    ),
    "schema": {
        "criteria": [
            {"name": "Development", "max": 5},
            {"name": "Organization", "max": 5},
            {"name": "Language Use", "max": 5},
        ],
        "grade_schema": GRADE_JSON_SCHEMA,
    },
}

TOEFL_WRITING_INTEGRATED = {
    "id": "toefl_writing_integrated",
    "version": 1,
    "module": "languages",
    "model": "",
    "prompt": (
        "Ты — оценщик TOEFL iBT Writing, задание Integrated: пересказ лекции в связи с "
        "прочитанным текстом. Оцени по шкале 0–5 по трём критериям: Content Accuracy "
        "(точность и полнота передачи пунктов лекции и их связи с текстом, без собственного "
        "мнения), Organization (структура, связность, переходы), Language Use (грамматика, "
        "лексика, перефраз вместо копирования). overall — среднее, округлённое до 0.5. "
        "Выпиши ключевые ошибки с корректировкой и объяснением и приведи улучшенный образец "
        "1–2 проблемных предложений."
    ),
    "schema": {
        "criteria": [
            {"name": "Content Accuracy", "max": 5},
            {"name": "Organization", "max": 5},
            {"name": "Language Use", "max": 5},
        ],
        "grade_schema": GRADE_JSON_SCHEMA,
    },
}

RUBRICS = [IELTS_WRITING_TASK2, TOEFL_WRITING_INDEPENDENT, TOEFL_WRITING_INTEGRATED]
