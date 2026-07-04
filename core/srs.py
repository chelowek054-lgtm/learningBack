"""SRS-хелперы backend: инициализация FSRS-состояния и вставка карточек.

Error-log → SRS: ошибки из скоринга становятся карточками (инвариант связи движков).
"""

from __future__ import annotations

import uuid
from datetime import datetime
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
                fsrs_state=initial_fsrs_state(now),
                due_at=now,
            )
        )
    return len(partials)
