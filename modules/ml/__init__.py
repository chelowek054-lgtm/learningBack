"""Backend-модуль «Программирование/ML» — заглушка Фазы 0.

Контракт ModuleBackend (rubrics/generators/grade) вводится в WS2 вместе с core/.
"""

MODULE_ID = "ml"

# Типы Activity зеркалят клиентский манифест (03-functional §2.1).
ACTIVITY_TYPES = [
    "material_read",
    "concept_recall",
    "concept_srs",
    "code_task",
]


def rubrics() -> list:
    """Рубрики-оценщики (пусто до Фазы 1)."""
    return []


def generators() -> dict:
    """Генераторы контента (пусто до Фазы 1)."""
    return {}
