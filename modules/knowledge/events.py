"""Подписка на изменение узла графа (T-0052).

Узел меняется в нескольких местах (правка куратором, промоция, личная правка, перестройка
модели); тем, кто держит производные данные (кэш заданий, способы запоминания), нужно
знать об этом без того, чтобы граф знал о них. Поэтому изменение объявляется событием,
а подписчик сам решает, что с ним делать.

Обработчики вызываются в процессе API синхронно (A-0021: один процесс) и не должны
бросать: ошибка подписчика не отменяет правку узла и не мешает другим подписчикам.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeChanged:
    node_id: uuid.UUID
    domain: str
    version: int
    #: Чья правка: None — канон (видна всем), иначе id пользователя — личный слой.
    user_id: uuid.UUID | None = None


_subscribers: list[Callable[[NodeChanged], None]] = []


def subscribe(callback: Callable[[NodeChanged], None]) -> Callable[[], None]:
    """Подписаться на изменения узлов; возвращает отписку."""
    _subscribers.append(callback)
    return lambda: unsubscribe(callback)


def unsubscribe(callback: Callable[[NodeChanged], None]) -> None:
    if callback in _subscribers:
        _subscribers.remove(callback)


def emit(event: NodeChanged) -> None:
    for callback in list(_subscribers):
        try:
            callback(event)
        except Exception:  # noqa: BLE001 — подписчик не должен ломать правку узла
            log.warning("Подписчик на изменение узла упал", exc_info=True)
