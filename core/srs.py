"""SRS-хелперы backend: инициализация FSRS-состояния и вставка карточек.

Error-log → SRS: ошибки из скоринга становятся карточками (инвариант связи движков).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.models import SrsCard


def initial_fsrs_state(now: datetime) -> dict[str, Any]:
    """Пустая карточка в формате ts-fsrs (клиент оживит `due` в Date при первом ревью)."""
    return {
        "due": now.isoformat(),
        "stability": 0,
        "difficulty": 0,
        "elapsed_days": 0,
        "scheduled_days": 0,
        "reps": 0,
        "lapses": 0,
        "state": 0,  # New
    }


def errors_to_card_partials(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ошибки из Grade → заготовки карточек (module-agnostic)."""
    partials = []
    for e in errors:
        excerpt = e.get("excerpt", "")
        partials.append(
            {
                "front": {"prompt": f"Исправь: «{excerpt}»", "kind": e.get("kind", "")},
                "back": {
                    "correction": e.get("correction", ""),
                    "explanation": e.get("explanation", ""),
                },
                "source": "error_log",
            }
        )
    return partials


def insert_cards(
    session: Session,
    user_id: uuid.UUID,
    module: str,
    partials: list[dict[str, Any]],
    now: datetime,
) -> int:
    """Вставить карточки из заготовок; проставить fsrs_state и due_at. Вернуть кол-во."""
    for p in partials:
        session.add(
            SrsCard(
                user_id=user_id,
                module=module,
                front=p["front"],
                back=p["back"],
                source=p["source"],
                concept_id=p.get("concept_id"),
                fsrs_state=initial_fsrs_state(now),
                due_at=now,
            )
        )
    return len(partials)


# ---- слияние карточки с нескольких устройств (T-0029, R-0019) ----


def _when(value: Any) -> datetime:
    """Время последнего ревью; карточки без ревью старше всех (минимальная дата)."""
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def review_key(state: dict[str, Any] | None) -> tuple[datetime, int, int]:
    """Чем «свежее» состояние карточки: последнее ревью, затем число повторов и провалов.

    Время записи на сервер не участвует: устройство, которое повторяло карточку вчера, но
    синхронизировалось сегодня, не должно затирать повтор, сделанный другим устройством позже.
    """
    state = state or {}
    return (
        _when(state.get("last_review")),
        int(state.get("reps") or 0),
        int(state.get("lapses") or 0),
    )


def incoming_wins(incoming: dict[str, Any] | None, stored: dict[str, Any] | None) -> bool:
    """Принять ли присланное состояние поверх хранимого. Равные не принимаем: повтор идемпотентен."""
    return review_key(incoming) > review_key(stored)
