"""Backend-модуль «Языки» (TOEFL/IELTS) — заглушка Фазы 0.

Контракт ModuleBackend (rubrics/generators/grade) вводится в WS2 вместе с core/.
Пока — самодостаточные метаданные без зависимостей.
"""

MODULE_ID = "languages"

# Типы Activity зеркалят клиентский манифест (03-functional §1.1).
ACTIVITY_TYPES = [
    "ielts_writing_task2",
    "ielts_writing_task1",
    "reading_drill",
    "listening_drill",
    "speaking_response",
    "vocab_srs",
]


def rubrics() -> list:
    """Рубрики-оценщики (пусто до Фазы 1)."""
    return []


def generators() -> dict:
    """Генераторы контента (пусто до Фазы 1)."""
    return {}
