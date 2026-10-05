"""Рубрики модуля «Программирование/ML». WS4."""

from core.ai_base import GRADE_JSON_SCHEMA

CONCEPT_CHECK = {
    "id": "concept_check",
    "version": 1,
    "module": "ml",
    # Пусто → модель берётся из конфига (LLM_MODEL_SCORING); слаг здесь
    # означал бы привязку рубрики к конкретному провайдеру.
    "model": "",
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

# Статическое ревью не запускает код: оценка может ошибаться в обе стороны.
# Пометка едет вместе с оценкой (см. core.jobs.process_job) и показывается в разборе.
CODE_REVIEW_CAVEAT = (
    "Это статическое ревью: код не запускался и тестами не проверялся. "
    "Оценка корректности — суждение по тексту решения, а не результат прогона."
)

# Версия 2: в живых базах id `ml_code_review` уже занят заглушкой Ф0 (версия 1)
# без соответствия в коде. Рубрики не перезаписываются (NFR-06), поэтому новая
# редакция — новая версия; get_rubric берёт последнюю.
ML_CODE_REVIEW = {
    "id": "ml_code_review",
    "version": 2,
    "module": "ml",
    "model": "",
    "prompt": (
        "Ты проводишь ревью решения задачи на код (ML/программирование) БЕЗ запуска кода. "
        "Оцени по критериям, каждый 0–5: Correctness (решает ли поставленную задачу, "
        "граничные случаи), Numerical stability / Efficiency (численная устойчивость, "
        "лишние циклы и копии, сложность), Idiomatic style (идиомы языка и библиотек, "
        "читаемость), Explanation (пояснения и комментарии к решению). overall — среднее. "
        "Не утверждай, что код проходит или не проходит тесты: ты их не запускал. "
        "Каждую найденную проблему выпиши как ошибку: kind — correctness | stability | "
        "efficiency | style, excerpt — фрагмент кода, correction — исправленный фрагмент, "
        "explanation — почему так лучше; ошибки уйдут в интервальное повторение. "
        "В exemplar дай образцовое решение."
    ),
    "schema": {
        "criteria": [
            {"name": "Correctness", "max": 5},
            {"name": "Numerical stability / Efficiency", "max": 5},
            {"name": "Idiomatic style", "max": 5},
            {"name": "Explanation", "max": 5},
        ],
        "caveat": CODE_REVIEW_CAVEAT,
        "grade_schema": GRADE_JSON_SCHEMA,
    },
}

RUBRICS = [CONCEPT_CHECK, ML_CODE_REVIEW]
