"""Этапы и уровни понятий (T-0087, A-0030): метки, которыми профиль навыка раскладывает граф.

Этап — название и порядковый номер группы понятий («Определители», 4); уровень — basic, middle или
advanced; «необязательное» — ветвь для знакомства, без которой цель достижима. Всё это метки на
понятии: отдельной сущности этапа нет. Модель присылает метки как попало, поэтому они чистятся.
"""

from __future__ import annotations

from typing import Any

LEVELS = ("basic", "middle", "advanced")
MAX_STAGE_TITLE = 120
_ALIASES = {
    "base": "basic",
    "beginner": "basic",
    "easy": "basic",
    "mid": "middle",
    "medium": "middle",
    "intermediate": "middle",
    "adv": "advanced",
    "hard": "advanced",
    "expert": "advanced",
}


def clean_level(raw: Any) -> str | None:
    """Уровень из известных трёх; неизвестное — None, а не выдумка."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    value = _ALIASES.get(value, value)
    return value if value in LEVELS else None


def clean_order(raw: Any) -> int | None:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 999 else None


def stage_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Поля `stage`, `stage_order`, `level`, `optional` для модели понятия из узла черновика."""
    title = node.get("stage")
    title = title.strip()[:MAX_STAGE_TITLE] if isinstance(title, str) and title.strip() else None
    return {
        "stage": title,
        "stage_order": clean_order(node.get("stageOrder", node.get("stage_order")))
        if title
        else None,
        "level": clean_level(node.get("level")),
        "optional": node.get("optional") is True,
    }
