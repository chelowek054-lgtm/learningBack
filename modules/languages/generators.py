"""Генераторы контента модуля «Языки». WS4.

AWL — стартовая колода Academic Word List (демо-выборка; полный список — открытый
вопрос плана). error→card — через общий core.srs.errors_to_card_partials.
"""

from typing import Any

# Демо-выборка Academic Word List (word, определение).
AWL_STARTER: list[tuple[str, str]] = [
    ("analyse", "изучать что-либо детально"),
    ("approach", "способ рассмотрения или подхода"),
    ("concept", "абстрактная идея или принцип"),
    ("consistent", "неизменный, согласованный"),
    ("significant", "существенный, важный"),
    ("establish", "устанавливать, учреждать"),
    ("interpret", "истолковывать значение"),
    ("derive", "выводить, извлекать из источника"),
    ("hypothesis", "предположение для проверки"),
    ("subsequent", "последующий, идущий следом"),
]


def awl_card_partials() -> list[dict[str, Any]]:
    """Заготовки карточек AWL (front/back/source)."""
    return [
        {"front": {"word": w}, "back": {"definition": d}, "source": "awl"}
        for w, d in AWL_STARTER
    ]
