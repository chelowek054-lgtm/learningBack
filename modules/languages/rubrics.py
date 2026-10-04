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

IELTS_WRITING_TASK1 = {
    "id": "ielts_writing_task1",
    "version": 1,
    "module": "languages",
    "model": "",
    "prompt": (
        "Ты — экзаменатор IELTS Academic Writing Task 1: описание графика, таблицы или схемы "
        "(не менее 150 слов). Оцени по официальным band descriptors (0–9) по четырём "
        "критериям: Task Achievement (выделены ли ключевые признаки и тенденции, есть ли "
        "общий обзор, точны ли данные — мнения и объяснения причин в этом задании не нужны), "
        "Coherence and Cohesion, Lexical Resource, Grammatical Range and Accuracy. Для каждого "
        "дай балл (0–9) и краткий комментарий. Итоговый overall — среднее, округлённое до 0.5. "
        "Сверяй числа в тексте с данными задания: неверная цифра — ошибка Task Achievement. "
        "Выпиши ключевые ошибки с корректировкой и объяснением и приведи улучшенный образец "
        "1–2 проблемных предложений."
    ),
    "schema": {
        "criteria": [
            {"name": "Task Achievement", "max": 9},
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

IELTS_SPEAKING = {
    "id": "ielts_speaking",
    "version": 1,
    "module": "languages",
    "model": "",
    "prompt": (
        "Ты — экзаменатор IELTS Speaking. Тебе дана расшифровка устного ответа и тайминги слов "
        "(темп, паузы); звука у тебя нет. Оцени по band descriptors (0–9) по четырём критериям: "
        "Fluency and Coherence (темп, паузы, самоисправления, связность), Lexical Resource, "
        "Grammatical Range and Accuracy, Pronunciation. Pronunciation по тексту оценить нельзя: "
        "поставь осторожный балл по косвенным признакам и прямо напиши в комментарии, что "
        "оценка приблизительна. Итоговый overall — среднее, округлённое до 0.5. Выпиши ключевые "
        "ошибки речи (грамматика, лексика) с корректировкой и объяснением."
    ),
    "schema": {
        "criteria": [
            {"name": "Fluency and Coherence", "max": 9},
            {"name": "Lexical Resource", "max": 9},
            {"name": "Grammatical Range and Accuracy", "max": 9},
            {"name": "Pronunciation", "max": 9},
        ],
        "caveat": (
            "Оценка по тексту и таймингам: произношение по расшифровке не проверить, "
            "балл Pronunciation приблизительный."
        ),
        "grade_schema": GRADE_JSON_SCHEMA,
    },
}

RUBRICS = [
    IELTS_SPEAKING,
    IELTS_WRITING_TASK2,
    IELTS_WRITING_TASK1,
    TOEFL_WRITING_INDEPENDENT,
    TOEFL_WRITING_INTEGRATED,
]
