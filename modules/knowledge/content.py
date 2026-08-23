"""Структура теории внутри узла (KG3-01).

Узел — не ярлык, а контейнер формализованного знания: из этого контента
детерминированно рождаются тест-айтемы и практика (KG3-02), поэтому форма
должна быть предсказуемой, а не «как повезёт с LLM». См. 05-knowledge-model §3.

Два режима разбора:
  * `NodeContent` — тип полей API: неверная форма от клиента даёт 422;
  * `coerce_content` — снисходительный разбор вывода LLM: мусорные поля
    отбрасываются, недостающие заполняются, узел не теряется целиком.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError


class Section(BaseModel):
    """Раздел теории. Примеры и контрпримеры — материал для заданий уровня apply."""

    heading: str = ""
    body: str = ""
    examples: list[str] = Field(default_factory=list)
    counter_examples: list[str] = Field(default_factory=list)


class Reference(BaseModel):
    """Источник — заземление узла (`source='material'` против галлюцинаций)."""

    title: str = ""
    url: str | None = None


class NodeContent(BaseModel):
    summary: str = ""
    sections: list[Section] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)

    def is_groundable(self) -> bool:
        """Хватает ли теории, чтобы генерировать по ней задания.

        Один голый `summary` — это ярлык, а не контейнер знания: заземлять
        генерацию не на чем (KG3-02 такие узлы пропускает).
        """
        return bool(self.sections) or len(self.summary.strip()) >= 120


EMPTY = NodeContent()


def coerce_content(raw: Any) -> dict[str, Any]:
    """Привести произвольный вход к форме NodeContent, ничего не роняя."""
    if isinstance(raw, NodeContent):
        return raw.model_dump()
    if not isinstance(raw, dict):
        return EMPTY.model_dump()
    try:
        return NodeContent.model_validate(raw).model_dump()
    except ValidationError:
        # Спасаем то, что разобралось: summary как строка — минимум, ради которого
        # стоит сохранять узел вообще.
        summary = raw.get("summary")
        return NodeContent(summary=summary if isinstance(summary, str) else "").model_dump()


def ensure_shape(stored: Any) -> dict[str, Any]:
    """Дополнить прочитанный из БД контент недостающими ключами.

    Узлы, созданные до KG3-01, лежат в укороченной форме; читатель не должен
    об этом знать.
    """
    return coerce_content(stored)
